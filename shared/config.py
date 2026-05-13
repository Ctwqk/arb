"""Configuration loaded from environment variables."""
import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # ── Infrastructure ──────────────────────────────────────────────────────
    redis_host: str = os.getenv("REDIS_HOST", "127.0.0.1")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_password: str = os.getenv("REDIS_PASSWORD", "")

    postgres_dsn: str = os.getenv(
        "POSTGRES_DSN", "postgresql://arb:arb@127.0.0.1:5432/arb"
    )

    qdrant_host: str = os.getenv("QDRANT_HOST", "127.0.0.1")
    qdrant_port: int = int(os.getenv("QDRANT_PORT", "6333"))
    qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "markets")

    # ── Kalshi ───────────────────────────────────────────────────────────────
    kalshi_api_key_id: str = os.getenv("KALSHI_API_KEY_ID", "")
    # PEM-encoded RSA private key (newlines as \n in env)
    kalshi_private_key_pem: str = os.getenv("KALSHI_PRIVATE_KEY_PEM", "").replace(
        "\\n", "\n"
    )
    kalshi_api_base: str = os.getenv(
        "KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2"
    )
    kalshi_ws_url: str = os.getenv(
        "KALSHI_WS_URL", "wss://api.elections.kalshi.com/trade-api/ws/v2"
    )

    # ── Polymarket ───────────────────────────────────────────────────────────
    poly_private_key: str = os.getenv("POLY_PRIVATE_KEY", "")  # 0x-prefixed hex
    poly_api_key: str = os.getenv("POLY_API_KEY", "")
    poly_api_secret: str = os.getenv("POLY_API_SECRET", "")
    poly_api_passphrase: str = os.getenv("POLY_API_PASSPHRASE", "")
    poly_clob_host: str = os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com")
    poly_ws_url: str = os.getenv(
        "POLY_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    )
    poly_chain_id: int = int(os.getenv("POLY_CHAIN_ID", "137"))  # Polygon mainnet

    # ── Strategy ─────────────────────────────────────────────────────────────
    # Minimum gross edge before considering a trade (fraction, e.g. 0.03 = 3%)
    min_edge: float = float(os.getenv("MIN_EDGE", "0.03"))
    # Estimated round-trip fee (both sides combined), used to compute net edge
    fee_estimate: float = float(os.getenv("FEE_ESTIMATE", "0.02"))
    # Maximum contracts per trade
    max_trade_size: float = float(os.getenv("MAX_TRADE_SIZE", "100.0"))
    # Maximum total position per market (contracts)
    max_position_per_market: float = float(os.getenv("MAX_POSITION_PER_MARKET", "500.0"))
    # Minimum available liquidity in the orderbook to proceed
    min_liquidity: float = float(os.getenv("MIN_LIQUIDITY", "10.0"))
    # Maximum total portfolio notional exposure (USD)
    max_portfolio_notional: float = float(os.getenv("MAX_PORTFOLIO_NOTIONAL", "10000.0"))

    # ── Resolver ─────────────────────────────────────────────────────────────
    match_threshold: float = float(os.getenv("MATCH_THRESHOLD", "0.85"))
    embed_model: str = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
    # auto | cuda | cpu
    embed_device: str = os.getenv("EMBED_DEVICE", "auto")
    # Bootstrap resolver from Redis market cache at startup
    resolver_backfill_on_startup: bool = _env_bool("RESOLVER_BACKFILL_ON_STARTUP", True)
    # Periodic cleanup interval for expired/stale markets and pairs
    resolver_cleanup_interval_secs: float = float(
        os.getenv("RESOLVER_CLEANUP_INTERVAL_SECS", "300.0")
    )
    # Grace period after market end timestamp before cleanup
    resolver_expired_grace_secs: float = float(
        os.getenv("RESOLVER_EXPIRED_GRACE_SECS", "0.0")
    )
    # Batch size for startup backfill embeddings (larger batches favor GPU throughput)
    resolver_backfill_embed_batch_size: int = int(
        os.getenv("RESOLVER_BACKFILL_EMBED_BATCH_SIZE", "64")
    )
    # Sleep between backfill batches to avoid overwhelming Qdrant during rebuilds.
    resolver_backfill_pause_secs: float = float(
        os.getenv("RESOLVER_BACKFILL_PAUSE_SECS", "0.0")
    )
    # Redis SSCAN count used while walking cached markets during backfill/cleanup.
    resolver_market_scan_batch: int = int(
        os.getenv("RESOLVER_MARKET_SCAN_BATCH", "1000")
    )

    # ── Executor ─────────────────────────────────────────────────────────────
    # Cancel order if not filled within this many seconds
    order_timeout_secs: float = float(os.getenv("ORDER_TIMEOUT_SECS", "30.0"))
    # Polling interval when waiting for fill
    order_poll_interval: float = float(os.getenv("ORDER_POLL_INTERVAL", "1.0"))


config = Config()
