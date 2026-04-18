# Backtest Runbook

## Scope

This runbook documents the minimum stable outputs for the Phase 0 / Phase 1
backtest stack:

- dataset + config loading
- one-step historical replay
- regime predictive-power experiments
- full-market baseline replay bundle output
- scorecard rendering
- fixture-backed research bundle output via CLI

## CLI fixture run

Run the narrow research CLI against the deterministic fixture config:

`python3 -m trading_system.app.backtest.cli run --config trading_system/tests/fixtures/backtest/minimal_config.json --output-dir /tmp/backtest-research`

Expected bundle path:

- `/tmp/backtest-research/regime_research__current_policy__no_rotation_suppression/manifest.json`
- `/tmp/backtest-research/regime_research__current_policy__no_rotation_suppression/summary.json`
- `/tmp/backtest-research/regime_research__current_policy__no_rotation_suppression/scorecard.json`

## Full-market baseline CLI run

Run the baseline CLI against a config that points at an imported dataset root
with `instrument_snapshot.json` in each bundle:

`python3 -m trading_system.app.backtest.cli run --config /path/to/full_market_baseline.json --output-dir /tmp/backtest-baseline`

Expected bundle path:

- `/tmp/backtest-baseline/full_market_baseline__<baseline_name>__<variant_name>/manifest.json`
- `/tmp/backtest-baseline/full_market_baseline__<baseline_name>__<variant_name>/summary.json`
- `/tmp/backtest-baseline/full_market_baseline__<baseline_name>__<variant_name>/breakdowns.json`
- `/tmp/backtest-baseline/full_market_baseline__<baseline_name>__<variant_name>/audit.json`

## Required dataset inputs

Each bundle under the dataset root must provide:

- `metadata.json`
- `market_context.json`
- `derivatives_snapshot.json`
- `account_snapshot.json`, or a dataset-level `baseline_account_snapshot.json`
- `instrument_snapshot.json` for `full_market_baseline`

The baseline replay reads `instrument_snapshot.json` for:

- `symbol`
- `market_type`
- `base_asset`
- `listing_timestamp`
- `quote_volume_usdt_24h`
- `liquidity_tier`
- `quantity_step`
- `price_tick`
- `has_complete_funding`

## Full-market baseline config contract

`full_market_baseline` requires these top-level fields:

- `dataset_root`
- `experiment_kind`
- `sample_windows`
- `baseline_name`
- `variant_name`
- `universe`
- `capital`
- `costs`

The current `universe` contract is:

- `listing_age_days`
- `min_quote_volume_usdt_24h`
- `require_complete_funding`

The current `capital` contract is:

- `model`
- `initial_equity`
- `risk_per_trade`
- `max_open_risk`

The current `costs` contract is:

- `fee_bps`
- `slippage_tiers`
- `funding_mode`

## Baseline workflow assumptions

The initial `full_market_baseline` path is intentionally narrow:

- the universe comes from imported bundle metadata, not a manually curated majors list
- spot and futures share one capital pool
- accepted, resized, and rejected portfolio decisions must stay auditable
- costs come from market fee tiers, liquidity-tier slippage, and futures funding

## Baseline output interpretation

Use the three baseline artifacts together:

- `summary.json` answers the portfolio-level question: total return, drawdown, turnover, trade count, and cost drag
- `breakdowns.json` explains where the result came from by market and by exit year
- `audit.json` shows whether the run was filtered or constrained heavily, starting with `rejection_count` and `rejection_reasons`

`manifest.json` is the contract file for automation. It records the bundle name,
dataset root, experiment kind, sample period, window counts, snapshot count,
and the artifact list the CLI wrote.

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

## Current baseline limitations

The current baseline is intentionally auditable before it is exhaustive. It does
not include:

- parameter search
- walk-forward optimization
- order-book simulation
- isolated per-symbol capital pools
- duplicate same-direction spot and futures exposure on one base asset

## Related docs

- `trading_system/docs/BACKTEST_DATA_SPEC.md`
- `trading_system/docs/HISTORICAL_DATA_ARCHITECTURE.md`
- `trading_system/docs/HISTORICAL_DATA_RUNBOOK.md`
- `trading_system/docs/HISTORICAL_DATA_RETENTION.md`
