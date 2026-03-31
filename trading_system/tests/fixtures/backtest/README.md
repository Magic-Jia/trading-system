# Backtest fixture dataset

This directory contains a minimal deterministic historical dataset used by the
Phase 0 / Phase 1 backtest tests.

Layout:

- `baseline_account_snapshot.json` — fallback account context for bundles that do
  not provide their own `account_snapshot.json`
- `<bundle>/metadata.json` — required bundle metadata with `timestamp` and `run_id`
- `<bundle>/market_context.json` — required market snapshot
- `<bundle>/derivatives_snapshot.json` — required derivatives snapshot
- `<bundle>/account_snapshot.json` — optional bundle-specific account snapshot
