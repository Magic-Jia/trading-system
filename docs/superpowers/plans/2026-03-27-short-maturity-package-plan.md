# Short Maturity Package Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the trading_system short side from a defensive placeholder into a mature downside-participation package with distinct short setups, squeeze-aware filtering, short-specific stop semantics, and clearer runtime/reporting behavior.

**Architecture:** Keep this as one package with three product chunks plus a package verification handoff. First mature short entry taxonomy and filters inside `short_engine`. Then thread short-specific stop / lifecycle semantics into runtime outputs without turning short into a separate execution-safety project. Finally improve runtime/reporting visibility so short behavior is reviewable and paper-safe, while keeping scope bounded and avoiding a new long detour.

**Tech Stack:** Python, pytest, trading_system runtime pipeline, `short_engine`, `stop_policy`, lifecycle/runtime reporting, defensive-regime / derivatives context.

---

## Package scope guardrails

This package is for **strategy maturity**, not for reopening execution-safety scope.

In scope:
- distinguish breakdown short vs failed-bounce short more clearly
- squeeze / crowded-short filtering
- short-specific stop / invalidation semantics
- runtime/reporting visibility for short setup behavior

Out of scope unless tests prove a tiny gap:
- live short execution enablement
- new exchange plumbing
- large allocator rewrites
- broad paper-trading architecture changes

---

## File structure impact

### Core strategy files
- Modify: `trading_system/app/signals/short_engine.py`
- Modify: `trading_system/app/risk/stop_policy.py`
- Modify: `trading_system/app/portfolio/lifecycle.py`
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/app/reporting/regime_report.py`

### Tests
- Modify: `trading_system/tests/test_short_engine.py`
- Modify: `trading_system/tests/test_stop_policy.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`
- Modify: `trading_system/tests/test_reporting.py`

### Docs / status
- Modify: `trading_system/README.md`
- Modify: `trading_system/docs/STRATEGY_GAPS_AND_UPGRADES.md`
- Modify: `memory/dev-status.md`

---

## Chunk 1: Short setup taxonomy + squeeze-aware entry quality

### Task 1: Make short setup distinctions real in `short_engine`

**Files:**
- Modify: `trading_system/app/signals/short_engine.py`
- Modify: `trading_system/tests/test_short_engine.py`

- [ ] **Step 1: Write or refine the failing short-engine tests**

Cover at least:
- a true `BREAKDOWN_SHORT` path where momentum is already weak and continuation deserves the breakdown label
- a true `FAILED_BOUNCE_SHORT` path where the bounce attempt fails and should not be grouped with breakdown continuation
- a weak/ambiguous downside path that should be rejected instead of being forced into either setup
- a crowded-short / squeeze-risk case that is rejected even if price structure looks weak

- [ ] **Step 2: Run the focused short-engine tests to verify the current gap**

Run:
`PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_short_engine.py -k 'short or squeeze or failed_bounce or breakdown'`

Expected:
- FAIL on at least one distinction/filter gap, or PASS and prove Chunk 1 is already done

- [ ] **Step 3: Implement the minimum short-engine changes**

Keep changes narrow:
- tighten setup classification logic for breakdown vs failed-bounce
- reject weak downside structures instead of over-labeling them
- preserve the current squeeze / crowded-short safety intent
- keep short confined to majors / defensive context unless tests require otherwise

- [ ] **Step 4: Re-run the focused short-engine tests**

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/signals/short_engine.py trading_system/tests/test_short_engine.py
git commit -m "feat: mature short setup classification"
```

---

## Chunk 2: Short-specific stop taxonomy + lifecycle semantics

### Task 2: Attach short-specific stop semantics instead of generic reclaim logic everywhere

