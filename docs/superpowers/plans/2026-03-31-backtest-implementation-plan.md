# Trading System Backtest Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the minimum complete backtest research stack for the current trading system so Claw can evaluate `regime -> suppression -> candidate -> allocation -> execution` with reproducible metrics, ablations, and promotion gates.

**Architecture:** Add a dedicated backtest layer alongside the current runtime stack instead of overloading `app/main.py`. Reuse existing `market_regime`, `signals`, `portfolio`, `risk`, and `reporting` modules as pure decision engines, then wrap them with historical dataset loaders, experiment runners, metrics, and research reports. Deliver in slices: first establish reproducible inputs/metrics, then regime research, then suppression/engine ablations, then allocator/friction studies, then walk-forward robustness.

**Tech Stack:** Python 3, existing `trading_system/app/*` modules, `pytest`, JSON fixtures/runtime snapshots, `uv`, markdown docs.

---

## File structure and responsibilities

### New files

- `trading_system/app/backtest/__init__.py`
  - Backtest package entry.
- `trading_system/app/backtest/types.py`
  - Shared typed payloads for experiment config, dataset rows, event rows, summary metrics, and attribution rows.
- `trading_system/app/backtest/config.py`
  - Parse and validate backtest config files.
- `trading_system/app/backtest/dataset.py`
  - Historical dataset discovery, snapshot loading, timestamp ordering, split helpers.
- `trading_system/app/backtest/metrics.py`
  - Portfolio metrics, trade metrics, drawdown, turnover, cost drag, bucket attribution.
- `trading_system/app/backtest/reporting.py`
  - Convert experiment outputs into machine-readable summaries and markdown-friendly tables/lists.
- `trading_system/app/backtest/engine.py`
  - Shared runner that executes one historical step through existing strategy modules and records per-layer outputs.
- `trading_system/app/backtest/experiments.py`
  - High-level experiments: regime predictive power, suppression comparisons, engine/filter ablations, allocator comparisons.
- `trading_system/app/backtest/walk_forward.py`
  - Rolling split and out-of-sample evaluation helpers.
- `trading_system/app/backtest/cli.py`
  - CLI entrypoint for launching experiments from config.
- `trading_system/tests/test_backtest_dataset.py`
  - Dataset ordering, split logic, and snapshot loading tests.
- `trading_system/tests/test_backtest_metrics.py`
  - Deterministic metric calculations.
- `trading_system/tests/test_backtest_engine.py`
  - One-step replay through existing strategy stack with layer outputs recorded.
- `trading_system/tests/test_backtest_regime_experiments.py`
  - Regime predictive-power experiment coverage.
- `trading_system/tests/test_backtest_ablation_experiments.py`
  - Suppression and filter ablation coverage.
- `trading_system/tests/fixtures/backtest/README.md`
  - Explain minimal historical dataset layout for tests.
- `trading_system/tests/fixtures/backtest/sample_dataset/...`
  - Small reproducible historical snapshots for backtest tests.
- `trading_system/docs/BACKTEST_DATA_SPEC.md`
  - Canonical historical input format and naming rules.
- `trading_system/docs/BACKTEST_RUNBOOK.md`
  - How to launch experiments, inspect outputs, and interpret scorecards.
- `trading_system/docs/BACKTEST_PROMOTION_GATE.md`
  - Required evidence before any rule/parameter promotion.

### Existing files to modify

- `trading_system/README.md`
  - Add backtest entrypoint and document new docs.
- `trading_system/docs/BACKTEST_ROADMAP.md`
  - Link to implementation plan and concrete execution order.
- `trading_system/app/main.py`
  - Only if needed to extract reusable pure-step helpers; avoid mixing runtime CLI and backtest CLI.
- `trading_system/app/reporting/regime_report.py`
  - Reuse or expose summary helpers for backtest reports if duplication appears.
- `trading_system/app/storage/state_store.py`
  - Only if a reusable serialization helper is needed for research outputs.

---

## Chunk 1: Phase 0 foundation — dataset, config, metrics, runner skeleton

### Task 1: Add backtest package skeleton and shared types

**Files:**
- Create: `trading_system/app/backtest/__init__.py`
- Create: `trading_system/app/backtest/types.py`
- Test: `trading_system/tests/test_backtest_dataset.py`

- [ ] **Step 1: Write the failing test**

Add a test that imports shared backtest types and asserts a minimal experiment config / dataset row can be instantiated.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_dataset.py -q -p no:cacheprovider`
Expected: FAIL because `trading_system.app.backtest` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create `__init__.py` and `types.py` with dataclasses or TypedDicts for:
- experiment metadata
- dataset snapshot row
- forward-return window definition
- trade summary row
- portfolio scorecard row
- attribution row

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/__init__.py trading_system/app/backtest/types.py trading_system/tests/test_backtest_dataset.py
git commit -m "feat: add backtest shared types"
```

