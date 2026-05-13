# Constructure Decoupling Design

## Goal

Make each Constructure repository independently understandable, testable, and
maintainable. A developer opening `Ctwqk/ibkr`, `Ctwqk/dashboard`,
`Ctwqk/rltrader`, or runtime operations should not need to infer hidden sibling
directory dependencies.

## Decisions

- Perform an architecture-level split, not only a directory cleanup.
- Migrate in compatible phases: new services first, dashboard proxy second,
  old implementations removed after tests pass.
- Rebuild `Ctwqk/ibkr` as the canonical private IBKR repository and replace its
  previous `main` history with a clean orphan history.
- Create private `Ctwqk/constructure-runtime-control` for privileged host
  control APIs.
- Keep `Ctwqk/dashboard` as a UI/BFF repository. It must not own broker,
  Docker, cron, host process, or cross-service datastore implementations.
- Use one shared Postgres instance, but separate databases and roles for
  services that own state: `ibkr`, `runtime_control`, `arb`, `news`, and
  `videoprocess`.
- Make `Ctwqk/rltrader` consume the canonical IBKR API instead of carrying an
  active local IBKR service.

## Target Architecture

### `Ctwqk/ibkr`

Owns all IBKR broker-adapter behavior:

- IB Gateway connectivity through `ib_insync`.
- Quotes, positions, account summary, open orders, order cancellation, and
  guarded order submission.
- Watchlist, Redis-backed alerts, and monitor loop.
- Portfolio groups, targets, previews, order runs, and action logs.
- VNC manager gateway status/restart integration.

The service exposes a local FastAPI API on `IBKR_API_PORT`, default `7701`, and
persists state through `IBKR_PG_DSN`, defaulting to the `ibkr` database.

### `Ctwqk/constructure-runtime-control`

Owns privileged host control behavior:

- Container listing/actions.
- Host metrics, process listing/actions, and GPU status.
- User and system cron listing, wrapping, running, editing, and logs.
- Environment file listing/editing/restart hooks.
- SSH tunnel and tunnel-group management.
- Exo, guardian, OpenBB, and news-publisher runtime control endpoints.

The service exposes a local FastAPI API on `RUNTIME_CONTROL_PORT`, default
`7702`, and persists its own small state through `RUNTIME_CONTROL_PG_DSN` when
database state is required. It is the only Constructure application service
that should mount the Docker socket, host PID namespace, cron spool, or full
host runtime root.

### `Ctwqk/dashboard`

Owns the UI and lightweight BFF routes:

- Static dashboard UI.
- Compatibility proxy routes for `/api/ibkr/*` and runtime-control endpoints.
- Local auth token enforcement for dashboard callers.

It should not import `ib_insync`, `docker`, or direct service datastores such as
the `arb`, `news`, or `ibkr` databases. It talks to local services through
HTTP.

### `Ctwqk/rltrader`

Owns trading experiments, AlphaVantage helpers, ML scripts, and Milvus test
stack. It may consume IBKR through `IBKR_API_URL`, but it must not run a second
active IBKR client or monitor.

### `Ctwqk/constructure-runtime`

Owns runtime docs, shared infra, compose/schedule entrypoints, systemd units,
and the GitHub sync manifest. It remains the source of truth for duplicated
`docs/constructure/*` snapshots in app repositories.

## Data Boundaries

- Add Postgres role/database `ibkr` for IBKR portfolio/order-run state.
- Add Postgres role/database `runtime_control` for runtime-control state.
- Migrate existing `ibkr_*` tables out of the `arb` database into the `ibkr`
  database.
- Runtime-control can continue to read host files and crontabs directly because
  those are host resources, but this access must not remain in dashboard.

## Compatibility Strategy

The first implementation keeps user-visible dashboard API paths stable by
proxying them:

- `/api/ibkr/*` -> `IBKR_API_URL`.
- `/api/containers`, `/api/system/*`, `/api/cron/*`, `/api/env/*`,
  `/api/ssh-tunnels`, `/api/tunnel-groups`, `/api/exo/*`,
  `/api/news-publisher/*`, and similar host-control paths ->
  `RUNTIME_CONTROL_API_URL`.

After smoke tests pass, direct implementations are removed from dashboard so the
repo can be opened independently.

## Security Rules

- All Constructure-owned repositories are private by default.
- No `.env`, API key files, OAuth token files, browser profiles, DB files, logs,
  media output, dependency directories, or build outputs may be committed.
- Existing leaked webhook/API secret values must be removed from tracked files
  and rotated outside git.
- Rebuilt `Ctwqk/ibkr` uses a clean history so the previous hard-coded webhook
  does not remain in that repository history.

