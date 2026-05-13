# Exo Watchdog Service

## Overview

Model health monitoring and watchdog service for AI models.

## Tech Stack

- **Language**: Python
- **APIs**: REST API

## Ports

| Port | Protocol | Description |
|------|----------|-------------|
| 8000 | HTTP | Watchdog API |

## Startup

```bash
cd exo-watchdog
docker compose up -d
```

## Project Structure

```
exo-watchdog/
├── app.py                 # Main application
├── stress_test.py         # Load testing
├── ssh/                   # SSH configuration
├── ssh-user/              # SSH user management
└── docker-compose.yml
```
