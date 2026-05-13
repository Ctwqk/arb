# RLTrader Cloud Service

## Overview

Reinforcement learning trading cloud infrastructure with AlphaVantage helpers,
ML scripts, and Milvus vector database experiments.

## Tech Stack

- **Infrastructure**: AWS, Kubernetes
- **Databases**: Milvus (vector database)

## Dependencies

- Milvus
- Canonical IBKR API through `IBKR_API_URL` when trading data is needed

## Startup

```bash
cd rltrader-cloud
docker compose up -d
```

## Project Structure

```
rltrader-cloud/
├── alpha/                   # Alpha strategies
├── ibkr_client.py           # HTTP client for canonical IBKR service
├── infra/                  # Infrastructure
├── milvus/                 # Milvus configuration
├── ml/                     # Machine learning models
├── api_keys                # API credentials (NOT committed)
└── docker-compose.yml
```
