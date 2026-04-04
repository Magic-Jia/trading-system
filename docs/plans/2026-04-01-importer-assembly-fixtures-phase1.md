# Importer Assembly Fixtures Phase-1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend importer assembly fixtures/tests with the smallest next phase-1 importer-to-dataset expectations.

**Architecture:** Build on the existing raw-market importer fixture slice by pinning the next assembly output shape at the test layer only. Keep scope strictly in fixtures/tests and avoid any downloader or broader production pipeline logic.

**Tech Stack:** Python, pytest, existing historical archive fixture helpers

---

### Task 1: Inspect the current importer assembly surface

**Files:**
- Modify: `memory/dev-status.md`
- Check: `trading_system/tests/`
- Check: `trading_system/`

**Step 1: Locate the existing fixture tests**

Run: `find trading_system/tests trading_system -type f | grep -E 'archive|importer|fixture|bundle|dataset'`
Expected: relevant importer/archive test files listed

**Step 2: Identify the next phase-1 expectation**

Run: `grep -RInE 'importer|dataset|assembly' trading_system/tests trading_system`
Expected: current raw-market slice and adjacent assembly APIs visible

### Task 2: Add the smallest failing expectation

**Files:**
- Modify: `trading_system/tests/test_backtest_raw_market_importer_fixture.py`
- Modify: fixture helper files only if the new expectation cannot be expressed with current helpers

**Step 1: Write the failing test**
Add one narrowly-scoped pytest asserting the next importer-to-dataset assembly behavior using local fixtures only.

**Step 2: Run the focused test**
Run: `UV_CACHE_DIR=/tmp/codex-uv-cache-historical uv run --with pytest python -m pytest -q <focused test target>`
Expected: FAIL only because the new expectation is not implemented/pinned yet

### Task 3: Add the minimal fixture/helper support

**Files:**
- Modify only the smallest fixture/helper files required by the failing test

**Step 1: Implement the minimal fixture/test support**
Keep the change at the test fixture boundary; do not introduce downloader logic.

**Step 2: Re-run focused verification**
Run: `UV_CACHE_DIR=/tmp/codex-uv-cache-historical uv run --with pytest python -m pytest -q <focused targets>`
Expected: PASS

### Task 4: Finalize the slice

**Files:**
- Modify: `memory/dev-status.md`

**Step 1: Update dev status**
Record last verified command/result, last commit, and next action.

**Step 2: Commit**
Run: `git add <scoped files> && git commit -m "test: extend importer assembly fixtures"`
Expected: either a clean scoped commit or a clean report that commit was intentionally left for main session
