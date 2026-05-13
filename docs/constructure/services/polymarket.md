# Polymarket Service

## Overview

Polymarket VPN and infrastructure for running executor in isolated network namespace.

## Tech Stack

- **Infrastructure**: Linux network namespaces, VPN (sing-box)

## Startup

```bash
cd polymarket
./start-polymarket-env.sh
```

## Project Structure

```
polymarket/
├── start-polymarket-env.sh   # VPN namespace startup script
├── polymarket-cli/           # CLI tools
└── README.md
```