### Task 2: Define canonical backtest config parser

**Files:**
- Create: `trading_system/app/backtest/config.py`
- Create: `trading_system/tests/fixtures/backtest/minimal_config.json`
- Test: `trading_system/tests/test_backtest_dataset.py`

- [ ] **Step 1: Write the failing test**

Add a test that loads a minimal JSON config and asserts required fields are normalized:
- dataset root
- experiment kind
- sample windows
- fee/slippage assumptions
- baseline and variant names

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_dataset.py::test_load_backtest_config -q -p no:cacheprovider`
Expected: FAIL because loader function is missing.

- [ ] **Step 3: Write minimal implementation**

Implement config loader and validation with exact error messages for missing required fields.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/config.py trading_system/tests/fixtures/backtest/minimal_config.json trading_system/tests/test_backtest_dataset.py
git commit -m "feat: add backtest config loader"
```

### Task 3: Define historical dataset spec and loader

**Files:**
- Create: `trading_system/app/backtest/dataset.py`
- Create: `trading_system/tests/fixtures/backtest/README.md`
- Create: `trading_system/tests/fixtures/backtest/sample_dataset/`
- Create: `trading_system/docs/BACKTEST_DATA_SPEC.md`
- Test: `trading_system/tests/test_backtest_dataset.py`

- [ ] **Step 1: Write the failing test**

Add tests that assert:
- snapshots are loaded in timestamp order
- missing required snapshot components fail loudly
- dataset split helpers return deterministic in-sample / out-of-sample windows

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_dataset.py -q -p no:cacheprovider`
Expected: FAIL because dataset loader and fixtures are incomplete.

- [ ] **Step 3: Write minimal implementation**

Implement dataset loader for historical bundles containing:
- market context snapshot
- derivatives snapshot
- optional account snapshot or baseline account context
- timestamp / run id metadata

Document exact required layout in `BACKTEST_DATA_SPEC.md`.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/dataset.py trading_system/tests/fixtures/backtest trading_system/docs/BACKTEST_DATA_SPEC.md trading_system/tests/test_backtest_dataset.py
git commit -m "feat: add historical backtest dataset loader"
```

### Task 4: Add deterministic metric module

**Files:**
- Create: `trading_system/app/backtest/metrics.py`
- Test: `trading_system/tests/test_backtest_metrics.py`

- [ ] **Step 1: Write the failing test**

Add tests for deterministic calculations of:
- total return
- max drawdown
- Sharpe / Sortino / Calmar
- win rate
- payoff ratio
- expectancy
- turnover
- cost drag

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_metrics.py -q -p no:cacheprovider`
Expected: FAIL because metrics module does not exist.

- [ ] **Step 3: Write minimal implementation**

Implement pure metric helpers that operate on fixed inputs without reading files.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/metrics.py trading_system/tests/test_backtest_metrics.py
git commit -m "feat: add backtest metrics module"
```

### Task 5: Add one-step historical replay engine

**Files:**
- Create: `trading_system/app/backtest/engine.py`
- Modify: `trading_system/app/main.py` (only if extracting reusable pure helper is necessary)
- Test: `trading_system/tests/test_backtest_engine.py`

- [ ] **Step 1: Write the failing test**

Add a test that replays one historical bundle through:
- regime
- universe
- candidate generation
- validation
- allocation

and asserts the engine records per-layer artifacts instead of only final portfolio output.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_engine.py -q -p no:cacheprovider`
Expected: FAIL because replay engine is missing.

- [ ] **Step 3: Write minimal implementation**

Implement one-step replay wrapper that reuses existing pure modules and records:
- regime snapshot
- suppression decisions
- universes
- raw/validated candidates
- allocations
- execution assumptions used

Avoid mutating live runtime files.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/engine.py trading_system/app/main.py trading_system/tests/test_backtest_engine.py
git commit -m "feat: add one-step backtest replay engine"
```

---

## Chunk 2: Phase 1 regime research implementation

### Task 6: Implement regime predictive-power experiment runner

**Files:**
- Create: `trading_system/app/backtest/experiments.py`
- Test: `trading_system/tests/test_backtest_regime_experiments.py`

- [ ] **Step 1: Write the failing test**

Add tests that run a small synthetic historical dataset and assert regime experiments emit:
- forward return by regime
- forward drawdown by regime
- regime duration statistics
- confidence/aggression bucket summary

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_regime_experiments.py -q -p no:cacheprovider`
Expected: FAIL because regime experiment functions are missing.

- [ ] **Step 3: Write minimal implementation**

Implement regime experiments:
- label predictive power
- confidence/aggression monotonicity
- regime stability

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/experiments.py trading_system/tests/test_backtest_regime_experiments.py
git commit -m "feat: add regime predictive-power experiments"
```

