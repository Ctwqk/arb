# RLTrader Cloud Service

## Overview

Reinforcement learning trading cloud infrastructure with Milvus vector database.

## Tech Stack

- **Infrastructure**: AWS, Kubernetes
- **Databases**: Milvus (vector database)

## Dependencies

- Milvus
- IBKR integration

## Startup

```bash
cd rltrader-cloud
docker compose up -d
```

## Project Structure

```
rltrader-cloud/
├── alpha/                   # Alpha strategies
├── ibkr/                   # Interactive Brokers integration
├── infra/                  # Infrastructure
├── milvus/                 # Milvus configuration
├── ml/                     # Machine learning models
├── api_keys                # API credentials (NOT committed)
└── docker-compose.yml
```
