# Constructure Repository Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create private-first logical repositories for Constructure and replace the shallow auto-push cron job with manifest-driven GitHub sync.

**Architecture:** Keep the current `/home/taiwei/Constructure` runtime workspace in place, and create clean source repositories under `/home/taiwei/Constructure-repos`. The new `constructure-runtime` repository owns docs, ops, shared infra, the sync manifest, and cron installation; app and service repositories consume copied runtime docs.

**Tech Stack:** Git, GitHub CLI (`gh`), Bash, Docker Compose, Python/Node/Rust project layouts.

---

### Task 1: Create Clean Repository Copies

**Files:**
- Create directories under `/home/taiwei/Constructure-repos`
- Copy source from `/home/taiwei/Constructure`

- [ ] Create `constructure-runtime`, `constructure-platform-upload`, `videoprocess`, `arb`, `constructure-news`, and `constructure-llm-infra`.
- [ ] Copy source files with explicit excludes for secrets, runtime state, dependencies, and build output.
- [ ] Add root `.gitignore` files to each new repository.

### Task 2: Add Runtime Sync Manifest And Script

**Files:**
- Create: `/home/taiwei/Constructure-repos/constructure-runtime/ops/github/repos.tsv`
- Create: `/home/taiwei/Constructure-repos/constructure-runtime/ops/github/sync-repos.sh`
- Modify: `/home/taiwei/Constructure-repos/constructure-runtime/ops/schedule/install-cron.sh`

- [ ] Write a manifest listing owned repositories and external repositories.
- [ ] Write a sync script that uses `gh` to create missing private repositories and `git` to commit/push syncable changes.
- [ ] Block known sensitive/runtime files before staging.
- [ ] Update the cron installer to point at the runtime sync script.

### Task 3: Test Sync Script Behavior

**Files:**
- Create: `/home/taiwei/Constructure-repos/constructure-runtime/tests/test_sync_repos.sh`

- [ ] Add shell tests for sensitive path detection, manifest parsing, and external repo skipping.
- [ ] Run the tests and shell syntax checks.

### Task 4: Initialize Git Repositories And Push

**Files:**
- All new repositories under `/home/taiwei/Constructure-repos`

- [ ] Initialize git repositories where needed.
- [ ] Create missing GitHub repositories as private.
- [ ] Commit and push each new repository.
- [ ] Set Constructure-adjacent existing public repositories private where allowed.

### Task 5: Update Host Cron

**Files:**
- Modify: `/home/taiwei/Constructure/sync-repos.sh`
- Modify active user crontab via `/home/taiwei/Constructure-repos/constructure-runtime/ops/schedule/install-cron.sh`

- [ ] Make the compatibility wrapper delegate to the new runtime sync script.
- [ ] Install the managed cron block.
- [ ] Verify `crontab -l` points to the new script path.

### Task 6: Verification

- [ ] Run `gh auth status`.
- [ ] Run `bash -n` on new shell scripts.
- [ ] Run runtime sync tests.
- [ ] Run secret/runtime path checks against all new repositories.
- [ ] Run `git status -sb` for each new repository.
- [ ] Confirm GitHub remotes and branch tracking.

