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

- [ ] 任一回测窗口内 L2/tick coverage ≥ 99%。
- [ ] 所有缺口可定位到 symbol/timeframe。
- [ ] 有自动化 data quality report。
- [ ] 数据缺失不能被静默 fallback 成 close/reference price。

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

- [ ] Taker cost 不再只靠固定 bps，必须支持 depth-driven fill。
- [ ] Maker 假设必须有真实 calibration records 支撑。
- [ ] fill-rate、partial-fill、missed-fill 与真实记录误差在阈值内。
- [ ] maker/low-cost sensitivity 不能单独作为上线依据。

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
- [ ] 主结果中 ambiguous exit trade 占比低于阈值。
- [ ] 每笔 live-candidate trade 都能生成 path replay artifact。

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
   - no favorable excursion；
   - cost-flipped winner；
   - small edge eaten by cost；
   - false breakdown；
   - squeeze/crowding；
   - regime mismatch；
   - exit/path ambiguity。
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

- [ ] 每个要上线的 setup 有足够样本。
- [ ] 单一 setup 或单一 symbol 不能支配全部收益。
- [ ] failure taxonomy 可解释主要亏损来源。
- [ ] 已证伪 setup 默认 quarantine，除非重写后重新通过 OOS gate。

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

- [ ] OOS 不显著塌陷。
- [ ] 多 regime 不是单点幸存。
- [ ] cost stress 后仍有边际价值。
- [ ] 参数没有 forward contamination。

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

- [ ] 所有 kill switch 有 dry-run/test 覆盖。
- [ ] 每次下单前后都有 position/order reconciliation。
- [ ] 无法 reconcile 时必须 fail closed。
- [ ] live dust 前不得允许 scale-up。

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

- [ ] 每笔 live trade 有完整 ledger。
- [ ] runtime 输出能解释为什么入场、为什么拒绝、为什么退出。
- [ ] live/backtest drift 超阈值自动降级或停机。

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

## 10. Non-negotiable rules

- 不用 approximate/reference_close 长窗口结果做上线结论。
- 不把 REST depth snapshot 当历史 L2 replay。
- 不把 maker/zero-cost sensitivity 当真实可上线收益。
- 不用 30 笔幸存样本推广策略。
- 不真实下单，除非当前轮次用户明确授权具体 testnet/live 操作。
- Gate 结果为 reject 时，结论就是 reject；不能用叙事覆盖 gate。