### Task 7: Add regime research reporting and scorecards

**Files:**
- Create: `trading_system/app/backtest/reporting.py`
- Test: `trading_system/tests/test_backtest_regime_experiments.py`
- Modify: `trading_system/docs/BACKTEST_RUNBOOK.md`

- [ ] **Step 1: Write the failing test**

Add tests asserting experiment outputs can be rendered into a stable scorecard with:
- metadata
- key metrics
- decision summary
- promotion gate status

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_regime_experiments.py::test_regime_scorecard_rendering -q -p no:cacheprovider`
Expected: FAIL because reporting helpers are missing.

- [ ] **Step 3: Write minimal implementation**

Implement backtest reporting helpers and document the output format in the runbook.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/reporting.py trading_system/docs/BACKTEST_RUNBOOK.md trading_system/tests/test_backtest_regime_experiments.py
git commit -m "feat: add regime backtest scorecard reporting"
```

---

## Chunk 3: Phase 2 suppression and Phase 3 engine/filter ablations

### Task 8: Implement suppression policy comparisons

**Files:**
- Modify: `trading_system/app/backtest/experiments.py`
- Test: `trading_system/tests/test_backtest_ablation_experiments.py`

- [ ] **Step 1: Write the failing test**

Add tests comparing:
- current suppression
- no suppression
- soft suppression

and assert outputs include:
- opportunity kill rate
- avoid-loss rate
- bucket-level PnL
- rotation-specific comparison rows

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_ablation_experiments.py::test_suppression_policy_comparison -q -p no:cacheprovider`
Expected: FAIL because suppression comparison is missing.

- [ ] **Step 3: Write minimal implementation**

Implement suppression A/B support without editing the live classifier defaults directly; use experiment variants/config toggles.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/experiments.py trading_system/tests/test_backtest_ablation_experiments.py
git commit -m "feat: add suppression policy backtests"
```

### Task 9: Implement engine standalone and filter-ablation experiments

**Files:**
- Modify: `trading_system/app/backtest/experiments.py`
- Test: `trading_system/tests/test_backtest_ablation_experiments.py`

- [ ] **Step 1: Write the failing test**

Add tests covering:
- trend-only
- rotation-only
- short-only
- removal of a single filter (for example `rotation` score floor or overheat filter)

and assert funnel counts are emitted by layer.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_ablation_experiments.py::test_engine_ablation_outputs_funnel_metrics -q -p no:cacheprovider`
Expected: FAIL because engine/filter ablations are missing.

- [ ] **Step 3: Write minimal implementation**

Implement standalone engine experiments and per-filter ablations with deterministic funnel reporting.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/experiments.py trading_system/tests/test_backtest_ablation_experiments.py
git commit -m "feat: add engine and filter ablation backtests"
```

### Task 10: Add promotion-gate document for research decisions

**Files:**
- Create: `trading_system/docs/BACKTEST_PROMOTION_GATE.md`
- Modify: `trading_system/docs/BACKTEST_ROADMAP.md`
- Test: none (docs-only)

- [ ] **Step 1: Draft the doc**

Document required evidence before promoting any new rule:
- A/B or ablation proof
- out-of-sample check
- costs included
- rollback criteria
- runtime observability requirement

- [ ] **Step 2: Link the roadmap**

Update `BACKTEST_ROADMAP.md` to reference the promotion-gate doc.

- [ ] **Step 3: Review the docs for consistency**

Ensure terms match the roadmap (`regime`, `suppression`, `candidate`, `allocation`, `execution`).

- [ ] **Step 4: Commit**

```bash
git add trading_system/docs/BACKTEST_PROMOTION_GATE.md trading_system/docs/BACKTEST_ROADMAP.md
git commit -m "docs: add backtest promotion gate"
```

---

## Chunk 4: Phase 4 allocator/friction and Phase 5 robustness

### Task 11: Implement allocator, sizing, and friction experiments

**Files:**
- Modify: `trading_system/app/backtest/experiments.py`
- Test: `trading_system/tests/test_backtest_ablation_experiments.py`

- [ ] **Step 1: Write the failing test**

Add tests comparing:
- current allocator
- equal weight baseline
- fixed-risk baseline
- low/base/stressed friction assumptions

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_ablation_experiments.py::test_allocator_and_friction_comparisons -q -p no:cacheprovider`
Expected: FAIL because allocator/friction experiments are missing.

- [ ] **Step 3: Write minimal implementation**

Implement allocator and friction comparison helpers and ensure cost drag attribution is reported.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/experiments.py trading_system/tests/test_backtest_ablation_experiments.py
git commit -m "feat: add allocator and friction backtests"
```

