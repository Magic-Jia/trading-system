# Historical Data Retention

## Purpose

这份文档定义 historical-data lane 的保留策略，重点不是“多久删一次文件”，而是先把不同数据层的保留责任分清楚：

- raw-market archive：交易所原始真相层
- runtime bundles：策略实际输入归档层
- imported datasets：研究输入层
- research artifacts：研究输出层

Phase 1 保留策略必须与批准的 raw-market policy 保持一致：**Binance-first、futures-first、coverage-driven、append-first**。

## Phase 1 guardrails

做 retention 判断前，先套这几条硬规则：

- raw-market 第一来源是 Binance
- 第一市场范围是 futures，不是 spot
- 同步与补数按 coverage window 管理，不按固定抓取行数管理
- manifest 应记录 `coverage_start` / `coverage_end`
- archive root 与 dataset root 不混放
- 历史归档优先 append-only，不原地改写

如果某次清理会破坏以上任一规则，就不应执行。

## Storage classes

### 1. Raw-market archive

这是长期可追溯层，职责是支持：

- 复盘历史覆盖
- 重新计算特征
- 证明研究输入来源

Phase 1 canonical root：

- `trading_system/data/archive/raw-market`
- canonical path：`trading_system/data/archive/raw-market/<exchange>/<market>/<dataset>/<symbol>/<timeframe?>/`

这层默认应长期保留，尤其是：

- `binance/futures/klines/...`
- `binance/futures/funding-history/...`
- `binance/futures/open-interest-history/...`

### 2. Runtime bundles

runtime bundles 保留策略略低于 raw-market 真相层，但高于普通研究输出。

原因：

- 它记录策略当时真正看到什么
- 它能解释“为什么这次决策会发生”
- 它是 future importer / replay 的关键输入

只要某次 bundle 对研究结论、问题复盘、策略行为解释仍有价值，就不应删除。

### 3. Imported dataset roots

imported datasets 是为当前 backtest loader 准备的研究输入层。

保留原则：

- 只要它仍被当前研究、文档、结果引用，就不要删
- 如果未来能稳定从 archive 重新导入，可以降低长期保留要求
- 在 importer 还没完全落地前，不要假设“随时都能重建”

### 4. Research artifacts

研究输出包括：

- `manifest.json`
- `summary.json`
- `scorecard.json`

这层可以按价值分级：

- smoke run：短期，可放 `/tmp`
- 需要复盘的研究：迁移到持久目录后再删 `/tmp`
- 用于基线比较/决策的研究：长期保留，并记录所用 dataset/config/archive 来源

## Naming and immutability rules

为减少 retention 混乱，采用以下约束：

- archive 路径体现 `exchange / market / dataset / symbol / timeframe`
- futures-first 阶段不要把 spot 数据塞进同一层级充数
- 以 coverage manifest 说明“覆盖到哪里”，不要把“latest-only”文件当唯一真相
- imported dataset bundle 继续保持时间点目录语义
- research output 重跑时优先写新目录，不原地覆盖旧结果

尤其注意：

- **不要**把 raw-market archive 直接改写成另一个 coverage window
- **不要**原地重写历史 runtime bundle
- **不要**在 dataset root 中混入 `archive/`、`notes/`、备份目录

## What should almost never be deleted

以下内容默认视为高价值：

- `trading_system/data/archive/raw-market/binance/futures/...` 下的正式 archive
- 唯一一份能证明某研究 coverage window 的 manifest
- 被结论、报告、问题单引用的 runtime bundle
- importer 尚未稳定前的正式 imported dataset root

## What can be deleted more aggressively

以下内容通常可以更积极清理：

- 明确标记为 smoke run 的 `/tmp/backtest-*` 结果
- 没被任何结论引用的临时研究产物
- 已确认重复且无差异的临时副本

但删除前仍要确认：

- 不是唯一一份
- 不影响 coverage 证明
- 不影响结果复现

## Manual retention workflow

建议按这个顺序做人工保留/清理：

1. 先判断对象属于 raw-market archive、runtime bundle、imported dataset 还是 research output
2. 确认它服务的是 futures-first Phase 1 还是后续扩展试验
3. 检查是否仍承担 coverage / provenance / replay / result 追溯责任
4. 如果要删，优先删完整临时目录，不要删成半残状态
5. 删除后仍要保证“明天重跑时能解释输入从哪来”

## `/tmp` boundary

`/tmp` 只适合：

- smoke validation
- 一次性临时结果
- 短时调试产物

`/tmp` 不应被视为：

- raw-market archive root
- 长期 imported dataset root
- 正式研究结果仓库

## Triggers for future automation

当出现以下需求时，说明 retention 应从“文档约束”升级为“实现任务”：

- raw-market archive 开始持续扩容
- 需要自动判断 coverage gap
- 需要自动 prune / archive
- 需要 checksum / schema version
- 需要跨人共享研究 archive

## Operator checklist

删任何 historical-data 相关目录前，先回答：

1. 它属于哪一层：archive、bundle、dataset，还是 result？
2. 它是否仍在支撑 Binance-first / futures-first Phase 1 的 coverage 证明？
3. 删掉后还能否证明 `coverage_start` / `coverage_end`？
4. 删掉后还能否重建研究输入或解释策略决策？
5. 这次删除是整批清理，还是会留下半残路径？

只要有一项答不上来，就先不要删。

## Related docs

- 架构：`trading_system/docs/HISTORICAL_DATA_ARCHITECTURE.md`
- 运行手册：`trading_system/docs/HISTORICAL_DATA_RUNBOOK.md`
- 当前 dataset 规范：`trading_system/docs/BACKTEST_DATA_SPEC.md`
- 已批准计划：`docs/superpowers/plans/2026-03-31-historical-data-and-backtest-dataset-plan.md`
