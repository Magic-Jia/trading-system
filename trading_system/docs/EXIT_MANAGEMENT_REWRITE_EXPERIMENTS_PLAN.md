# Exit Management Rewrite Experiments Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add opt-in, evidence-backed exit-management experiments for after-cost breakeven, MFE giveback cuts, and no-breakeven time stops, then validate them on corrected chunks and OOS/regime windows without promoting from the 30-trade survivor sample.

**Architecture:** Keep current production/backtest baseline unchanged by default. Add an experiment-only exit policy layer that consumes chronological trade-print path data and materializes separate comparison artifacts. Every rule must use executable trade-print timestamps/prices, never optimistic bar high/low assumptions.

**Tech Stack:** Python backtest engine under `trading_system/app/backtest`, pytest, JSON/Markdown artifacts, existing `exit_fill_*`, `exit_path_replay`, and live-readiness gate infrastructure.

---

## Source evidence

- Corrected gate: `/tmp/trading-system-execution-candidate-90d-20260105-0405-run/analysis/live_readiness_gate_90d_quarantine_exit_evidence_corrected.md`
- Corrected failure taxonomy: `/tmp/trading-system-execution-candidate-90d-20260105-0405-run/analysis/quarantine_30_trade_corrected_failure_taxonomy.md`
- Exit rewrite hypotheses: `/tmp/trading-system-execution-candidate-90d-20260105-0405-run/analysis/exit_management_rewrite_hypotheses_corrected_30.md`
- Corrected 30-trade exit policy comparison: `/tmp/trading-system-execution-candidate-90d-20260105-0405-run/analysis/exit_policy_experiment_corrected_30_comparison.md`

Current corrected survivor state:

- trades: 30
- net_pnl: -4046.26
- entry evidence coverage: 100%
- exit evidence coverage: 100%
- TP hits: 0
- SL hits: 0
- after-cost breakeven hits: 18
- after-cost breakeven hit then final loss: 8
- never reached after-cost breakeven: 12
- corrected 30-trade policy matrix: all six pre-declared policies underperformed the corrected fixed-horizon trade-print baseline; best was `mfe_giveback_cut` at `-4,815.68`, still `-769.43` worse than baseline.

Non-negotiable: these are hypotheses only. Do not promote any rule until it passes full corrected 90d chunks plus OOS/walk-forward/regime validation.

---

### Task 1: Add exit policy config schema

**Files:**
- Modify: `trading_system/app/backtest/types.py`
- Modify: `trading_system/app/backtest/config.py`
- Test: `trading_system/tests/test_backtest_dataset.py`

**Step 1: Write failing config tests**

Add tests that parse an opt-in `experiment_params.exit_policy` object:

```json
{
  "experiment_params": {
    "exit_policy": {
      "name": "after_cost_breakeven_stop",
      "after_cost_buffer_bps": 2.0,
      "activation_minute": 0
    }
  }
}
```

Also test invalid policy names and negative bps/minutes.

**Step 2: Run failing tests**

Run:

```bash
python -m pytest -q trading_system/tests/test_backtest_dataset.py::test_load_backtest_config_parses_exit_policy
```

Expected: fail because `exit_policy` is not implemented.

**Step 3: Implement minimal dataclass/config parser**

Add an immutable dataclass such as:

```python
@dataclass(frozen=True, slots=True)
class ExitPolicyParams:
    name: str
    after_cost_buffer_bps: float = 0.0
    activation_minute: int = 0
    giveback_fraction: float | None = None
    giveback_floor_bps: float | None = None
    no_breakeven_time_stop_minute: int | None = None
```

Add `exit_policy: ExitPolicyParams | None = None` to `ExperimentParams`.

**Step 4: Verify**

Run focused config tests and commit:

```bash
python -m pytest -q trading_system/tests/test_backtest_dataset.py
git add trading_system/app/backtest/types.py trading_system/app/backtest/config.py trading_system/tests/test_backtest_dataset.py
git commit -m "feat(backtest): add exit policy experiment config"
```

---

### Task 2: Build chronological trade-print exit policy simulator

**Files:**
- Create or modify: `trading_system/app/backtest/exit_policies.py`
- Test: `trading_system/tests/test_backtest_exit_policies.py`

**Step 1: Write failing tests for executable trigger selection**

Cover:

1. After-cost breakeven for long uses first trade print whose move covers costs plus buffer.
2. After-cost breakeven for short uses symmetric move.
3. Giveback cut triggers only after MFE activation and uses chronological prints.
4. No-breakeven time stop uses the first trade print at or after the time-stop timestamp.
5. If no eligible trade print exists, returns `not_triggered` rather than falling back to bar close.

**Step 2: Implement pure functions**

Implement pure policy evaluation functions that accept:

- side
- entry price
- qty/notional/cost pct
- entry timestamp
- fixed exit timestamp
- chronological trade prints
- policy params

Return:

```python
{
  "triggered": bool,
  "exit_price": float | None,
  "exit_timestamp": datetime | None,
  "exit_policy_reason": str,
  "exit_price_source": "trade_print" | "none",
  "fill_quality": "evidence_backed" | "no_evidence"
}
```

