# Paper/Live-Sim Evidence Phase 2 Ledger

Status: in_progress
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
