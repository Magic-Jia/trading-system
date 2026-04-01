# Historical Data Runbook

## Purpose

这份 runbook 说明 historical-data lane 的 operator 在 Phase 1 应该如何检查、验证、排障：

1. raw-market policy 有没有跑偏
2. archive path layout 有没有混乱
3. imported dataset root 是否仍兼容当前 backtest loader
4. 当前仓库里的轻量验证命令怎么跑

它同时服务两种现实：

- **已批准的 archive 目标模型**
- **当前已落地的 backtest dataset / CLI**

## Phase 1 policy summary

先记住四条硬规则：

- **Binance-first**：raw-market 第一来源是 Binance historical APIs
- **futures-first**：期货优先，spot 暂不纳入 Phase 1 主范围
- **coverage-driven**：按 coverage window 补齐，不按“固定每次抓多少行”定义数据合同
- **archive separation**：raw-market archive、runtime bundles、imported datasets、research outputs 不能混放

如果实际目录或操作习惯违背这四条，优先按“策略/流程跑偏”处理，而不是先猜测 backtest loader 出 bug。

## Canonical paths to inspect

本 lane 需要区分至少三类路径：

- raw-market archive root：`trading_system/data/archive/raw-market`
- imported dataset root：供 `load_historical_dataset` 读取的 dataset root
- research output dir：例如 `/tmp/backtest-research` 或持久研究目录

raw-market canonical path：

`trading_system/data/archive/raw-market/<exchange>/<market>/<dataset>/<symbol>/<timeframe?>/`

Phase 1 正常情况下，应优先看到：

- `<exchange>` = `binance`
- `<market>` = `futures`

## Step 1: confirm the requested scope

在检查任何目录前，先把目标窗口写清楚：

- exchange 是不是 `binance`
- market 是不是 `futures`
- dataset 是 `klines`、`funding-history` 还是 `open-interest-history`
- symbol set 是哪些交易对
- timeframe 是不是 `1h` / `4h` / `1d`
- 目标 coverage window 从哪里到哪里

如果这些问题答不上来，就不要再谈“补数完成”。

## Step 2: inspect the raw-market archive root

raw-market 根目录重点看三件事：

1. 路径有没有遵守 `<exchange>/<market>/<dataset>/<symbol>/<timeframe?>`
2. manifest 有没有写 coverage，而不是只写抓取页数/行数
3. 有没有把 spot、临时文件、人工笔记混进 futures archive

最小人工检查可以用：

```bash
find trading_system/data/archive/raw-market -maxdepth 6 -type d | sort
```

看到异常时优先排查：

- 路径里出现 `spot/`，但当前任务明明是 futures-first
- dataset 名没有清楚区分 `klines` / `funding-history` / `open-interest-history`
- 同一 symbol/timeframe 下只有“latest”文件，没有 coverage manifest
- archive root 下混入 `.bak`、`notes/`、临时导出目录

## Step 3: validate imported dataset roots

当前真正被 backtest loader 消费的，仍然是 imported dataset root，而不是 raw-market archive 本身。

最低 contract：

- `baseline_account_snapshot.json`（可选）
- `<bundle>/metadata.json`
- `<bundle>/market_context.json`
- `<bundle>/derivatives_snapshot.json`
- `<bundle>/account_snapshot.json`（可选，但若缺失则需 baseline）

重点检查：

- dataset root 下只放 bundle 目录和允许的 baseline 文件
- 不要把 `archive/`、`notes/`、人工说明目录塞进去
- `metadata.json` 里有 `timestamp` 与 `run_id`
- `derivatives_snapshot.json` 结构合法

## Step 4: smoke-test current repo reality

因为 archive CLI / importer 还在计划实现阶段，当前最可靠的轻量验证仍是：

1. 验证 dataset loader
2. 验证现有 backtest engine/output

推荐命令：

```bash
export UV_CACHE_DIR=/tmp/uv-cache-historical-archive-docs
uv run --with pytest python3 -m pytest -q -p no:cacheprovider \
  trading_system/tests/test_backtest_dataset.py \
  trading_system/tests/test_backtest_engine.py
```

