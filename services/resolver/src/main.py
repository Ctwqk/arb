"""Resolver service — cross-exchange market matching.

Responsibilities:
  - Bootstrap from cached markets on startup (cold-start backfill)
  - Consume market_meta events from stream:market_updates
  - Embed each market title and store in Qdrant
  - Find matching markets on the opposite exchange
  - Periodically remove expired/stale markets and stale pairs
  - Publish MatchedPair records to Redis (cache + stream)

Uses sentence-transformers + Qdrant for semantic matching.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import sys
import time
from types import SimpleNamespace
from typing import Dict, List, Optional, Set, Tuple

import httpx
import redis.asyncio as aioredis
from qdrant_client import AsyncQdrantClient

sys.path.insert(0, "/app")

from embedder import Embedder
from matcher import MarketMatcher, build_match_text
from shared.config import config
from shared.redis_keys import (
    CG_RESOLVER,
    PAIRS_SET,
    STREAM_MARKET_UPDATES,
    market_key,
    markets_set_key,
    orderbook_key,
    price_key,
    pair_key,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("resolver")

# Shadow deployments can override the market/pair namespace without changing
# the default live key layout.
_MARKET_DATA_PREFIX = os.getenv("ARB_MARKET_DATA_PREFIX", "")
_PAIR_DATA_PREFIX = os.getenv("ARB_PAIR_DATA_PREFIX", "")
_STREAM_MARKET_UPDATES = os.getenv(
    "RESOLVER_MARKET_UPDATES_STREAM",
    f"{_MARKET_DATA_PREFIX}{STREAM_MARKET_UPDATES}",
)
_MATCHED_MARKETS_STREAM = os.getenv(
    "RESOLVER_MATCHED_MARKETS_STREAM",
    f"{_PAIR_DATA_PREFIX}stream:matched_markets",
)
_PAIRS_SET = f"{_PAIR_DATA_PREFIX}{PAIRS_SET}"
_RESOLVER_GROUP = os.getenv("RESOLVER_CONSUMER_GROUP", CG_RESOLVER)
# Consumer name for this instance
_CONSUMER = os.getenv("RESOLVER_CONSUMER_NAME", "resolver-0")
# How many stream messages to read per batch
_BATCH = 10
# Interval between consumer-group reads (ms)
_BLOCK_MS = 1000
_PAIR_TTL_SECS = 86400 * 7
_EXCHANGES = ("kalshi", "polymarket")
_BACKFILL_PROGRESS_EVERY = 200
_MARKET_SCAN_BATCH = max(
    1,
    int(getattr(config, "resolver_market_scan_batch", os.getenv("RESOLVER_MARKET_SCAN_BATCH", "1000"))),
)
_ORDERBOOK_TTL_SECS = 600
_MATCH_SIGNATURE_CACHE: Dict[Tuple[str, str], str] = {}
_SIGNATURE_META_KEYS = (
    "yes_sub_title",
    "status",
    "closed",
    "end_date",
    "close_time",
    "market_type",
    "outcome_count",
    "yes_token_id",
    "no_token_id",
    "condition_id",
    "market_slug",
    "question_id",
    "event_ticker",
    "series_ticker",
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_HYDRATE_ORDERBOOKS_ON_MATCH = _env_bool("RESOLVER_HYDRATE_ORDERBOOKS_ON_MATCH", False)
_HYDRATE_ORDERBOOKS_INTERVAL_SECS = float(
    os.getenv("RESOLVER_HYDRATE_ORDERBOOKS_INTERVAL_SECS", "0")
)
_MATCH_ONLY_EXCHANGE = os.getenv("RESOLVER_MATCH_ONLY_EXCHANGE", "").strip().lower()


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


def _validated_key(pair_id: str) -> str:
    return f"{_PAIR_DATA_PREFIX}pair_validated:{pair_id}"


def _market_signature(exchange: str, title: str, meta: dict, match_title: str) -> str:
    relevant_meta = {
        key: meta.get(key)
        for key in _SIGNATURE_META_KEYS
        if isinstance(meta.get(key), (str, int, float, bool))
    }
    payload = {
        "exchange": exchange,
        "title": title,
        "match_title": match_title,
        "meta": relevant_meta,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def ensure_consumer_group(r: aioredis.Redis) -> None:
    try:
        await r.xgroup_create(_STREAM_MARKET_UPDATES, _RESOLVER_GROUP, id="0", mkstream=True)
        logger.info("Created consumer group %s on %s", _RESOLVER_GROUP, _STREAM_MARKET_UPDATES)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


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


def _is_market_expired(meta: dict, now_ts: float, grace_secs: float) -> bool:
    status = str(meta.get("status", "open")).strip().lower()
    if status and status not in {"open", "active", "trading"}:
        return True

    closed = meta.get("closed")
    if isinstance(closed, bool) and closed:
        return True

    end_ts = _parse_timestamp(meta.get("end_date") or meta.get("close_time"))
    if end_ts is not None and (end_ts + grace_secs) <= now_ts:
        return True

    return False


async def _load_market_meta(
    r: aioredis.Redis,
    exchange: str,
    market_id: str,
) -> Optional[dict]:
    raw = await r.get(_market_key(exchange, market_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Skipping invalid JSON market meta for %s/%s", exchange, market_id)
        return None


async def _iter_market_id_batches(
    r: aioredis.Redis,
    exchange: str,
    count: int = _MARKET_SCAN_BATCH,
):
    cursor = 0
    key = _markets_set_key(exchange)
    while True:
        cursor, members = await r.sscan(key, cursor=cursor, count=count)
        if members:
            yield members
        if cursor == 0:
            break


def _normalize_poly_levels(raw_levels: List[dict]) -> List[List[float]]:
    levels: List[List[float]] = []
    for level in raw_levels:
        try:
            price = float(level.get("price", 0))
            size = float(level.get("size", 0))
        except (TypeError, ValueError):
            continue
        levels.append([price, size])
    return levels


def _normalize_kalshi_yes_orderbook(payload: dict) -> Tuple[List[List[float]], List[List[float]]]:
    orderbook = payload.get("orderbook_fp", {}) if isinstance(payload, dict) else {}
    yes_bids = []
    for price, size in orderbook.get("yes_dollars", []) or []:
        try:
            yes_bids.append([float(price), float(size)])
        except (TypeError, ValueError):
            continue
    yes_bids.sort(key=lambda level: level[0], reverse=True)

    yes_asks = []
    for price, size in orderbook.get("no_dollars", []) or []:
        try:
            yes_asks.append([round(1.0 - float(price), 6), float(size)])
        except (TypeError, ValueError):
            continue
    yes_asks.sort(key=lambda level: level[0])
    return yes_bids, yes_asks


async def _publish_orderbook_snapshot(
    r: aioredis.Redis,
    exchange: str,
    market_id: str,
    bids: List[List[float]],
    asks: List[List[float]],
    ts: float,
) -> None:
    orderbook = {
        "market_id": market_id,
        "exchange": exchange,
        "bids": bids,
        "asks": asks,
        "ts": ts,
    }
    await r.set(_orderbook_key(exchange, market_id), json.dumps(orderbook), ex=_ORDERBOOK_TTL_SECS)
    if asks:
        await r.set(_price_key(exchange, market_id), str(asks[0][0]), ex=_ORDERBOOK_TTL_SECS)
    await r.xadd(
        _STREAM_MARKET_UPDATES,
        {
            "event": "orderbook",
            "exchange": exchange,
            "market_id": market_id,
            "ts": str(ts),
        },
        maxlen=10_000,
        approximate=True,
    )


async def _hydrate_pair_orderbooks(
    r: aioredis.Redis,
    client: httpx.AsyncClient,
    pair,
) -> None:
    poly_meta = await _load_market_meta(r, "polymarket", pair.polymarket_market_id)
    if not poly_meta:
        return
    poly_yes_token_id = str(poly_meta.get("yes_token_id") or "").strip()
    poly_no_token_id = str(poly_meta.get("no_token_id") or "").strip()
    if not poly_yes_token_id or not poly_no_token_id:
        return

    ts = time.time()
    try:
        kalshi_resp = await client.get(
            f"{config.kalshi_api_base}/markets/{pair.kalshi_market_id}/orderbook",
            timeout=20.0,
        )
        kalshi_resp.raise_for_status()
        kalshi_bids, kalshi_asks = _normalize_kalshi_yes_orderbook(kalshi_resp.json())
        if kalshi_bids or kalshi_asks:
            await _publish_orderbook_snapshot(
                r,
                "kalshi",
                pair.kalshi_market_id,
                kalshi_bids,
                kalshi_asks,
                ts,
            )
    except Exception as exc:
        logger.warning(
            "Failed to hydrate Kalshi orderbook for %s: %s",
            pair.kalshi_market_id,
            exc,
        )

    try:
        poly_resp = await client.get(
            f"{config.poly_clob_host}/book",
            params={"token_id": poly_yes_token_id},
            headers={"User-Agent": "Constructure-ShadowResolver/1.0"},
            timeout=20.0,
        )
        poly_resp.raise_for_status()
        poly_payload = poly_resp.json()
        poly_bids = _normalize_poly_levels(poly_payload.get("bids", []) or [])
        poly_asks = _normalize_poly_levels(poly_payload.get("asks", []) or [])
        if poly_bids or poly_asks:
            await _publish_orderbook_snapshot(
                r,
                "polymarket",
                pair.polymarket_market_id,
                poly_bids,
                poly_asks,
                ts,
            )
    except Exception as exc:
        logger.warning(
            "Failed to hydrate Polymarket orderbook for %s (token=%s): %s",
            pair.polymarket_market_id,
            poly_yes_token_id,
            exc,
        )


async def _store_new_pairs(
    r: aioredis.Redis,
    pairs: List,
    source: str,
    orderbook_client: Optional[httpx.AsyncClient] = None,
) -> int:
    created = 0
    for pair in pairs:
        pid = pair.pair_id
        inserted = await r.set(_pair_key(pid), pair.model_dump_json(), ex=_PAIR_TTL_SECS, nx=True)
        if not inserted:
            continue

        await r.sadd(_PAIRS_SET, pid)
        await r.xadd(
            _MATCHED_MARKETS_STREAM,
            {
                "pair_id": pid,
                "kalshi_market_id": pair.kalshi_market_id,
                "polymarket_market_id": pair.polymarket_market_id,
                "similarity_score": str(pair.similarity_score),
                "ts": str(time.time()),
            },
            maxlen=5000,
            approximate=True,
        )
        created += 1
        logger.info(
            "New pair %s: K=%s P=%s score=%.3f source=%s",
            pid,
            pair.kalshi_market_id,
            pair.polymarket_market_id,
            pair.similarity_score,
            source,
        )
        if _HYDRATE_ORDERBOOKS_ON_MATCH and orderbook_client is not None:
            await _hydrate_pair_orderbooks(r, orderbook_client, pair)
    return created


async def _cleanup_pairs(
    r: aioredis.Redis,
    removed_markets: Dict[str, Set[str]],
) -> int:
    kalshi_removed = removed_markets.get("kalshi", set())
    poly_removed = removed_markets.get("polymarket", set())
    has_market_removals = bool(kalshi_removed or poly_removed)

    pair_ids = sorted(await r.smembers(_PAIRS_SET))
    if not pair_ids:
        return 0

    raw_pairs = await r.mget([_pair_key(pid) for pid in pair_ids])
    to_delete: List[str] = []
    for pid, raw in zip(pair_ids, raw_pairs):
        if not raw:
            to_delete.append(pid)
            continue
        try:
            pair = json.loads(raw)
        except json.JSONDecodeError:
            to_delete.append(pid)
            continue

        if has_market_removals and (
            pair.get("kalshi_market_id") in kalshi_removed
            or pair.get("polymarket_market_id") in poly_removed
        ):
            to_delete.append(pid)

    if not to_delete:
        return 0

    pipe = r.pipeline(transaction=False)
    keys_to_delete = []
    for pid in to_delete:
        keys_to_delete.extend([_pair_key(pid), _validated_key(pid)])
    pipe.delete(*keys_to_delete)
    pipe.srem(_PAIRS_SET, *to_delete)
    await pipe.execute()
    return len(to_delete)


async def _drop_markets(
    r: aioredis.Redis,
    matcher: MarketMatcher,
    removed_markets: Dict[str, Set[str]],
) -> int:
    removals: List[Tuple[str, str]] = []
    for exchange, ids in removed_markets.items():
        for market_id in ids:
            removals.append((exchange, market_id))

    if removals:
        await matcher.remove_markets(removals)

    removed_count = 0
    for exchange, ids in removed_markets.items():
        if not ids:
            continue
        removed_count += len(ids)
        for market_id in ids:
            _MATCH_SIGNATURE_CACHE.pop((exchange, market_id), None)
        ids_list = list(ids)
        keys_to_delete: List[str] = []
        for market_id in ids_list:
            keys_to_delete.extend(
                [
                    _market_key(exchange, market_id),
                    _orderbook_key(exchange, market_id),
                    _price_key(exchange, market_id),
                ]
            )

        pipe = r.pipeline(transaction=False)
        if keys_to_delete:
            pipe.delete(*keys_to_delete)
        pipe.srem(_markets_set_key(exchange), *ids_list)
        await pipe.execute()

    return removed_count


async def _process_market_meta(
    r: aioredis.Redis,
    matcher: MarketMatcher,
    exchange: str,
    market_id: str,
    meta: dict,
    source: str,
    orderbook_client: Optional[httpx.AsyncClient] = None,
) -> int:
    now_ts = time.time()
    if _is_market_expired(meta, now_ts=now_ts, grace_secs=config.resolver_expired_grace_secs):
        removed_markets = {"kalshi": set(), "polymarket": set()}
        removed_markets[exchange].add(market_id)
        pairs_removed = await _cleanup_pairs(r, removed_markets)
        await _drop_markets(r, matcher, removed_markets)
        logger.info(
            "Removed expired market %s/%s from %s (pairs_removed=%d)",
            exchange,
            market_id,
            source,
            pairs_removed,
        )
        return 0

    title = str(meta.get("title", "")).strip()
    if not title:
        return 0

    vector = matcher.embed_title(title)
    match_title = build_match_text(exchange, title, meta)
    await matcher.index_market(
        exchange=exchange,
        market_id=market_id,
        title=title,
        meta={**meta, "match_title": match_title},
        vector=matcher.embed_title(match_title),
    )
    if _MATCH_ONLY_EXCHANGE and exchange != _MATCH_ONLY_EXCHANGE:
        return 0
    pairs = await matcher.find_matches_for(
        exchange=exchange,
        market_id=market_id,
        title=title,
        query_vector=matcher.embed_title(match_title),
    )
    return await _store_new_pairs(
        r,
        pairs,
        source=source,
        orderbook_client=orderbook_client,
    )


async def _process_market_batch(
    r: aioredis.Redis,
    matcher: MarketMatcher,
    items: List[Tuple[str, str, dict, str]],
    source: str,
    orderbook_client: Optional[httpx.AsyncClient] = None,
) -> Tuple[int, int]:
    if not items:
        return 0, 0

    match_titles = [build_match_text(exchange, title, meta) for exchange, _market_id, meta, title in items]
    vectors = matcher.embed_titles(match_titles)
    if len(vectors) != len(items):
        raise RuntimeError(
            f"embedding batch size mismatch: vectors={len(vectors)} items={len(items)}"
        )

    batch_points = []
    batch_entries = []
    for (exchange, market_id, meta, title), vector, match_title in zip(items, vectors, match_titles):
        batch_points.append(
            {
                "exchange": exchange,
                "market_id": market_id,
                "title": title,
                "meta": {**meta, "match_title": match_title},
                "vector": vector,
            }
        )
        batch_entries.append((exchange, market_id, meta, title, vector, match_title))

    await matcher.index_markets(batch_points)

    processed = 0
    created_pairs = 0
    for exchange, market_id, meta, title, vector, match_title in batch_entries:
        _MATCH_SIGNATURE_CACHE[(exchange, market_id)] = _market_signature(
            exchange,
            title,
            meta,
            match_title,
        )
        if _MATCH_ONLY_EXCHANGE and exchange != _MATCH_ONLY_EXCHANGE:
            processed += 1
            continue
        pairs = await matcher.find_matches_for(
            exchange=exchange,
            market_id=market_id,
            title=title,
            meta={**meta, "match_title": match_title},
            query_vector=vector,
        )
        created_pairs += await _store_new_pairs(
            r,
            pairs,
            source=source,
            orderbook_client=orderbook_client,
        )
        processed += 1

    return processed, created_pairs


async def cleanup_expired_data(r: aioredis.Redis, matcher: MarketMatcher) -> None:
    removed_count = 0
    dropped_count = 0
    pairs_removed = 0
    now_ts = time.time()
    for exchange in _EXCHANGES:
        async for market_ids in _iter_market_id_batches(r, exchange):
            raw_metas = await r.mget([_market_key(exchange, mid) for mid in market_ids])
            removed_batch: Dict[str, Set[str]] = {exchange_name: set() for exchange_name in _EXCHANGES}
            for market_id, raw_meta in zip(market_ids, raw_metas):
                if not raw_meta:
                    removed_batch[exchange].add(market_id)
                    continue
                try:
                    meta = json.loads(raw_meta)
                except json.JSONDecodeError:
                    removed_batch[exchange].add(market_id)
                    continue
                if _is_market_expired(
                    meta,
                    now_ts=now_ts,
                    grace_secs=config.resolver_expired_grace_secs,
                ):
                    removed_batch[exchange].add(market_id)

            batch_removed = len(removed_batch[exchange])
            if not batch_removed:
                continue

            pairs_removed += await _cleanup_pairs(r, removed_batch)
            dropped_count += await _drop_markets(r, matcher, removed_batch)
            removed_count += batch_removed

            if removed_count and removed_count % 10000 == 0:
                logger.info(
                    "Cleanup progress: removed_markets=%d dropped_vectors=%d removed_pairs=%d",
                    removed_count,
                    dropped_count,
                    pairs_removed,
                )

    if removed_count or pairs_removed:
        logger.info(
            "Cleanup complete: removed_markets=%d dropped_vectors=%d removed_pairs=%d",
            removed_count,
            dropped_count,
            pairs_removed,
        )


async def backfill_from_cache(
    r: aioredis.Redis,
    matcher: MarketMatcher,
    orderbook_client: Optional[httpx.AsyncClient] = None,
) -> None:
    started = time.time()
    processed = 0
    created_pairs = 0
    pending: List[Tuple[str, str, dict, str]] = []
    batch_size = max(
        1,
        int(
            getattr(
                config,
                "resolver_backfill_embed_batch_size",
                os.getenv("RESOLVER_BACKFILL_EMBED_BATCH_SIZE", "64"),
            )
        ),
    )
    batch_pause_secs = max(
        0.0,
        float(
            getattr(
                config,
                "resolver_backfill_pause_secs",
                os.getenv("RESOLVER_BACKFILL_PAUSE_SECS", "0.0"),
            )
        ),
    )
    next_progress = _BACKFILL_PROGRESS_EVERY
    exchange_sizes = {
        exchange: await r.scard(_markets_set_key(exchange))
        for exchange in _EXCHANGES
    }
    if _MATCH_ONLY_EXCHANGE in _EXCHANGES:
        exchange_order = [
            exchange
            for exchange in _EXCHANGES
            if exchange != _MATCH_ONLY_EXCHANGE
        ] + [_MATCH_ONLY_EXCHANGE]
    else:
        exchange_order = sorted(_EXCHANGES, key=lambda exchange: exchange_sizes[exchange])
    logger.info(
        "Backfill exchange order: %s",
        ", ".join(f"{exchange}={exchange_sizes[exchange]}" for exchange in exchange_order),
    )
    exchange_iters = {
        exchange: _iter_market_id_batches(r, exchange).__aiter__()
        for exchange in exchange_order
    }
    exchange_progress = {exchange: 0 for exchange in exchange_order}

    async def flush_pending() -> None:
        nonlocal processed, created_pairs, pending, next_progress
        if not pending:
            return
        p_count, c_count = await _process_market_batch(
            r,
            matcher,
            pending,
            source="backfill",
            orderbook_client=orderbook_client,
        )
        processed += p_count
        created_pairs += c_count
        pending = []
        while processed >= next_progress:
            logger.info("Backfill progress: processed=%d created_pairs=%d", processed, created_pairs)
            next_progress += _BACKFILL_PROGRESS_EVERY
        if batch_pause_secs > 0:
            await asyncio.sleep(batch_pause_secs)
        else:
            await asyncio.sleep(0)

    remaining = set(exchange_order)
    while remaining:
        made_progress = False
        for exchange in exchange_order:
            if exchange not in remaining:
                continue
            try:
                market_ids = await anext(exchange_iters[exchange])
            except StopAsyncIteration:
                remaining.remove(exchange)
                logger.info(
                    "Backfill source complete: %s processed=%d created_pairs=%d",
                    exchange,
                    exchange_progress[exchange],
                    created_pairs,
                )
                continue

            made_progress = True
            raw_metas = await r.mget([_market_key(exchange, mid) for mid in market_ids])
            now_ts = time.time()
            for market_id, raw_meta in zip(market_ids, raw_metas):
                exchange_progress[exchange] += 1
                if not raw_meta:
                    continue
                try:
                    meta = json.loads(raw_meta)
                except json.JSONDecodeError:
                    continue
                if _is_market_expired(
                    meta,
                    now_ts=now_ts,
                    grace_secs=config.resolver_expired_grace_secs,
                ):
                    continue

                title = str(meta.get("title", "")).strip()
                if not title:
                    continue
                pending.append((exchange, market_id, meta, title))
                if len(pending) >= batch_size:
                    await flush_pending()

                if exchange_progress[exchange] % _BACKFILL_PROGRESS_EVERY == 0:
                    await asyncio.sleep(0)

        if not made_progress:
            break

    await flush_pending()
    logger.info(
        "Backfill complete: processed_markets=%d created_pairs=%d batch_size=%d pause=%.3fs elapsed=%.2fs",
        processed,
        created_pairs,
        batch_size,
        batch_pause_secs,
        time.time() - started,
    )


async def _load_market_meta_batch(
    r: aioredis.Redis,
    keys: List[Tuple[str, str]],
) -> Dict[Tuple[str, str], Optional[dict]]:
    if not keys:
        return {}

    grouped: Dict[str, List[str]] = {}
    for exchange, market_id in keys:
        grouped.setdefault(exchange, []).append(market_id)

    results: Dict[Tuple[str, str], Optional[dict]] = {}
    for exchange, market_ids in grouped.items():
        raw_metas = await r.mget([_market_key(exchange, market_id) for market_id in market_ids])
        for market_id, raw_meta in zip(market_ids, raw_metas):
            if not raw_meta:
                results[(exchange, market_id)] = None
                continue
            try:
                results[(exchange, market_id)] = json.loads(raw_meta)
            except json.JSONDecodeError:
                logger.warning("Skipping invalid JSON market meta for %s/%s", exchange, market_id)
                results[(exchange, market_id)] = None
    return results


async def _process_stream_entries(
    r: aioredis.Redis,
    matcher: MarketMatcher,
    entries: List[Tuple[str, dict]],
    orderbook_client: Optional[httpx.AsyncClient] = None,
) -> None:
    latest_updates: Dict[Tuple[str, str], dict] = {}
    for _entry_id, fields in entries:
        if fields.get("event") != "market_meta":
            continue
        exchange = fields.get("exchange", "")
        market_id = fields.get("market_id", "")
        if exchange not in _EXCHANGES or not market_id:
            continue
        latest_updates[(exchange, market_id)] = fields

    if not latest_updates:
        return

    metas = await _load_market_meta_batch(r, list(latest_updates.keys()))
    removed_markets: Dict[str, Set[str]] = {exchange: set() for exchange in _EXCHANGES}
    pending: List[Tuple[str, str, dict, str]] = []
    skipped_unchanged = 0
    now_ts = time.time()

    for exchange, market_id in latest_updates.keys():
        meta = metas.get((exchange, market_id))
        if not meta:
            removed_markets[exchange].add(market_id)
            continue
        if _is_market_expired(
            meta,
            now_ts=now_ts,
            grace_secs=config.resolver_expired_grace_secs,
        ):
            removed_markets[exchange].add(market_id)
            continue

        title = str(meta.get("title", "")).strip()
        if not title:
            continue

        match_title = build_match_text(exchange, title, meta)
        signature = _market_signature(exchange, title, meta, match_title)
        if _MATCH_SIGNATURE_CACHE.get((exchange, market_id)) == signature:
            skipped_unchanged += 1
            continue
        pending.append((exchange, market_id, meta, title))

    removed_count = sum(len(ids) for ids in removed_markets.values())
    if removed_count:
        pairs_removed = await _cleanup_pairs(r, removed_markets)
        await _drop_markets(r, matcher, removed_markets)
        logger.info(
            "Stream cleanup: removed_markets=%d removed_pairs=%d",
            removed_count,
            pairs_removed,
        )

    if pending:
        processed, created_pairs = await _process_market_batch(
            r,
            matcher,
            pending,
            source="stream",
            orderbook_client=orderbook_client,
        )
        logger.info(
            "Stream batch processed=%d created_pairs=%d skipped_unchanged=%d deduped_updates=%d",
            processed,
            created_pairs,
            skipped_unchanged,
            len(entries) - len(latest_updates),
        )
    elif skipped_unchanged or len(entries) != len(latest_updates):
        logger.info(
            "Stream batch skipped_unchanged=%d deduped_updates=%d",
            skipped_unchanged,
            len(entries) - len(latest_updates),
        )


async def consume_stream(
    r: aioredis.Redis,
    matcher: MarketMatcher,
    orderbook_client: Optional[httpx.AsyncClient] = None,
) -> None:
    while True:
        try:
            messages = await r.xreadgroup(
                groupname=_RESOLVER_GROUP,
                consumername=_CONSUMER,
                streams={_STREAM_MARKET_UPDATES: ">"},
                count=_BATCH,
                block=_BLOCK_MS,
            )
            if not messages:
                continue

            for _stream, entries in messages:
                await _process_stream_entries(
                    r,
                    matcher,
                    entries,
                    orderbook_client=orderbook_client,
                )
                await r.xack(
                    _STREAM_MARKET_UPDATES,
                    _RESOLVER_GROUP,
                    *[entry_id for entry_id, _fields in entries],
                )

        except aioredis.ConnectionError:
            logger.warning("Redis connection lost, retrying...")
            await asyncio.sleep(2)
        except Exception as exc:
            logger.exception("Resolver stream error: %s", exc)
            await asyncio.sleep(1)


async def cleanup_loop(r: aioredis.Redis, matcher: MarketMatcher) -> None:
    interval = config.resolver_cleanup_interval_secs
    if interval <= 0:
        logger.info("Cleanup loop disabled (RESOLVER_CLEANUP_INTERVAL_SECS <= 0)")
        return

    while True:
        await asyncio.sleep(interval)
        try:
            await cleanup_expired_data(r, matcher)
        except Exception as exc:
            logger.exception("Cleanup loop error: %s", exc)
            await asyncio.sleep(1)


async def hydrate_pairs_loop(
    r: aioredis.Redis,
    client: Optional[httpx.AsyncClient],
) -> None:
    interval = _HYDRATE_ORDERBOOKS_INTERVAL_SECS
    if not _HYDRATE_ORDERBOOKS_ON_MATCH or client is None or interval <= 0:
        logger.info("Orderbook hydration loop disabled")
        return

    while True:
        await asyncio.sleep(interval)
        try:
            pair_ids = sorted(await r.smembers(_PAIRS_SET))
            if not pair_ids:
                continue
            raw_pairs = await r.mget([_pair_key(pid) for pid in pair_ids])
            hydrated = 0
            for raw_pair in raw_pairs:
                if not raw_pair:
                    continue
                try:
                    pair = json.loads(raw_pair)
                except json.JSONDecodeError:
                    continue
                await _hydrate_pair_orderbooks(
                    r,
                    client,
                    SimpleNamespace(**pair),
                )
                hydrated += 1
            if hydrated:
                logger.info("Hydrated orderbooks for %d pairs", hydrated)
        except Exception as exc:
            logger.exception("Orderbook hydration loop error: %s", exc)
            await asyncio.sleep(1)


async def main() -> None:
    r = aioredis.Redis(
        host=config.redis_host,
        port=config.redis_port,
        password=config.redis_password or None,
        decode_responses=True,
    )
    await r.ping()

    qdrant = AsyncQdrantClient(host=config.qdrant_host, port=config.qdrant_port)
    embedder = Embedder(model_name=config.embed_model, device=config.embed_device)
    matcher = MarketMatcher(
        qdrant,
        embedder,
        threshold=config.match_threshold,
        collection=config.qdrant_collection,
    )
    await matcher.ensure_collection()
    orderbook_client = httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": "Constructure-Resolver/1.0"},
    )

    await ensure_consumer_group(r)
    await cleanup_expired_data(r, matcher)
    if config.resolver_backfill_on_startup:
        await backfill_from_cache(r, matcher, orderbook_client=orderbook_client)
    else:
        logger.info("Startup backfill disabled (RESOLVER_BACKFILL_ON_STARTUP=false)")

    logger.info(
        "Resolver ready — consuming %s (market_prefix=%s pair_prefix=%s matched_stream=%s)",
        _STREAM_MARKET_UPDATES,
        _MARKET_DATA_PREFIX or "<none>",
        _PAIR_DATA_PREFIX or "<none>",
        _MATCHED_MARKETS_STREAM,
    )
    await asyncio.gather(
        consume_stream(r, matcher, orderbook_client=orderbook_client),
        cleanup_loop(r, matcher),
        hydrate_pairs_loop(r, orderbook_client),
    )


async def handle_entry(
    r: aioredis.Redis,
    matcher: MarketMatcher,
    entry_id: str,
    fields: dict,
    orderbook_client: Optional[httpx.AsyncClient] = None,
) -> None:
    if fields.get("event") != "market_meta":
        return

    exchange = fields.get("exchange", "")
    market_id = fields.get("market_id", "")
    if exchange not in _EXCHANGES or not market_id:
        return

    meta = await _load_market_meta(r, exchange, market_id)
    if not meta:
        # market metadata no longer exists in cache; drop stale state
        removed_markets = {"kalshi": set(), "polymarket": set()}
        removed_markets[exchange].add(market_id)
        pairs_removed = await _cleanup_pairs(r, removed_markets)
        await _drop_markets(r, matcher, removed_markets)
        logger.info(
            "Dropped stale market %s/%s from stream event (pairs_removed=%d)",
            exchange,
            market_id,
            pairs_removed,
        )
        return

    await _process_market_meta(
        r,
        matcher,
        exchange=exchange,
        market_id=market_id,
        meta=meta,
        source="stream",
        orderbook_client=orderbook_client,
    )


if __name__ == "__main__":
    asyncio.run(main())
