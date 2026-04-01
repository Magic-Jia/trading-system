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

## Phase 1 boundary

这份 spec 只描述 **imported dataset root**，不描述 raw-market archive 叶子目录。

换句话说：

- `load_historical_dataset` 应读取整理好的 dataset root
- 不应直接读取 `trading_system/data/archive/raw-market/...`
- raw-market archive 仍遵守 Binance-first / futures-first / coverage-driven 的独立 contract

当前仓库现实也要讲清楚：已经落地并可验证的是 dataset loader / backtest CLI；通用 archive importer 还在批准计划内，尚未成为当前 repo 的现成入口。

## Operator handoff

operator 在 Phase 1 应按这条链路理解数据流：

1. 先在 raw-market archive 层完成 backfill 或 incremental refresh
2. 再把需要研究的数据整理成当前 loader 认可的 dataset root
3. 最后才交给 `load_historical_dataset` 和 backtest CLI

如果某个目录仍保留 `<exchange>/<market>/<dataset>/<symbol>/<timeframe?>` 结构，它就还是 archive 层，不是本 spec 里的 dataset root。

## Related docs

- `trading_system/docs/HISTORICAL_DATA_ARCHITECTURE.md`
- `trading_system/docs/HISTORICAL_DATA_RUNBOOK.md`
- `trading_system/docs/HISTORICAL_DATA_RETENTION.md`
