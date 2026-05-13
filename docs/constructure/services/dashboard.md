# Dashboard Service

## Overview

Local dashboard UI and BFF proxy. Privileged system management and IBKR broker
operations live in separate local services.

## Tech Stack

- **Language**: Python
- **Framework**: FastAPI
- **Integrations**: `constructure-runtime-control` API and canonical `ibkr` API

## Startup

```bash
cd /home/taiwei/Constructure/apps/dashboard
docker compose up -d
```

## Project Structure

```
dashboard/
├── src/                   # Source code
├── static/                # Static assets
└── docker-compose.yml
```

## Runtime Boundary

- `/api/ibkr/*` proxies to `IBKR_API_URL`, default `http://127.0.0.1:7701`.
- Other `/api/*` paths proxy to `RUNTIME_CONTROL_API_URL`, default
  `http://127.0.0.1:7702`.
- The dashboard repository must not own Docker socket access, crontab writes,
  IBKR clients, or direct cross-service database access.
