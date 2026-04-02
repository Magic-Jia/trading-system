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

## Step 1A: classify the operation before touching files

先明确这次到底是哪一类操作：

### A. Raw-market backfill

适用场景：

- `binance/futures/<dataset>/<symbol>/<timeframe?>/` 路径还不存在
- 路径存在，但目标 coverage window 里有明显历史缺口
- 需要把 coverage 从零或从更早起点补齐到某个研究窗口

执行重点：

- 先写清目标 `coverage_start` / `coverage_end`
- 以 canonical path 为准落 archive，不要先造 dataset root
- 完成标准是 manifest 能证明目标 coverage window 已补齐

### B. Incremental refresh

适用场景：

- canonical archive path 已存在
- 旧的 coverage 已经可用，只是要把尾部继续向前推进
- 目标是让现有 archive 保持接近最新，而不是回补整段历史

执行重点：

- 先读现有 manifest，确认当前 `coverage_end`
- 只扩展已有 coverage 的边界，不要顺手改写旧历史窗口语义
- 完成标准是 manifest 把 `coverage_end` 推进到新的目标点

如果既没有已有 path，也说不清 coverage gap，就先按 backfill 思维澄清窗口；不要模糊地说“顺手 refresh 一下”。

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

## Step 2A: use the matching checklist

### Raw-market backfill checklist

- 目标路径是否明确落在 `trading_system/data/archive/raw-market/binance/futures/...`
- dataset 是否仍在 Phase 1 范围：`klines`、`funding-history`、`open-interest-history`
- `klines` 是否只使用当前批准的 `1h` / `4h` / `1d`
- manifest 是否准备记录 `coverage_start`、`coverage_end`、fetch timestamp、source/endpoint
- 完成判断是否基于 coverage window，而不是“抓了几页”
- archive 与 imported dataset root 是否仍然分开

### Incremental refresh checklist

- 现有 canonical path 是否已经存在并可识别
- 现有 manifest 是否能读出旧的 `coverage_end`
- 这次 refresh 是否只是在原 coverage 边界上向前追加
- refresh 后是否仍保持 Binance-first / futures-first，不混入 spot
- 是否避免把 refresh 结果直接当成 loader dataset root
- 是否记录新的 readback / smoke verification 结果

## Step 2B: prepare the importer-facing handoff

已批准的 Phase 1 数据流仍然是：

`raw-market archive -> imported dataset root -> load_historical_dataset -> backtest CLI`

但要明确当前 repo reality：

- `trading_system/app/backtest/archive/importer.py` 还没落地
- `trading_system/app/backtest/archive/cli.py` 也还没落地
- 当前没有“通用 downloader 一跑完就自动生成 dataset root”的现成入口

所以 operator 在 Phase 1 要做的是：**先证明 archive coverage 正确，再准备一份 importer-facing handoff，再把研究需要的数据整理成 loader 可读的 dataset root**。

### Importer-facing handoff checklist

- 是否已经明确列出要使用的 raw-market canonical path
- 每个 source path 是否都能说明 `coverage_start` / `coverage_end`
- 是否写清这次研究实际采用的 symbol set、dataset、timeframe、coverage window
- 目标 dataset root 是否与 `trading_system/data/archive/raw-market/...` 分离
- dataset root 准备放在哪个路径、由谁装配、何时做 readback，是否已经在 handoff note 里写清
- 是否已说明账户快照策略：bundle 自带 `account_snapshot.json`，还是依赖 dataset root 级 `baseline_account_snapshot.json`
- 是否说明 bundle 顺序以 `metadata.json` 内的 `timestamp` / `run_id` 为准，而不是以目录名推断
- provenance / handoff note 是否保存在 operator 记录、研究记录或 ticket 中，而不是塞进 dataset root 目录
- 是否明确把这一步表述为“手工整理 / 当前文档约束下的导入准备”，而不是声称仓库已有自动 importer / downloader

## Step 2C: assemble the imported dataset root

当 handoff 已经清楚后，下一步才是把研究输入装配成当前 loader 能读的 dataset root。

这一步在当前 repo reality 下是 **手工 assembly / 人工校对**，不是自动 downloader / importer 流程。

先锁定 materialization 决策，再开始复制/整理文件：

- 本次 dataset root 服务的是哪一个明确的 research window / bundle 集合
- 每个 bundle 的 `timestamp` / `run_id` 打算写什么，来源记录是哪份
- `market_context.json` 与 `derivatives_snapshot.json` 准备从哪些已确认输入整理而来
- 账户上下文采用 root 级 `baseline_account_snapshot.json`，还是 bundle 各自携带 `account_snapshot.json`
- provenance / handoff note 放在哪个 dataset root 外部位置

