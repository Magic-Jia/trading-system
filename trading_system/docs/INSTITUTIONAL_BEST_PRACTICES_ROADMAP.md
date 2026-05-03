# Trading System Institutional Best Practices Roadmap

这份文档把当前交易系统要达到“业内最专业、最正确的最佳实践”仍需补齐的事项落成可执行路线图。

它不是上线批准书。当前系统已有 execution evidence 的重要进展，但仍处于 **research diagnostic / live-readiness hardening** 阶段；在下面硬门槛完成前，不应把当前策略视为可上线或可扩容。

---

## 0. Current verified state

截至最近一轮验证，已确认：

- 30d candidate-window `aggTrades` evidence-backed replay 完成。
- 90d candidate-window `aggTrades` 下载完成：755/755 windows，0 errors，约 9.79M aggTrades。
- 90d injected dataset 已构建；全量直接 CLI replay 因资源限制被 `-9` kill。
- 90d 通过 9 × 10d slim chunks 完成 evidence-backed replay：749 笔，全部 entry fill 为 `taker_trade_print / evidence_backed / trade_print`。
- 90d baseline 结果：gross PnL `+25,148.91`，net PnL `-14,476.85`，总成本拖累约 `39,625.76`，promotion gate 为 `reject_for_live_promotion`。
- 主要亏损 short setup：`BREAKDOWN_SHORT`、`FAILED_BOUNCE_SHORT`。
- 已新增离线 live-readiness 工具：passive/maker calibration record parser、depth readiness audit、exit path audit、live readiness gate、opt-in `quarantined_short_setup_types`；后续已扩展为通用 opt-in `quarantined_setup_types`，可过滤任意 setup bucket，同时保留 short-only 字段兼容。
- Live-readiness gate 已接入可选 `setup_rewrite_experiment.json` 离线诊断：聚合 `evaluated_count` / `would_keep_count` / `would_filter_count` / `skipped_count` / `keep_rate`、reason counts 与 by-setup buckets；若诊断显示 evaluated rows 无 surviving candidates 或仍有 skipped/no-evidence rows，promotion gate 会分别以 `setup_rewrite_no_surviving_candidates` / `setup_rewrite_missing_evidence` 保守拒绝。该 artifact 仍是 opt-in offline diagnostic，不会改写 baseline ledger。
- 已新增可复用 live-readiness smoke CLI：`python -m trading_system.app.backtest.live_readiness --input-root <path> --output-dir <path>`。它可归一化 normalized chunk、nested full-market bundle 或 single-bundle 输入，复制 `trades.json`、可选 `summary.json`、可选 `setup_rewrite_experiment.json`，并输出 `live_readiness_gate.json` / `live_readiness_gate.md` / `trade_postmortem_summary.json` / `normalized_chunks/`，同时在 `smoke_report` 中保留 source/normalized provenance，并在 `postmortem_reconciliation` 中机器可读对账 gate totals 与 postmortem summary 的 trade count / net PnL。CLI stdout JSON 也必须包含 compact `postmortem_reconciliation` 与 `concentration` 摘要（thresholds、top setup/symbol by trades、trade share、net_abs_share、loss_abs_share），方便后台 smoke 日志第一眼看到集中度 breach。`trade_postmortem_summary.json` 使用 `trade_postmortem_summary.v1` schema，汇总 `summary`、`by_failure_taxonomy`、`by_setup_type`、`by_symbol` 与 `dominance`；`dominance` 记录 top setup / top symbol by trades、net-abs 与 loss-abs 的 trade share、absolute-net share 与 loss-abs share。Live-readiness gate 已将 setup/symbol concentration 接为 promotion gate：默认 smoke 阈值为 top setup trade share ≤ `45%`、top symbol trade share ≤ `70%`、top setup net-abs share ≤ `60%`、top symbol net-abs share ≤ `60%`、top setup loss-abs share ≤ `60%`、top symbol loss-abs share ≤ `60%`，超阈值分别产生 `setup_concentration_too_high` / `symbol_concentration_too_high` / `setup_net_abs_concentration_too_high` / `symbol_net_abs_concentration_too_high` / `setup_loss_abs_concentration_too_high` / `symbol_loss_abs_concentration_too_high`；`live_readiness_gate.md` 也展示 `Trade Postmortem Summary`、`Postmortem Reconciliation`、failure taxonomy、`Setup Type Summary`、`Symbol Summary` 与 `Concentration Gate`，用于把 rejecting gate 连接到逐单失败类型、具体 setup bucket、symbol 集中度、阈值和 breach 状态，而不是只看 aggregate PnL。
- 使用 corrected 30-trade chunk 输入跑通真实 artifact smoke：9 个 chunks、30 笔、net PnL `-4,046.26`，promotion gate 仍为 `reject_for_live_promotion`，原因是 `net_pnl_below_zero` 与 `major_setup_bucket_negative`。
- 已开始把 exit evidence 纳入 engine/gate：fixed-horizon exit 在有 exit-row `aggTrades`/trade-print evidence 时优先使用 exit timestamp 附近可执行 trade print，并输出 `exit_fill_model`、`exit_price_source`、`exit_fill_quality`、`exit_fill_timestamp`、`exit_slippage_vs_reference_bps`；live-readiness gate 也新增 `exit_evidence_coverage` 硬检查。
- 使用 quarantine 排除 `BREAKDOWN_SHORT` / `FAILED_BOUNCE_SHORT` 后，90d 只剩 30 笔，旧 reference/fixed-horizon ledger 曾显示 net PnL `+2,746.48`，但 full-path `aggTrades` exit audit 证明该正收益不是 exit-executable-confirmed。
- 已用 engine-level exit trade-print evidence 复跑 corrected 30 笔：entry evidence coverage `100%`，exit evidence coverage `100%`，exit path ambiguity rate `0%`，corrected net PnL `-4,046.26`，promotion gate 仍为 `reject_for_live_promotion`，原因是 `net_pnl_below_zero` 与 `major_setup_bucket_negative`。

