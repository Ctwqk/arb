# Worldmonitor Service

## Overview

**Real-time global intelligence dashboard** — AI-powered news aggregation, geopolitical monitoring, and infrastructure tracking in a unified situational awareness interface.

## Variants

| Variant | URL | Focus |
|---------|-----|-------|
| **World Monitor** | worldmonitor.app | Geopolitics, military, conflicts, infrastructure |
| **Tech Monitor** | tech.worldmonitor.app | Startups, AI/ML, cloud, cybersecurity |
| **Finance Monitor** | finance.worldmonitor.app | Global markets, trading, central banks, Gulf FDI |

## Tech Stack

- **Frontend**: TypeScript, Vite, deck.gl (WebGL 3D globe), MapLibre GL JS
- **Desktop**: Tauri 2 (Rust) with Node.js sidecar
- **AI/ML**: Groq (Llama 3.1), OpenRouter, Transformers.js (browser-side)
- **Caching**: Redis (Upstash), Vercel CDN
- **Deployment**: Vercel Edge Functions, Railway (WebSocket relay), Tauri desktop

## Key Features

### Interactive 3D Globe
- WebGL-accelerated rendering with deck.gl + MapLibre GL JS
- 35+ toggleable data layers (conflicts, military bases, nuclear facilities, undersea cables, pipelines, etc.)
- 8 regional presets, time filtering (1h to 7d)
- URL state sharing for shareable views

### AI-Powered Intelligence
- **World Brief**: LLM-synthesized summary of top global developments
- **Country Instability Index (CII)**: Real-time stability scores for 22 monitored nations
- **Focal Point Detection**: Correlates entities across news, military, protests, outages
- **Threat Classification**: Hybrid keyword + LLM classifier
- **Trending Keyword Spike Detection**: 2-hour rolling window vs 7-day baseline

### Real-Time Data Layers

**Geopolitical**:
- Active conflict zones (UCDP + ACLED)
- Social unrest events
- Natural disasters (USGS, GDACS, NASA EONET)
- Cyber threat IOCs

**Military & Strategic**:
- 220+ military bases
- Live military flight tracking (ADS-B)
- Naval vessel monitoring (AIS)
- Nuclear facilities

**Infrastructure**:
- Undersea cables
- Oil & gas pipelines
- 111 AI datacenters
- 83 strategic ports

**Market & Crypto**:
- 7-signal macro radar (BUY/CASH verdict)
- BTC ETF flow tracker
- Stablecoin peg health monitor
- Fear & Greed Index

### Live News & Video
- 150+ RSS feeds across geopolitics, defense, energy, tech, finance
- 8 live video streams (Bloomberg, Sky News, Al Jazeera, etc.)
- 19 live webcams from geopolitical hotspots
- Custom keyword monitors

### Desktop Application (Tauri)
- Native desktop app for macOS, Windows, Linux
- OS keychain integration for API keys
- Token-authenticated sidecar
- Cloud fallback when local API fails
- Auto-update checker

## Architecture

```
┌─────────────────────────────────────┐
│          Vercel (Edge)              │
│  60+ edge functions · static SPA    │
└──────────┬─────────────┬────────────┘
           │             │
           ▼             ▼
┌─────────────────────────────────────┐
│       Railway (Relay Server)        │
│  WebSocket relay · AIS vessel      │
└─────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│     Tauri Desktop (Rust + Node)    │
│  OS keychain · Local API handlers   │
└─────────────────────────────────────┘
```

## Ports

| Port | Protocol | Description |
|------|----------|-------------|
| 3000 | HTTP | Main web application |
| 46123 | HTTP | Desktop sidecar API |

## Dependencies

- **worldmonitor-hourly**: Hourly news data cache
- **exo-watchdog**: Model health monitoring
- **IBKR**: Interactive Brokers for market quotes
- **Redis**: Caching layer (Upstash)

## Startup

```bash
# Clone and run
git clone https://github.com/koala73/worldmonitor.git
cd worldmonitor
npm install
vercel dev  # Runs frontend + all 60+ API edge functions

# Or just frontend (no API)
npm run dev
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GROQ_API_KEY` | AI summarization |
| `OPENROUTER_API_KEY` | AI fallback |
| `UPSTASH_REDIS_REST_URL` | Cache |
| `FINNHUB_API_KEY` | Market data |
| `FRED_API_KEY` | Economic data |
| `EIA_API_KEY` | Energy data |
| `ACLED_ACCESS_TOKEN` | Conflict data |
| `VITE_VARIANT` | full/tech/finance |

## Project Structure

```
worldmonitor/
├── src/                   # Frontend source
├── src-tauri/            # Tauri desktop app
├── api/                  # 60+ Edge Functions
├── public/               # Static assets
├── e2e/                  # Playwright tests
├── docs/                 # Documentation
├── scripts/              # Build/deploy scripts
└── docker-compose.yml
```

## Data Sources

- **RSS**: 150+ curated feeds
- **Military**: OpenSky (aircraft), AIS (maritime)
- **Conflicts**: ACLED, UCDP, GDELT
- **Disasters**: USGS, GDACS, NASA EONET
- **Markets**: Yahoo Finance, CoinGecko, mempool.space
- **Threat Intel**: abuse.ch, AlienVault OTX, AbuseIPDB

## Country Instability Index (CII)

22 tier-1 monitored countries: US, Russia, China, Ukraine, Iran, Israel, Taiwan, North Korea, Saudi Arabia, Turkey, Poland, Germany, France, UK, India, Pakistan, Syria, Yemen, Myanmar, Venezuela, Brazil, UAE

Scoring components:
- Baseline risk (40%)
- Unrest events (20%)
- Security activity (20%)
- Information velocity (20%)

## Build Commands

```bash
npm run dev:full      # World Monitor variant
npm run dev:tech      # Tech variant  
npm run dev:finance   # Finance variant

npm run build:full
npm run build:tech
npm run build:finance

# Desktop
npm run desktop:package:macos:full
npm run desktop:package:windows:full
```
