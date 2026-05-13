# Application Services

This document is the agent-facing reference for the Constructure application
layer. Use it with [Infra Services](./infra-services.md) when adding, moving,
or changing application services so each app has an explicit service inventory
and shared infra dependency list.

## Layer Model

The app layer consumes infra; it should not own duplicate copies of shared
databases, queues, browser managers, or LLM gateways.

```text
infra
|-- platform-upload
|-- apps
|-- schedule
`-- on-demand
```

- `apps/` owns application projects and their app-specific compose files.
- `platform-upload/` owns publisher and browser-automation services that are
  app-adjacent. VideoProcess and news-publisher consume this layer.
- `schedule` and `on-demand` are runtime classifications, not separate product
  projects. They still depend on infra.
- Standalone compose profiles inside app directories are for local development
  and testing. In the current host runtime, prefer `infra/shared-infra`.

## Application Inventory

| Application | Source | Runtime class | Services | Infra dependencies |
| --- | --- | --- | --- | --- |
| Runtime Control | `Constructure-repos/constructure-runtime-control/` | always-on privileged control plane | `runtime-control` | Docker socket and host mounts, cron spool, `shared-postgres` databases `arb`, `news`, and `runtime_control`, `arb-redis`, `shared-qdrant`, `exo-watchdog`, `news-server` |
| IBKR | `Constructure-repos/ibkr/` | always-on broker adapter | `ibkr` | `shared-postgres` database `ibkr`, `arb-redis` for watchlist/alerts, `vnc-manager.service` / IB Gateway |
| Dashboard | `apps/dashboard/` | always-on UI/BFF | `dashboard` | `runtime-control` API, `ibkr` API |
| VideoProcess | `apps/VideoProcess/` | always-on UI/API plus scheduled workers | `api`, `frontend`, `ffmpeg-worker`, `xtts-api`; consumes `youtube-manager`, `platform-browser-manager`, `xiaohongshu-browser-manager` | `shared-postgres` database `videoprocess`, `vp-redis`, MinIO, `shared-qdrant`, `exo-watchdog`, Exo model metadata, embedding gateway, VNC/Chrome CDP through platform-upload |
| Arb | `apps/arb/` | always-on trading services plus scheduled maintenance | `collector`, `strategy`, `executor-kalshi`, `resolver`, `validator`, `arb-executor-polymarket` | `arb-redis`, `shared-postgres` database `arb`, `shared-qdrant`, `exo-watchdog`, Exo model metadata, Polymarket VPN namespace |
| News | `apps/news/` and Mac3 native service dirs | always-on API plus scheduled collector | `news-server`, `news-collector` | `shared-postgres` database `news`, `shared-qdrant`, `exo-watchdog`, Exo model metadata, embedding gateway |
| News Publisher | `apps/news-publisher/` | scheduled one-shot job | `news-publisher` | `news-server`, `x-bot` for X posting, optional Upstash cache mirror; indirectly uses VNC/Chrome CDP through platform-upload |
| Job Autoflow | `apps/job-autoflow/` | on-demand app | `job-autoflow` | shared desktop/X11 session when browser login flows are needed, optional `gmail-bridge` for OTP/email, optional local or OpenAI-compatible model endpoint |
| Gmail Bridge | `apps/gmail-bridge/` | on-demand local API | `gmail-bridge` | Google OAuth/Gmail API credentials and local `data/` state; no shared Constructure infra dependency |
| World Monitor | external/project doc only in this workspace | always-on web app plus generated cache payload | `worldmonitor-web`, `worldmonitor-hourly` cache payload | `news-publisher` cache output, `news-server`, `exo-watchdog`, IB Gateway where market routes are enabled, external Vercel/Railway/Upstash services |

Detailed service notes live under `docs/services/`:

- [Dashboard](./services/dashboard.md)
- [VideoProcess](./services/videoprocess.md)
- [Arb](./services/arb.md)
- [News](./services/news.md)
- [News Publisher](./services/news-publisher.md)
- [Job Autoflow](./services/job-autoflow.md)
- [Gmail Bridge](./services/gmail-bridge.md)
- [World Monitor](./services/worldmonitor.md)
- [Worldmonitor Hourly](./services/worldmonitor-hourly.md)

## Runtime Services

### Always-On App Services

`ops/compose/apps-up.sh` starts the always-on app subset:

| Compose file | Services started | Purpose |
| --- | --- | --- |
| `Constructure-repos/constructure-runtime-control/docker-compose.yml` | `runtime-control` | privileged operations API |
| `Constructure-repos/ibkr/docker-compose.yml` | `ibkr` | broker adapter and portfolio/order API |
| `apps/dashboard/docker-compose.yml` | `dashboard` | UI and BFF proxy |
| `apps/VideoProcess/docker-compose.yml` | `api`, `frontend` | media workflow API and UI |
| `apps/arb/docker-compose.yml` | `collector`, `strategy`, `executor-kalshi` | live market ingestion, arbitrage detection, Kalshi execution |

`ops/compose/apps-status.sh` also reports services that may be schedule-managed:
`ffmpeg-worker`, `resolver`, and `validator`.

### Platform-Upload Services Consumed By Apps

| Service | Source | Consumer apps | Infra dependencies |
| --- | --- | --- | --- |
| `youtube-manager` | `platform-upload/YouTubeManager/` | VideoProcess | YouTube OAuth credentials and app-local download state |
| `platform-browser-manager` | `platform-upload/PlatformBrowserManager/` | VideoProcess, `x-bot` | `vnc-manager.service`, Chrome CDP `127.0.0.1:18810`, browser profile state |
| `xiaohongshu-browser-manager` | `platform-upload/PlatformBrowserManager/` | VideoProcess | X11/VNC desktop and browser profile state |
| `x-bot` | `platform-upload/x-bot/` | news-publisher | VideoProcess platform API, MiniMax API, Chrome CDP when browser automation is used |

Platform-upload services are not generic infra. They are app-facing runtime
services, but they are shared enough that they should stay outside a single app
directory.

### Schedule-Controlled Services

| Window or job | Services | Control files | Infra dependencies |
| --- | --- | --- | --- |
| Video window | main host `ffmpeg-worker`, main host `xtts-api`, Mac1 `vp-worker`, Mac1 `tts-service` | `ops/schedule/cycle-video-open.sh`, `cycle-video-drain.sh`, `cycle-video-close.sh` | `vp-redis`, `shared-postgres`, MinIO/local storage, `exo-watchdog`, GPU/host media tooling |
| News night window | Mac3 `news-collector` | `ops/schedule/cycle-news-open.sh`, `cycle-news-close.sh` | `shared-postgres`, `shared-qdrant`, `exo-watchdog`, embedding gateway |
| Arb maintenance window | `resolver`, `validator` | `ops/schedule/cycle-arb-open.sh`, `cycle-arb-close.sh` | `arb-redis`, `shared-qdrant`, `exo-watchdog`, Exo model metadata |
| Morning distribution | `news-publisher` | `ops/schedule/run-news-publisher*.sh` | `news-server`, `x-bot`, optional Discord webhook and Upstash |

### On-Demand Services

| Service | Compose file | Purpose | Dependencies |
| --- | --- | --- | --- |
| `job-autoflow` | `apps/job-autoflow/docker-compose.yml` | local job search and application automation | local SQLite/runtime volumes, X11 browser sessions, optional `gmail-bridge` |
| `gmail-bridge` | `apps/gmail-bridge/docker-compose.yml` | local Gmail HTTP API | Gmail OAuth credentials and token state |

`ops/compose/host-core-status.sh` reports on-demand services, but
`host-core-up.sh` does not start them.

## Infra Dependency Matrix

| Infra capability | App consumers |
| --- | --- |
| `shared-postgres` | VideoProcess (`videoprocess`), Arb (`arb`), News (`news`), IBKR (`ibkr`), Runtime Control (`runtime_control`) |
| `shared-qdrant` | Arb resolver, News indexing/search, VideoProcess retrieval/embedding features, Runtime Control status views |
| `arb-redis` | Arb collector/strategy/executors, IBKR watchlists/alerts, Runtime Control arb state views |
| `vp-redis` | VideoProcess API and workers |
| MinIO | VideoProcess object/file storage when S3-compatible storage is enabled |
| `exo-watchdog` and Exo model metadata | VideoProcess LLM flows, Arb validator, News collector summaries, Runtime Control health views |
| Embedding gateway | News and embedding workloads, VideoProcess when configured for remote embeddings |
| `vnc-manager.service` and Chrome CDP | Platform-browser services, `x-bot`, browser-login workflows, dashboard IBKR visibility |
| IB Gateway | Canonical `ibkr` service; other apps consume IBKR through the local IBKR API |
| Polymarket VPN namespace | `arb-executor-polymarket` |

## Operational Commands

Start and inspect the application layer:

```bash
bash ops/compose/apps-up.sh
bash ops/compose/apps-status.sh
```

Inspect full runtime state:

```bash
bash ops/compose/host-core-status.sh
bash ops/schedule/schedule-status.sh
```

Start on-demand tools:

```bash
bash ops/compose/interactive-tools-start.sh
bash ops/compose/interactive-tools-status.sh
bash ops/compose/interactive-tools-stop.sh
```

## Development Rules For Agents

- Add every new app to the application inventory with its source directory,
  runtime class, services, and infra dependencies.
- Prefer `infra/shared-infra` dependencies over app-local Postgres, Redis,
  Qdrant, MinIO, browser managers, or LLM gateways.
- When adding a shared Postgres role to an existing runtime, update the init
  script and run `ops/database/provision-shared-postgres.sh`; Docker init files
  only run for fresh volumes.
- If an app adds a compose service, update the matching `ops/compose/*` or
  `ops/schedule/*` entrypoint before documenting it as active runtime.
- Classify services as always-on, schedule-controlled, one-shot, or on-demand.
  Do not hide a scheduled job in the always-on app list.
- Keep platform-upload services in the platform-upload layer unless the service
  is truly private to one app.
- Document variable names and default endpoints, not live secrets from `.env`
  files.
- When app dependencies change, update this document and
  [Infra Services](./infra-services.md) in the same change.
