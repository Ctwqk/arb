import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = Path(__file__).resolve().parent
for path in (ROOT, SRC_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# Minimal stubs so main.py can be imported without the full runtime deps.
redis_stub = types.ModuleType("redis")
redis_asyncio_stub = types.ModuleType("redis.asyncio")
redis_asyncio_stub.Redis = object
redis_asyncio_stub.ResponseError = RuntimeError
redis_asyncio_stub.ConnectionError = RuntimeError
redis_stub.asyncio = redis_asyncio_stub
sys.modules.setdefault("redis", redis_stub)
sys.modules.setdefault("redis.asyncio", redis_asyncio_stub)

httpx_stub = types.ModuleType("httpx")
httpx_stub.AsyncClient = object
sys.modules.setdefault("httpx", httpx_stub)

qdrant_client_stub = types.ModuleType("qdrant_client")
qdrant_client_stub.AsyncQdrantClient = object
sys.modules.setdefault("qdrant_client", qdrant_client_stub)

embedder_stub = types.ModuleType("embedder")
embedder_stub.Embedder = object
sys.modules.setdefault("embedder", embedder_stub)

matcher_stub = types.ModuleType("matcher")
matcher_stub.MarketMatcher = object
matcher_stub.build_match_text = lambda exchange, title, meta=None: (
    meta.get("match_title") if isinstance(meta, dict) and meta.get("match_title") else title
)
sys.modules.setdefault("matcher", matcher_stub)

shared_config_stub = types.ModuleType("shared.config")
shared_config_stub.config = SimpleNamespace(
    resolver_expired_grace_secs=0.0,
    resolver_market_scan_batch=1000,
    resolver_backfill_embed_batch_size=2,
    resolver_backfill_pause_secs=0.0,
)
sys.modules.setdefault("shared.config", shared_config_stub)

shared_redis_keys_stub = types.ModuleType("shared.redis_keys")
shared_redis_keys_stub.CG_RESOLVER = "cg:resolver"
shared_redis_keys_stub.PAIRS_SET = "pairs:all"
shared_redis_keys_stub.STREAM_MARKET_UPDATES = "stream:market_updates"
shared_redis_keys_stub.market_key = lambda exchange, market_id: f"market:{exchange}:{market_id}"
shared_redis_keys_stub.markets_set_key = lambda exchange: f"markets:{exchange}"
shared_redis_keys_stub.orderbook_key = lambda exchange, market_id: f"orderbook:{exchange}:{market_id}"
shared_redis_keys_stub.price_key = lambda exchange, market_id: f"price:{exchange}:{market_id}"
shared_redis_keys_stub.pair_key = lambda pair_id: f"pair:{pair_id}"
sys.modules.setdefault("shared.redis_keys", shared_redis_keys_stub)

import main  # noqa: E402


class ResolverBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main._MATCH_SIGNATURE_CACHE.clear()
        self._orig_cleanup_pairs = main._cleanup_pairs
        self._orig_drop_markets = main._drop_markets
        self._orig_load_market_meta_batch = main._load_market_meta_batch
        self._orig_process_market_batch = main._process_market_batch
        self._orig_store_new_pairs = main._store_new_pairs
        self._orig_match_only_exchange = main._MATCH_ONLY_EXCHANGE
        main._MATCH_ONLY_EXCHANGE = ""

    async def asyncTearDown(self):
        main._cleanup_pairs = self._orig_cleanup_pairs
        main._drop_markets = self._orig_drop_markets
        main._load_market_meta_batch = self._orig_load_market_meta_batch
        main._process_market_batch = self._orig_process_market_batch
        main._store_new_pairs = self._orig_store_new_pairs
        main._MATCH_ONLY_EXCHANGE = self._orig_match_only_exchange
        main._MATCH_SIGNATURE_CACHE.clear()

    async def test_process_market_batch_uses_single_batch_upsert(self):
        matcher = SimpleNamespace()
        matcher.embed_titles = lambda titles: [[float(i)] for i, _ in enumerate(titles, start=1)]
        matcher.index_markets = AsyncMock()
        matcher.find_matches_for = AsyncMock(return_value=[])

        main._store_new_pairs = AsyncMock(return_value=0)

        items = [
            ("kalshi", "k1", {"yes_sub_title": "A"}, "Who wins race A?"),
            ("polymarket", "p1", {"yes_token_id": "yes", "no_token_id": "no"}, "Will A win race A?"),
        ]

        processed, created = await main._process_market_batch(
            object(),
            matcher,
            items,
            source="test",
            orderbook_client=None,
        )

        self.assertEqual(processed, 2)
        self.assertEqual(created, 0)
        matcher.index_markets.assert_awaited_once()
        upsert_batch = matcher.index_markets.await_args.args[0]
        self.assertEqual(len(upsert_batch), 2)
        self.assertEqual(matcher.find_matches_for.await_count, 2)

    async def test_process_stream_entries_dedupes_and_skips_unchanged(self):
        meta = {
            "title": "Who will win race A?",
            "yes_sub_title": "Alice",
            "match_title": "Will Alice win race A?",
        }
        signature = main._market_signature(
            "kalshi",
            meta["title"],
            meta,
            meta["match_title"],
        )
        main._MATCH_SIGNATURE_CACHE[("kalshi", "m1")] = signature

        main._load_market_meta_batch = AsyncMock(return_value={("kalshi", "m1"): meta})
        main._cleanup_pairs = AsyncMock(return_value=0)
        main._drop_markets = AsyncMock(return_value=0)
        main._process_market_batch = AsyncMock(return_value=(0, 0))

        entries = [
            ("1-0", {"event": "market_meta", "exchange": "kalshi", "market_id": "m1"}),
            ("1-1", {"event": "market_meta", "exchange": "kalshi", "market_id": "m1"}),
        ]

        await main._process_stream_entries(object(), object(), entries, orderbook_client=None)

        main._process_market_batch.assert_not_awaited()
        main._cleanup_pairs.assert_not_awaited()
        main._drop_markets.assert_not_awaited()

    async def test_process_stream_entries_keeps_latest_unique_market(self):
        metas = {
            ("kalshi", "m1"): {
                "title": "Who will win race A?",
                "yes_sub_title": "Alice",
                "match_title": "Will Alice win race A?",
            },
            ("polymarket", "m2"): {
                "title": "Will Alice win race A?",
                "yes_token_id": "yes",
                "no_token_id": "no",
            },
        }
        main._load_market_meta_batch = AsyncMock(return_value=metas)
        main._cleanup_pairs = AsyncMock(return_value=0)
        main._drop_markets = AsyncMock(return_value=0)
        main._process_market_batch = AsyncMock(return_value=(2, 0))

        entries = [
            ("1-0", {"event": "market_meta", "exchange": "kalshi", "market_id": "m1"}),
            ("1-1", {"event": "market_meta", "exchange": "kalshi", "market_id": "m1"}),
            ("1-2", {"event": "market_meta", "exchange": "polymarket", "market_id": "m2"}),
        ]

        await main._process_stream_entries(object(), object(), entries, orderbook_client=None)

        main._process_market_batch.assert_awaited_once()
        items = main._process_market_batch.await_args.args[2]
        self.assertEqual(len(items), 2)
        self.assertEqual({(exchange, market_id) for exchange, market_id, _meta, _title in items},
                         {("kalshi", "m1"), ("polymarket", "m2")})

    async def test_backfill_honors_batch_pause(self):
        original_batch = main.config.resolver_backfill_embed_batch_size
        original_pause = main.config.resolver_backfill_pause_secs
        original_iter = main._iter_market_id_batches
        original_process = main._process_market_batch
        original_sleep = main.asyncio.sleep

        main.config.resolver_backfill_embed_batch_size = 2
        main.config.resolver_backfill_pause_secs = 0.25
        fake_payload = json.dumps({"title": "Market title"})
        fake_redis = SimpleNamespace(
            scard=AsyncMock(return_value=2),
            mget=AsyncMock(return_value=[fake_payload, fake_payload]),
        )

        async def fake_iter_market_id_batches(_r, _exchange, count=0):
            yield ["m1", "m2"]

        async def fake_process_market_batch(_r, _matcher, items, source, orderbook_client=None):
            return len(items), 0

        sleep_calls = []

        async def fake_sleep(duration):
            sleep_calls.append(duration)

        main._iter_market_id_batches = fake_iter_market_id_batches
        main._process_market_batch = fake_process_market_batch
        main.asyncio.sleep = fake_sleep

        try:
            await main.backfill_from_cache(fake_redis, object(), orderbook_client=None)
        finally:
            main.config.resolver_backfill_embed_batch_size = original_batch
            main.config.resolver_backfill_pause_secs = original_pause
            main._iter_market_id_batches = original_iter
            main._process_market_batch = original_process
            main.asyncio.sleep = original_sleep

        self.assertIn(0.25, sleep_calls)


if __name__ == "__main__":
    unittest.main()
