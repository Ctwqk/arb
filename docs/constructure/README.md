# Constructure Project

## Overview

Constructure is a multi-service system containing various microservices for arbitrage trading, monitoring, news processing, and cloud infrastructure management.

## Current Runtime Docs

- [Compose Runtime And Schedule](./runtime-compose-schedule.md)
  - current source of truth for the post-K8s runtime
  - includes machine roles, service classification, schedule windows, and operations commands
- [Infra Services](./infra-services.md)
  - agent-facing reference for shared databases, cache, object storage, AI/embedding, desktop/browser, and network namespace services
- [Application Services](./app-services.md)
  - agent-facing reference for app inventory, runtime services, schedule/on-demand classification, and infra dependencies

## Tech Stack

- **Container Orchestration**: Docker, Docker Compose, Kubernetes
- **Programming Languages**: Python, TypeScript, Rust, Go
- **Databases**: PostgreSQL, Redis, Qdrant (Vector DB), Milvus
- **Infrastructure**: AWS, Kubernetes, Linux network namespaces
- **APIs**: REST, WebSocket, gRPC

## Services

| Service | Description |
|---------|-------------|
| **arb** | Cross-exchange arbitrage engine for prediction markets (Kalshi ↔ Polymarket) |
| **worldmonitor** | Real-time world news and market monitoring web application |
| **worldmonitor-hourly** | Hourly news data collection and caching service |
| **job-autoflow** | Job automation workflow system |
| **news** | News collection and aggregation service |
| **news-publisher** | News publishing and distribution platform |
| **gmail-bridge** | Gmail integration bridge |
| **exo-watchdog** | Model health monitoring and watchdog service |
| **dashboard** | Admin dashboard for system management |
| **rltrader-cloud** | Reinforcement learning trading cloud infrastructure |
| **polymarket** | Polymarket VPN and infrastructure |
| **opennews-mcp** | OpenNews Model Context Protocol server |
| **cmdsage** | Command-line sage tool |
| **clash2sing-box** | Clash to Sing-box configuration converter |
| **VideoProcess** | Video processing services |

## Architecture

```
Constructure/
├── arb/                    # Arbitrage trading system
│   ├── collector/          # WebSocket data collection
│   ├── resolver/           # Cross-exchange market matching
│   ├── strategy/           # Arbitrage detection
│   ├── executor-kalshi/    # Kalshi trade execution
│   ├── executor-polymarket/# Polymarket trade execution
│   └── infra/              # Infrastructure scripts
│
├── worldmonitor/           # Main web application (Node.js)
├── worldmonitor-hourly/    # Hourly data service
├── job-autoflow/          # Job automation (TypeScript/Node.js)
├── news/                  # News system (Python + Go)
├── news-publisher/        # News publisher (Node.js)
├── gmail-bridge/          # Gmail integration (Python)
├── exo-watchdog/          # Model watchdog (Python)
├── dashboard/             # Admin dashboard (Python)
├── polymarket/            # VPN infrastructure
├── rltrader-cloud/        # RL trading cloud
├── opennews-mcp/          # MCP server for OpenNews
├── cmdsage/               # CLI tool (Rust)
├── clash2sing-box/         # Config converter (Rust)
└── VideoProcess/          # Video processing
```

## Dependencies

### Infrastructure Services

- **Redis**: Orderbook cache, inter-service messaging
- **PostgreSQL**: Persistent data storage, trade history
- **Qdrant**: Vector database for market matching
- **Milvus**: ML model vector storage

### External Services

- **Kalshi API**: Prediction market exchange
- **Polymarket API**: Prediction market exchange
- **IBKR**: Interactive Brokers for quotes
- **Gmail API**: Email integration
- **Exo**: Local LLM execution

## Ports

| Service | Port | Protocol | Description |
|---------|------|----------|-------------|
| worldmonitor-web | 3000 | HTTP | Main web application |
| worldmonitor-web | 46123 | HTTP | WebSocket/API |
| arb-redis | 6379 | Redis | Cache service |
| arb-postgres | 5432 | PostgreSQL | Database |
| arb-qdrant | 6333 | HTTP | Vector DB |
| arb-qdrant-gRPC | 6334 | gRPC | Vector DB |

## Startup

### All Services

```bash
# Each service directory has its own docker-compose.yml
cd <service-directory>
docker compose up -d
```

### Specific Services

```bash
# Arb system
cd arb
make setup    # Copy .env.example → .env
make start    # Full system start

# Worldmonitor
cd worldmonitor
docker compose up -d

# Job Autoflow
cd job-autoflow
docker compose up -d
```

## Configuration

Environment variables are configured via `.env.example` files in each service directory. Never commit actual `.env` files.

## Logs

```bash
# Docker logs
docker logs -f <container-name>

# Arb specific
cd arb
make logs
make logs-strategy
```

## Maintenance

- **Backup**: Each service handles its own data backup
- **Updates**: Pull latest images or rebuild with `docker compose build`
- **Cleanup**: `docker compose down -v` removes volumes (careful!)
