# Job Autoflow Service

## Overview

Local-first job search automation for North America roles. A comprehensive system supporting multiple fully isolated local accounts, each with its own materials, vault, browser sessions, job history, and automation settings.

## Tech Stack

- **Frontend**: React + Vite
- **Backend**: Fastify + SQLite + Drizzle
- **Automation**: Playwright for ATS submission flows
- **Document Rendering**: Markdown/YAML → Pandoc → LaTeX → xelatex → PDF
- **Job Sources**: LinkedIn, Indeed, Greenhouse, Lever, Workday
- **AI**: OpenAI-compatible endpoint, MiniMax, Local model, rule-based template fallback

## Architecture

### Workspace Layout

```
apps/api              # Local API, SQLite storage, sync/orchestration
apps/web              # Browser-based control panel
packages/shared       # Domain types, schemas, shared helpers
packages/documents    # Profile loading, AI fallback chain, LaTeX/PDF rendering
packages/automation   # Job connectors and ATS submitters
data/accounts/<slug>/profile    # Editable profile bundle (Markdown/YAML)
data/accounts/<slug>/searches.yaml  # Connector/search presets per account
runtime/accounts/<slug>/browser-data  # Playwright login state
output/accounts/<slug>           # Rendered documents and artifacts
```

## Features

### Multi-Account Isolation
- Each account has isolated profile, vault, browser session, and automation settings
- UI account switcher changes entire workspace context
- Vault entries keep credentials locally encrypted per account

### Job Ingestion
- **LinkedIn**: Scrapes search result pages with Playwright, native Easy Apply flows
- **Indeed**: Scrapes search results, Apply Now flows
- **Greenhouse**: Public boards, full automation
- **Lever**: Public boards, full automation
- **Workday**: Syncs from configured cxs endpoints, dedicated multi-step flow

### Document Generation
- Profile YAML with structured data (experiences, projects, target roles)
- AI-enhanced resume tailoring against job descriptions
- Dynamic reordering of experience/project bullets
- Multi-language support: English (en), Chinese (zh)
- Output formats: .md, .tex, .pdf

### AI Fallback Chain
1. OpenAI-compatible endpoint
2. MiniMax
3. Local model endpoint
4. Rule-based template fallback

### Automation
- Full pipeline: sync jobs → create applications → generate tailored materials → auto-submit
- Login buttons appear when connectors hit login walls
- Company-site flows for LinkedIn/Indeed listings bouncing to career sites
- Gmail bridge integration for OTP reading
- Session reuse detection

## Ports

| Port | Protocol | Description |
|------|----------|-------------|
| 4310 | HTTP | API and Web panel |

## Startup

```bash
# Install dependencies
npm install

# Install pandoc for PDF generation
# xelatex should already be installed on the machine

# Start API and UI
npm run dev

# Docker
docker compose up --build
# Open http://localhost:4310
```

## API Endpoints

### Profile Import
- `POST /accounts/:accountId/profile/import/files` - Import pdf, docx, txt, md files
- `POST /accounts/:accountId/profile/import/github` - Fetch GitHub repositories

### Vault
- `POST /accounts/:accountId/vault/unlock` - Unlock vault
- Account-scoped read/write routes for vault entries

### Automation
- `GET /automation/sessions` - Check reusable sessions (LinkedIn, Indeed, Google)

## Configuration

### Profile Files (per account)
- `profile.yaml` - Basic profile info
- `target-roles.yaml` - Target role families
- `experiences.yaml` - Work experiences
- `projects.yaml` - Projects
- `resume-base.md` - Base resume template

### Preferences
- `preferences.documentLanguage` - Generated document language (en/zh)
- Active target role family selection

## Project Structure

```
job-autoflow/
├── apps/
│   ├── api/                  # Fastify API server
│   └── web/                  # React control panel
├── packages/
│   ├── shared/               # Shared types and schemas
│   ├── documents/            # Document rendering
│   └── automation/           # Job connectors
├── data/
│   └── accounts/<slug>/      # Per-account data
├── runtime/
│   └── accounts/<slug>/      # Browser data, SQLite
├── output/
│   └── accounts/<slug>/      # Rendered documents
├── docs/
│   └── setup-accounts-and-materials.md
└── docker-compose.yml
```

## Resume Rendering Commands

```bash
npm run render:sample  # Render sample documents
# Outputs: output/accounts/<slug>/documents/*.md, *.tex, *.pdf
```

## Environment

- Docker mounts: `data/`, `runtime/`, `output/`
- X11 forwarding: `/tmp/.X11-unix` and `~/.Xauthority`
- Auto-detect active X11 display from `XAUTHORITY`
