# Compose Runtime And Schedule

This document describes the current production runtime after the K8s exit.

The active runtime model is:

- `docker compose` on the main host for shared infra and most host services
- `systemd` for host-native infrastructure that should not be wrapped into compose
- `ssh + start.sh/stop.sh/status.sh` for native services on the active Macs
- `cron + ops/schedule/*` for time-windowed services and one-shot jobs

If this document disagrees with older K8s notes, treat this file as the source of truth.

## Current Control Surface

Linux service source lives under:

- `infra/`
- `platform-upload/`
- `apps/`

See [Linux Service Layers](../LINUX_SERVICE_LAYERS.md) for the canonical
directory layout and canonical path policy.

Main host commands:

```bash
bash ops/compose/infra-up.sh
bash ops/compose/platform-upload-up.sh
bash ops/compose/apps-up.sh
bash ops/compose/host-core-up.sh
bash ops/compose/host-core-status.sh
bash ops/schedule/schedule-status.sh
```

On-demand services:

```bash
bash ops/compose/interactive-tools-start.sh
bash ops/compose/interactive-tools-status.sh
bash ops/compose/interactive-tools-stop.sh
```

Cron install:

```bash
bash ops/schedule/install-cron.sh
crontab -l
```

Cron log:

```bash
tail -f dashboard/data/cronlogs/cronwrap.jsonl
```

## Machine Roles

| Machine | Role | Runtime style |
| --- | --- | --- |
| Main host | shared infra, control plane, realtime services, cron scheduler | `docker compose` + `systemd` |
| Mac1 `10.0.0.127` | VideoProcess worker node | native service scripts |
| Mac3 `10.0.0.126` | news + embedding node | native service scripts |

## Linux Service Layers

The Linux host uses a dependency graph, not a single linear chain:

```text
infra
├── platform-upload
├── apps
├── schedule
└── on-demand
```

- `platform-upload`, `apps`, `schedule`, and `on-demand` all depend on `infra`.
- Some `apps` features also depend on `platform-upload`, but apps that do not
  publish or browse platforms can still be reasoned about as app-layer services.
- `host-core-up.sh` remains the compatibility entrypoint and starts the layers in
  dependency order.

### Infra

Support services and host infrastructure:

- `shared-postgres`
- `shared-qdrant`
- `vp-redis`
- `arb-redis`
- `minio`
- `exo-watchdog`
- `vnc-manager.service`
  - manages `Xvfb :99`
  - manages `x11vnc :5999`
  - manages `chrome-cdp :18810`
  - manages `ibkr-gateway :4001`
- `constructure-polymarket.service`
  - starts the Polymarket VPN namespace and `arb-executor-polymarket`

### Platform Upload

Platform upload, platform browser automation, and publisher backends:

- `youtube-manager`
- `platform-browser-manager`
  - currently serves X and Bilibili platform browser APIs
- `xiaohongshu-browser-manager`
- `x-bot`

### Apps

Always-on application services:

- `dashboard`
- `videoprocess api`
- `videoprocess frontend`
- `arb collector`
- `arb strategy`
- `arb executor-kalshi`
- `worldmonitor-web`

Mac3:

- `embedding-gateway`
- `news-server`

### Schedule-Controlled Services

Video window:

- main host `vp_ffmpeg_worker`
- main host `xtts-api`
- Mac1 `vp-worker`
- Mac1 `tts-service`

News night window:

- Mac3 `news-collector`

Arb maintenance window:

- `arb resolver`
- `arb validator`

### One-Shot Scheduled Jobs

- `news-publisher`
  - unified news distribution job
  - fetches news once, then fan-outs to the configured channels
  - current managed channels are `x` and `discord`
  - the `x` channel delegates to `x-bot`; it no longer uses the direct Playwright publisher path
  - also refreshes the `worldmonitor-hourly` cache payload used by World Monitor

### On-Demand Services

- `job-autoflow`
- `gmail-bridge`