如果这些问题里仍有任何一项需要靠“未来 importer/downloader 会替我补齐”来回答，就不要继续 materialization。

最小装配顺序：

1. 先选定一个与 archive 根分离的 dataset root 目录
2. 先做 root 级空目录检查，避免把 `archive/`、`notes/`、备份目录带进来
3. 如需全局账户基线，先准备 `baseline_account_snapshot.json`
4. 为每个研究时间点建立一个一级 bundle 目录
5. 在每个 bundle 中写入 `metadata.json`、`market_context.json`、`derivatives_snapshot.json`
6. 如该 bundle 需要覆盖默认账户上下文，再补 `account_snapshot.json`
7. provenance / handoff note 仍写在 dataset root 外部，不要混入一级目录

### Root validation gates before assembly

在真正复制/整理 snapshot 之前，先过一遍 root 级闸门：

- dataset root 是否与 raw-market archive、runtime bundle、research output 目录彻底分离
- root 下除可选的 `baseline_account_snapshot.json` 与 importer-owned `import_manifest.json` 外，是否只准备放一级 bundle 目录
- 是否不存在 `archive/`、`notes/`、`tmp/`、`backup/` 之类会被误当成 bundle 的一级目录
- 是否没有把 provenance note、checksum、下载日志或 free-form manifest 直接塞到 dataset root

要特别记住当前 loader reality：`load_historical_dataset` 会把 dataset root 下的**每一个一级目录**都当成 bundle 尝试读取。
所以 Phase 1 的 root validation 重点不是“目录长得像不像”，而是**一级目录里有没有任何非 bundle 目录**。

### Imported dataset assembly checklist

- dataset root 是否与 `trading_system/data/archive/raw-market/...` 完全分离
- 一级子目录是否只保留 bundle 目录，而不是 `<exchange>/<market>/<dataset>` archive 结构
- 是否只放当前 contract 允许的 root 文件：`baseline_account_snapshot.json`、可选的 `import_manifest.json`，以及 bundle 内四类 snapshot/metadata 文件
- 是否已经为每个 bundle 写清“这个目录代表哪个研究时间点/窗口”，而不是仅靠目录名脑补
- `metadata.json` 是否至少包含 `timestamp` 与 `run_id`
- `timestamp` 是否是当前 loader 可解析的 ISO-8601 UTC 字符串
- bundle 目录名是否只作为人工可读标签，而不是被误当成排序/契约来源
- `derivatives_snapshot.json` 是否为数组，或是带 `rows` 数组的对象
- 缺 bundle 级 `account_snapshot.json` 时，是否已提供 root 级 baseline
- 若有 `import_manifest.json`，它是否明确是 importer-owned machine manifest，而不是人工 handoff note
- root 级普通文件是否除 `baseline_account_snapshot.json` 与可选的 `import_manifest.json` 外一律不混入，避免 handoff / checksum 污染
- 是否确认任何一级附加目录都会被 loader 当成 bundle，因而会直接破坏 readback
- 是否避免把 notes、handoff、checksum、临时下载结果直接塞进 dataset root
- 是否没有把“未来会有自动 importer / downloader”写成当前可执行步骤

## Step 3: validate imported dataset roots

当前真正被 backtest loader 消费的，仍然是 imported dataset root，而不是 raw-market archive 本身。

最低 contract：

- `baseline_account_snapshot.json`（可选）
- `import_manifest.json`（可选；仅限 Phase 1 importer-owned root manifest）
- `<bundle>/metadata.json`
- `<bundle>/market_context.json`
- `<bundle>/derivatives_snapshot.json`
- `<bundle>/account_snapshot.json`（可选，但若缺失则需 baseline）

当前 loader / importer validation 还有几条容易被忽略的现实约束：

- dataset root 下的每个一级目录都会被当成 bundle 读取；多余目录不会被自动忽略
- bundle 排序依据是 `metadata.json` 里的 `timestamp`，再按 `run_id` 排序，不看目录名
- root 级普通文件里，当前只有 `baseline_account_snapshot.json` 会被 loader 读取；`import_manifest.json` 不参与 loader 排序，但若存在，会进入 importer readback validation
- 若 `import_manifest.json` 存在，至少要能读回 `dataset_root`、`scope`、`snapshot_count`、`symbols`、`archive_root`、`source`、`bundle_dirs`、`bundle_timestamps`、`start_timestamp`、`end_timestamp`

重点检查：