**Files:**
- Modify: `trading_system/app/risk/stop_policy.py`
- Modify: `trading_system/tests/test_stop_policy.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write or refine the failing stop-policy tests**

Cover at least:
- `BREAKDOWN_SHORT` gets a continuation-style short invalidation / stop reference
- `FAILED_BOUNCE_SHORT` gets a different failure-style short invalidation / stop reference
- squeeze-sensitive short context either widens / changes stop family or is rejected before entry, whichever the current architecture supports more cleanly

- [ ] **Step 2: Run the focused short stop-policy tests**

Run:
`PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_stop_policy.py trading_system/tests/test_main_v2_cycle.py -k 'short and (stop or invalidation or protective)'`

Expected:
- FAIL on missing short-specific semantics, or PASS and prove Chunk 2 is already done

- [ ] **Step 3: Implement the minimum short stop-policy plumbing**

Only if tests expose a real gap:
- differentiate short invalidation strings / stop references by setup type
- keep behavior aligned with the existing shared taxonomy style already used by trend / rotation
- avoid reopening unrelated long-side logic

- [ ] **Step 4: Re-run the focused tests**

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/risk/stop_policy.py trading_system/tests/test_stop_policy.py trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: add short-specific stop taxonomy"
```

---

## Chunk 3: Runtime/reporting maturity for short behavior

### Task 3: Make short behavior reviewable in runtime output and reporting

**Files:**
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/app/portfolio/lifecycle.py`
- Modify: `trading_system/app/reporting/regime_report.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`
- Modify: `trading_system/tests/test_reporting.py`

- [ ] **Step 1: Write the failing runtime/reporting tests**

Prove at least:
- runtime output shows which short setup family was selected (`BREAKDOWN_SHORT` vs `FAILED_BOUNCE_SHORT`)
- runtime/reporting surfaces short invalidation / stop semantics clearly enough for review
- squeeze / crowded-short rejection or suppression is visible in a human-readable way instead of disappearing silently

- [ ] **Step 2: Run the focused runtime/reporting tests**

Run:
`PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_main_v2_cycle.py trading_system/tests/test_reporting.py -k 'short and (runtime or reporting or lifecycle or squeeze)'`

Expected:
- FAIL on missing visibility, or PASS and prove Chunk 3 is already done

- [ ] **Step 3: Implement the minimum runtime/reporting changes**

Keep changes explanation-focused:
- surface short setup family, stop family, invalidation semantics, and rejection reasons
- preserve the current execution boundary if short execution is still intentionally disabled
- do not turn this into a broad UI/output rewrite

- [ ] **Step 4: Re-run the focused runtime/reporting tests**

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/main.py trading_system/app/portfolio/lifecycle.py trading_system/app/reporting/regime_report.py trading_system/tests/test_main_v2_cycle.py trading_system/tests/test_reporting.py
git commit -m "feat: surface mature short behavior in runtime reporting"
```

---

## Chunk 4: Package verification + docs handoff

### Task 4: Verify short maturity package and update docs/status

**Files:**
- Modify: `trading_system/README.md`
- Modify: `trading_system/docs/STRATEGY_GAPS_AND_UPGRADES.md`
- Modify: `memory/dev-status.md`

- [ ] **Step 1: Run package verification**

Run:
`PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_short_engine.py trading_system/tests/test_stop_policy.py trading_system/tests/test_main_v2_cycle.py trading_system/tests/test_reporting.py`

Expected:
- PASS for the short-maturity package surface

- [ ] **Step 2: Update docs and status**

Record:
- what “short maturity” now means in the system
- what is still intentionally out of scope (for example live short execution plumbing)
- the latest verification command/result
- the next likely package recommendation after short maturity

- [ ] **Step 3: Commit docs/status**

```bash
git add trading_system/README.md trading_system/docs/STRATEGY_GAPS_AND_UPGRADES.md memory/dev-status.md
git commit -m "docs: update short maturity execution status"
```

---

## Review notes for the implementing agent

Before touching code, re-read these references:
- `trading_system/docs/STRATEGY_GAPS_AND_UPGRADES.md`
- `docs/superpowers/plans/2026-03-23-trading-system-p0-p1-p2-roadmap.md`
- `trading_system/app/signals/short_engine.py`
- `trading_system/tests/test_short_engine.py`

Keep the package bounded:
- If a task starts to require live short execution or new exchange plumbing, stop and surface that as a separate execution-safety track item.
- If the focused tests are already green, do not invent new architecture just to make the package look bigger.

---

Plan complete and saved to `docs/superpowers/plans/2026-03-27-short-maturity-package-plan.md`. Ready to execute?
