-- Arbitrage system database schema
-- Postgres is used only for historical records and analytics.
-- The hot path (collector → strategy → executors) does NOT touch this DB.

-- ── Trades ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    id              BIGSERIAL PRIMARY KEY,
    signal_id       TEXT NOT NULL,
    exchange        TEXT NOT NULL,          -- 'kalshi' | 'polymarket'
    market_id       TEXT NOT NULL,
    side            TEXT NOT NULL,          -- 'yes' | 'no'
    size            NUMERIC(18, 6) NOT NULL,
    price           NUMERIC(8, 6) NOT NULL,
    expected_edge   NUMERIC(8, 6),
    order_id        TEXT NOT NULL,
    status          TEXT NOT NULL,          -- 'filled' | 'cancelled' | 'failed'
    filled_size     NUMERIC(18, 6) DEFAULT 0,
    avg_price       NUMERIC(8, 6) DEFAULT 0,
    ts              DOUBLE PRECISION NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT trades_order_id_unique UNIQUE (order_id)
);

CREATE INDEX IF NOT EXISTS idx_trades_signal_id ON trades(signal_id);
CREATE INDEX IF NOT EXISTS idx_trades_market_id ON trades(market_id);
CREATE INDEX IF NOT EXISTS idx_trades_exchange   ON trades(exchange);
CREATE INDEX IF NOT EXISTS idx_trades_ts         ON trades(ts);

-- ── Matched Pairs ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS matched_pairs (
    pair_id                 TEXT PRIMARY KEY,
    kalshi_market_id        TEXT NOT NULL,
    polymarket_market_id    TEXT NOT NULL,
    similarity_score        NUMERIC(6, 4),
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    disabled                BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_pairs_kalshi  ON matched_pairs(kalshi_market_id);
CREATE INDEX IF NOT EXISTS idx_pairs_poly    ON matched_pairs(polymarket_market_id);

-- ── Arbitrage Opportunities Log ───────────────────────────────────────────────
-- Optional: log every detected opportunity for backtesting/analysis
CREATE TABLE IF NOT EXISTS opportunities (
    id              BIGSERIAL PRIMARY KEY,
    pair_id         TEXT NOT NULL,
    kalshi_side     TEXT NOT NULL,
    poly_side       TEXT NOT NULL,
    kalshi_price    NUMERIC(8, 6),
    poly_price      NUMERIC(8, 6),
    gross_edge      NUMERIC(8, 6),
    available_size  NUMERIC(18, 6),
    acted           BOOLEAN DEFAULT FALSE,
    ts              DOUBLE PRECISION NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_opp_pair_id ON opportunities(pair_id);
CREATE INDEX IF NOT EXISTS idx_opp_ts      ON opportunities(ts);

-- ── Summary View ──────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW trade_summary AS
SELECT
    DATE_TRUNC('day', TO_TIMESTAMP(ts)) AS day,
    exchange,
    COUNT(*)                             AS num_trades,
    SUM(filled_size)                     AS total_filled,
    AVG(expected_edge)                   AS avg_expected_edge,
    SUM(CASE WHEN status = 'filled'    THEN 1 ELSE 0 END) AS filled_count,
    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_count,
    SUM(CASE WHEN status = 'failed'    THEN 1 ELSE 0 END) AS failed_count
FROM trades
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