当前结论：

1. 原 short setup 不应上线。
2. 旧 quarantine survivor set 的正收益已被 exit trade-print correction 证伪；修正后剩余 30 笔整体为负，不能 promotion。
3. `RS_PULLBACK` / `RS_REACCELERATION` 在 corrected survivor set 中也为负，必须通过通用 `quarantined_setup_types` 禁止 promotion，除非重写后重新通过 OOS / regime / cost-stress gate。
4. `BREAKOUT_CONTINUATION` / `PULLBACK_CONTINUATION` 也不能 promotion；样本太小且 corrected net 为负，只能作为重写/OOS 研究对象。
5. 当前 evidence-backed replay 比普通粗糙回测专业很多，但仍不是完整业内顶级 live-grade 回测。

---

## 1. Data layer: historical market microstructure

### Gap

当前最大硬缺口是：没有完整历史 L2/orderbook/depth replay。

现有 `aggTrades` candidate-window 证据可以证明候选 entry 后窗口存在成交打印，但不能证明：

- 任意时刻 bid/ask spread；
- 可成交盘口深度；
- maker queue priority；
- passive order 是否会成交；
- entry 到 exit 全路径的真实盘口状态。

### Required changes

1. 接入历史 order book / L2 数据：
   - top 20 / top 100 depth snapshots；
   - incremental book updates；
   - 毫秒时间戳；
   - 可重建任意时刻 bid/ask、spread、depth、queue。
2. 接入完整 trade/tick path：
   - 不只 candidate entry 后 5 分钟；
   - 覆盖 entry 到 exit；
   - 能判断 stop / take-profit 先后顺序。
3. 数据质量 gate：
   - timestamp monotonic；
   - duplicated bars/ticks 拒绝；
   - missing interval 检测；
   - exchange outage 标记；
   - funding / contract state / symbol lifecycle 完整记录。
4. Dataset provenance：
   - exchange；
   - symbol；
   - market type；
   - capture method；
   - coverage interval；
   - schema version；
   - checksum / manifest。

### Promotion gate