## Time Schedule

Timezone: `America/Los_Angeles`

### 00:30 - 07:00

- `news-collector` runs on Mac3
- purpose: fetch, chunk, embed, and write to Qdrant at night

### 01:00 - 06:00

- `video OPEN`
- new VideoProcess jobs are allowed into worker consumption
- host worker + XTTS run
- Mac1 worker + TTS run

### 06:00 - 07:00

- `video DRAINING`
- no new video jobs are released into Redis
- already-running jobs continue to finish

### 07:00

- `video CLOSE`
- `news-collector CLOSE`

### 07:15

- run `news-publisher` once

### 19:00 - 01:00

- `arb resolver + validator` window

## VideoProcess Window Semantics

The VideoProcess frontend is always available.

Window state only controls consumption, not UI access.

- `OPEN`
  - newly submitted jobs go into normal execution
- `DRAINING`
  - fresh submissions are stored as `WAITING_WINDOW`
  - already-running jobs keep finishing
- `CLOSED`
  - fresh submissions are stored as `WAITING_WINDOW`

Relevant internal API:

- `GET /internal/schedule/video/status`
- `POST /internal/schedule/video/open`
- `POST /internal/schedule/video/drain`
- `POST /internal/schedule/video/close`

## Status Views

### Host Runtime

```bash
bash ops/compose/host-core-status.sh
```

Shows:

- infra
- platform-upload
- apps
- on-demand compose services
- Polymarket executor service state

### Scheduler View

```bash
bash ops/schedule/schedule-status.sh
```

Shows:

- VideoProcess window state
- desktop infrastructure state
- app services
- platform-upload services
- windowed services
- Mac1 worker states
- Mac3 news states
- latest morning one-shot job results
- on-demand service states

## What Is Not In Compose

Not every service is a compose container.

Expected non-compose runtime:

- `vnc-manager.service`
- `constructure-polymarket.service`
- Mac native service directories under `~/Constructure/services/...`

Do not try to force these into the compose lifecycle unless you also redesign their start model.

## Maintenance Notes

- K3s is no longer the active runtime path on the main host.
- Old K8s documents may still exist in the repo, but the active operations path is `ops/compose` and `ops/schedule`.
- `exo` is not part of the required base runtime. It is an optional watchdog backend.
- If Mac1 needs more room for video, stop `exo` first before reducing VP capacity.
- `news-publisher` is the active news distribution path.
- X and Discord share the same content selection pass; the channel fan-out happens inside `news-publisher`.
- The `x` channel is delivered through `x-bot`, which then calls the shared `platform-browser-manager` path behind the VideoProcess frontend proxy.
- Discord posting is controlled by `NP_DISCORD_WEBHOOK_URL`. If it is empty, the Discord channel stays in dry-run mode.

## Useful Checks

Main host:

```bash
systemctl --user status vnc-manager.service --no-pager
systemctl status constructure-polymarket.service --no-pager
curl -fsS http://127.0.0.1:7799/ | python3 -m json.tool
docker ps
```

Mac resource spot check:

```bash
ssh -i ~/.ssh/id_mini_wenjie wenjieliu@10.0.0.127 "top -l 1 | egrep 'PhysMem|CPU usage|Load Avg'"
ssh -i ~/.ssh/id_mini_wenjie magi1@10.0.0.126 "top -l 1 | egrep 'PhysMem|CPU usage|Load Avg'"
```

Mac service spot check:

```bash
ssh -i ~/.ssh/id_mini_wenjie wenjieliu@10.0.0.127 "bash ~/Constructure/services/vp-worker/status.sh; bash ~/Constructure/services/tts-service/status.sh"
ssh -i ~/.ssh/id_mini_wenjie magi1@10.0.0.126 "bash ~/Constructure/services/news-server/status.sh; bash ~/Constructure/services/news-collector/status.sh; bash ~/Constructure/services/embedding-gateway/status.sh"
```
