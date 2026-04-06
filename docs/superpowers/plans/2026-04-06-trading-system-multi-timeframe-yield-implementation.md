# Trading System Multi-Timeframe Yield Uplift Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the multi-timeframe upgrade that keeps daily structure as the strategic brain while using closed-bar `1h` confirmation and `1h` exit management to improve risk-adjusted returns without accepting a material drop in total return.

**Architecture:** Keep the current daily `regime -> candidate -> allocator -> paper execution` spine intact, then add three bounded layers around it: a deterministic multi-timeframe loader with hard time semantics, an intraday confirmation gate that can reject low-quality entries without inventing new daily candidates, and an intraday exit manager that can `hold | reduce | exit` existing positions with explicit priority rules. Extend the backtest stack to replay `daily close -> closed 1h bars` event order, compare baseline vs variants, and enforce the new acceptance gates before any promotion.

**Tech Stack:** Python 3, existing `trading_system/app/*` runtime stack, existing `trading_system/app/backtest/*` research stack, `pytest`, JSON fixtures / historical datasets, `uv`, markdown docs.

---

## File structure and responsibilities

### New files

- `trading_system/app/data_sources/multi_timeframe_loader.py`
  - Load aligned `daily` + `1h` + optional `4h` market / derivatives slices using closed-bar-only semantics.
- `trading_system/app/signals/intraday_confirmation.py`
  - Apply the MVP intraday entry rules: overheat filter, crowding filter, entry-quality confirmation, missing-data rejection.
- `trading_system/app/portfolio/intraday_exit_manager.py`
  - Apply the MVP intraday exit rules: failure exit, crowding unwind exit, time exit, one-time `reduce` profit protection.
- `trading_system/tests/test_multi_timeframe_loader.py`
  - Verify time alignment, closed-bar semantics, and missing-data behavior.
- `trading_system/tests/test_intraday_confirmation.py`
  - Verify intraday confirmation acceptance / rejection and deterministic reasons.
- `trading_system/tests/test_intraday_exit_manager.py`
  - Verify `hold | reduce | exit` contract, single `reduce`, and rule priority.
- `trading_system/docs/MULTI_TIMEFRAME_RUNBOOK.md`
  - Explain data expectations, event order, experiment variants, and how to read outputs.

### Existing files to modify

- `trading_system/app/types.py`
  - Extend candidate / position / runtime payload types with multi-timeframe fields and reason codes.
- `trading_system/app/risk/regime_risk.py`
  - Produce daily leg-level budget / mode outputs (`rotation` off / limited / normal, `short` allowed / blocked).
- `trading_system/app/portfolio/allocator.py`
  - Respect leg budgets and stop cross-leg budget leakage.
- `trading_system/app/main.py`
  - Wire daily candidates through intraday confirmation and intraday exit management without reopening live execution scope.
- `trading_system/app/storage/state_store.py`
  - Persist confirmation reasons, timestamps, exit actions, and missing-data markers.
- `trading_system/app/reporting/regime_report.py`
  - Surface leg budgets, intraday rejection reasons, and exit actions in human-readable output.
- `trading_system/app/backtest/dataset.py`
  - Support loading aligned daily / intraday slices for strategy-layer replay.
- `trading_system/app/backtest/engine.py`
  - Replay `daily close -> next closed 1h bars` event order and enforce no look-ahead.
- `trading_system/app/backtest/experiments.py`
  - Add baseline / variant comparison helpers for confirmation, exits, rotation soft-gating, short conditioning, and leg budgets.
- `trading_system/app/backtest/metrics.py`
  - Add acceptance-gate helpers / thresholds for risk-adjusted return, drawdown, turnover, and leg contribution checks.
- `trading_system/app/backtest/reporting.py`
  - Emit scorecards that show baseline vs variant deltas and acceptance-gate pass / fail.
- `trading_system/README.md`
  - Link the new runbook / plan and summarize the multi-timeframe package.
- `trading_system/docs/BACKTEST_ROADMAP.md`
  - Reference this package as the next strategy-evaluation branch.
- `trading_system/docs/STRATEGY_GAPS_AND_UPGRADES.md`
  - Update strategy status once the package lands.
- `memory/dev-status.md`
  - Keep active execution status current during implementation.

---

## Chunk 1: Multi-timeframe strategy uplift package

### Task 1: Add closed-bar multi-timeframe data contracts and loader

**Files:**
- Create: `trading_system/app/data_sources/multi_timeframe_loader.py`
- Modify: `trading_system/app/types.py`
- Create: `trading_system/tests/test_multi_timeframe_loader.py`
- Modify: `trading_system/tests/fixtures/backtest/README.md`

- [ ] **Step 1: Write the failing loader tests**