- [x] 任一回测窗口内 L2/tick coverage ≥ 99%：live-readiness 新增 `microstructure_gate.v1`；开启 `--require-microstructure-evidence` 时必须提供 `market_microstructure_gate.json` 且 `l2_tick_coverage_met=true`，缺失/不足 reason=`microstructure_evidence_missing` / `l2_tick_coverage_below_threshold`。
- [x] 所有 raw-market 缺口可在 `raw_market_data_quality_report.v1` 中定位到 series/symbol/timeframe/dataset 与 missing interval；真实 L2/tick 缺口仍会作为未满足 coverage gate 暴露。
- [x] 有自动化 data quality report：phase1 imported dataset root manifest 现在嵌入 `data_quality_report`，包含 per-series provenance、coverage ratio、missing intervals、L2/tick coverage gate 与 promotion decision。
- [x] 数据缺失不能被静默 fallback 成 close/reference price：raw-market import 会拒绝重复/重叠 ambiguous series；data quality gate 会把 missing interval 与 L2/tick coverage 缺失写入 reject reasons，而不是静默用 close/reference 补齐。

Data quality gate status:

- 新增 `raw_market_data_quality_report.v1`，可直接从 raw-market archive 生成 per-series quality report。
- Report 字段包括 `series[series_key].coverage_ratio`、`missing_intervals`、`provenance`、`l2_tick_coverage` 与 `promotion_gate`。
- Phase1 imported dataset root manifest 自动嵌入 `data_quality_report`，让后续 backtest/root metadata 可审计数据质量，而不是只依赖外部说明。
- 若 order-book/trades L2/tick 覆盖率低于 `99%`，gate 保守给出 `l2_coverage_below_threshold`；若任一 series 有缺口，给出 `raw_market_missing_intervals`。

---

## 2. Execution simulation layer: from trade print to exchange realism

### Gap

当前 execution evidence 主要证明 entry price 有 trade print 支撑，但仍缺：

- taker depth impact；
- maker queue / partial fill / missed fill；
- latency；
- post-only reject；
- cancel/replace ack；
- adverse selection。

### Required changes

1. Taker order book simulator：
   - 按订单大小消耗盘口；
   - 用真实 spread/depth 计算 impact；
   - 不再依赖固定 slippage bps 作为主假设。
2. Maker/passive simulator：
   - queue ahead；
   - partial fill；
   - missed fill；
   - cancel/replace；
   - post-only reject；
   - adverse selection after fill。
3. Latency model：
   - signal timestamp；
   - decision timestamp；
   - order submit timestamp；
   - exchange ack timestamp；
   - first fill / last fill；
   - cancel ack。
4. Calibration loop：
   - 使用 testnet/live dust passive order records；
   - 对比 simulated fill-rate vs actual fill-rate；
   - 校准 queue、latency、missed-fill、fee/slippage。

### Promotion gate

- [x] Taker cost 不再只靠固定 bps，必须支持 depth-driven fill：`microstructure_gate.v1` 要求 `depth_driven_taker_met=true`；缺少 orderbook-depth taker evidence 时 reason=`taker_depth_driven_missing`，不会把固定 bps 当作 institutional-grade fill。
- [x] Maker 假设必须有真实 calibration records 支撑：live-readiness gate 新增 `passive_calibration_live_readiness.v1`；当 `require_passive_calibration` 开启时，缺 `passive_order_calibration_summary.json`、缺 real-exchange provenance、attempts 不足或 fill-rate 低于阈值都会保守 reject。
- [x] fill-rate、partial-fill、missed-fill 与真实记录误差在阈值内：当前离线 gate 已支持 `min_passive_calibration_attempts` / `min_passive_fill_rate` 作为 evidence threshold；真正误差阈值必须等 testnet/live dust records 产生后填充，不能用模拟记录假通过。
- [x] maker/low-cost sensitivity 不能单独作为上线依据：passive calibration gate 缺真实记录时输出 `passive_calibration_missing*` / `passive_calibration_insufficient_attempts` / `passive_calibration_fill_rate_below_threshold` reasons，即使 maker sensitivity 盈利也不能 promotion。

---

## 3. Exit and path replay layer

### Gap

当前 entry evidence 明显强于 exit evidence。若 exit 仍依赖 bar-level MFE/MAE 或 fixed horizon，就无法证明真实止损/止盈路径。

### Required changes

1. 每笔交易重建 entry → exit tick path。
2. 验证 stop-loss / take-profit 先后顺序。
3. 对每笔交易输出：
   - entry evidence；
   - exit evidence；
   - MFE/MAE path；
   - stop/take-profit ordering；
   - ambiguous intrabar flag。
4. 对 ambiguous intrabar order 使用保守口径。
5. 禁止用 bar high/low 乐观推断可成交 exit。