这组测试通过，说明：

- 当前 loader contract 没被文档改坏
- 当前 backtest CLI 的最小输出契约仍成立

## Step 5: read back the docs boundary

跑完验证后，回读这三层边界：

- `HISTORICAL_DATA_ARCHITECTURE.md`：定义目标数据模型与路径边界
- `HISTORICAL_DATA_RETENTION.md`：定义 archive / dataset / result 的保留策略
- `BACKTEST_DATA_SPEC.md`：定义当前 importer 最终要落到什么 dataset contract

如果某条说明会让 operator 误以为“raw-market archive 可以直接拿给 loader 读”，那就是文档错误，应立即修正。

## Failure modes and first checks

### 1. 路径是对的，但市场范围错了

症状：

- archive 放在 `binance/spot/...`
- 但当前任务本来是 futures-first 研究

先查：

- 是不是误把现货下载脚本当成 Phase 1 主入口
- 是不是把 futures / spot 数据落到了同一 dataset 名下

### 2. 文档写成“固定每次抓 N 行”

症状：

- operator 用“单次页数”定义任务完成标准

先查：

- manifest 是否记录 `coverage_start` / `coverage_end`
- runbook 是否明确写出 coverage-driven 规则

### 3. 把 archive root 当成 dataset root

症状：

- `load_historical_dataset` 直接对 raw-market archive 路径运行
- loader 开始读取错误目录结构

先查：

- 是否遗漏 importer / dataset build 这一步
- dataset root 里是否出现 `<exchange>/<market>/<dataset>` 结构

### 4. 把 dataset root 当成归档层

症状：

- 在 dataset root 下加 `archive/`、`notes/`、备份子目录

先查：

- 当前目录是不是本来要给 loader 读取
- 是否应该把附加资料移到 archive 根或文档目录

### 5. 把 `/tmp` 当成长期 archive

症状：

- 关键研究输入或结果只存在 `/tmp`

先查：

- 是否只是 smoke run
- 是否已经迁移到正式 archive / dataset / research 路径

## Operator dos and don'ts

建议：

- 先确认 coverage window，再开始抓取或补数
- 把 raw-market archive 与 imported dataset root 分开管理
- futures-first 任务先检查 `binance/futures/...` 是否完整
- 每次 readback 时都检查 docs 有没有误导 operator 绕过 importer

不要：

- 用“抓了多少页”替代“覆盖到哪里”
- 把 spot 数据混进当前 futures-first phase
- 把 raw-market archive 直接当 dataset root 给 loader 读
- 把长期研究资产只放在 `/tmp`

## Minimal verification commands

### 当前仓库最小验证

```bash
export UV_CACHE_DIR=/tmp/uv-cache-historical-archive-docs
uv run --with pytest python3 -m pytest -q -p no:cacheprovider \
  trading_system/tests/test_backtest_dataset.py \
  trading_system/tests/test_backtest_engine.py
```

### 文档 readback

```bash
grep -nE 'Binance-first|futures-first|coverage-driven|raw-market|archive' \
  trading_system/docs/HISTORICAL_DATA_ARCHITECTURE.md \
  trading_system/docs/HISTORICAL_DATA_RUNBOOK.md \
  trading_system/docs/HISTORICAL_DATA_RETENTION.md
```

## Escalate when

出现以下情况时，不要只改 runbook 文案，应升级为实现任务：

- 需要真正写入 raw-market manifest
- 需要 archive path builder / importer
- 需要把 runtime cycle 自动归档
- 需要自动 prune / retention job
- 需要证明历史覆盖没有断档

## Related docs

- 架构：`trading_system/docs/HISTORICAL_DATA_ARCHITECTURE.md`
- 保留策略：`trading_system/docs/HISTORICAL_DATA_RETENTION.md`
- 当前 dataset 规范：`trading_system/docs/BACKTEST_DATA_SPEC.md`
- 当前 research runbook：`trading_system/docs/BACKTEST_RUNBOOK.md`
- 已批准计划：`docs/superpowers/plans/2026-03-31-historical-data-and-backtest-dataset-plan.md`
