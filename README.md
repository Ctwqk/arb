# Kalshi ↔ Polymarket Arbitrage System

Cross-exchange arbitrage engine for prediction markets. Detects price discrepancies between Kalshi and Polymarket, then executes hedged trades on both sides.

## Architecture

```
Kalshi API        Polymarket API
    │                   │
    └───────┬───────────┘
            ▼
       collector          ← WebSocket feeds, no trading
            │
            ▼
    Redis (orderbook cache + streams)
            │
     ┌──────┴──────┐
     ▼              ▼
 resolver       strategy
 (Qdrant)    (arb detection)
                    │
             ┌──────┴──────┐
             ▼              ▼
      executor-kalshi   executor-polymarket
             │              │
        Kalshi API     VPN namespace
                            │
                      Polymarket API
```

### Services

| Service | Role |
|---|---|
| **collector** | Subscribes to Kalshi + Polymarket WebSockets, publishes normalized orderbooks to Redis |
| **resolver** | Embeds market titles (sentence-transformers), matches equivalent markets across exchanges via Qdrant, backfills on startup, and cleans expired market/pair state |
| **strategy** | Reads orderbooks from Redis, detects arbitrage (YES+NO cross-leg), applies risk checks, emits trade signals |
| **executor-kalshi** | Consumes signals, places orders via Kalshi REST API (RSA auth) |
| **executor-polymarket** | Consumes signals, places EIP-712 signed orders via Polymarket CLOB API. Runs inside `vpn-polymarket` namespace |

### Infrastructure

| Component | Purpose |
|---|---|
| **Redis** | Orderbook cache + inter-service event streams (hot path); production uses the 150 shared Redis endpoint |
| **PostgreSQL** | Trade history, analytics (cold path only); production uses the 150 shared Postgres endpoint |
| **Qdrant** | Vector similarity search for market matching; production uses the 150 shared Qdrant endpoint |

## Production Runtime

Current production is split between the 127 Swarm app node and 150-only market
execution infrastructure.

| Service | Production location | Notes |
|---|---|---|
| `arb-resolver-swarm` | 127 Colima/Swarm node | Scaled by the arb open/close schedule; uses 150 embedding/Qdrant/Redis |
| `arb-validator-swarm` | 127 Colima/Swarm node | Scaled by the arb open/close schedule; uses 150 LLM/Redis/Postgres |
| `arb-executor-polymarket-swarm` | 150 | Kept with wallet/VPN namespace and account/networking state |

Source changes should be made in
`10.0.0.150:/home/taiwei/Constructure-repos/arb` and pushed to GitHub. The 150
deploy sync job deploys the app copy to
`10.0.0.127:/Users/wenjieliu/arb-swarm-src`; that 127 directory is not the
source-of-truth git workspace.

Production dependencies are provided by the 150 infra layer:

- Arb Redis: `redis://10.0.0.150:6379`
- Postgres: `10.0.0.150:5435`
- Qdrant: `http://10.0.0.150:6333`
- Embedding gateway: `http://10.0.0.150:8080`

Do not start an arb-local Postgres, Redis, or Qdrant for production. The compose
and Makefile flows below are still useful for local development and isolated
tests.

## Arbitrage Logic

```
Exchange A: YES ask = 0.55
Exchange B: YES bid = 0.62  →  NO ask = 0.38

Buy YES on A:  $0.55
Buy NO  on B:  $0.38
               ─────
Total cost:    $0.93
Payout:        $1.00  (one side always wins)
Gross edge:     7%
```

The system evaluates both legs (buy YES on Kalshi + NO on Polymarket, and vice versa) and picks the better one when the net edge exceeds the configured threshold.

## Networking

Legacy local compose flows use `network_mode: host`. Production app services run
under Swarm with explicit 150 infra endpoints. The Polymarket executor remains
on 150 inside the `vpn-polymarket` Linux network namespace so its traffic routes
through the VPN (sing-box).

A veth pair bridges the namespaces:
- Main namespace: `veth-arb-main` @ `192.168.100.1/30`
- VPN namespace: `veth-arb-vpn` @ `192.168.100.2/30`

The executor-polymarket container connects to Redis/Postgres via `192.168.100.1`.

## Setup

```bash
# 1. Copy and fill in credentials
make setup
# Edit .env with your Kalshi API key, Polymarket wallet key, etc.

# 2. Full system start (VPN + veth + services + poly executor)
make start
```

### Manual startup

```bash
# Start VPN namespace
~/Constructure/infra/polymarket/start-polymarket-env.sh

# Create veth bridge
bash infra/scripts/setup-veth.sh

# Start infra + app services (everything except poly executor)
make up

# Launch poly executor in VPN namespace
make poly-start
```

## Operations