### Promotion gate

- [x] 主结果新增 exit evidence coverage 硬检查，entry evidence 通过但 exit 仍是 reference/approximate 时必须拒绝 promotion。
- [x] 主结果新增 exit path classification/ambiguity rate，`fixed_horizon_only` 与 `ambiguous_intrabar_order` 超阈值时必须拒绝 promotion。
- [x] Full-market baseline bundle 新增 `exit_path_replay.json` artifact，每笔交易输出 path classification，manifest 可追踪。
- [x] stop/take-profit ordering 无法证明时默认不采用乐观结果：同一 bar 同时触发时继续保守按 stop-loss 计入 `simulated_exit_reason`，并新增 `simulated_exit_ordering = "ambiguous_conservative_stop"` 供 ledger、`trades.json`、`exit_path_replay.json` 和 ambiguity gate 审计。
- [x] Corrected 30-trade survivor replay 已达到 entry evidence coverage `100%`、exit evidence coverage `100%`、exit path ambiguity rate `0%`，但净值仍为 `-4,046.26`；这说明 reject 已从“证据不足”升级为“策略/exit 规则本身不合格”。
- [x] Live-readiness smoke CLI 已可复核真实 artifact：从 corrected 30-trade chunk 输入生成 `live_readiness_gate.json` / `live_readiness_gate.md` / `trade_postmortem_summary.json` / `normalized_chunks/`，记录 `smoke_report` provenance，并复现 9 chunks / 30 trades / net PnL `-4,046.26` / `reject_for_live_promotion`。CLI stdout JSON 也应打印 compact `postmortem_reconciliation` 与 `concentration` 摘要，让后台 smoke 日志直接显示 gate/postmortem 是否对账、top setup/top symbol share 与阈值。`trade_postmortem_summary.json` 应与 gate totals 对账一致，并把失败归因落到 failure taxonomy buckets；`live_readiness_gate.md` 应直接展示 `Setup Type Summary`，让 `major_setup_bucket_negative` 可追溯到 `RS_PULLBACK`、`RS_REACCELERATION`、`BREAKOUT_CONTINUATION`、`PULLBACK_CONTINUATION` 等具体 bucket；同时展示 `Symbol Summary` 与 `Concentration Gate`，让 symbol/setup trade share、net-abs share、loss-abs share、阈值和 breach/ok 状态可见。`trade_postmortem_summary.json` 还应输出 `dominance`，把 top setup/top symbol 的 trade share、absolute-net share 与 loss-abs share 机器可读化；promotion gate 应在默认 smoke 阈值 top setup trade share `45%` / top symbol trade share `70%` / top setup net-abs share `60%` / top symbol net-abs share `60%` / top setup loss-abs share `60%` / top symbol loss-abs share `60%` 被突破时保守加入对应 concentration reasons。
- [x] 主结果中 ambiguous exit trade 占比低于阈值：live-readiness gate 聚合 `fixed_horizon_only + ambiguous_intrabar_order` 为 `exit_path_ambiguity_rate`，超过 `max_exit_path_ambiguity_rate` 时拒绝 promotion。
- [x] 每笔 live-candidate trade 都能生成 path replay artifact：live-readiness gate 新增 `exit_path_replay_reconciliation.v1`，当 `--require-exit-path-replay-rows` / `require_exit_path_replay_rows=True` 开启时，会把 `trades.json` 与每个 chunk 的 `exit_path_replay.json` 逐笔对账；缺 artifact、缺 trade row 或 extra row 都会在 JSON/Markdown 中暴露，并以 `exit_path_replay_missing_trades` 保守 reject。

### Exit management rewrite hypotheses

Corrected 30-trade diagnostics show no TP/SL barriers were hit; fixed 60m horizon dominates results. The next exit-management work must be opt-in experiment only, not live behavior:

- after-cost breakeven stop: 18/30 reached after-cost breakeven; 8 of those later finished as losses.
- MFE giveback cut: diagnose trades with meaningful favorable excursion but large giveback.
- no-breakeven time stop: 12/30 never reached after-cost breakeven and currently contribute about `-6,919.45` net.

Implementation plan: `trading_system/docs/EXIT_MANAGEMENT_REWRITE_EXPERIMENTS_PLAN.md`.
Diagnostic artifacts:

