# Backtest Data Spec

## Dataset root

Historical datasets live under a single root directory.

- `baseline_account_snapshot.json` — optional fallback account context
- `<bundle>/metadata.json` — required, contains `timestamp` and `run_id`
- `<bundle>/market_context.json` — required market snapshot
- `<bundle>/derivatives_snapshot.json` — required derivatives snapshot
- `<bundle>/account_snapshot.json` — optional bundle override

## Bundle requirements

Each bundle must be self-describing and deterministic:

- timestamps use ISO-8601 UTC
- bundle files are immutable research inputs
- repeated loads must preserve timestamp ordering
- forward returns / drawdowns belong in `metadata.json`

## Loader behavior

`trading_system.app.backtest.dataset.load_historical_dataset`:

- sorts bundles by `timestamp`, then `run_id`
- fails loudly if any required snapshot file is missing
- falls back to `baseline_account_snapshot.json` when bundle account data is absent

## Related docs

- `trading_system/docs/HISTORICAL_DATA_ARCHITECTURE.md`
- `trading_system/docs/HISTORICAL_DATA_RUNBOOK.md`
- `trading_system/docs/HISTORICAL_DATA_RETENTION.md`
