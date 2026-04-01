# Archive runtime fixture scaffold

This fixture set prepares the archive-runtime slice without implementing the
real archive writer yet.

Layout:

- `runtime/paper/research/` mirrors the current runtime bucket contract exposed
  by `trading_system.app.runtime_paths.build_runtime_paths`
- `archive_dataset/<bundle>/` mirrors the existing historical dataset contract
  consumed by `trading_system.app.backtest.dataset.load_historical_dataset`

The paired payloads stay intentionally in sync so future archive/export code can
reuse the same snapshot shapes without touching live runtime state.
