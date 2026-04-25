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

## 研究执行流程（按步骤）

下面这套流程用于把“研究结果”转成“是否允许改交易程序”的标准动作。

### Step 1. 准备 dataset root

确认 dataset root 下每个 bundle 至少有：

- `metadata.json`
- `market_context.json`
- `derivatives_snapshot.json`
- `account_snapshot.json`（或者 dataset 根目录的 `baseline_account_snapshot.json`）
- `instrument_snapshot.json`（`full_market_baseline` 必需）

### Step 2. 选择 baseline config / variant config

优先从 `trading_system/tests/fixtures/backtest/` 复制现有配置，再改成真实研究配置。

当前已接入统一 CLI bundle writer 的 experiment 包括：

- `full_market_baseline`
- `rotation_suppression`
- `allocator_friction`
- `engine_filter_ablation`
- `walk_forward_validation`

### Step 3. 运行 research bundle

统一使用：

`python -m trading_system.app.backtest.cli run --config /path/to/config.json --output-dir /tmp/backtests`

每次 run 都会落一个 bundle 目录，目录名形如：

- `full_market_baseline__<baseline_name>__<variant_name>`
- `rotation_suppression__<baseline_name>__<variant_name>`
- `allocator_friction__<baseline_name>__<variant_name>`
- `engine_filter_ablation__<baseline_name>__<variant_name>`
- `walk_forward_validation__<baseline_name>__<variant_name>`

### Step 4. 检查 bundle 目录里有哪些文件

不同 experiment 的 bundle 会稳定写出这些 artifact：

- `manifest.json`：自动化 contract，记录 dataset root、sample period、window counts、snapshot count、artifact 列表
- `summary.json`：结构化主结果
- `scorecard.json`：最关键结论摘要，回答“是否值得继续研究”
- `comparison_rows.json`：`rotation_suppression` / `allocator_friction` 的明细比较行
- `windows.json`：`walk_forward_validation` 的逐窗口结果
- `breakdowns.json` / `audit.json`：`full_market_baseline` 的收益归因和审计输出

### Step 5. 比较 baseline bundle 和 variant bundle

统一使用：

`python -m trading_system.app.backtest.cli compare --baseline-bundle /tmp/backtests/<baseline_bundle> --variant-bundle /tmp/backtests/<variant_bundle> --output-dir /tmp/backtests/compare_out`

`compare` 不会重跑 experiment；它只读取现有 JSON bundle，然后落盘：

- `promotion_gate.json`
- `decision_summary.json`

### Step 6. 打开 `promotion_gate.json` 看决策

重点检查：

- `decision`
- `checks.has_baseline_variant_pair`
- `checks.has_cost_adjusted_edge`
- `checks.has_out_of_sample_evidence`
- `checks.has_attribution_or_funnel_explanation`
- `checks.has_runtime_observability_plan`
- `checks.has_rollback_plan`
- `metric_deltas`
- `why`

### Step 7. 什么时候允许进入“改交易程序”阶段

只有在下面条件都满足时，才允许进入“改交易程序”阶段：

- 有 baseline vs variant 对照
- 有成本后结果
- 有 attribution / funnel / kill-rate 解释
- 有样本外证据，且没有明显塌陷
- 有 runtime 观测字段
- 有 rollback target / trigger / observation window
- `promotion_gate.json["decision"] == "candidate_for_promotion"`

### Step 8. 什么情况下必须继续研究，不准改源码

出现下面任一情况，都必须继续研究，不准改源码：

- `decision == "hold"`
- `decision == "reject"`
- 缺 OOS 证据
- 成本后优势消失
- attribution / funnel / kill-rate 解释不清
- runtime 观测字段缺失
- rollback plan 缺失

换句话说：**回测结果只是“看起来不错”还不够；只有 promotion gate 满足，才允许把结论写回交易程序。**

## Related docs

- `trading_system/docs/BACKTEST_DATA_SPEC.md`
- `trading_system/docs/HISTORICAL_DATA_ARCHITECTURE.md`
- `trading_system/docs/HISTORICAL_DATA_RUNBOOK.md`
- `trading_system/docs/HISTORICAL_DATA_RETENTION.md`
- `trading_system/docs/BACKTEST_PROMOTION_GATE.md`
- `trading_system/docs/BACKTEST_ROADMAP.md`
