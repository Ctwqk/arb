# polymarket

Local helper repository for bringing up a Polymarket trading environment inside a dedicated network namespace.

## Highlights

- Namespace bootstrap for isolated Polymarket connectivity
- Utility wrapper around sing-box configuration and runtime checks
- Minimal but practical environment setup repository

## Tech Stack

- Shell, Linux namespaces, sing-box, Polymarket CLI wrapper

## Repository Layout

- `start-polymarket-env.sh`
- `README.md`

## Getting Started

- Configure the `POLYMARKET_NS` and `SINGBOX_CONFIG` environment variables if you do not want the defaults.
- Run `./start-polymarket-env.sh` on a Linux host with `ip netns` and `sing-box` installed.
- Use this repo as an environment helper rather than a standalone application.

## Current Status

- This README was refreshed from a code audit and is intentionally scoped to what is directly visible in the repository.