Cover at least:
- daily candidates are only allowed to read same-day closed daily bars
- intraday confirmation only sees closed `1h` bars after `daily close`
- `4h` data is optional context and never required for a decision to exist
- missing `1h` data yields a deterministic `cannot_confirm_intraday` path instead of silent fallback

- [ ] **Step 2: Run the focused loader tests to verify the gap**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_multi_timeframe_loader.py -q -p no:cacheprovider`

Expected:
- FAIL because the loader and shared payloads do not exist yet.

- [ ] **Step 3: Write the minimum implementation**

Implement:
- a loader that accepts daily timestamp + symbol and returns aligned closed-bar `1h` / optional `4h` slices
- explicit timestamp fields for `candidate_created_at`, `intraday_bar_closed_at`, and timezone-safe event ordering
- deterministic missing-data result objects instead of ad-hoc `None` handling

Keep this unit pure and file-loading focused; do not add signal rules here.

- [ ] **Step 4: Re-run the loader tests**

Run the same command.

Expected:
- PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/data_sources/multi_timeframe_loader.py trading_system/app/types.py trading_system/tests/test_multi_timeframe_loader.py trading_system/tests/fixtures/backtest/README.md
git commit -m "feat: add multi-timeframe loader contracts"
```

### Task 2: Add daily leg-budget outputs and allocator budget plumbing

**Files:**
- Modify: `trading_system/app/risk/regime_risk.py`
- Modify: `trading_system/app/portfolio/allocator.py`
- Modify: `trading_system/tests/test_allocator.py`
- Modify: `trading_system/tests/test_market_regime.py`

- [ ] **Step 1: Write the failing regime / allocator tests**

Cover at least:
- `rotation` can be `off | limited | normal`
- `short` can be explicitly blocked outside defensive conditions
- `trend`, `rotation`, and `short` budgets are isolated from each other
- allocator cannot leak rejected `short` or capped `rotation` demand into `trend` sizing automatically

- [ ] **Step 2: Run the focused tests to verify the gap**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_market_regime.py trading_system/tests/test_allocator.py -q -p no:cacheprovider`

Expected:
- FAIL on missing leg-budget fields or incorrect allocation behavior.

- [ ] **Step 3: Write the minimum implementation**

Implement:
- daily leg-budget / mode outputs in `regime_risk.py`
- allocator support for leg-specific caps and mode-aware suppression
- no broad sizing rewrite; keep this to bounded leg-budget enforcement

- [ ] **Step 4: Re-run the focused tests**

Expected:
- PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/risk/regime_risk.py trading_system/app/portfolio/allocator.py trading_system/tests/test_market_regime.py trading_system/tests/test_allocator.py
git commit -m "feat: add leg-aware budget controls"
```

### Task 3: Implement the MVP intraday confirmation gate

**Files:**
- Create: `trading_system/app/signals/intraday_confirmation.py`
- Modify: `trading_system/app/types.py`
- Create: `trading_system/tests/test_intraday_confirmation.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write the failing confirmation tests**

Cover at least:
- overheat rejects an otherwise valid daily candidate
- crowding rejects an otherwise valid daily candidate
- a non-overheated, non-crowded candidate with acceptable location passes
- missing `1h` data returns `cannot_confirm_intraday`
- if multiple rejection reasons exist, the primary reason is deterministic (`overheat` before `crowding`)

- [ ] **Step 2: Run the focused confirmation tests to verify the gap**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_intraday_confirmation.py trading_system/tests/test_main_v2_cycle.py -q -p no:cacheprovider -k 'intraday or confirmation or overheat or crowding'`

Expected:
- FAIL because the confirmation gate is not implemented or not wired.

- [ ] **Step 3: Write the minimum implementation**

Implement the bounded MVP rule order only:
1. overheat filter
2. crowding filter
3. entry-quality confirmation
4. optional `4h` context veto

Do not add extra heuristics beyond the approved spec.

- [ ] **Step 4: Re-run the focused confirmation tests**

Expected:
- PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/signals/intraday_confirmation.py trading_system/app/types.py trading_system/tests/test_intraday_confirmation.py trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: add intraday confirmation gate"
```

### Task 4: Wire multi-timeframe confirmation into the main runtime and state outputs

**Files:**
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/app/storage/state_store.py`
- Modify: `trading_system/app/reporting/regime_report.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`
- Modify: `trading_system/tests/test_reporting.py`

- [ ] **Step 1: Write the failing runtime/reporting tests**

Cover at least:
- runtime state records which daily candidates were blocked by intraday confirmation and why
- runtime output shows leg budgets plus intraday rejection / approval reasons
- a candidate that cannot confirm intraday is visible as rejected, not silently dropped

