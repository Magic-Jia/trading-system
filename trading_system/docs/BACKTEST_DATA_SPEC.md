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

## Imported dataset assembly contract

这份 spec 约束的是 **最终交给 loader 的 imported dataset root**，因此 assembly 阶段必须保持最小、确定性、可读回：

- dataset root 一级只允许 bundle 目录和可选的 `baseline_account_snapshot.json`
- bundle 内只允许当前 loader contract 需要的 snapshot / metadata 文件
- provenance、handoff note、archive manifest、人工说明、备份目录应保留在 dataset root 外
- 如果目录仍保留 `<exchange>/<market>/<dataset>/<symbol>/<timeframe?>` 结构，它就还是 archive 层，不属于本 spec

这也意味着：当前 repo 的 Phase 1 operator 可以**手工装配 / 人工校对** dataset root，但不能把 future importer / downloader 说成当前仓库已经提供的现成功能

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

换句话说，Phase 1 当前可执行的 operator 路径是：

- 先在 raw-market archive 层确认 coverage 与 provenance
- 再按 loader contract 手工整理或人工校对 dataset root
- 最后把 dataset root 交给 `load_historical_dataset`

不要把这段话读成“仓库已经有自动 downloader / importer 会直接产出 dataset root”。

## Operator handoff

operator 在 Phase 1 应按这条链路理解数据流：

1. 先在 raw-market archive 层完成 backfill 或 incremental refresh
2. 再把需要研究的数据整理成当前 loader 认可的 dataset root
3. 最后才交给 `load_historical_dataset` 和 backtest CLI

如果某个目录仍保留 `<exchange>/<market>/<dataset>/<symbol>/<timeframe?>` 结构，它就还是 archive 层，不是本 spec 里的 dataset root。

交接时至少再复核这四件事：

- archive coverage 已经先被证明，而不是边缺数据边假装导入完成
- dataset root 与 `trading_system/data/archive/raw-market/...` 完全分离
- provenance / handoff 说明保留在 dataset root 之外
- 当前表述没有暗示仓库已经存在通用 importer / archive CLI

再补两条当前 loader 视角下的 assembly 复核：

- `metadata.json` 是否真的提供了 `timestamp` 与 `run_id`
- 当 bundle 缺少 `account_snapshot.json` 时，dataset root 是否真的存在 `baseline_account_snapshot.json`

## Related docs

- `trading_system/docs/HISTORICAL_DATA_ARCHITECTURE.md`
- `trading_system/docs/HISTORICAL_DATA_RUNBOOK.md`
- `trading_system/docs/HISTORICAL_DATA_RETENTION.md`
