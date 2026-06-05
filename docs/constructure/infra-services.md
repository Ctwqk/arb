# Infra Services

This document is the agent-facing reference for the current Constructure
runtime. Use it when changing application services so production keeps one
shared infra layer instead of recreating databases or browser managers inside
each app.

Status date: 2026-05-29.

## Current Topology

| Host | Role | Runtime identity |
| --- | --- | --- |
| `10.0.0.150` / `ccttww-lap` | Swarm manager, shared infrastructure, GPU services, dashboard, IBKR, VNC/browser state | Docker Swarm manager node `ccttww-lap` |
| `10.0.0.126` / `CASPERs-Mac-mini.local` | ForWin and news application runtime | Colima/Swarm node `colima-swarmbridged`; host forwards expose app ports |
| `10.0.0.127` / `Wenjies-Mac-mini.local` | VideoProcess, PDS, feature aggregator, and arb application runtime | Colima/Swarm node `colima-127`; host forwards expose app ports |

Constructure as a folder/concept exists only on `10.0.0.150`. The two Mac
hosts should keep project-specific directories such as `ForWin-swarm` and
`VideoProcess-app`; do not create a new Constructure root there for production
apps.

## Source And Deployment Boundaries

Production is deployed by GitHub-tracked repositories, but the runtime copies on
126 and 127 are mostly deployment directories.

| Project | Source-of-truth edit location | Production target |
| --- | --- | --- |
| ForWin | `10.0.0.246:/home/kikuhiko/ForWin` on branch `master` | `10.0.0.126:/Users/magi1/ForWin-swarm` |
| VideoProcess | Preferred dev checkout `10.0.0.246:/home/kikuhiko/videoprocess` on branch `main`; auxiliary checkouts `10.0.0.126:/Users/magi1/VideoProcess-app` and `10.0.0.150:/home/taiwei/Constructure-repos/videoprocess` | `10.0.0.127:/Users/wenjieliu/VideoProcess-app` |
| Policy Decision Service | `10.0.0.150:/home/taiwei/Constructure-repos/policy-decision-service` | `10.0.0.127:/Users/wenjieliu/.deploy-build/policy-decision-service` |
| VP Feature Aggregator | `10.0.0.150:/home/taiwei/Constructure-repos/vp-feature-aggregator` | `10.0.0.127:/Users/wenjieliu/.deploy-build/vp-feature-aggregator` |
| Arb | `10.0.0.150:/home/taiwei/Constructure-repos/arb` | `10.0.0.127:/Users/wenjieliu/arb-swarm-src` |
| News | GitHub repo `Ctwqk/news`; deploy mirror currently at `10.0.0.150:/home/taiwei/deploy-github-sync/repos/news` | `10.0.0.126:/Users/magi1/Constructure/news` |
| IBKR | `10.0.0.150:/home/taiwei/Constructure-repos/ibkr` | 150 only |
| Runtime control / dashboard / shared infra | `10.0.0.150:/home/taiwei/Constructure-repos/constructure-runtime*` | 150 only |

The 126/127 directories with `.deploy-sync-project` and
`.deploy-sync-source-commit` are deployment outputs. Do not create long-lived
Codex coding projects there unless the task is explicitly about inspecting a
deployed artifact.

## Infra Inventory

All shared stateful infrastructure is centralized on `10.0.0.150`.

| Capability | Production endpoint | Main consumers |
| --- | --- | --- |
| Shared PostgreSQL | `10.0.0.150:5435` | ForWin, VideoProcess, PDS, arb, news, IBKR, runtime-control |
| Shared Qdrant | HTTP `http://10.0.0.150:6333`, gRPC `10.0.0.150:6334` | news retrieval, arb matching, optional app vector features |
| Arb Redis | `redis://10.0.0.150:6379` | arb orderbooks/state, arb app services |
| VideoProcess Redis | `redis://10.0.0.150:6380` | VP queues, PDS/aggregator/event relay integration |
| MinIO | API `http://10.0.0.150:9000`, console `http://10.0.0.150:9001` | object/artifact storage |
| Redpanda | overlay `redpanda:9092`, host bridge `10.0.0.150:19092` | PDS, VP outbox relay, feature aggregator |
| Embedding gateway | `http://10.0.0.150:8080` | news and arb embedding workloads |
| Dashboard | SSH-tunneled `http://127.0.0.1:7700` on 150 | operator UI |
| IBKR API | `http://127.0.0.1:7701` on 150 | dashboard IBKR tab and IBKR clients |
| VNC manager | API `127.0.0.1:7799`, VNC `127.0.0.1:5999`, IB Gateway `127.0.0.1:4001` on 150 | durable browser desktop, IBKR login |