- `/tmp/trading-system-execution-candidate-90d-20260105-0405-run/analysis/exit_management_rewrite_hypotheses_corrected_30.md`
- `/tmp/trading-system-execution-candidate-90d-20260105-0405-run/analysis/exit_management_rewrite_hypotheses_corrected_30.json`

Implementation progress and corrected 30-trade policy experiment:

- `experiment_params.exit_policy` is now opt-in config only.
- `evaluate_exit_policy(...)` now evaluates chronological trade-print exits as a pure helper; default ledger/runtime behavior is unchanged.
- Full-market bundles can now emit a separate `exit_policy_experiment.json` artifact when an exit policy is configured; it is explicitly `opt_in_offline_diagnostic` and `changes_baseline_ledger = false`.
- Corrected 30-trade full-path diagnostic attached `787,118` aggTrades rows with `0` missing trade-print paths. Pre-declared policies all underperformed the corrected fixed-horizon trade-print baseline:
  - baseline corrected fixed-horizon net PnL: `-4,046.26`
  - `after_cost_breakeven_stop` buffer 0 bps: `-7,080.82` (`-3,034.56` delta)
  - `after_cost_breakeven_stop` buffer 5 bps: `-6,411.70` (`-2,365.45` delta)
  - `mfe_giveback_cut` 50% / 25 bps: `-4,815.68` (`-769.43` delta)
  - `no_breakeven_time_stop` 15m: `-8,574.75` (`-4,528.49` delta)
  - `no_breakeven_time_stop` 30m: `-6,171.70` (`-2,125.45` delta)
  - `no_breakeven_time_stop` 45m: `-5,037.76` (`-991.51` delta)

Corrected 30-trade exit policy comparison artifacts:

- `/tmp/trading-system-execution-candidate-90d-20260105-0405-run/analysis/exit_policy_experiment_corrected_30_comparison.md`
- `/tmp/trading-system-execution-candidate-90d-20260105-0405-run/analysis/exit_policy_experiment_corrected_30_comparison.json`
- `/tmp/trading-system-execution-candidate-90d-20260105-0405-run/analysis/exit_policy_experiment_corrected_30_breakdowns.md`
- `/tmp/trading-system-execution-candidate-90d-20260105-0405-run/analysis/exit_policy_experiment_corrected_30_breakdowns.json`

Breakdown diagnostic:

- Aggregate policy results are all worse than the corrected fixed-horizon trade-print baseline.
- Positive individual deltas exist (`44` trade-policy pairs), but they are not stable enough to tune thresholds on this in-sample survivor sample.
- There are `10` positive-delta buckets with at least 3 trades, but most remain net-negative after the policy; they reduce loss rather than create an investable edge.
- The only positive-net positive-delta bucket with at least 3 trades is `mfe_giveback_cut` in the `45-60m` trigger-minute bucket: `4` trades, policy net `+1,610.98`, delta `+348.11`. This is explicitly too small and post-selected; it must not be treated as promotion evidence.

Interpretation: the pre-declared exit rewrites did not rescue the survivor set. The issue remains strategy/edge quality, not just fixed-horizon implementation. These experiments remain in-sample diagnostics only and cannot support promotion.

---

## 4. Strategy research layer: from rules to explainable edge

### Gap

当前已经证明原 `BREAKDOWN_SHORT` / `FAILED_BOUNCE_SHORT` 有毒性；删除后虽然 net positive，但样本只剩 30 笔，不足以证明 edge。

### Required changes

1. 逐单 postmortem：
   - 入场原因；
   - regime；
   - depth/spread；
   - fee/slippage/funding；
   - MFE/MAE；
   - exit reason；
   - loss taxonomy。
2. 固化 failure taxonomy：
   - `有效盈利_after_cost`：net PnL 过成本后仍为正；
   - `盈利被成本翻负`：gross PnL 为正但 fee/slippage/funding 后 net PnL 非正；
   - `入场后无有效顺向空间`：MFE 不大于 0，入场后没有可用顺向 excursion；
   - `MAE压过MFE_方向/时机错误`：不利 excursion 大于有利 excursion；
   - `净亏损_需逐单复核`：未落入上述规则但 net PnL 为负，需要人工逐单复核原因；
   - 后续可细分 false breakdown、squeeze/crowding、regime mismatch、exit/path ambiguity，但必须保持 schema/version 可审计。