- dataset root 下只放 bundle 目录和允许的 root 文件（baseline、可选 importer manifest）
- 不要把 `archive/`、`notes/`、人工说明目录塞进去
- `metadata.json` 里有 `timestamp` 与 `run_id`
- `derivatives_snapshot.json` 结构合法
- 如需保留 provenance / handoff 说明，应放在 dataset root 外的 operator/readback 记录中
- 若 `import_manifest.json` 存在，确认 `source.manifest_paths` 都在同一个 `raw-market` 树下，且反推出的 `archive_root` 没漂移
- 继续打开这些 `source.manifest_paths` 指向的 raw-market manifest，确认文件没有丢失，且 payload 仍明确是 `exchange=binance`、`market=futures`

### Step 3A: do a lightweight imported-dataset readback

在当前 repo 里，最有价值的轻量 readback 不是“联网跑一遍下载器”，而是确认 dataset root 真能满足 loader contract。

建议至少读回四件事：

1. dataset root 一级目录是否干净
2. bundle 必需文件是否齐全
3. `metadata.json` / baseline / `import_manifest.json` 逻辑是否与当前 loader / importer 约束一致
4. 文档表述有没有把 archive / importer / downloader 说过头

最小人工 readback 可以用：

```bash
DATASET_ROOT=trading_system/tests/fixtures/backtest/sample_dataset

find "$DATASET_ROOT" -maxdepth 2 -type f | sort

find "$DATASET_ROOT" -mindepth 1 -maxdepth 1 -type f | sort | while read -r entry; do
  case "$(basename "$entry")" in
    baseline_account_snapshot.json|import_manifest.json)
      ;;
    *)
      echo "unexpected top-level file: $entry"
      ;;
  esac
done

find "$DATASET_ROOT" -mindepth 1 -maxdepth 1 -type d | sort | while read -r entry; do
  case "$(basename "$entry")" in
    archive|notes|tmp|backup|backups)
      echo "unexpected top-level directory: $entry"
      ;;
  esac
done

find "$DATASET_ROOT" -mindepth 1 -maxdepth 1 -type d | sort | while read -r bundle; do
  test -f "$bundle/metadata.json" || echo "missing metadata.json: $bundle"
  test -f "$bundle/market_context.json" || echo "missing market_context.json: $bundle"
  test -f "$bundle/derivatives_snapshot.json" || echo "missing derivatives_snapshot.json: $bundle"
  test -f "$bundle/account_snapshot.json" || test -f "$DATASET_ROOT/baseline_account_snapshot.json" || \
    echo "missing account snapshot and no baseline: $bundle"
done

grep -RInE '"timestamp"|"run_id"' "$DATASET_ROOT"/*/metadata.json

test ! -f "$DATASET_ROOT/import_manifest.json" || \
  grep -nE '"schema_version"|"scope"|"archive_root"|"dataset_root"|"snapshot_count"|"symbols"|"bundle_dirs"|"bundle_timestamps"|"start_timestamp"|"end_timestamp"|"source"' \
    "$DATASET_ROOT/import_manifest.json"
```

如果要对真实 dataset root 做同类检查，只替换根目录，不要改读回标准。

这组 readback 是本地文件系统检查，不依赖 downloader、网络连通性或任何尚未落地的 archive importer。

如果读的是由 runtime bundle 派生出的 imported dataset，还要额外确认 provenance 指针没有断：

- runtime `latest.json` 至少要暴露 `source_bundle`、`source_run_id`、`source_timestamp`
- config `metadata`、bundle `metadata.json`、dataset row `meta` 之间要共享 `source_bundle`、`source_mode`、`source_runtime_env`、`source_finished_at`
- `source_run_id` 应与 bundle `run_id` 对齐，`source_timestamp` 应与 bundle `timestamp` 对齐
- runtime `runtime_state.json` 至少要保留 `latest_allocations[*].execution` 与 `paper_trading`
- runtime `latest.json.paper_trading` 只做 summary；若要对表 `intent_id` / ledger 路径，要回到 `runtime_state.json`

最小 readback 可以追加：

```bash
CONFIG_JSON=trading_system/tests/fixtures/archive_runtime/imported_dataset_backtest_config.json
LATEST_JSON=trading_system/tests/fixtures/archive_runtime/runtime/paper/research/latest.json
STATE_JSON=trading_system/tests/fixtures/archive_runtime/runtime/paper/research/runtime_state.json

grep -nE '"source_bundle"|"source_run_id"|"source_timestamp"|"source_mode"|"source_runtime_env"|"source_finished_at"' "$CONFIG_JSON"
grep -nE '"mode"|"runtime_env"|"finished_at"|"source_bundle"|"source_run_id"|"source_timestamp"' "$LATEST_JSON"
grep -nE '"latest_allocations"|"execution"|"paper_trading"|"ledger_path"|"intent_id"|"ledger_event_count"|"emitted_count"|"replayed_count"' "$STATE_JSON"
grep -RInE '"run_id"|"timestamp"|"source_bundle"|"source_mode"|"source_runtime_env"|"source_finished_at"' "$DATASET_ROOT"/*/metadata.json
```

