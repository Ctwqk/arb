# Gmail Bridge Service

## Overview

Small Dockerized Gmail bridge for personal use. Exposes a local HTTP API for Gmail operations without exposing credentials.

## Tech Stack

- **Language**: Python
- **APIs**: Gmail API
- **Authentication**: OAuth 2.0

## Features

- List messages
- Fetch full message body
- Mark message as read
- Send new messages

## Setup

### Prerequisites

1. A Google Cloud project with Gmail API enabled
2. OAuth client ID JSON file for a Desktop app
3. Docker and Docker Compose

### Installation Steps

```bash
# Create folders and env file
cd /home/taiwei/Constructure/apps/gmail-bridge
mkdir -p data secrets
cp .env.example .env

# Put OAuth client JSON at
# /home/taiwei/Constructure/apps/gmail-bridge/secrets/credentials.json

# Build the image
docker compose build

# Run one-time OAuth flow
docker compose run --rm --service-ports gmail-bridge python auth.py

# Or manual mode if callback fails
docker compose run --rm --service-ports gmail-bridge python auth.py --manual

# Start the API
docker compose up -d
```

## API Endpoints

Base URL: `http://localhost:8080`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/messages` | List messages (supports query params) |
| GET | `/messages/{id}` | Get single message |
| POST | `/messages/{id}/mark-read` | Mark message as read |
| POST | `/send` | Send new message |

### Example Requests

```bash
# Health check
curl http://localhost:8080/health

# List unread inbox messages
curl "http://localhost:8080/messages?label_ids=INBOX,UNREAD&max_results=10"

# Search mail
curl "http://localhost:8080/messages?q=from:alerts@example.com newer_than:7d"

# Get one message
curl http://localhost:8080/messages/MESSAGE_ID

# Mark as read
curl -X POST http://localhost:8080/messages/MESSAGE_ID/mark-read

# Send mail
curl -X POST http://localhost:8080/send \
  -H "Content-Type: application/json" \
  -d '{
    "to": ["person@example.com"],
    "subject": "Test from docker",
    "text": "Hello from gmail-bridge"
  }'
```

## Configuration

### OAuth Scopes

- `gmail.modify` - Read messages and modify labels
- `gmail.send` - Send messages

### Default Settings

- Redirect host: `127.0.0.1` (to avoid IPv6 callback issues)

### Files

| File | Description |
|------|-------------|
| `secrets/credentials.json` | Google OAuth client JSON |
| `data/token.json` | OAuth access token |

## Notes

- Default scopes are `gmail.modify` and `gmail.send`
- API does not support attachments yet
- Keep `data/token.json` and `secrets/credentials.json` private
