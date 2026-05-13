# IBKR Scaled Current Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user set per-stock percentages from 0% to 300% and save the current group holdings multiplied by those percentages as a target profile.

**Architecture:** Add a focused backend route that resolves the group, reads current positions, applies per-conId scale percentages, and stores a shares-based target profile. Update the group composition UI to show one per-stock scale control and simplify target creation to button-driven flows without manual JSON.

**Tech Stack:** FastAPI, Pydantic, existing IBKR portfolio/store modules, static HTML/CSS/JavaScript dashboard, pytest, Playwright.

---

### Task 1: Backend Route

**Files:**
- Modify: `/home/taiwei/Constructure/apps/dashboard/src/ibkr/routes.py`
- Test: `/home/taiwei/Constructure/apps/dashboard/tests/test_ibkr_gateway_routes.py`

- [ ] Add a failing route test for `POST /api/ibkr/groups/{group_id}/targets/from-scaled-current` that verifies scale percentages are converted to `shares` target items.
- [ ] Implement `ScaledCurrentTargetReq` and the route by resolving the group, reading current positions, multiplying each position size by `scale_pct / 100`, storing a profile, and logging the action.
- [ ] Run the route test until it passes.

### Task 2: Group Composition UI

**Files:**
- Modify: `/home/taiwei/Constructure/apps/dashboard/static/index.html`
- Test: `/home/taiwei/Constructure/apps/dashboard/tests/test_dashboard_static_ibkr_groups.py`

- [ ] Add a failing static test proving the UI contains scaled-target controls, the save button, the new route, and no visible manual JSON fill controls.
- [ ] Add per-stock scale sliders and numeric inputs under each stock row. Each control is keyed by conId and defaults to 100%.
- [ ] Add `ibkrSaveScaledCurrentTarget()` to collect scale values and call the new backend route.
- [ ] Remove the main JSON textarea and the `FILL ...` buttons from the target profile panel.

### Task 3: Verification

**Files:**
- Verify: `/home/taiwei/Constructure/apps/dashboard`

- [ ] Run `PYTHONPATH=src python3 -m pytest tests -q`.
- [ ] Run JavaScript syntax check on the extracted dashboard script.
- [ ] Rebuild/restart the dashboard container if needed.
- [ ] Validate the Groups UI in a browser with Playwright: page loads, no relevant console errors, scale controls render, and the save button calls the expected route shape.