读回时重点问：

- 有没有把 archive 目录结构误放进 dataset root
- 有没有 bundle 缺 `metadata.json` 或 `derivatives_snapshot.json`
- 有没有 bundle 缺 `account_snapshot.json` 且 root 级又没有 `baseline_account_snapshot.json`
- 有没有把 bundle 目录名误当成排序或时间戳合同
- 有没有把 provenance note 错放到 loader 会扫描的一级目录
- 有没有 `source.manifest_paths` 指向不存在、或已经漂成 spot / 非 futures 的 raw-market manifest
- 有没有把 `latest.json` 当成完整 execution 账本；真正的 paper execution continuity 仍要回看 `runtime_state.json` 与 ledger
- 有没有任何说明让 operator 误以为当前仓库已经存在自动 importer / downloader

### Step 3B: materialization readback loop

对真实 dataset root，建议把装配与读回视为一个短回路，而不是“全部复制完再祈祷”：

1. 先建立空 root 并做一级目录污染检查
2. 先放入 `baseline_account_snapshot.json`（若本次需要）
3. 逐个 bundle 写入三份必需文件，再决定是否补 `account_snapshot.json`
4. 每写完一批 bundle，就跑一次上面的 lightweight readback
5. 只有 readback 仍然干净时，才继续下一批或交给 backtest CLI

这样做的目标不是模拟 future importer，而是在当前 Phase 1 repo reality 下，用最小本地读回把 materialization 失误尽早暴露出来。

### Step 3C: keep an external materialization note

当 Step 3A / 3B 的读回已经干净后，再补一份 dataset root 外部的 operator 记录；它可以放在 ticket、研究记录或 handoff note 中，但不要放进 dataset root。

最小模板可以直接照着写：

```text
dataset_root:
research_window_or_bundle_set:
bundle_timestamps_and_run_ids:
market_context_source:
derivatives_snapshot_source:
account_snapshot_mode: baseline | per-bundle
baseline_account_snapshot_source:
last_lightweight_readback:
readback_result:
open_questions:
```

写这份记录时，重点不是“把故事写漂亮”，而是把 Phase 1 materialization 的几个关键决定固定下来：

- dataset root 到底服务哪个研究窗口，而不是模糊地“给 backtest 用”
- `timestamp` / `run_id` 是从哪份现有记录写入，而不是现场脑补
- snapshot 来源是否已经能被下一位 operator 复核
- 最近一次 lightweight readback 是否真的通过

只要这些字段还空着，就不要把 dataset root 当成已完成 handoff。

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

额外回读一遍操作类型边界：

- backfill 解决的是 coverage 缺口
- incremental refresh 解决的是 coverage 末端推进
- 二者都只发生在 raw-market archive 层
- 当前 repo reality 里，真正可执行并已验证的消费入口仍是 imported dataset root + backtest loader

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

### 3A. 把“future importer”当成当前已实现入口

症状：

- operator 说“downloader 会自动出 dataset root”
- 文档让人误以为当前仓库已经有 archive import CLI

先查：

- `trading_system/app/backtest/archive/importer.py` 是否实际上存在
- runbook / spec 是否明确写出“当前仍是 handoff + 手工整理，不是自动化入口”

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
- 先判定是 backfill 还是 incremental refresh，再碰路径和 manifest
- 把 raw-market archive 与 imported dataset root 分开管理
- futures-first 任务先检查 `binance/futures/...` 是否完整
- 每次 readback 时都检查 docs 有没有误导 operator 绕过 importer

不要：

- 用“抓了多少页”替代“覆盖到哪里”
- 把 backfill 和 incremental refresh 混成一句“补数据”
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
grep -nE 'Binance-first|futures-first|coverage-driven|raw-market|archive|handoff|importer|dataset root' \
  trading_system/docs/HISTORICAL_DATA_ARCHITECTURE.md \
  trading_system/docs/HISTORICAL_DATA_RUNBOOK.md \
  trading_system/docs/HISTORICAL_DATA_RETENTION.md \
  trading_system/docs/BACKTEST_DATA_SPEC.md
```

```bash
find trading_system/tests/fixtures/backtest/sample_dataset -maxdepth 2 -type f | sort
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
