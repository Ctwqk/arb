# Infra Services

This document is the agent-facing reference for the Constructure infra layer.
Use it when adding or changing application services so shared runtime
dependencies are reused instead of duplicated.

## Layer Model

Constructure is not a strict two-layer system, but the dependency model has one
base layer:

```text
infra
|-- platform-upload
|-- apps
|-- schedule
`-- on-demand
```

- `infra/` owns shared support services and host-native runtime primitives.
- `apps/` owns application services such as VideoProcess, arb, dashboard, news,
  news-publisher, gmail-bridge, and job-autoflow.
- `platform-upload/` is a separate app-adjacent layer for browser automation and
  publisher backends. It depends on infra and is consumed by some apps.
- `schedule` and `on-demand` are control surfaces, not separate product
  projects. They also depend on infra.

For most development decisions, think in two groups: infra provides shared
capabilities; apps and platform-upload consume them.

## Infra Inventory

| Capability | Runtime owner | Default endpoint | Main consumers |
| --- | --- | --- | --- |
| Shared PostgreSQL | `infra/shared-infra/docker-compose.yml` service `shared-postgres` | `127.0.0.1:5435` | VideoProcess, arb, news, dashboard |
| Shared Qdrant | `infra/shared-infra/docker-compose.yml` service `shared-qdrant` | `http://127.0.0.1:6333`, gRPC `127.0.0.1:6334` | arb resolver, news, VideoProcess, dashboard |
| Arb Redis | `infra/shared-infra/docker-compose.yml` service `arb-redis` | `redis://127.0.0.1:6379` | arb services, dashboard |
| VideoProcess Redis | `infra/shared-infra/docker-compose.yml` service `vp-redis` | `redis://127.0.0.1:6380` | VideoProcess API and workers |
| MinIO | `infra/shared-infra/docker-compose.yml` service `minio` | API `127.0.0.1:9000`, console `127.0.0.1:9001` | Object/file storage consumers |
| Exo watchdog | `infra/exo-watchdog/docker-compose.yml` service `exo-watchdog` | `http://127.0.0.1:8000` | LLM-bound workloads, dashboard, news, VideoProcess, arb validator |
| Exo model metadata | `infra/exo/model.txt`, scripts in `infra/exo/` | Local file and watchdog/state APIs | Services that need the active local LLM model |
| Embedding gateway | `infra/embedding-gateway/` native service on Mac3 | `http://10.0.0.126:17056` in current schedule status | News and embedding workloads |
| Desktop/VNC manager | `infra/vnc-manager/vnc-manager.service` | API `127.0.0.1:7799`, VNC `127.0.0.1:5999`, Chrome CDP `127.0.0.1:18810` | browser automation, X/Bilibili publishing, IBKR |
| IB Gateway | managed by `infra/vnc-manager/apps.yaml` | `127.0.0.1:4001` | arb/dashboard market data integrations |
| Polymarket VPN namespace | `ops/systemd/constructure-polymarket.service` plus `infra/polymarket/` | namespace `vpn-polymarket` | `arb-executor-polymarket` |

## Shared Datastores

### PostgreSQL

`shared-postgres` is the canonical host PostgreSQL for shared-mode services.
It runs with `network_mode: host`; apps usually connect through
`host.docker.internal:5435` from containers or `127.0.0.1:5435` from host-native
processes.

The init script creates these roles and databases:

| Role | Database | Intended consumer |
| --- | --- | --- |
| `vp` | `videoprocess` | VideoProcess |
| `arb` | `arb` | arbitrage system |
| `news` | `news` | news collector/server |

If a new app needs relational storage, prefer adding a dedicated database and
role to `infra/shared-infra/init/001-create-databases.sh` instead of starting a
new Postgres container. Do not assume app-local standalone Postgres profiles are
active in production.

### Qdrant

`shared-qdrant` is the shared vector database. It exposes HTTP on `6333` and
gRPC on `6334`. Services should namespace their collections clearly to avoid
cross-app collisions.

Use this shared instance for search, embeddings, matching, and retrieval
features unless there is a strong isolation reason to create a separate vector
store.

### Redis

There are two Redis instances because the traffic patterns are different:

- `arb-redis` on port `6379` uses a password and persistent append-only storage.
  It is for orderbooks, arb state, and arb inter-service coordination.
- `vp-redis` on port `6380` is for VideoProcess queues and scheduling state.

Do not reuse `arb-redis` for unrelated app queues. If an app needs a new queue,
either justify sharing `vp-redis` or add a clearly named Redis service in infra.

### MinIO

`minio` is the shared S3-compatible object store. The default API port is
`9000`; the console is on `9001`. Apps should use separate buckets and avoid
writing directly into another app's bucket prefix.

## AI And Embedding Services

### Exo Watchdog

