# Constructure Repository Split Design

## Goal

Split the current `/home/taiwei/Constructure` runtime workspace into logical
GitHub repositories that are private by default, while keeping the running host
layout stable until services can be migrated deliberately.

## Current State

The workspace root is not a git repository. It contains several nested git
repositories plus runtime-only source directories such as `docs/`, `ops/`,
`infra/shared-infra`, `infra/vnc-manager`, `infra/embedding-gateway`,
`platform-upload/PlatformBrowserManager`, and `platform-upload/YouTubeManager`.

Large and sensitive local state is mixed into the workspace: `.env` files,
OAuth tokens, browser profiles, local databases, logs, media output,
`node_modules`, virtualenvs, and Rust `target` directories. New repositories
must exclude those files.

The current cron entry runs `/home/taiwei/Constructure/sync-repos.sh`, which
delegates to the old `k8s-Constructure` sync script. That script discovers git
repos by shallow directory search and currently misses many nested app and infra
repositories.

## Target Repositories

All new repositories are created as private unless explicitly changed later.

| Repository | Local source after split | Purpose |
| --- | --- | --- |
| `constructure-runtime` | `/home/taiwei/Constructure-repos/constructure-runtime` | Runtime docs, ops scripts, shared infra, host-native service managers, sync manifest, cron installer |
| `constructure-platform-upload` | `/home/taiwei/Constructure-repos/constructure-platform-upload` | Shared upload/browser automation services: PlatformBrowserManager, YouTubeManager, and x-bot |
| `videoprocess` | `/home/taiwei/Constructure-repos/videoprocess` | VideoProcess application, API, frontend, workers, deployment notes |
| `arb` | `/home/taiwei/Constructure-repos/arb` | Prediction-market arbitrage engine and Polymarket runtime glue |
| `constructure-news` | `/home/taiwei/Constructure-repos/constructure-news` | News collector/server and publisher/distribution code |
| `constructure-llm-infra` | `/home/taiwei/Constructure-repos/constructure-llm-infra` | Exo model metadata helpers and exo-watchdog |

Existing standalone repositories remain usable where their boundaries are
already sensible: `dashboard`, `job-autoflow`, `gmail-bridge`, `cmdsage`,
`dashcam`, and `rltrader`. Public Constructure-adjacent repositories should be
made private where GitHub permissions allow it.

External/upstream repositories such as `opennews-mcp`,
`infra/polymarket/polymarket-cli`, and `clash2sing-box` should not be pushed
back to upstream remotes by automation. They can be mirrored or forked later if
they need local changes preserved.

## Overlap Strategy

Runtime documentation should be duplicated into app repositories as a snapshot,
not symlinked. Each app repository may keep:

- `docs/constructure/infra-services.md`
- `docs/constructure/app-services.md`
- `docs/constructure/runtime-compose-schedule.md`
- `docs/constructure/services/<service>.md`
- `docs/constructure/SOURCE`

`constructure-runtime` remains the source of truth. The `SOURCE` file records
the runtime commit used for the copied docs.

## Sync Strategy

Replace shallow git discovery with a manifest-driven sync script in
`constructure-runtime`. The manifest lists local path, GitHub repository,
visibility, ownership, and whether automatic commit/push is allowed.

The sync script must:

- refuse to commit known sensitive or runtime-state files
- skip external/upstream repositories
- commit only syncable changes
- skip push when remote is ahead or branches diverged
- use `gh` to create missing repositories as private
- push with normal `git` after authentication is confirmed

The host cron entry should call the new runtime sync script. The old
`/home/taiwei/Constructure/sync-repos.sh` may remain as a compatibility wrapper,
but it should delegate to the new manifest-driven script.

## Safety Rules

- Do not delete old working directories during the first split.
- Do not move live runtime data during the first split.
- Do not add `.env`, `.env.*`, token files, OAuth credentials, browser profiles,
  local DB files, logs, media output, dependency directories, or build output.
- Prefer private GitHub repositories for everything in the Constructure runtime
  family.
- Keep external/upstream repositories out of automated push unless they have a
  `Ctwqk/*` mirror.