### Task 12: Implement walk-forward and parameter-stability evaluation

**Files:**
- Create: `trading_system/app/backtest/walk_forward.py`
- Modify: `trading_system/app/backtest/experiments.py`
- Test: `trading_system/tests/test_backtest_ablation_experiments.py`

- [ ] **Step 1: Write the failing test**

Add tests asserting rolling windows:
- never overlap incorrectly
- preserve ordering
- separate in-sample and out-of-sample slices
- emit summary metrics for each window

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_ablation_experiments.py::test_walk_forward_splits_and_outputs -q -p no:cacheprovider`
Expected: FAIL because walk-forward helpers are missing.

- [ ] **Step 3: Write minimal implementation**

Implement walk-forward split helpers and robustness summaries.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/walk_forward.py trading_system/app/backtest/experiments.py trading_system/tests/test_backtest_ablation_experiments.py
git commit -m "feat: add walk-forward backtest validation"
```

---

## Chunk 5: CLI, runbook, and integration handoff

### Task 13: Add backtest CLI entrypoint

**Files:**
- Create: `trading_system/app/backtest/cli.py`
- Modify: `trading_system/README.md`
- Modify: `trading_system/docs/BACKTEST_RUNBOOK.md`
- Test: `trading_system/tests/test_backtest_engine.py`

- [ ] **Step 1: Write the failing test**

Add a test that invokes the CLI with a fixture config and asserts:
- it runs without touching live runtime state
- it writes summary outputs to a research directory
- it returns non-zero for invalid configs

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_engine.py::test_backtest_cli_runs_fixture_experiment -q -p no:cacheprovider`
Expected: FAIL because CLI entrypoint is missing.

- [ ] **Step 3: Write minimal implementation**

Implement CLI with commands such as:
- run one experiment config
- render scorecard
- list result bundles

Keep it narrow; do not add optimization sweeps yet.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/cli.py trading_system/README.md trading_system/docs/BACKTEST_RUNBOOK.md trading_system/tests/test_backtest_engine.py
git commit -m "feat: add backtest CLI"
```

### Task 14: Add full verification pass

**Files:**
- Modify: docs only if commands changed
- Test: existing and new backtest test files

- [ ] **Step 1: Run focused backtest test suite**

Run:
`uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_backtest_dataset.py trading_system/tests/test_backtest_metrics.py trading_system/tests/test_backtest_engine.py trading_system/tests/test_backtest_regime_experiments.py trading_system/tests/test_backtest_ablation_experiments.py`
Expected: PASS.

- [ ] **Step 2: Run broader regression slice**

Run:
`uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_market_regime.py trading_system/tests/test_rotation_engine.py trading_system/tests/test_allocator.py trading_system/tests/test_main_v2_cycle.py trading_system/tests/test_reporting.py`
Expected: PASS.

- [ ] **Step 3: Smoke-test the CLI on fixture data**

Run a documented fixture command from `BACKTEST_RUNBOOK.md`.
Expected: summary output written to the expected result directory.

- [ ] **Step 4: Commit**

```bash
git add trading_system/README.md trading_system/docs/BACKTEST_RUNBOOK.md trading_system/docs/BACKTEST_DATA_SPEC.md trading_system/docs/BACKTEST_PROMOTION_GATE.md
# plus any remaining code/test files
git commit -m "feat: land minimum backtest research stack"
```

---

## Recommended implementation order for Codex

1. Chunk 1 first — no shortcuts.
2. Chunk 2 next — prove regime value before suppression debates.
3. Chunk 3 next — especially `rotation suppression` and funnel attribution.
4. Chunk 4 after that — allocator/friction and robustness.
5. Chunk 5 last — CLI and runbook only after internals are stable.

## Guardrails for the implementing agent

- Do **not** bolt backtest logic directly into `app/main.py` unless a small extraction is clearly necessary.
- Keep live/runtime state writes out of the backtest path.
- Prefer pure functions and deterministic fixtures.
- Do not add optimization sweeps or parameter search in the first implementation package.
- Every experiment output must include metadata, scorecard metrics, and a plain-language decision summary.
- Any new rule-comparison path must be configurable; avoid hard-coding one-off toggles for only `rotation suppression`.

## First execution target

If only the first bounded package is to be executed now, ship:

- Chunk 1
- Chunk 2
- the `rotation suppression` part of Chunk 3

That gives you a usable first research stack without waiting for the entire roadmap.

---

Plan complete and saved to `docs/superpowers/plans/2026-03-31-backtest-implementation-plan.md`. Ready to execute?