# Paper/Live-Sim Evidence Phase 2 Ledger

Status: batch_1_integrated
Base: bbd3c0ab
Scope: simulated live / paper-live only. In this project context `live` means simulated live unless explicitly stated otherwise.

## Goal

Move from offline fail-closed contracts to operational evidence loops for simulated live/paper-live trading. The system should produce and consume daily/periodic evidence that validates execution assumptions, drift, reconciliation, and promotion readiness.

## Initial frontiers

- `paper-live-sim-evidence-bundle`: collect canonical simulated-live evidence bundle with signal/order/risk/ack/fill/reconcile, paper/shadow snapshots, freshness, lineage, and fail-closed schema checks.
- `tca-calibration-report`: compare backtest assumptions against simulated-live observations: slippage, fill probability, maker/taker, latency, partial fills, adverse selection, fees/funding, reject reasons.
- `daily-quality-gate-report`: produce machine-readable daily decision (`pass_for_continued_paper`, `hold_for_review`, `reject_live_promotion`) from evidence bundle + TCA + drift/reconcile/latency checks.

## Acceptance

- RED→GREEN tests for each frontier.
- Production code and consumer wiring, not fixture-only tests.
- No real-money or real-exchange side effects.
- Simulated live/paper-live operations are allowed.
- Worker exact audit and mainline exact audit for every integrated commit.
- Bounded full-suite checkpoint after each batch.


## Completed batch 1

- `tca-calibration-report` — `9588d590`, simulated-live TCA calibration report; main exact 4047 passed + diff-check.
- `daily-quality-gate-report` — `3bd39d1f`, machine-readable daily quality gate and testnet daily report surfacing; focused exact 8 passed + diff-check.
- `paper-live-sim-evidence-bundle` — `f6497387`, canonical paper/live-sim evidence bundle wired into live-readiness and promotion bundle; focused exact 966 passed + diff-check.

## Next frontiers

- Wire daily scheduled generation for simulated-live evidence bundle, TCA report, and daily quality gate from current runtime artifacts.
- Add longitudinal trend report over multiple simulated-live days.
- Add alert/hold workflow for quality-gate regressions.