**Step 3: Verify**

Run:

```bash
python -m pytest -q trading_system/tests/test_backtest_exit_policies.py
```

Commit:

```bash
git add trading_system/app/backtest/exit_policies.py trading_system/tests/test_backtest_exit_policies.py
git commit -m "feat(backtest): simulate trade-print exit policies"
```

---

### Task 3: Add experiment artifact without changing baseline ledger

**Files:**
- Modify: `trading_system/app/backtest/cli.py`
- Modify or create: `trading_system/app/backtest/exit_policy_experiment.py`
- Test: `trading_system/tests/test_backtest_engine.py` or new focused CLI test

**Step 1: Write failing artifact test**

Given a small fixture with trade prints and `experiment_params.exit_policy`, the full-market bundle should include a separate artifact, for example:

```text
exit_policy_experiment.json
```

It must not mutate `trades.json` baseline PnL unless explicitly configured in a later task.

**Step 2: Implement artifact generation**

For each trade, evaluate policy against available chronological trade prints and output:

- baseline exit price/net PnL
- policy exit price/net PnL
- policy trigger reason/timestamp
- evidence quality
- delta vs baseline

**Step 3: Verify**

Run focused CLI smoke test and commit:

```bash
python -m pytest -q trading_system/tests/test_backtest_engine.py::test_backtest_cli_runs_full_market_baseline_smoke_fixture
git add trading_system/app/backtest/cli.py trading_system/app/backtest/exit_policy_experiment.py trading_system/tests/test_backtest_engine.py
git commit -m "feat(backtest): emit exit policy experiment artifacts"
```

---

### Task 4: Run corrected 30-trade diagnostic matrix

**Status:** completed as an in-sample diagnostic; results were negative and do not support promotion.

**Files/artifacts:**
- `/tmp/trading-system-execution-candidate-90d-20260105-0405-run/analysis/exit_policy_experiment_corrected_30_comparison.json`
- `/tmp/trading-system-execution-candidate-90d-20260105-0405-run/analysis/exit_policy_experiment_corrected_30_comparison.md`

**Completed evaluation:**

The run attached `787,118` full-path `aggTrades` rows to the corrected 30 trades with `0` missing trade-print paths. All pre-declared policies underperformed the corrected fixed-horizon trade-print baseline (`-4,046.26` net):

| policy | triggered | net PnL | delta vs baseline |
|---|---:|---:|---:|
| `after_cost_breakeven_stop` buffer 0 bps | 16 | `-7,080.82` | `-3,034.56` |
| `after_cost_breakeven_stop` buffer 5 bps | 16 | `-6,411.70` | `-2,365.45` |
| `mfe_giveback_cut` 50% / 25 bps | 11 | `-4,815.68` | `-769.43` |
| `no_breakeven_time_stop` 15m | 26 | `-8,574.75` | `-4,528.49` |
| `no_breakeven_time_stop` 30m | 18 | `-6,171.70` | `-2,125.45` |
| `no_breakeven_time_stop` 45m | 16 | `-5,037.76` | `-991.51` |

**Interpretation:** these simple exit-management rewrites did not rescue the survivor set. This strengthens the rejection: the problem is strategy/edge quality, not only the fixed 60m exit implementation.

**Step 3: Do not tune thresholds on this sample**

No grid search over arbitrary thresholds. If a threshold is not pre-declared above, put it in a future OOS plan.

---

### Task 5: Expand to full corrected 90d chunks and OOS/regime splits

**Files/artifacts:**
- Existing corrected chunks under `/tmp/trading-system-execution-candidate-90d-20260105-0405-run/results_chunks_10d_quarantine_exit_evidence_corrected`
- New analysis outputs under `/tmp/.../analysis/`

**Step 1: Run policy matrix over all corrected chunks**

Use the same fixed policies from Task 4.

**Step 2: Split by time/regime/setup**

At minimum report:

- first half vs second half
- active chunks vs empty chunks
- setup type
- symbol
- cost buckets
- time-to-best buckets

**Step 3: Gate interpretation**

A policy can only become a candidate if:

- exit evidence coverage stays >= 95%
- net PnL non-negative after current costs
- no major setup bucket remains negative with material sample
- OOS split does not collapse
- ambiguity rate remains below threshold

Commit code separately from generated `/tmp` analysis artifacts.

---

### Task 6: Update roadmap and gate docs

**Files:**
- Modify: `trading_system/docs/INSTITUTIONAL_BEST_PRACTICES_ROADMAP.md`

Add a short section under Exit and Strategy layers documenting:

- exit policy experiments are opt-in only;
- fixed 60m horizon is not promotion-ready;
- after-cost breakeven/giveback/time-stop are hypotheses requiring OOS;
- no live/testnet order behavior changes are allowed from this work.

Verify:

```bash
git diff --check HEAD
python -m pytest -q trading_system/tests/test_backtest_dataset.py trading_system/tests/test_backtest_exit_policies.py trading_system/tests/test_backtest_engine.py
```

Commit:

```bash
git add trading_system/docs/INSTITUTIONAL_BEST_PRACTICES_ROADMAP.md
git commit -m "docs: plan exit management experiments"
```