- [ ] **Step 2: Run the focused runtime/reporting tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_main_v2_cycle.py trading_system/tests/test_reporting.py -q -p no:cacheprovider -k 'intraday or runtime or reporting or rejection'`

Expected:
- FAIL on missing fields or missing reporting output.

- [ ] **Step 3: Write the minimum implementation**

Implement:
- `main.py` wiring from daily candidates -> intraday confirmation -> allocator input
- state-store persistence for timestamps / reason codes / missing-data markers
- reporting summaries that stay concise but explicit

Do not reopen live execution or add UI-heavy reporting work.

- [ ] **Step 4: Re-run the focused runtime/reporting tests**

Expected:
- PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/main.py trading_system/app/storage/state_store.py trading_system/app/reporting/regime_report.py trading_system/tests/test_main_v2_cycle.py trading_system/tests/test_reporting.py
git commit -m "feat: surface intraday confirmation in runtime state"
```

### Task 5: Implement the MVP intraday exit manager

**Files:**
- Create: `trading_system/app/portfolio/intraday_exit_manager.py`
- Modify: `trading_system/app/types.py`
- Create: `trading_system/tests/test_intraday_exit_manager.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write the failing exit-manager tests**

Cover at least:
- failure exit has highest priority
- crowding unwind exit beats time exit and profit-protection reduce
- `reduce` trims exactly 50% notional and can only happen once per position lifecycle
- once `reduce` has fired, a later stronger signal becomes `exit`
- missing exit data does not invent a stronger protection state

- [ ] **Step 2: Run the focused exit-manager tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_intraday_exit_manager.py trading_system/tests/test_main_v2_cycle.py -q -p no:cacheprovider -k 'exit or reduce or intraday'`

Expected:
- FAIL because the exit manager does not exist yet.

- [ ] **Step 3: Write the minimum implementation**

Implement the approved rule order only:
1. failure exit
2. crowding unwind exit
3. time exit
4. one-time profit-protection `reduce`

Keep the output contract fixed to `hold | reduce | exit` and let existing execution / state plumbing consume those actions.

- [ ] **Step 4: Re-run the focused exit-manager tests**

Expected:
- PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/portfolio/intraday_exit_manager.py trading_system/app/types.py trading_system/tests/test_intraday_exit_manager.py trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: add intraday exit manager"
```

### Task 6: Wire intraday exits into runtime state and reporting

**Files:**
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/app/storage/state_store.py`
- Modify: `trading_system/app/reporting/regime_report.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`
- Modify: `trading_system/tests/test_reporting.py`
- Modify: `trading_system/tests/test_paper_executor.py`

- [ ] **Step 1: Write the failing runtime / executor tests**

Cover at least:
- runtime state stores `hold | reduce | exit` actions and reasons
- `reduce` is passed through as a management action without pretending to be a fresh entry
- reporting clearly distinguishes new-entry rejection from post-entry exit management

- [ ] **Step 2: Run the focused tests to verify the gap**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_main_v2_cycle.py trading_system/tests/test_reporting.py trading_system/tests/test_paper_executor.py -q -p no:cacheprovider -k 'reduce or exit or intraday or management'`

Expected:
- FAIL on missing state / executor handling.

- [ ] **Step 3: Write the minimum implementation**

Implement:
- runtime state persistence for intraday exit actions
- paper-executor plumbing for one-time `reduce` previews / events
- reporting output that makes exits reviewable without bloating logs

Do not broaden this into a general order-management rewrite.

- [ ] **Step 4: Re-run the focused tests**

Expected:
- PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/main.py trading_system/app/storage/state_store.py trading_system/app/reporting/regime_report.py trading_system/tests/test_main_v2_cycle.py trading_system/tests/test_reporting.py trading_system/tests/test_paper_executor.py
git commit -m "feat: wire intraday exits into runtime state"
```

### Task 7: Extend the backtest engine with daily-close -> intraday replay semantics

**Files:**
- Modify: `trading_system/app/backtest/dataset.py`
- Modify: `trading_system/app/backtest/engine.py`
- Modify: `trading_system/app/backtest/types.py`
- Modify: `trading_system/tests/test_backtest_dataset.py`
- Modify: `trading_system/tests/test_backtest_engine.py`

- [ ] **Step 1: Write the failing backtest replay tests**

Cover at least:
- daily candidates are generated at `daily close`
- intraday confirmation starts only on subsequent closed `1h` bars
- no component reads future `1h` data before the current event time
- `4h` remains optional context and cannot become a hidden execution requirement