`exo-watchdog` is the local LLM control-plane service. It monitors the Exo
cluster, exposes a stable request ingress, tracks model state, handles stale-job
recovery, and can coordinate remote restarts when configured.

Important defaults from the compose file:

- Service URL: `http://127.0.0.1:8000`
- Upstream Exo endpoints: `http://192.168.20.1:52415` and
  `http://192.168.20.2:52415`
- Desired model: `mlx-community/Qwen3-30B-A3B-4bit`
- Runtime data: `infra/exo-watchdog/data/watchdog.db`

Apps that need local LLM calls should depend on this service rather than calling
individual Exo nodes directly.

### Exo Model Helpers

`infra/exo/` is runtime glue for discovering and syncing the active model. The
most important file for consumers is `infra/exo/model.txt`, which is mounted
read-only into services that need the active model name.

Use `infra/exo/resolve_model.sh` or `infra/exo/update_models.sh` when a service
needs to refresh model metadata.

### Embedding Gateway

`infra/embedding-gateway/` is a FastAPI service backed by
`sentence-transformers`. It exposes:

- `GET /health`
- `GET /metadata`
- `POST /embed`

The Dockerfile exposes port `8080`, but current schedule status reports the
Mac3 native service as `embedding-gateway: running:17056`. Treat the runtime
port as environment-specific and read it from the schedule/service config before
hard-coding a client.

## Desktop, Browser, And Market Access

### VNC Manager

`vnc-manager.service` is host-native systemd infrastructure. It owns the virtual
desktop and keeps browser/desktop processes alive:

- Xvfb display `:99`
- x11vnc on port `5999`, bound to localhost by default
- manager API on port `7799`, bound to `127.0.0.1` by default
- Chrome CDP on port `18810`
- IB Gateway on port `4001`

The managed apps are configured in `infra/vnc-manager/apps.yaml`.

Use this service when work needs a durable headed browser session, login state,
or IBKR Gateway access. Do not start a second Xvfb/IBKR manager for app code;
that will fight with this service.

The default access model is SSH/tmux tunnel first. Override only when you have a
specific network exposure requirement:

- `VNC_MANAGER_API_HOST=127.0.0.1`
- `VNC_MANAGER_API_TOKEN=` optional bearer token for the manager API
- `VNC_MANAGER_VNC_LOCALHOST=true`

When `VNC_MANAGER_API_TOKEN` is set, status scripts pass it through to the
manager API using the `Authorization: Bearer ...` header.

### Dashboard Control Plane

The dashboard can start, stop, and inspect host containers, processes, cron
entries, and env files. It should be treated as a privileged control plane, not
as a public web app.

Default access is local-only for SSH tunnel use:

- `DASHBOARD_BIND_HOST=127.0.0.1`
- `DASHBOARD_API_TOKEN=` optional bearer token for `/api/*`
- `DASHBOARD_CORS_ORIGINS=http://127.0.0.1:7700,http://localhost:7700`

If `DASHBOARD_API_TOKEN` is set, static UI assets remain public on the local
listener, but every `/api/*` request must include `Authorization: Bearer <token>`.

### Polymarket Namespace

`constructure-polymarket.service` is a host systemd unit that prepares the
`vpn-polymarket` Linux network namespace with `sing-box`, then starts
`arb-executor-polymarket`. It is intentionally outside the normal compose
lifecycle because it manages host networking.

Do not move this into app compose without redesigning the namespace and cleanup
model. App services that need Polymarket execution should treat the namespace
and executor as infra-managed runtime.

## Operational Commands

Start and inspect the infra layer:

```bash
bash ops/compose/infra-up.sh
bash ops/compose/infra-status.sh
```

Inspect the complete host runtime:

```bash
bash ops/compose/host-core-status.sh
bash ops/schedule/schedule-status.sh
```

Direct status checks that are often useful:

```bash
docker compose -f infra/shared-infra/docker-compose.yml ps
docker compose -f infra/exo-watchdog/docker-compose.yml ps
systemctl --user status vnc-manager.service
systemctl status constructure-polymarket.service
```

## Development Rules For Agents

- Reuse infra services before adding app-local databases, Redis instances,
  browser managers, or LLM gateways.
- Keep production/shared-mode config separate from standalone development
  profiles. Several app compose files include `standalone` profiles for local
  testing; those are not the current host runtime.
- Use `host.docker.internal` for container-to-host infra access when existing
  compose files follow that pattern.
- Add new shared databases, ports, credentials, and buckets to this document
  when changing infra.
- Do not commit secrets from `.env` files. Document variable names and defaults,
  not live credentials.
- Be careful with host-networked services. Many infra and app containers use
  `network_mode: host`, so port collisions are production-impacting.
- Treat `ops/compose/*` and `ops/schedule/*` as the public operational entry
  points. Avoid teaching future agents to run ad hoc `docker compose up` commands
  unless the command is scoped to a documented compose file.