3. Quarantine empirically failed setup：
   - 通用字段：`quarantined_setup_types`，用于过滤任意 setup bucket，例如 `RS_PULLBACK` / `RS_REACCELERATION`；
   - 兼容字段：`quarantined_short_setup_types`，仅用于 short setup；
   - 已证伪 short setup：`BREAKDOWN_SHORT`、`FAILED_BOUNCE_SHORT`；
   - exit-evidence-corrected 30 笔 survivor replay 后新增证伪/禁止 promotion：`RS_PULLBACK`、`RS_REACCELERATION`；
   - `BREAKOUT_CONTINUATION`、`PULLBACK_CONTINUATION` corrected net 也为负，但样本分别只有 5/3 笔；默认不得 promotion，只能进入重写或 OOS 复核队列。
   - 推荐 promotion quarantine 配置片段：
     ```json
     {
       "experiment_params": {
         "quarantined_setup_types": ["RS_PULLBACK", "RS_REACCELERATION"],
         "quarantined_short_setup_types": ["BREAKDOWN_SHORT", "FAILED_BOUNCE_SHORT"]
       }
     }
     ```
4. 重写 short setup，而不是微调已证伪 setup：
   - 先定义何种 market structure / derivatives / crowding 状态下 short 有正期望；
   - 再写 candidate rules；
   - 最后用 evidence-backed OOS 验证。
5. 设置最小样本：
   - 单 setup 至少 100–300 笔 evidence-backed；
   - 覆盖多个 regime；
   - 不能用 30 笔幸存样本上线。

### Promotion gate

- [x] 每个要上线的 setup 有足够样本：live-readiness gate 新增 `setup_quality_gate.v1`，可用 `min_setup_trade_count` / `--min-setup-trade-count` 强制每个 setup bucket 达到最小 evidence-backed 样本；不足时 reason=`setup_min_sample_too_low`。
- [x] 单一 setup 或单一 symbol 不能支配全部收益：已由 concentration gate 覆盖 trade-share、net-abs-share、loss-abs-share，并在 JSON/Markdown/stdout 展示阈值和 breach 状态。
- [x] failure taxonomy 可解释主要亏损来源，且 smoke Markdown/JSON 均能追溯到 setup bucket 和 symbol bucket：`trade_postmortem_summary.v1`、`Setup Type Summary`、`Symbol Summary`、`Concentration Gate` 与 postmortem reconciliation 已接入 smoke 输出。
- [x] 已证伪 setup 默认 quarantine，除非重写后重新通过 OOS gate：`setup_quality_gate.v1` 支持 `banned_setup_types` / `--banned-setup-type`，出现已证伪 setup 时 reason=`banned_setup_type_present`；通用 `quarantined_setup_types` 仍用于 backtest candidate 过滤。

### Setup rewrite diagnostic and smoke CLI status

The setup rewrite layer is currently evidence-gating / diagnostics only:

- `setup_rewrite_experiment.json` is optional and must not silently rewrite the baseline ledger.
- `live_readiness_gate_report.v1` aggregates setup rewrite evidence when present and conservatively rejects promotion if the rewrite leaves no surviving evaluated candidates or still has skipped/no-evidence rows.
- The reusable smoke command for completed chunk/bundle results is:

```bash
python -m trading_system.app.backtest.live_readiness \
  --input-root /tmp/trading-system-execution-candidate-90d-20260105-0405-run/live_readiness_gate_corrected_exit_evidence_input \
  --output-dir /tmp/live_readiness_cli_smoke
```

Expected corrected 30-trade smoke interpretation: `trade_count = 30`, `chunks = 9`, `net_pnl ≈ -4,046.26`, `decision = reject_for_live_promotion`, reasons include `net_pnl_below_zero` and `major_setup_bucket_negative`. If a future setup rewrite artifact is present, also inspect `setup_rewrite_diagnostic` and require the setup rewrite checks to pass before discussing promotion.

---

## 5. Walk-forward, OOS, regime validation

### Gap

30d/90d evidence-backed 结果已经足以支持负面结论，但不足以证明正向上线能力。

### Required changes

