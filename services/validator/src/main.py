"""Validator service — LLM-based pair verification + expiration cleanup.

Periodically scans all matched pairs and:
  1. Removes pairs where either market is expired
  2. Uses LLM to verify unvalidated pairs actually match
  3. Cleans expired Polymarket markets from the system
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
import redis.asyncio as aioredis

sys.path.insert(0, "/app")

from llm import LLMClient
from shared.redis_keys import (
    PAIRS_SET,
    market_key,
    markets_set_key,
    orderbook_key,
    pair_key,
    price_key,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("validator")

_MARKET_DATA_PREFIX = os.getenv("ARB_MARKET_DATA_PREFIX", "")
_PAIR_DATA_PREFIX = os.getenv("ARB_PAIR_DATA_PREFIX", "")


def _parse_model_line(line: str) -> str:
    value = (line or "").strip()
    if not value:
        return ""
    if value.endswith(")") and "(" in value:
        return value.rsplit("(", 1)[-1].rstrip(")").strip()
    return value


def _resolve_llm_model(default_model: str) -> str:
    model_file = os.getenv("EXO_MODEL_FILE", "/home/taiwei/Constructure/infra/exo/model.txt")
    env_model = os.getenv("LLM_MODEL", "").strip()

    try:
        with open(model_file, "r", encoding="utf-8") as fh:
            candidates = [_parse_model_line(line) for line in fh]
    except OSError:
        candidates = []

    candidates = [candidate for candidate in candidates if candidate]
    if env_model and env_model in candidates:
        return env_model
    if candidates:
        return candidates[0]
    if env_model:
        return env_model
    return default_model

# Config
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://192.168.20.1:52415/v1")
LLM_MODEL = _resolve_llm_model("mlx-community/GLM-4.7-Flash-5bit")
LLM_SOURCE = os.getenv("LLM_SOURCE", "arb-validator").strip() or "arb-validator"
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
VALIDATOR_INTERVAL_SECS = int(os.getenv("VALIDATOR_INTERVAL_SECS", "300"))
VALIDATOR_BATCH_DELAY = float(os.getenv("VALIDATOR_BATCH_DELAY", "1.0"))
VALIDATOR_MAX_CONCURRENCY = max(1, int(os.getenv("VALIDATOR_MAX_CONCURRENCY", "2")))
VALIDATOR_MAX_PAIRS_PER_RUN = max(0, int(os.getenv("VALIDATOR_MAX_PAIRS_PER_RUN", "0")))
PAIR_VALIDATED_TTL = 86400 * 7  # 7 days, matching pair TTL
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "markets")


def _market_key(exchange: str, market_id: str) -> str:
    return f"{_MARKET_DATA_PREFIX}{market_key(exchange, market_id)}"


def _orderbook_key(exchange: str, market_id: str) -> str:
    return f"{_MARKET_DATA_PREFIX}{orderbook_key(exchange, market_id)}"


def _price_key(exchange: str, market_id: str) -> str:
    return f"{_MARKET_DATA_PREFIX}{price_key(exchange, market_id)}"


def _markets_set_key(exchange: str) -> str:
    return f"{_MARKET_DATA_PREFIX}{markets_set_key(exchange)}"


def _pair_key(pair_id: str) -> str:
    return f"{_PAIR_DATA_PREFIX}{pair_key(pair_id)}"


def _pairs_set_key() -> str:
    return f"{_PAIR_DATA_PREFIX}{PAIRS_SET}"


def _validated_key(pair_id: str) -> str:
    return f"{_PAIR_DATA_PREFIX}pair_validated:{pair_id}"


def _market_point_id(exchange: str, market_id: str) -> int:
    return int(hashlib.sha256(f"{exchange}:{market_id}".encode()).hexdigest()[:16], 16)


def _parse_timestamp(raw: object) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _is_expired(meta: dict, now_ts: float) -> bool:
    status = str(meta.get("status", "open")).strip().lower()
    if status and status not in {"open", "active", "trading"}:
        return True
    closed = meta.get("closed")
    if isinstance(closed, bool) and closed:
        return True
    end_ts = _parse_timestamp(meta.get("end_date") or meta.get("close_time"))
    if end_ts is not None and end_ts <= now_ts:
        return True
    return False


async def _load_meta(r: aioredis.Redis, exchange: str, market_id: str) -> Optional[dict]:
    raw = await r.get(_market_key(exchange, market_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def _remove_pair(r: aioredis.Redis, pair_id: str) -> None:
    pipe = r.pipeline(transaction=False)
    pipe.delete(_pair_key(pair_id), _validated_key(pair_id))
    pipe.srem(_pairs_set_key(), pair_id)
    await pipe.execute()


async def _remove_market(
    r: aioredis.Redis,
    qdrant: httpx.AsyncClient,
    exchange: str,
    market_id: str,
) -> bool:
    point_id = _market_point_id(exchange, market_id)
    try:
        response = await qdrant.post(
            f"/collections/{QDRANT_COLLECTION}/points/delete?wait=false",
            json={"points": [point_id]},
        )
        response.raise_for_status()
    except Exception:
        logger.exception("Failed to delete Qdrant point for %s/%s", exchange, market_id)
        return False

    pipe = r.pipeline(transaction=False)
    pipe.delete(
        _market_key(exchange, market_id),
        _orderbook_key(exchange, market_id),
        _price_key(exchange, market_id),
    )
    pipe.srem(_markets_set_key(exchange), market_id)
    await pipe.execute()
    return True


async def cleanup_expired_polymarket(r: aioredis.Redis, qdrant: httpx.AsyncClient) -> int:
    """Remove expired Polymarket markets."""
    now_ts = time.time()
    market_ids = sorted(await r.smembers(_markets_set_key("polymarket")))
    if not market_ids:
        return 0

    raw_metas = await r.mget([_market_key("polymarket", mid) for mid in market_ids])
    removed = 0
    for market_id, raw in zip(market_ids, raw_metas):
        if not raw:
            if await _remove_market(r, qdrant, "polymarket", market_id):
                removed += 1
            continue
        try:
            meta = json.loads(raw)
        except json.JSONDecodeError:
            if await _remove_market(r, qdrant, "polymarket", market_id):
                removed += 1
            continue
        if _is_expired(meta, now_ts):
            if await _remove_market(r, qdrant, "polymarket", market_id):
                removed += 1

    return removed


async def validate_pairs(r: aioredis.Redis, llm: LLMClient) -> dict:
    """Scan all pairs: remove expired, LLM-validate new ones."""
    now_ts = time.time()
    stats = {"total": 0, "expired": 0, "validated": 0, "rejected": 0, "skipped": 0, "errors": 0}

    pair_ids = list(await r.smembers(_pairs_set_key()))
    random.shuffle(pair_ids)
    stats["total"] = len(pair_ids)
    if not pair_ids:
        return stats

    raw_pairs = await r.mget([_pair_key(pid) for pid in pair_ids])
    already_validated = set()
    validated_flags = await r.mget([_validated_key(pid) for pid in pair_ids])
    for pid, flag in zip(pair_ids, validated_flags):
        if flag:
            already_validated.add(pid)
    pending = []

    for pid, raw in zip(pair_ids, raw_pairs):
        if not raw:
            await _remove_pair(r, pid)
            stats["expired"] += 1
            continue

        try:
            pair = json.loads(raw)
        except json.JSONDecodeError:
            await _remove_pair(r, pid)
            stats["expired"] += 1
            continue

        kalshi_id = pair.get("kalshi_market_id", "")
        poly_id = pair.get("polymarket_market_id", "")

        # Check expiration on both sides
        kalshi_meta = await _load_meta(r, "kalshi", kalshi_id)
        poly_meta = await _load_meta(r, "polymarket", poly_id)

        if not kalshi_meta or not poly_meta:
            await _remove_pair(r, pid)
            stats["expired"] += 1
            continue

        if _is_expired(kalshi_meta, now_ts) or _is_expired(poly_meta, now_ts):
            await _remove_pair(r, pid)
            stats["expired"] += 1
            logger.info("Removed expired pair %s: K=%s P=%s", pid, kalshi_id, poly_id)
            continue

        # Skip if already LLM-validated
        if pid in already_validated:
            stats["skipped"] += 1
            continue

        pending.append((pid, kalshi_meta, poly_meta))
        if VALIDATOR_MAX_PAIRS_PER_RUN and len(pending) >= VALIDATOR_MAX_PAIRS_PER_RUN:
            break

    semaphore = asyncio.Semaphore(VALIDATOR_MAX_CONCURRENCY)

    async def _validate_one(pid: str, kalshi_meta: dict, poly_meta: dict):
        async with semaphore:
            result = await llm.validate_pair(
                kalshi_title=kalshi_meta.get("title", ""),
                kalshi_desc=kalshi_meta.get("description", ""),
                kalshi_yes_outcome=kalshi_meta.get("yes_sub_title", "") or "",
                kalshi_end_date=kalshi_meta.get("end_date", "") or "",
                poly_title=poly_meta.get("title", ""),
                poly_desc=poly_meta.get("description", ""),
                poly_end_date=poly_meta.get("end_date", "") or "",
            )
            if VALIDATOR_BATCH_DELAY > 0:
                await asyncio.sleep(VALIDATOR_BATCH_DELAY)
            return pid, kalshi_meta, poly_meta, result

    tasks = [
        asyncio.create_task(_validate_one(pid, kalshi_meta, poly_meta))
        for pid, kalshi_meta, poly_meta in pending
    ]

    for task in asyncio.as_completed(tasks):
        pid, kalshi_meta, poly_meta, result = await task
        if result is None:
            stats["errors"] += 1
            logger.warning("LLM validation failed for pair %s", pid)
        elif result:
            await r.set(_validated_key(pid), "1", ex=PAIR_VALIDATED_TTL)
            stats["validated"] += 1
            logger.info(
                "LLM CONFIRMED pair %s: K=%s P=%s",
                pid,
                kalshi_meta.get("title", "")[:60],
                poly_meta.get("title", "")[:60],
            )
        else:
            await _remove_pair(r, pid)
            stats["rejected"] += 1
            logger.info(
                "LLM REJECTED pair %s: K=%s P=%s",
                pid,
                kalshi_meta.get("title", "")[:60],
                poly_meta.get("title", "")[:60],
            )

    return stats


async def run_once(r: aioredis.Redis, qdrant: httpx.AsyncClient, llm: LLMClient) -> None:
    # Clean expired Polymarket markets
    poly_removed = await cleanup_expired_polymarket(r, qdrant)
    if poly_removed:
        logger.info("Removed %d expired Polymarket markets", poly_removed)

    # Validate pairs
    stats = await validate_pairs(r, llm)
    logger.info(
        "Validation complete: total=%d expired=%d validated=%d rejected=%d skipped=%d errors=%d",
        stats["total"],
        stats["expired"],
        stats["validated"],
        stats["rejected"],
        stats["skipped"],
        stats["errors"],
    )


async def main() -> None:
    logger.info(
        "Starting validator (interval=%ds, model=%s, endpoint=%s)",
        VALIDATOR_INTERVAL_SECS,
        LLM_MODEL,
        LLM_BASE_URL,
    )

    r = aioredis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD or None,
        decode_responses=True,
    )
    await r.ping()
    logger.info("Redis connected")

    llm = LLMClient(
        base_url=LLM_BASE_URL,
        model=LLM_MODEL,
        source=LLM_SOURCE,
        max_retries=LLM_MAX_RETRIES,
    )
    logger.info("LLM client ready")
    qdrant = httpx.AsyncClient(
        base_url=f"http://{QDRANT_HOST}:{QDRANT_PORT}",
        timeout=10.0,
    )
    logger.info("Qdrant cleanup client ready: %s:%d/%s", QDRANT_HOST, QDRANT_PORT, QDRANT_COLLECTION)

    while True:
        try:
            await run_once(r, qdrant, llm)
        except Exception:
            logger.exception("Validation run failed")
        logger.info("Sleeping %d seconds...", VALIDATOR_INTERVAL_SECS)
        await asyncio.sleep(VALIDATOR_INTERVAL_SECS)


if __name__ == "__main__":
    asyncio.run(main())
