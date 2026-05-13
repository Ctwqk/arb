"""Executor Kalshi — consumes trade signals and places Kalshi orders.

Reads from stream:trade_signals (consumer group cg:executor-kalshi).
Writes order updates to stream:order_updates.
Persists trade records to PostgreSQL.
Updates Redis position cache.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import sys
import time

import asyncpg
import redis.asyncio as aioredis

sys.path.insert(0, "/app")

from kalshi_client import KalshiClient
from shared.config import config
from shared.models import OrderStatus, OrderUpdate
from shared.redis_keys import (
    CG_EXECUTOR_KALSHI,
    POSITIONS_SET,
    STREAM_ORDER_UPDATES,
    STREAM_TRADE_SIGNALS,
    position_key,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("executor-kalshi")

_CONSUMER = "executor-kalshi-0"
_BATCH = 5
_BLOCK_MS = 500


async def ensure_consumer_group(r: aioredis.Redis) -> None:
    try:
        await r.xgroup_create(STREAM_TRADE_SIGNALS, CG_EXECUTOR_KALSHI, id="$", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def publish_order_update(r: aioredis.Redis, update: OrderUpdate) -> None:
    payload = json.loads(update.model_dump_json())
    await r.xadd(
        STREAM_ORDER_UPDATES,
        {k: str(v) for k, v in payload.items()},
        maxlen=10000,
        approximate=True,
    )


async def record_trade(db: asyncpg.Connection, signal: dict, order: dict, status: str) -> None:
    """Write completed trade to PostgreSQL."""
    await db.execute(
        """
        INSERT INTO trades (
            signal_id, exchange, market_id, side, size, price,
            expected_edge, order_id, status, filled_size, avg_price, ts
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        ON CONFLICT (order_id) DO UPDATE
            SET status=$9, filled_size=$10, avg_price=$11, updated_at=NOW()
        """,
        signal.get("signal_id", ""),
        "kalshi",
        signal.get("kalshi_market_id", ""),
        signal.get("kalshi_side", ""),
        float(signal.get("size", 0)),
        float(signal.get("kalshi_price", 0)),
        float(signal.get("expected_edge", 0)),
        order.get("id", ""),
        status,
        float(order.get("remaining_count", 0)),
        float(order.get("yes_price", order.get("no_price", 0))) / 100.0,
        time.time(),
    )


async def handle_signal(
    r: aioredis.Redis,
    db: asyncpg.Connection,
    client: KalshiClient,
    signal: dict,
) -> None:
    # ── Validate required fields ──────────────────────────────────────────────
    required = ("signal_id", "kalshi_market_id", "kalshi_side", "size", "kalshi_price")
    if not all(signal.get(f) for f in required):
        logger.warning("Dropping malformed signal: missing %s", [f for f in required if not signal.get(f)])
        return

    signal_id = signal["signal_id"]
    market_id = signal["kalshi_market_id"]
    side = signal["kalshi_side"]
    size = float(signal["size"])
    price_frac = float(signal["kalshi_price"])

    if size < 1:
        return

    # ── Idempotency: skip if already processed ────────────────────────────────
    existing = await db.fetchval("SELECT 1 FROM trades WHERE signal_id = $1 AND exchange = 'kalshi'", signal_id)
    if existing:
        logger.info("Signal %s already processed — skipping", signal_id[:8])
        return

    # Convert fraction price to cents (Kalshi uses integer cents 1–99)
    price_cents = max(1, min(99, round(price_frac * 100)))
    count = max(1, math.floor(size))

    logger.info(
        "Executing Kalshi signal %s | %s %s %d@%dc",
        signal_id[:8],
        market_id,
        side,
        count,
        price_cents,
    )

    order: dict = {}
    try:
        order = await client.place_order(
            ticker=market_id,
            side=side,
            action="buy",
            count=count,
            price_cents=price_cents,
            client_order_id=signal_id,
        )
        order_id = order.get("id", "")

        # Emit pending update
        update = OrderUpdate(
            signal_id=signal_id,
            executor="kalshi",
            exchange_order_id=order_id,
            status=OrderStatus.OPEN,
        )
        await publish_order_update(r, update)

        # Wait for fill
        final_order = await client.wait_for_fill(
            order_id,
            timeout=config.order_timeout_secs,
            poll_interval=config.order_poll_interval,
        )
        status_str = final_order.get("status", "unknown")
        filled = final_order.get("remaining_count", 0)  # remaining = unfilled
        filled_size = count - int(filled)
        avg_price_cents = final_order.get(
            "yes_price" if side == "yes" else "no_price", price_cents
        )

        final_status = OrderStatus.FILLED if status_str == "filled" else OrderStatus.CANCELLED
        update = OrderUpdate(
            signal_id=signal_id,
            executor="kalshi",
            exchange_order_id=order_id,
            status=final_status,
            filled_size=filled_size,
            avg_price=avg_price_cents / 100.0,
        )
        await publish_order_update(r, update)

        # Persist to Postgres first (source of truth)
        await record_trade(db, signal, final_order, final_status.value)

        # Then update position in Redis
        if filled_size > 0:
            pos_key = position_key("kalshi", market_id)
            await r.incrbyfloat(pos_key, filled_size)
            await r.sadd(POSITIONS_SET, pos_key)
            await r.expire(pos_key, 86400 * 30)

        logger.info(
            "Kalshi order %s | status=%s filled=%d/%d",
            order_id[:8],
            final_status.value,
            filled_size,
            count,
        )

    except Exception as exc:
        logger.error("Kalshi execution error for signal %s: %s", signal_id[:8], exc)
        order_id = order.get("id", signal_id)
        update = OrderUpdate(
            signal_id=signal_id,
            executor="kalshi",
            exchange_order_id=order_id,
            status=OrderStatus.FAILED,
        )
        await publish_order_update(r, update)
        # Persist failure to Postgres for audit
        try:
            await record_trade(db, signal, order, OrderStatus.FAILED.value)
        except Exception:
            logger.exception("Failed to persist failed trade record")


async def main() -> None:
    r = aioredis.Redis(
        host=config.redis_host,
        port=config.redis_port,
        password=config.redis_password or None,
        decode_responses=True,
    )
    await r.ping()

    db = await asyncpg.connect(config.postgres_dsn)
    client = KalshiClient(
        api_key_id=config.kalshi_api_key_id,
        private_key_pem=config.kalshi_private_key_pem,
        api_base=config.kalshi_api_base,
    )

    await ensure_consumer_group(r)
    logger.info("Executor-Kalshi ready")

    try:
        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=CG_EXECUTOR_KALSHI,
                    consumername=_CONSUMER,
                    streams={STREAM_TRADE_SIGNALS: ">"},
                    count=_BATCH,
                    block=_BLOCK_MS,
                )
                if not messages:
                    continue
                for _stream, entries in messages:
                    for entry_id, fields in entries:
                        try:
                            await handle_signal(r, db, client, fields)
                        finally:
                            # Always ack to prevent re-processing (idempotency check guards against dupes)
                            await r.xack(STREAM_TRADE_SIGNALS, CG_EXECUTOR_KALSHI, entry_id)
            except aioredis.ConnectionError:
                logger.warning("Redis connection lost, retrying...")
                await asyncio.sleep(2)
            except Exception as exc:
                logger.exception("Executor-Kalshi loop error: %s", exc)
                await asyncio.sleep(1)
    finally:
        await client.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
