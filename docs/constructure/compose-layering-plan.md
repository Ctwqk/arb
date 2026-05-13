# Linux Compose Layering Implementation Plan

Goal: add Linux operations entrypoints and canonical directories that group
existing services into infra, platform-upload, and apps layers without changing
container names, ports, or schedules.

Architecture:

- `infra` owns shared support services: shared databases, Redis, MinIO, Qdrant,
  exo-watchdog, VNC/X11 manager, and Polymarket system service status.
- `platform-upload` owns platform-facing upload/browser automation services:
  YouTube manager, platform browser managers, and x-bot.
- `apps` owns application services that depend on infra and may use
  platform-upload features.
- Existing `host-core-*` scripts remain stable public entrypoints and delegate to
  the new layer scripts.

Tasks:

1. Add `ops/compose/infra-up.sh` and `ops/compose/infra-status.sh`.
2. Add `ops/compose/platform-upload-up.sh` and
   `ops/compose/platform-upload-status.sh`.
3. Add `ops/compose/apps-up.sh` and `ops/compose/apps-status.sh`.
4. Refactor `host-core-up.sh` and `host-core-status.sh` to call the new layers.
5. Update `schedule-status.sh` and runtime docs so the status view matches the
   new layer model.
6. Verify shell syntax, the layer entrypoint assertions, and non-mutating status
   commands.

Physical layout:

- `infra/`
- `platform-upload/`
- `apps/`

Old compatibility symlinks have been removed. Active compose files, scripts, and
installed systemd units should use the canonical directories directly.
