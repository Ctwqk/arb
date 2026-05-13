# News Service

## Overview

News collection and aggregation service with GPU support for processing.

## Tech Stack

- **Language**: Python, Go
- **Infrastructure**: Docker with GPU support

## Services

| Service | Description |
|---------|-------------|
| **collector** | News data collection |
| **server** | News API server |

## Startup

```bash
cd news
docker compose up -d        # CPU version
docker compose -f docker-compose.gpu.yml up -d  # GPU version
```