Applications may contain database code, migrations, compose files, and local
development profiles. That does not mean production should start app-local
Postgres, Redis, MinIO, or Qdrant on 126/127. Production containers connect to
the 150-hosted infra endpoints or to Swarm service names when they are on the
same overlay network.

## Application Entry Points

| Surface | URL / port | Notes |
| --- | --- | --- |
| ForWin UI/API | `http://10.0.0.126:8899` | Host-forwarded from the 126 Colima/Swarm runtime |
| ForWin MCP | `10.0.0.126:8896` | Host-forwarded from the 126 runtime |
| News server health/API | `http://10.0.0.126:6551` | Host-forwarded from the 126 runtime |
| ForWin/browser helper ports | `10.0.0.126:18896`, `10.0.0.126:18899` | Host-forwarded browser/helper surfaces |
| VideoProcess frontend | `http://10.0.0.127:3001` | Host-forwarded from the 127 Colima/Swarm runtime |
| VideoProcess API | `http://10.0.0.127:18080` | Host-forwarded from the 127 runtime |
| Dashboard | SSH tunnel to `10.0.0.150:7700` localhost | Do not expose directly on LAN without redesign |
| IBKR VNC | SSH tunnel to `10.0.0.150:5999` localhost | Used for IBKR Mobile / Gateway login checks |

## Swarm Services

Current production service names:

| Service | Normal replicas | Target role |
| --- | --- | --- |
| `forwin-app-swarm` | `1/1` | ForWin app on 126 |
| `forwin-mcp-swarm` | `1/1` | ForWin MCP on 126 |
| `news-server-swarm` | `1/1` | News API on 126 |
| `news-collector-swarm` | `0/0` when disabled | News collector on 126 |
| `vp-frontend-swarm` | `1/1` | VP frontend on 127 |
| `vp-api-swarm` | `1/1` | VP API on 127 |
| `vp-channel-agent-runner-swarm` | `1/1` | VP worker on 127 |
| `vp-event-outbox-relay-swarm` | `1/1` | VP event relay on 127 |
| `vp-pds-swarm` | `1/1` | PDS on 127 |
| `vp-feature-aggregator-swarm` | `1/1` | Feature aggregator on 127 |
| `arb-resolver-swarm` | scheduled by arb open/close window | Arb matching on 127 |
| `arb-validator-swarm` | scheduled by arb open/close window | Arb validation on 127 |
| `arb-executor-polymarket-swarm` | `1/1` | Wallet/VPN-bound execution; keep on 150 |
| `redpanda` | `1/1` | Kafka/Redpanda bridge |

Do not infer service health only from repository files. Verify with
`docker service ls`, browser checks for UI surfaces, and the `.deploy-sync-*`
markers on target hosts.

## Data Store Rules

- New production relational state should use a dedicated database/role inside
  the 150 shared PostgreSQL instance.
- New production queue/state traffic should reuse the appropriate 150 Redis
  instance only when the workload matches that instance's purpose.
- New object storage should use a dedicated MinIO bucket or prefix, not an
  app-local MinIO container.
- New vector data should use separate Qdrant collections in the 150 shared
  Qdrant instance.
- Do not start Postgres, Redis, MinIO, or Qdrant on 126/127 for production.

## Browser, Desktop, And Market Access

`vnc-manager.service` on 150 owns the virtual desktop:

- Xvfb display `:99`
- x11vnc `127.0.0.1:5999`
- manager API `127.0.0.1:7799`
- managed Chrome/CDP sessions
- IB Gateway `127.0.0.1:4001`

IBKR remains 150-only because it depends on local VNC state, localhost Gateway
ports, and account login. Arb Polymarket execution remains tied to the 150 VPN
namespace/wallet path unless that networking model is redesigned.

## Deployment Sync

The 150 cron job runs:

```bash
/home/taiwei/deploy-github-sync/bin/deploy-github-sync.sh --apply
```

It tracks GitHub branches, builds deploy images, rsyncs target directories to
126/127, updates Swarm services, and writes `.deploy-sync-project` plus
`.deploy-sync-source-commit` markers. ForWin changes must be made in the 246
ForWin repo and pushed to `master` before this deploy path can pick them up.

## Triage Commands

```bash
# Swarm and service distribution
ssh 10.0.0.150 'docker node ls && docker service ls'

# App browser entrypoints
open http://10.0.0.126:8899
open http://10.0.0.127:3001

# API checks
curl http://10.0.0.126:6551/health
curl http://10.0.0.127:18080/api/v1/node-types

# IBKR dashboard through SSH tunnel
ssh -L 7700:127.0.0.1:7700 -L 5999:127.0.0.1:5999 10.0.0.150
open http://127.0.0.1:7700
```
