# Historical Data Architecture

## Scope

这份文档描述 **historical-data lane 已批准的 Phase 1 架构目标**，同时明确它与当前仓库里已存在的 backtest dataset / research CLI 现实边界。

要点只有四个：

1. raw-market 层采用 **Binance-first**
2. market scope 采用 **futures-first**
3. 抓取与补数采用 **coverage-driven**，按覆盖区间管理而不是按“每次抓多少行”管理
4. archive root、imported dataset root、research output 必须分层，不能混放

当前仓库已经有可运行的 backtest dataset loader / CLI；raw-market archive 与 runtime archive 还主要处于**已批准、待实现**阶段。因此本文件既说明目标数据模型，也说明当前代码已经稳定依赖的 dataset contract。

## Architecture at a glance

historical-data 采用双轨 + 导入层模型：

1. **Track A: raw-market archive**
   - 保存原始交易所历史
   - 目标是可追溯、可重算、可持续补数
2. **Track B: strategy runtime bundles**
   - 保存策略在单次运行周期里真正看到的输入
   - 目标是 exact-decision replay
3. **Imported backtest dataset roots**
   - 从 archive 层导入为当前 `load_historical_dataset` 可消费的 dataset root
4. **Research artifacts**
   - 由 `trading_system.app.backtest.cli` 输出的研究结果

最小逻辑链路：

`raw-market archive / runtime bundle archive -> imported dataset root -> load_backtest_config -> load_historical_dataset -> backtest CLI -> result bundle`

## Approved Phase 1 policy

### Raw-market policy

Phase 1 的原始市场数据层明确采用：

- **Binance-first**：第一实现只把 Binance historical APIs 作为 raw-market 主来源
- **futures-first**：期货是第一阶段 source-of-truth；spot 明确延后
- **coverage-driven**：抓取以目标覆盖窗口为准，持续使用交易所允许的最大分页直到填满 coverage window
- **append-first**：优先通过 manifest 记录 `coverage_start` / `coverage_end` / fetch metadata，而不是维护“latest-only”可变文件

这意味着 operator 管理历史数据时，先问“覆盖到哪里了”，而不是先问“抓了多少页、多少行”。

### Required Phase 1 raw datasets

Phase 1 需要优先支持的 raw-market datasets：

- OHLCV（首批 timeframe：`1h`、`4h`、`1d`）
- funding history
- open interest history

以下仅作为后续扩展候选，不属于第一阶段硬范围：

- basis / premium
- long-short ratio
- taker flow
- liquidation history

## Phase 1 operator path

operator 在 Phase 1 不应把 historical-data 理解成“随便抓一点市场数据”。正确路径是：

1. 先定义任务：`exchange=binance`、`market=futures`、dataset、symbol set、timeframe、目标 coverage window
2. 再判断操作类型：这是第一次补齐历史（backfill），还是沿现有 coverage 往前补（incremental refresh）
3. 只在 raw-market archive 层处理原始交易所历史，不要把它直接交给 loader
4. 需要研究/回放时，再把 archive 资料整理成 imported dataset root
5. 最后只用当前仓库已经存在的 loader / backtest CLI contract 做轻量验证

如果某一步做不到，应该停在该层排障，而不是跳层硬跑。

### Backfill vs incremental refresh

- **Backfill**：目标是首次补齐某个 coverage window，或者修复明显缺失的历史区间；完成标准是 `coverage_start` / `coverage_end` 达到目标窗口
- **Incremental refresh**：目标是在既有 coverage 边界上继续向前扩展；完成标准是 manifest 把新的 `coverage_end` 推进到最新目标点

Phase 1 的判断优先级很简单：

- 如果还没有 `binance/futures/...` canonical archive path，先做 backfill
- 如果路径已存在但 coverage window 不完整，仍按 backfill / gap-repair 处理
- 如果路径与 coverage 都已存在，只需要把末端往前补，则做 incremental refresh
- 不要把“这次抓了多少页”当作 backfill 或 refresh 的完成定义

## Canonical archive layout

### Raw-market archive root