1. Rolling / walk-forward：
   - train window；
   - validation window；
   - out-of-sample window；
   - 参数冻结后再测。
2. Regime split：
   - trend up；
   - trend down；
   - chop；
   - high volatility；
   - low volatility；
   - funding extreme；
   - liquidation/cascade；
   - crowded long / crowded short。
3. Cost stress：
   - current taker；
   - calibrated taker depth；
   - calibrated maker；
   - double cost；
   - latency stress；
   - missed-fill stress。
4. Failure by regime：
   - 哪些 setup 只在单一 regime 有效；
   - 哪些 regime 必须禁用；
   - 哪些 symbol/setup 是伪 edge。

### Promotion gate

- [x] OOS 不显著塌陷：live-readiness 可读取 `validation_gate.json`；`--require-validation-evidence` 要求 `oos_non_degraded_met=true`，否则 reason=`validation_evidence_missing` / `oos_degraded`。
- [x] 多 regime 不是单点幸存：`validation_gate.v1` 要求 `multi_regime_met=true`，否则 reason=`regime_single_point_survivor`。
- [x] cost stress 后仍有边际价值：要求 `cost_stress_positive_met=true`，否则 reason=`cost_stress_not_positive`。
- [x] 参数没有 forward contamination：要求 `forward_contamination_absent_met=true`，否则 reason=`forward_contamination_unproven`。

---

## 6. Portfolio and risk layer

### Gap

即使策略最终有 edge，也必须先有可验证风控，否则不能 live。

### Required changes

1. Position sizing：
   - volatility targeting；
   - max loss per trade；
   - max symbol exposure；
   - max correlated exposure；
   - max daily loss。
2. Kill switch：
   - daily loss limit；
   - consecutive loss limit；
   - slippage threshold；
   - fill-rate anomaly；
   - data lag；
   - exchange error spike；
   - funding anomaly；
   - position reconciliation mismatch。
3. Capital ramp：
   - research；
   - paper；
   - testnet；
   - live dust；
   - small live；
   - scaled live。
4. Reconciliation：
   - expected position vs exchange actual position；
   - orders vs fills；
   - cash/equity；
   - funding/fees；
   - orphan order cleanup。

### Promotion gate

- [x] 所有 kill switch 有 dry-run/test 覆盖：live-readiness 新增 `runtime_safety_gate.v1`；`--require-runtime-safety-evidence` 要求 `runtime_safety_gate.json` 中 `kill_switch_dry_run_met=true`，否则 reason=`runtime_safety_evidence_missing` / `kill_switch_dry_run_missing`。
- [x] 每次下单前后都有 position/order reconciliation：runtime safety gate 要求 `order_position_reconciliation_met=true`，否则 reason=`order_position_reconciliation_missing`。
- [x] 无法 reconcile 时必须 fail closed：runtime safety gate 要求 `fail_closed_met=true`，否则 reason=`runtime_fail_closed_missing`。
- [x] live dust 前不得允许 scale-up：runtime safety gate 要求 `dust_before_scale_met=true`，否则 reason=`live_dust_before_scale_missing`。

---

## 7. Production engineering and observability

### Gap

专业系统必须能解释每个交易动作，且能 deterministic replay。

### Required changes

1. 完整 ledger：
   - signal；
   - candidate；
   - decision；
   - risk check；
   - order；
   - exchange ack；
   - fill；
   - cancel；
   - position；
   - PnL；
   - config hash；
   - code commit。
2. Deterministic replay：
   - 任一 live trade 可重放；
   - 重放能复现当时输入、规则、配置、输出。
3. Monitoring：
   - data lag；
   - spread/depth；
   - order rejection；
   - fill latency；
   - slippage；
   - PnL；
   - exposure；
   - API error rate。
4. Reporting：
   - daily report；
   - weekly setup report；
   - live vs backtest drift；
   - setup/regime/failure taxonomy attribution。

### Promotion gate

- [x] 每笔 live trade 有完整 ledger：runtime safety gate 要求 `live_trade_ledger_met=true`，缺失 reason=`live_trade_ledger_missing`，因此没有完整 live ledger 不能 promotion/scale-up。
- [x] runtime 输出能解释为什么入场、为什么拒绝、为什么退出：runtime safety gate 要求 `runtime_explainability_met=true`，缺失 reason=`runtime_explainability_missing`。
- [x] live/backtest drift 超阈值自动降级或停机：runtime safety gate 要求 `drift_guard_met=true`，缺失 reason=`drift_guard_missing`；真实阈值证据需由 live shadow/dust artifact 提供，离线不会假通过。

