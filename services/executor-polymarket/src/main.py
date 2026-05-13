"""Executor Polymarket — consumes trade signals and places Polymarket orders.

IMPORTANT: This container MUST run inside the vpn-polymarket network namespace
so all outbound traffic to Polymarket is routed through the VPN.

Launch via:
  ip netns exec vpn-polymarket docker run --network=host \\
    --env-file .env arb-executor-polymarket

Redis/Postgres are reachable via the veth bridge IP (REDIS_HOST env var).
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

from poly_client import PolymarketClient
from shared.config import config
from shared.models import OrderStatus, OrderUpdate
from shared.redis_keys import (
    CG_EXECUTOR_POLY,
    POSITIONS_SET,
    STREAM_ORDER_UPDATES,
    STREAM_TRADE_SIGNALS,
    market_key,
    position_key,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("executor-polymarket")

_CONSUMER = "executor-polymarket-0"
_BATCH = 5
_BLOCK_MS = 500


async def ensure_consumer_group(r: aioredis.Redis) -> None:
    try:
        await r.xgroup_create(STREAM_TRADE_SIGNALS, CG_EXECUTOR_POLY, id="$", mkstream=True)
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


async def get_token_id(r: aioredis.Redis, market_id: str, side: str) -> str | None:
    """Look up YES or NO token_id from Redis market metadata."""
    raw = await r.get(market_key("polymarket", market_id))
    if not raw:
        return None
    meta = json.loads(raw)
    if side == "yes":
        return meta.get("yes_token_id")
    return meta.get("no_token_id")


async def record_trade(
    db: asyncpg.Connection,
    signal: dict,
    order: dict,
    status: str,
) -> None:
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
        "polymarket",
        signal.get("polymarket_market_id", ""),
        signal.get("poly_side", ""),
        float(signal.get("size", 0)),
        float(signal.get("poly_price", 0)),
        float(signal.get("expected_edge", 0)),
        order.get("id", order.get("orderID", "")),
        status,
        float(order.get("size_matched", 0)),
        float(order.get("price", signal.get("poly_price", 0))),
        time.time(),
    )


async def handle_signal(
    r: aioredis.Redis,
    db: asyncpg.Connection,
    client: PolymarketClient,
    signal: dict,
) -> None:
    # ── Validate required fields ──────────────────────────────────────────────
    required = ("signal_id", "polymarket_market_id", "poly_side", "size", "poly_price")
    if not all(signal.get(f) for f in required):
        logger.warning("Dropping malformed signal: missing %s", [f for f in required if not signal.get(f)])
        return

    signal_id = signal["signal_id"]
    market_id = signal["polymarket_market_id"]
    side = signal["poly_side"]
    size = float(signal["size"])
    price = float(signal["poly_price"])

    if size < 1:
        return

    # ── Idempotency: skip if already processed ────────────────────────────────
    existing = await db.fetchval("SELECT 1 FROM trades WHERE signal_id = $1 AND exchange = 'polymarket'", signal_id)
    if existing:
        logger.info("Signal %s already processed — skipping", signal_id[:8])
        return

    # Resolve token_id from Redis cache
    token_id = await get_token_id(r, market_id, side)
    if not token_id:
        logger.warning("No token_id found for %s side=%s — skipping", market_id, side)
        return

    # Polymarket: "BUY" to buy YES/NO shares
    poly_side = "BUY"

    # Size in USDC = contracts * price (each contract pays $1 if correct)
    usdc_size = round(size * price, 2)
    if usdc_size < 1.0:
        logger.warning("Signal %s: USDC size too small (%.2f) — skipping", signal_id[:8], usdc_size)
        return

    logger.info(
        "Executing Polymarket signal %s | %s %s token=%s price=%.3f size=$%.2f",
        signal_id[:8],
        market_id,
        side,
        token_id[:16],
        price,
        usdc_size,
    )

    order: dict = {}
    try:
        order = await client.place_order(
            token_id=token_id,
            side=poly_side,
            price=price,
            size=usdc_size,
        )
        order_id = order.get("orderID", order.get("id", signal_id))

        update = OrderUpdate(
            signal_id=signal_id,
            executor="polymarket",
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
        status_str = (final_order.get("status") or "").lower()
        filled_size = float(final_order.get("size_matched", 0))
        avg_price = float(final_order.get("price", price))

        final_status = (
            OrderStatus.FILLED if status_str in ("matched", "filled")
            else OrderStatus.CANCELLED
        )
        update = OrderUpdate(
            signal_id=signal_id,
            executor="polymarket",
            exchange_order_id=order_id,
            status=final_status,
            filled_size=filled_size,
            avg_price=avg_price,
        )
        await publish_order_update(r, update)

        # Persist to Postgres first (source of truth)
        await record_trade(db, signal, final_order, final_status.value)

        # Then update position in Redis
        if filled_size > 0:
            pos_key = position_key("polymarket", market_id)
            await r.incrbyfloat(pos_key, filled_size)
            await r.sadd(POSITIONS_SET, pos_key)
            await r.expire(pos_key, 86400 * 30)

        logger.info(
            "Polymarket order %s | status=%s filled=%.2f",
            order_id[:16],
            final_status.value,
            filled_size,
        )

    except Exception as exc:
        logger.error("Polymarket execution error for signal %s: %s", signal_id[:8], exc)
        order_id = order.get("orderID", order.get("id", signal_id))
        update = OrderUpdate(
            signal_id=signal_id,
            executor="polymarket",
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
    logger.info("Connected to Redis at %s:%d", config.redis_host, config.redis_port)

    db = await asyncpg.connect(config.postgres_dsn)

    client = PolymarketClient(
        private_key=config.poly_private_key,
        api_key=config.poly_api_key,
        api_secret=config.poly_api_secret,
        api_passphrase=config.poly_api_passphrase,
        chain_id=config.poly_chain_id,
        host=config.poly_clob_host,
    )

    await ensure_consumer_group(r)
    logger.info("Executor-Polymarket ready (VPN namespace)")

    try:
        while True:
            try:
                messages = await r.xreadgroup(
                    groupname=CG_EXECUTOR_POLY,
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
                            await r.xack(STREAM_TRADE_SIGNALS, CG_EXECUTOR_POLY, entry_id)
            except aioredis.ConnectionError:
                logger.warning("Redis connection lost, retrying...")
                await asyncio.sleep(2)
            except Exception as exc:
                logger.exception("Executor-Polymarket loop error: %s", exc)
                await asyncio.sleep(1)
    finally:
        await client.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