批准方案里最明确的 archive contract 是 raw-market 路径：

- archive root：`trading_system/data/archive/raw-market`
- canonical path：`trading_system/data/archive/raw-market/<exchange>/<market>/<dataset>/<symbol>/<timeframe?>/`

Phase 1 的主路径应优先长这样：

```text
trading_system/data/archive/raw-market/
└── binance/
    └── futures/
        ├── klines/
        │   └── BTCUSDT/
        │       └── 1h/
        ├── funding-history/
        │   └── BTCUSDT/
        └── open-interest-history/
            └── BTCUSDT/
```

每个叶子目录都应有可审计的 fetch manifest，至少记录：

- source / endpoint
- exchange / market / dataset
- symbol set
- fetch timestamp
- `coverage_start`
- `coverage_end`

### Runtime bundle archive contract

runtime bundle 是“策略真实看到什么”的归档层，文件集合应至少包括：

- `metadata.json`
- `market_context.json`
- `derivatives_snapshot.json`
- `account_snapshot.json`
- `runtime_state.json`

重点不是把它伪装成 raw-market 数据，而是保留一次实际决策周期的完整上下文。

### Imported dataset root contract

导入层负责把 archive 数据转换成当前 backtest loader 认可的 dataset root。这个 contract 仍以当前实现为准：

- `baseline_account_snapshot.json`
- `<bundle>/metadata.json`
- `<bundle>/market_context.json`
- `<bundle>/derivatives_snapshot.json`
- `<bundle>/account_snapshot.json`

示意：

```text
sample_dataset/
├── baseline_account_snapshot.json
├── 2026-03-10T00-00-00Z/
│   ├── metadata.json
│   ├── market_context.json
│   ├── derivatives_snapshot.json
│   └── account_snapshot.json
└── 2026-03-11T00-00-00Z/
    ├── metadata.json
    ├── market_context.json
    └── derivatives_snapshot.json
```

**不要**把 raw-market archive 子目录、人工笔记目录、备份目录直接塞进 dataset root；当前 loader 会把一级子目录都当成 bundle 尝试读取。

### Imported dataset assembly boundary

Phase 1 里要特别明确：**imported dataset root 不是 raw-market archive 的镜像目录，也不是 downloader 的落盘目录**。

它只是一个被当前 `load_historical_dataset` 消费的、最小且确定性的 research input 目录。对 operator 来说，装配时应遵守这几条：

1. raw-market archive 继续保留 `<exchange>/<market>/<dataset>/<symbol>/<timeframe?>` 语义
2. imported dataset root 只保留 loader 当前认识的文件集合
3. provenance / handoff note 放在 dataset root 外部
4. 任何需要网络抓取、分页回补、自动映射 archive 的能力，都仍属于 future importer / downloader scope，不属于当前 repo 已实现能力

因此，当前 Phase 1 的“importer assembly”本质上是：

- 先从 archive / runtime 记录中确认研究窗口
- 再人工整理成 loader contract
- 最后交给已有 backtest CLI

而不是“运行一个现成 importer / downloader，然后自动得到 dataset root”。

### Phase 1 operator handoff into imported datasets

按已批准的 Phase 1 policy，raw-market archive 喂给 backtest 的链路应理解为：

1. 先在 `trading_system/data/archive/raw-market/binance/futures/...` 证明 coverage 已满足研究窗口
2. 再选出本次研究真正要消费的 archive path / symbol set / timeframe / coverage window
3. 然后把这些输入整理成当前 loader 可读的 dataset root
4. 最后才交给 `load_historical_dataset` 与 backtest CLI

这里要特别区分“批准的目标模型”和“当前仓库现实”：

- 批准的目标模型里会有通用 importer / archive CLI
- 当前仓库里还没有 `trading_system/app/backtest/archive/importer.py`
- 当前仓库里也还没有可以把 raw-market archive 自动转成 dataset root 的统一入口

因此，现阶段的 importer-facing operator 工作更接近**手工 handoff / 手工整理**：

