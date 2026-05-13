# Arb Service

## Overview

Cross-exchange arbitrage engine for prediction markets. Detects price discrepancies between **Kalshi** and **Polymarket**, then executes hedged trades on both sides simultaneously.

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

## Services

| Service | Role |
|---------|------|
| **collector** | Subscribes to Kalshi + Polymarket WebSockets, publishes normalized orderbooks to Redis |
| **resolver** | Embeds market titles (sentence-transformers), matches equivalent markets across exchanges via Qdrant, backfills on startup |
| **strategy** | Reads orderbooks from Redis, detects arbitrage (YES+NO cross-leg), applies risk checks, emits trade signals |
| **executor-kalshi** | Consumes signals, places orders via Kalshi REST API (RSA auth) |
| **executor-polymarket** | Consumes signals, places EIP-712 signed orders via Polymarket CLOB API. Runs inside `vpn-polymarket` namespace |

## Tech Stack

- **Language**: Python
- **Databases**: Redis, PostgreSQL, Qdrant (Vector DB)
- **APIs**: Kalshi REST API, Polymarket CLOB API, WebSocket
- **Infrastructure**: Linux network namespaces, VPN (sing-box)
- **ML**: sentence-transformers for market matching

## Infrastructure

| Component | Purpose | Port |
|-----------|---------|------|
| **Redis** | Orderbook cache + inter-service event streams | 6379 |
| **PostgreSQL** | Trade history, analytics | 5432 |
| **Qdrant** | Vector similarity search for market matching | 6333/6334 |

## Networking

All services use `network_mode: host`. The Polymarket executor runs separately inside the `vpn-polymarket` Linux network namespace so its traffic routes through the VPN (sing-box).

A veth pair bridges the namespaces:
- Main namespace: `veth-arb-main` @ `192.168.100.1/30`
- VPN namespace: `veth-arb-vpn` @ `192.168.100.2/30`

The executor-polymarket container connects to Redis/Postgres via `192.168.100.1`.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MIN_EDGE` | 0.03 | Minimum gross edge to trade (3%) |
| `FEE_ESTIMATE` | 0.02 | Estimated round-trip fees (2%) |
| `MAX_TRADE_SIZE` | 100 | Max contracts per trade |
| `MAX_POSITION_PER_MARKET` | 500 | Max contracts per market |
| `MAX_PORTFOLIO_NOTIONAL` | 10000 | Max total exposure (USD) |
| `MATCH_THRESHOLD` | 0.85 | Cosine similarity for market matching |
| `EMBED_DEVICE` | auto | Embedding device: auto, cuda, or cpu |
| `RESOLVER_BACKFILL_ON_STARTUP` | true | Re-index cached markets on startup |
| `RESOLVER_BACKFILL_EMBED_BATCH_SIZE` | 64 | Embedding batch size during backfill |
| `RESOLVER_CLEANUP_INTERVAL_SECS` | 300 | Cleanup cadence for expired markets |
| `RESOLVER_EXPIRED_GRACE_SECS` | 0 | Delay before considering expired |
| `RESOLVER_TORCH_VERSION` | 2.5.1 | Torch version for resolver image |
| `ORDER_TIMEOUT_SECS` | 30 | Cancel unfilled orders after this |
| `REDIS_PASSWORD` | - | Redis password |
| `POSTGRES_PASSWORD` | arb | PostgreSQL password |

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

## Startup

### Full System Start

```bash
cd arb

# 1. Copy and fill in credentials
make setup
# Edit .env with your Kalshi API key, Polymarket wallet key, etc.

# 2. Full system start (VPN + veth + services + poly executor)
make start
```

### Manual Startup

```bash
# Start VPN namespace
~/Constructure/infra/polymarket/start-polymarket-env.sh

# Create veth bridge
bash infra/scripts/setup-veth.sh

# Start infra + app services
make up

# Launch poly executor in VPN namespace
make poly-start
```

### GPU Support

```bash
# Build resolver with CUDA
make build-gpu

# Start with GPU-enabled resolver
make up-gpu
```

## Make Targets

| Target | Description |
|--------|-------------|
| `make setup` | Copy .env.example → .env |
| `make build` | Build all Docker images |
| `make build-gpu` | Build resolver with CUDA |
| `make up` | Build + start all services (except poly executor) |
| `make up-gpu` | Start with GPU-enabled resolver |
| `make down` | Stop all services |
| `make start` | Full system start |
| `make poly-start` | Launch executor-polymarket in VPN namespace |
| `make poly-stop` | Stop executor-polymarket |
| `make logs` | Tail all logs |
| `make logs-collector` | Collector logs |
| `make logs-resolver` | Resolver logs |
| `make logs-strategy` | Strategy logs |
| `make logs-kalshi` | Kalshi executor logs |
| `make logs-poly` | Polymarket executor logs |
| `make ps` | Show service status |
| `make redis-cli` | Open Redis CLI |
| `make clean` | Destroy containers + volumes |

## Project Structure

```
arb/
├── docker-compose.yml
├── docker-compose.gpu.yml    # GPU override
├── .env.example
├── Makefile
├── shared/                     # Shared Python library
│   ├── models.py               # Pydantic data models
│   ├── redis_keys.py           # Key schema constants
│   └── config.py               # Env-based config
├── services/
│   ├── collector/
│   │   └── src/                # WebSocket data collection
│   ├── resolver/
│   │   └── src/                # Cross-exchange matching (Qdrant)
│   ├── strategy/
│   │   └── src/                # Arbitrage detection + risk
│   ├── executor-kalshi/
│   │   └── src/                # Kalshi order execution
│   └── executor-polymarket/
│       └── src/                # Polymarket order execution
└── infra/
    ├── postgres/
    │   └── init.sql            # DB schema
    └── scripts/
        ├── setup-veth.sh       # veth bridge setup
        ├── start-poly-executor.sh
        └── start.sh           # Full system startup
```

## Resolver Behavior

- **Startup backfill**: Embeds cached markets from `markets:kalshi` and `markets:polymarket`, publishes recovered matches to `stream:matched_markets`
- **Periodic cleanup**: Removes expired/stale markets from Qdrant + Redis
- **Embedding device**: Logged at boot. `EMBED_DEVICE=auto` uses CUDA when available
- **Real-time processing**: Consumes `stream:market_updates`, produces to `stream:matched_markets`

## CPU vs GPU Guidance

- **CPU**: Usually faster for low-QPS, tiny batches, latency-sensitive single-title embedding
- **GPU**: Faster for startup backfill / high-throughput with larger batches
- Set `EMBED_DEVICE=cpu` for sparse real-time updates
- Set `EMBED_DEVICE=auto` or `cuda` for large backfills
