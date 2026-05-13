# Constructure Decoupling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split Constructure into independently maintainable service
repositories for IBKR, runtime-control, dashboard, rltrader, and shared runtime
operations.

**Architecture:** Rebuild `Ctwqk/ibkr` as the canonical broker adapter, create
`Ctwqk/constructure-runtime-control` for privileged host APIs, and reduce
dashboard to a UI/BFF that proxies local services. Runtime docs, shared
database creation, compose entrypoints, cron, and GitHub sync remain owned by
`Ctwqk/constructure-runtime`.

**Tech Stack:** FastAPI, Python 3, asyncpg, Redis, ib_insync, Docker SDK,
psutil, Bash, Docker Compose, GitHub CLI.

---

### Task 1: Document the Design

**Files:**
- Create: `/home/taiwei/Constructure-repos/constructure-runtime/docs/superpowers/specs/2026-05-13-constructure-decoupling-design.md`
- Create: `/home/taiwei/Constructure-repos/constructure-runtime/docs/superpowers/plans/2026-05-13-constructure-decoupling.md`

- [ ] Write the design document with the target repo boundaries, data
  boundaries, compatibility strategy, and security rules.
- [ ] Write this implementation plan with concrete phases and verification
  commands.
- [ ] Commit the docs in `constructure-runtime` before code extraction.

### Task 2: Rebuild `Ctwqk/ibkr`

**Files:**
- Create repository directory: `/home/taiwei/Constructure-repos/ibkr`
- Source from: `/home/taiwei/Constructure/apps/dashboard/src/ibkr`
- Source from: `/home/taiwei/Constructure/rltrader-cloud/ibkr/snapshot.py`
- Tests: `/home/taiwei/Constructure-repos/ibkr/tests`

- [ ] Create a clean FastAPI service package with app, settings, client,
  monitor, routes, portfolio, store, and optional snapshot endpoints.
- [ ] Change storage ownership from `ARB_PG_DSN` to `IBKR_PG_DSN`.
- [ ] Add tests for health, route wiring, settings defaults, store DSN
  selection, and portfolio behavior copied from dashboard.
- [ ] Add Dockerfile, docker-compose.yml, `.env.example`, README, and
  constructure docs snapshot.
- [ ] Initialize git with a clean orphan `main` and force-push to private
  `Ctwqk/ibkr`.

### Task 3: Create `constructure-runtime-control`

**Files:**
- Create repository directory:
  `/home/taiwei/Constructure-repos/constructure-runtime-control`
- Source from: `/home/taiwei/Constructure/apps/dashboard/src/app.py`
- Source from: `/home/taiwei/Constructure/apps/dashboard/scripts/cronwrap.sh`
- Tests: `/home/taiwei/Constructure-repos/constructure-runtime-control/tests`

- [ ] Extract host-control endpoints into a standalone FastAPI service.
- [ ] Keep privileged mounts and dependencies only in runtime-control compose.
- [ ] Add health and token middleware.
- [ ] Add tests for cron parsing/log reading, proxy-safe auth behavior, and
  settings defaults.
- [ ] Push the private `Ctwqk/constructure-runtime-control` repository.

### Task 4: Convert `dashboard` to UI/BFF

**Files:**
- Modify: `/home/taiwei/Constructure/apps/dashboard/src/app.py`
- Modify: `/home/taiwei/Constructure/apps/dashboard/docker-compose.yml`
- Modify: `/home/taiwei/Constructure/apps/dashboard/requirements.txt`
- Modify tests under `/home/taiwei/Constructure/apps/dashboard/tests`

- [ ] Add tests proving dashboard proxies `/api/ibkr/*` and runtime-control
  paths to configured local services.
- [ ] Replace direct IBKR and privileged host implementations with HTTP proxy
  calls.
- [ ] Remove dashboard imports and dependencies for `ib_insync`, Docker SDK,
  and direct cross-service datastore access.
- [ ] Remove privileged compose mounts from dashboard.
- [ ] Run dashboard tests with `PYTHONPATH=src python3 -m pytest tests -q`.

### Task 5: Update `rltrader`

**Files:**
- Modify: `/home/taiwei/Constructure/rltrader-cloud/docker-compose.yml`
- Create: `/home/taiwei/Constructure/rltrader-cloud/ibkr_client.py`
- Remove or archive active local service code under
  `/home/taiwei/Constructure/rltrader-cloud/ibkr`

- [ ] Add a small HTTP client for the canonical IBKR service.
- [ ] Remove `ibkr-client` and `ibkr-monitor` services from rltrader compose.
- [ ] Remove hard-coded webhook and MinIO secret values from tracked files.
- [ ] Compile rltrader Python files with `python3 -m compileall -q`.

### Task 6: Update `constructure-runtime`

**Files:**
- Modify:
  `/home/taiwei/Constructure-repos/constructure-runtime/infra/shared-infra/init/001-create-databases.sh`
- Modify:
  `/home/taiwei/Constructure-repos/constructure-runtime/infra/shared-infra/.env.example`
- Modify:
  `/home/taiwei/Constructure-repos/constructure-runtime/ops/github/repos.tsv`
- Add compose/schedule entrypoints under
  `/home/taiwei/Constructure-repos/constructure-runtime/ops`

- [ ] Add `ibkr` and `runtime_control` Postgres role/database setup.
- [ ] Add `ibkr` and `constructure-runtime-control` to the sync manifest.
- [ ] Add compose start/status entrypoints for the new services.
- [ ] Update cron installation to point cronwrap at runtime-control.
- [ ] Run runtime shell tests and syntax checks.

### Task 7: Verification and Sync

**Files:**
- All changed Constructure-owned repositories.

- [ ] Run each repository's tests and syntax checks.
- [ ] Run secret/runtime-state scans for tracked files.
- [ ] Run `git status -sb` in every changed repo.
- [ ] Commit each repo with focused commit messages.
- [ ] Push all changed private repositories through `gh`/`git`.
- [ ] Run the manifest sync script once in dry-run/check mode where possible,
  then run sync if statuses are clean.