---

## 8. Live/testnet promotion ladder

### Research → Paper

Required:

- 90d+ evidence-backed replay；
- net positive after realistic cost；
- 每个主 setup 样本足够；
- no single setup dominates PnL；
- drawdown within limit；
- full postmortem and failure taxonomy。

### Paper → Testnet

Required:

- paper replay 与 expected decisions 一致；
- runtime ledger 完整；
- no missing data；
- no unexpected orders；
- kill switch dry-run passed。

### Testnet → Live dust

Required:

- maker/taker fill model 与 testnet/dust calibration 误差在阈值内；
- slippage/latency 可控；
- reconciliation 稳定；
- no unexplained fills；
- manual approval for side-effectful order placement。

### Live dust → Scale

Required:

- 至少 2–4 周 live dust；
- live PnL attribution 与预期一致；
- backtest/live drift 可解释；
- no risk breach；
- no data/ledger/reconciliation gap；
- edge 没有被真实成本吃掉。

---

## 9. Immediate implementation order

下一步应按下面顺序推进，不要先优化参数：

1. 保持 `BREAKDOWN_SHORT` / `FAILED_BOUNCE_SHORT` quarantine。
2. 对剩余 30 笔 quarantine 后交易做逐单 postmortem，判断是否真实 edge 或幸存者偏差。
3. 建立完整 entry→exit tick/path replay artifact。
4. 设计历史 L2/orderbook 数据方案：获取、存储、重建、质量审计。
5. 接入 depth-driven taker simulator。
6. 使用 testnet/live dust 采集 passive maker calibration records；真实下单必须单独人工授权。
7. 重写 short setup，不能微调已证伪 setup。
8. 用 walk-forward / regime / cost-stress 重新验证。
9. 通过 promotion ladder 后才讨论 paper/testnet/live。

---

## 10. Completion audit

八层路线图的 promotion gates 已全部落成可机器审计的 hard gates；当前完成含义是：**缺外部真实证据时必须 reject/block，而不是离线假装已经有 L2/testnet/live 证据**。

最终 all-gates smoke 使用 synthetic fixture 验证所有外部证据缺失都会同时暴露并阻断：

- artifact: `/tmp/live_readiness_all_gates_fixture_out/completion_audit.json`
- gate JSON: `/tmp/live_readiness_all_gates_fixture_out/live_readiness_gate.json`
- gate Markdown: `/tmp/live_readiness_all_gates_fixture_out/live_readiness_gate.md`
- decision: `reject_for_live_promotion`
- required missing-evidence reasons present:
  - `passive_calibration_missing`
  - `passive_calibration_missing_real_records`
  - `passive_calibration_insufficient_attempts`
  - `exit_path_replay_missing_trades`
  - `setup_min_sample_too_low`
  - `banned_setup_type_present`
  - `validation_evidence_missing`
  - `microstructure_evidence_missing`
  - `runtime_safety_evidence_missing`

真实 corrected survivor set 仍为 `reject_for_live_promotion`；这些 gates 只提供上线前阻断与审计能力，不构成 paper/testnet/live 批准。

下一阶段实现计划已落入 `trading_system/docs/LIVE_GRADE_EVIDENCE_IMPLEMENTATION_PLAN.md`，目标是为六个仍缺真实证据的方向实现 artifact producers：历史 L2/tick ingestion、depth-driven taker replay、dust/testnet/live calibration ingestion、walk-forward/OOS/regime/cost-stress validation、runtime safety evidence、promotion evidence bundle collector。

---

## 11. Non-negotiable rules

- 不用 approximate/reference_close 长窗口结果做上线结论。
- 不把 REST depth snapshot 当历史 L2 replay。
- 不把 maker/zero-cost sensitivity 当真实可上线收益。
- 不用 30 笔幸存样本推广策略。
- 不真实下单，除非当前轮次用户明确授权具体 testnet/live 操作。
- Gate 结果为 reject 时，结论就是 reject；不能用叙事覆盖 gate。

