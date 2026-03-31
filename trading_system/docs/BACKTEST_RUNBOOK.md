# Backtest Runbook

## Scope

This runbook documents the minimum stable outputs for the Phase 0 / Phase 1
backtest stack:

- dataset + config loading
- one-step historical replay
- regime predictive-power experiments
- scorecard rendering

## Scorecard shape

`trading_system.app.backtest.reporting.render_regime_scorecard` returns a stable
dictionary with four sections:

- `metadata`
- `key_metrics`
- `decision_summary`
- `promotion_gate`

## Promotion gate

The initial gate is intentionally conservative:

- observe at least two regimes in-sample
- show a positive strongest 3d regime return
- show a negative weakest 3d regime return

Failing the gate does not kill research; it means the rule stays in research mode
until more sample evidence exists.