- [ ] **Step 2: Run the focused backtest replay tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_dataset.py trading_system/tests/test_backtest_engine.py -q -p no:cacheprovider`

Expected:
- FAIL on missing event-order semantics.

- [ ] **Step 3: Write the minimum implementation**

Implement:
- aligned daily / intraday event iterators in `dataset.py`
- `engine.py` replay that runs `daily close -> intraday confirmation / exits -> next executable action`
- explicit assertions or guardrails that fail loudly on impossible timestamp orderings

- [ ] **Step 4: Re-run the focused backtest replay tests**

Expected:
- PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/dataset.py trading_system/app/backtest/engine.py trading_system/app/backtest/types.py trading_system/tests/test_backtest_dataset.py trading_system/tests/test_backtest_engine.py
git commit -m "feat: replay daily and intraday events in backtests"
```

### Task 8: Add experiment variants, acceptance gates, and strategy scorecards

**Files:**
- Modify: `trading_system/app/backtest/experiments.py`
- Modify: `trading_system/app/backtest/metrics.py`
- Modify: `trading_system/app/backtest/reporting.py`
- Modify: `trading_system/tests/test_backtest_ablation_experiments.py`
- Modify: `trading_system/tests/test_backtest_metrics.py`

- [ ] **Step 1: Write the failing experiment / scorecard tests**

Cover at least:
- baseline vs `intraday_confirmation` variant
- baseline vs `intraday_exit` variant
- baseline vs combined variant
- combined variant + `rotation` layered gating
- combined variant + conditional `short`
- combined variant + leg budgets
- acceptance-gate pass / fail output for return, drawdown, turnover, walk-forward, and `short` drag contribution

- [ ] **Step 2: Run the focused experiment / scorecard tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_ablation_experiments.py trading_system/tests/test_backtest_metrics.py -q -p no:cacheprovider`

Expected:
- FAIL because variants / gates are incomplete.

- [ ] **Step 3: Write the minimum implementation**

Implement:
- experiment helpers that compare the required variant ladder
- acceptance-gate helpers matching the approved spec thresholds
- reporting output that clearly shows which gate passed / failed and why

Do not add new optimization search or parameter-sweep tooling in this package.

- [ ] **Step 4: Re-run the focused experiment / scorecard tests**

Expected:
- PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/experiments.py trading_system/app/backtest/metrics.py trading_system/app/backtest/reporting.py trading_system/tests/test_backtest_ablation_experiments.py trading_system/tests/test_backtest_metrics.py
git commit -m "feat: add multi-timeframe strategy scorecards"
```

### Task 9: Update docs, run package verification, and close out the package

**Files:**
- Create: `trading_system/docs/MULTI_TIMEFRAME_RUNBOOK.md`
- Modify: `trading_system/README.md`
- Modify: `trading_system/docs/BACKTEST_ROADMAP.md`
- Modify: `trading_system/docs/STRATEGY_GAPS_AND_UPGRADES.md`
- Modify: `memory/dev-status.md`

- [ ] **Step 1: Run package verification**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_multi_timeframe_loader.py trading_system/tests/test_intraday_confirmation.py trading_system/tests/test_intraday_exit_manager.py trading_system/tests/test_market_regime.py trading_system/tests/test_allocator.py trading_system/tests/test_main_v2_cycle.py trading_system/tests/test_reporting.py trading_system/tests/test_paper_executor.py trading_system/tests/test_backtest_dataset.py trading_system/tests/test_backtest_engine.py trading_system/tests/test_backtest_ablation_experiments.py trading_system/tests/test_backtest_metrics.py -q -p no:cacheprovider`

Expected:
- PASS for the multi-timeframe package surface.

- [ ] **Step 2: Update docs and status**

Record:
- the final event-order semantics
- what `rotation` layered gating now means in practice
- what `conditional short` means in practice
- what remains intentionally out of scope
- the latest verification command and result

- [ ] **Step 3: Commit docs / status**

```bash
git add trading_system/docs/MULTI_TIMEFRAME_RUNBOOK.md trading_system/README.md trading_system/docs/BACKTEST_ROADMAP.md trading_system/docs/STRATEGY_GAPS_AND_UPGRADES.md memory/dev-status.md
git commit -m "docs: record multi-timeframe strategy package"
```

---

## Scope guardrails for the implementing worker

- Do not reopen live execution or exchange-plumbing work.
- Do not add minute-level or tick-level logic.
- Do not turn `4h` into a second full execution layer in the MVP.
- Do not add large parameter-search tooling.
- Do not rewrite the whole allocator when leg-budget controls are enough.
- If a task exposes a broader architecture problem, stop and write it down instead of expanding scope silently.

## Verification / promotion guardrails

The package is **not done** if only total return rises. Promotion requires the implemented scorecards to prove:

- risk-adjusted metrics pass the agreed thresholds
- total return does not violate the allowed drawdown in return
- drawdown / turnover behavior stays inside the acceptance gates
- `trend` remains the main positive contributor
- `short` no longer acts as a structural drag source
- the combined variant survives walk-forward better than the current baseline

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-06-trading-system-multi-timeframe-yield-implementation.md`. Ready to execute?
