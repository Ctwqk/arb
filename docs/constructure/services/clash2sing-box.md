# Clash2Sing-box Service

## Overview

Clash to Sing-box configuration converter tool.

## Tech Stack

- **Language**: Rust

## Startup

```bash
cd clash2sing-box
cargo build --release
# or use nix flake
nix run .
```

## Project Structure

```
clash2sing-box/
├── ctos/                   # Clash to Sing-box converter
├── web/                    # Web interface
├── .envrc                  # Direnv configuration
├── flake.nix              # Nix flake
└── Cargo.toml
```
