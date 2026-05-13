# OpenNews MCP Service

## Overview

**Crypto News Aggregation · AI Ratings · Trading Signals · Real-time Updates**

Model Context Protocol (MCP) server for AI assistants to access crypto news, AI ratings, and trading signals.

## Tech Stack

- **Language**: Python
- **Framework**: FastMCP (Model Context Protocol)
- **APIs**: 6551.io REST API + WebSocket

## Features

### News Access
- Latest crypto articles
- Full-text keyword search
- Filter by coin (BTC, ETH, SOL, etc.)
- Filter by source (Bloomberg, Reuters, etc.)
- Filter by engine type (news, listing, onchain, meme, market)

### AI-Powered
- AI impact scores (0-100)
- Trading signals (long/short/neutral)
- Multi-language summaries (English, Chinese)

### Real-time
- WebSocket subscription for live news

## Installation

### Claude Code

```bash
claude mcp add opennews \
  -e OPENNEWS_TOKEN=<your-token> \
  -- uv --directory /path/to/opennews-mcp run opennews-mcp
```

### OpenClaw

```bash
export OPENNEWS_TOKEN="<your-token>"
cp -r openclaw-skill/opennews ~/.openclaw/skills/
```

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "opennews": {
      "command": "uv",
      "args": ["--directory", "/path/to/opennews-mcp", "run", "opennews-mcp"],
      "env": {
        "OPENNEWS_TOKEN": "<your-token>"
      }
    }
  }
}
```

## Available Tools

| Category | Tool | Description |
|----------|------|-------------|
| Discovery | `get_news_sources` | Get all news source category tree |
| | `list_news_types` | All available news source codes |
| Search | `get_latest_news` | Latest articles |
| | `search_news` | Keyword search |
| | `search_news_by_coin` | By coin symbol |
| | `get_news_by_source` | By engine type and source |
| | `get_news_by_engine` | By type (news, listing, onchain, meme, market) |
| | `search_news_advanced` | Advanced multi-filter search |
| AI | `get_high_score_news` | Articles with score >= threshold |
| | `get_news_by_signal` | By signal (long/short/neutral) |
| Real-time | `subscribe_latest_news` | WebSocket live collection |

## Usage Examples

| You Say | It Does |
|---------|---------|
| "Latest crypto news" | Get latest articles |
| "Search SEC regulation news" | Full-text keyword search |
| "BTC related news" | Filter by coin |
| "Bloomberg articles" | Filter by source |
| "On-chain events" | Filter by engine type (onchain) |
| "Important news with AI score above 80" | High score filtering |
| "Bullish signals" | Filter by trading signal (long) |
| "Subscribe to real-time news" | WebSocket live updates |

## Data Structure

```json
{
  "id": "unique-article-id",
  "text": "Title / Content",
  "newsType": "Bloomberg",
  "engineType": "news",
  "link": "https://...",
  "coins": [{ "symbol": "BTC", "market_type": "spot", "match": "title" }],
  "aiRating": {
    "score": 85,
    "grade": "A",
    "signal": "long",
    "status": "done",
    "summary": "Chinese summary",
    "enSummary": "English summary"
  },
  "ts": 1708473600000
}
```

### AI Rating Fields

| Field | Description |
|-------|-------------|
| `score` | 0-100 impact score |
| `signal` | `long` (bullish), `short` (bearish), `neutral` |
| `status` | `done` = AI analysis completed |

## Configuration

### Get API Token

Get token from: https://6551.io/mcp

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENNEWS_TOKEN` | **Yes** | 6551 API Bearer Token |
| `OPENNEWS_API_BASE` | No | Override REST API URL |
| `OPENNEWS_WSS_URL` | No | Override WebSocket URL |
| `OPENNEWS_MAX_ROWS` | No | Max results per request (default 100) |

### Config File

Also supports `config.json` in project root (env vars take precedence):

```json
{
  "api_base_url": "https://ai.6551.io",
  "wss_url": "wss://ai.6551.io/open/news_wss",
  "api_token": "<your-token>",
  "max_rows": 100
}
```

## Compatibility

| Client | Installation | Status |
|--------|--------------|--------|
| **Claude Code** | `claude mcp add` | One-click |
| **OpenClaw** | Copy Skill directory | One-click |
| Claude Desktop | JSON config | Supported |
| Cursor | JSON config | Supported |
| Windsurf | JSON config | Supported |
| Cline | JSON config | Supported |
| Continue.dev | YAML / JSON | Supported |
| Cherry Studio | GUI | Supported |
| Zed | JSON config | Supported |

## Project Structure

```
opennews-mcp/
├── src/opennews_mcp/
│   ├── server.py              # Entry point
│   ├── app.py                 # FastMCP instance
│   ├── config.py              # Config loading
│   ├── api_client.py          # HTTP + WebSocket
│   └── tools/                 # MCP tools
├── openclaw-skill/           # OpenClaw integration
├── knowledge/                # Embedded knowledge
├── pyproject.toml
├── config.json
└── README.md
```

## Development

```bash
cd /path/to/opennews-mcp
uv sync
uv run opennews-mcp

# MCP Inspector test
npx @modelcontextprotocol/inspector uv --directory /path/to/opennews-mcp run opennews-mcp
```
