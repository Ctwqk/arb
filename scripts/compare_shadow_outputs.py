#!/usr/bin/env python3
"""Compare live and shadow arb Redis outputs.

Examples:
  python compare_shadow_outputs.py --mode collector --limit 100
  python compare_shadow_outputs.py --mode strategy --limit 50
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any

import redis


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    parser.add_argument("--mode", choices=("collector", "strategy"), required=True)
    parser.add_argument("--shadow-prefix", default="shadow:")
    parser.add_argument("--limit", type=int, default=100)
    return parser.parse_args()


def _decode(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if isinstance(value, dict):
        return {_decode(key): _decode(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return tuple(_decode(item) for item in value)
    return value


def _normalize_stream_entries(
    entries: list[tuple[str, dict[str, str]]], *, ignore_fields: set[str] | None = None
) -> list[dict[str, str]]:
    ignore_fields = ignore_fields or set()
    normalized: list[dict[str, str]] = []
    for _, payload in entries:
        item = {key: value for key, value in payload.items() if key not in ignore_fields}
        normalized.append(item)
    return normalized


def _load_stream(
    client: redis.Redis, stream_key: str, limit: int
) -> list[tuple[str, dict[str, str]]]:
    raw = client.xrevrange(stream_key, count=limit)
    entries: list[tuple[str, dict[str, str]]] = []
    for stream_id, fields in reversed(raw):
        decoded_fields = _decode(fields)
        entries.append((_decode(stream_id), decoded_fields))
    return entries


def _load_json_value(client: redis.Redis, key: str) -> Any | None:
    raw = client.get(key)
    if raw is None:
        return None
    return json.loads(_decode(raw))


def _compare_collector(client: redis.Redis, shadow_prefix: str, limit: int) -> int:
    live_entries = _load_stream(client, "stream:market_updates", limit)
    shadow_entries = _load_stream(client, f"{shadow_prefix}stream:market_updates", limit)
    live_norm = _normalize_stream_entries(live_entries)
    shadow_norm = _normalize_stream_entries(shadow_entries)

    print(f"live stream entries:   {len(live_norm)}")
    print(f"shadow stream entries: {len(shadow_norm)}")

    live_counter = Counter(json.dumps(item, sort_keys=True) for item in live_norm)
    shadow_counter = Counter(json.dumps(item, sort_keys=True) for item in shadow_norm)
    stream_only_live = live_counter - shadow_counter
    stream_only_shadow = shadow_counter - live_counter

    problems = 0
    if stream_only_live or stream_only_shadow:
        problems += 1
        print("stream mismatch detected")
        if stream_only_live:
            print("  only in live:")
            for item, count in stream_only_live.most_common(10):
                print(f"    {count}x {item}")
        if stream_only_shadow:
            print("  only in shadow:")
            for item, count in stream_only_shadow.most_common(10):
                print(f"    {count}x {item}")
    else:
        print("stream entries match for the sampled window")

    live_keys = sorted(_decode(key) for key in client.keys("orderbook:*"))
    live_keys += sorted(_decode(key) for key in client.keys("market:*"))
    sample_keys = live_keys[:limit]
    checked = 0
    for live_key in sample_keys:
        shadow_key = f"{shadow_prefix}{live_key}"
        live_value = _load_json_value(client, live_key)
        shadow_value = _load_json_value(client, shadow_key)
        checked += 1
        if live_value != shadow_value:
            problems += 1
            print(f"value mismatch: {live_key} != {shadow_key}")
            print(f"  live:   {json.dumps(live_value, sort_keys=True)[:300]}")
            print(f"  shadow: {json.dumps(shadow_value, sort_keys=True)[:300]}")
            break
    print(f"checked cache keys: {checked}")
    return problems


def _compare_strategy(client: redis.Redis, shadow_prefix: str, limit: int) -> int:
    live_entries = _load_stream(client, "stream:trade_signals", limit)
    shadow_entries = _load_stream(client, f"{shadow_prefix}stream:trade_signals", limit)
    live_norm = _normalize_stream_entries(live_entries, ignore_fields={"signal_id", "ts"})
    shadow_norm = _normalize_stream_entries(shadow_entries, ignore_fields={"signal_id", "ts"})

    print(f"live signal entries:   {len(live_norm)}")
    print(f"shadow signal entries: {len(shadow_norm)}")

    live_counter = Counter(json.dumps(item, sort_keys=True) for item in live_norm)
    shadow_counter = Counter(json.dumps(item, sort_keys=True) for item in shadow_norm)
    only_live = live_counter - shadow_counter
    only_shadow = shadow_counter - live_counter
    if only_live or only_shadow:
        print("signal mismatch detected")
        if only_live:
            print("  only in live:")
            for item, count in only_live.most_common(10):
                print(f"    {count}x {item}")
        if only_shadow:
            print("  only in shadow:")
            for item, count in only_shadow.most_common(10):
                print(f"    {count}x {item}")
        return 1
    print("signal entries match for the sampled window")
    return 0


def main() -> int:
    args = _parse_args()
    client = redis.Redis.from_url(args.redis_url)
    if args.mode == "collector":
        return _compare_collector(client, args.shadow_prefix, args.limit)
    return _compare_strategy(client, args.shadow_prefix, args.limit)


if __name__ == "__main__":
    sys.exit(main())