```bash
make ps              # Service status
make logs            # All logs
make logs-strategy   # Strategy logs only
make logs-poly       # Polymarket executor logs
make redis-cli       # Redis shell
make up-gpu          # Start services with GPU-enabled resolver

make down            # Stop everything
make clean           # Destroy containers + volumes
```

## Configuration

All parameters are in `.env` (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `MIN_EDGE` | `0.03` | Minimum gross edge to trade (3%) |
| `FEE_ESTIMATE` | `0.02` | Estimated round-trip fees (2%) |
| `MAX_TRADE_SIZE` | `100` | Max contracts per trade |
| `MAX_POSITION_PER_MARKET` | `500` | Max contracts per market |
| `MAX_PORTFOLIO_NOTIONAL` | `10000` | Max total exposure (USD) |
| `MATCH_THRESHOLD` | `0.85` | Cosine similarity for market matching |
| `EMBED_DEVICE` | `auto` | Embedding device selection: `auto`, `cuda`, or `cpu` |
| `RESOLVER_BACKFILL_ON_STARTUP` | `true` | Re-index cached markets on startup and rebuild matches without new stream events |
| `RESOLVER_BACKFILL_EMBED_BATCH_SIZE` | `64` | Embedding batch size during startup backfill |
| `RESOLVER_CLEANUP_INTERVAL_SECS` | `300` | Cleanup cadence for expired/stale markets and stale pair cache |
| `RESOLVER_EXPIRED_GRACE_SECS` | `0` | Delay before considering `end_date` markets expired |
| `RESOLVER_TORCH_VERSION` | `2.5.1` | Torch version used when building resolver image |
| `RESOLVER_TORCH_INDEX_URL` | `.../cpu` | Torch wheel index URL for standard (CPU) resolver build |
| `RESOLVER_TORCH_INDEX_URL_GPU` | `.../cu124` | Torch wheel index URL used by GPU override compose file |
| `ORDER_TIMEOUT_SECS` | `30` | Cancel unfilled orders after this |

## Resolver Behavior

- On startup, resolver now runs an optional cache backfill from `markets:kalshi` and `markets:polymarket`, then publishes recovered matches to `stream:matched_markets`.
- Resolver periodically removes expired/stale markets from Qdrant + Redis (`market:*`, `orderbook:*`, `price:*`, `markets:*`) and drops affected `pair:*` / `pairs:all` entries.
- Embedding device is logged at boot. `EMBED_DEVICE=auto` uses CUDA when available, otherwise CPU fallback.
- Real-time stream processing remains unchanged (`stream:market_updates` consumer group + `stream:matched_markets` publisher), but now avoids duplicate per-market embedding work by reusing the same vector for index and search.
- Startup backfill embeds in batches (`RESOLVER_BACKFILL_EMBED_BATCH_SIZE`) to improve throughput, especially on GPU.

## Resolver GPU

Use the GPU override compose file to enable CUDA for resolver:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build resolver
```

Equivalent Make target:

```bash
make up-gpu
```

GPU requirements:
- NVIDIA driver + NVIDIA Container Toolkit installed on host
- Docker supports `gpus: all`

CPU vs GPU guidance:
- CPU is usually faster for low-QPS, tiny batches, and latency-sensitive single-title embedding.
- GPU is usually faster for startup backfill / high-throughput workloads with larger embedding batches.
- If your resolver mainly processes sparse real-time updates, set `EMBED_DEVICE=cpu`.
- If your resolver handles large backfills or sustained matching load, use GPU (`EMBED_DEVICE=auto` or `cuda`) and increase `RESOLVER_BACKFILL_EMBED_BATCH_SIZE`.

## Redis Schema

```
orderbook:{exchange}:{market_id}   → JSON orderbook (bids/asks)
price:{exchange}:{market_id}       → best ask price
market:{exchange}:{market_id}      → market metadata
pair:{pair_id}                     → matched market pair
position:{exchange}:{market_id}    → current position size

stream:market_updates              → collector → strategy
stream:trade_signals               → strategy → executors
stream:order_updates               → executors → monitoring
stream:matched_markets             → resolver → strategy
```

## Project Structure

```
arb/
├── docker-compose.yml
├── .env.example
├── Makefile
├── shared/                     # Shared Python library
│   ├── models.py               # Pydantic data models
│   ├── redis_keys.py           # Key schema constants
│   └── config.py               # Env-based config
├── rust/
│   ├── arb-proto/              # Shared Redis schema + payload structs
│   ├── collector-rs/           # Rust market data collection
│   └── strategy-rs/            # Rust arbitrage detection + risk
├── services/
│   ├── resolver/src/           # Cross-exchange matching
│   ├── validator/src/          # LLM pair verification
│   ├── executor-kalshi/src/    # Kalshi order execution
│   └── executor-polymarket/src/# Polymarket order execution
└── infra/
    ├── postgres/init.sql       # DB schema
    └── scripts/                # Startup + networking scripts
```