- archive 层负责 coverage 与 provenance
- dataset root 层负责满足 loader contract
- handoff note 负责说明“哪个 archive window 生成了哪个 dataset root”

这个 handoff note 应保存在 operator 日志、研究记录或 ticket 中，而不是塞进 dataset root 内部破坏 loader 目录假设。

推荐 handoff note 至少写清：

- source archive path
- exchange / market / dataset / symbol set / timeframe
- `coverage_start` / `coverage_end`
- 目标 dataset root 路径
- bundle timestamp 列表或研究窗口
- 是否使用 `baseline_account_snapshot.json`
- 任何手工裁剪、聚合、补齐说明

只要这些信息还停留在 operator 脑子里，就说明 handoff 还没有真正完成。

## Repository reality

当前仓库里已经存在并能验证的实现：

- 配置解析：`trading_system/app/backtest/config.py`
- 数据加载：`trading_system/app/backtest/dataset.py`
- 研究 CLI：`trading_system/app/backtest/cli.py`
- 数据规范：`trading_system/docs/BACKTEST_DATA_SPEC.md`
- 运行说明：`trading_system/docs/BACKTEST_RUNBOOK.md`
- 样本测试：`trading_system/tests/test_backtest_dataset.py`
- CLI 输出测试：`trading_system/tests/test_backtest_engine.py`

当前仓库里**尚未落地**、但已在批准计划中定义的实现方向：

- `trading_system/app/backtest/archive/paths.py`
- `trading_system/app/backtest/archive/raw_market.py`
- `trading_system/app/backtest/archive/runtime_bundle.py`
- `trading_system/app/backtest/archive/importer.py`
- `trading_system/app/backtest/archive/cli.py`

因此，现阶段应把这份文档视为：

- 对 Phase 1 archive contract 的明确约束
- 对当前 backtest dataset reality 的兼容说明
- 后续 archive / importer / CLI 实现的文档基线

也就是说，**当前仓库现实并不是“archive 流程已经自动化”**。当前已落地的是 dataset loader / backtest CLI；raw-market archive operator path 目前仍以文档约束、目录边界、readback 检查为主。

## Loader and ordering constraints

当前 `trading_system.app.backtest.dataset.load_historical_dataset` 仍有这些硬约束：

- bundle 必须是 dataset root 下的一级目录
- `metadata.json` 必须提供 `timestamp` 和 `run_id`
- `derivatives_snapshot.json` 必须是数组，或是带 `rows` 数组的对象
- 缺少 bundle 级 `account_snapshot.json` 时，需要 dataset root 级 `baseline_account_snapshot.json`
- 读取顺序稳定按 `timestamp`、`run_id` 排序

这些约束决定了 archive importer 的职责：**先把 archive 资料整理成 loader 能消费的 deterministic dataset root，再交给研究 CLI**。

换句话说，当前 importer assembly 至少要满足：

- bundle 目录名可以是任意一级目录名，但 bundle 内 `metadata.json` 必须给出合法 `timestamp` / `run_id`
- `derivatives_snapshot.json` 必须直接是数组，或提供 `rows` 数组
- 如果 bundle 不带 `account_snapshot.json`，就必须准备 dataset root 级 baseline
- dataset root 一级目录中不要混入 archive 层级、handoff note、备份目录、临时下载目录

## Operator guidance

实践中请把四类东西分清：

- raw-market archive：原始交易所真相层
- runtime bundles：策略实际输入层
- imported datasets：研究输入层
- research outputs：研究结果层

只要这四层被混放，后续就会同时破坏：

- provenance
- reproducibility
- retention
- troubleshooting

## Related docs

- 运行手册：`trading_system/docs/HISTORICAL_DATA_RUNBOOK.md`
- 保留策略：`trading_system/docs/HISTORICAL_DATA_RETENTION.md`
- 当前 dataset 规范：`trading_system/docs/BACKTEST_DATA_SPEC.md`
- 当前 research runbook：`trading_system/docs/BACKTEST_RUNBOOK.md`
- 已批准计划：`docs/superpowers/plans/2026-03-31-historical-data-and-backtest-dataset-plan.md`
