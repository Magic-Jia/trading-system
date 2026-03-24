# Trading System Roadmap — Execution-Safety Track + Strategy-Development Track

> Goal: rewrite the roadmap so future work does not confuse “safe automation plumbing” with “actual strategy edge.”

## 1. Current system snapshot

当前系统已经形成一条完整但仍是 **partial v2** 的纸面主链路：

1. `regime`
2. `universe`
3. `trend engine`
4. `rotation engine`
5. `short engine`
6. `validator / allocator / risk gate`
7. `paper execution`
8. `lifecycle / reporting / runtime state`

当前真实边界：

- live execution 仍未启用；系统本质上仍是 paper-first
- short 候选已经生成、评分、分配并写入 runtime state，但 short 执行仍未打通
- allocator / guardrails / lifecycle 已有骨架，但策略成熟度并未跟上模块数量
- `partial_v2_coverage=true` 依然是正确标签：结构比过去完整，但还不能假装它已经是成熟策略系统

## 2. Current strategy defects

### 2.1 The system is still too price-structure-heavy

当前大多数判断仍然来自：

- EMA 堆叠关系
- 近 24h / 3d / 7d 涨跌幅
- breakout / pullback / breakdown 这些价格结构形态
- 以 EMA 为主的默认止损锚点

这些规则在任何趋势市场都能工作一点，但它们还不够 **crypto-specific**。

### 2.2 What is still missing from a crypto-native system

- 衍生品特征主要还停留在 majors 聚合级别的 regime 摘要，而不是 candidate-level 决策输入
- crowding / positioning 没有真正进入 trend、rotation、short 的过滤逻辑
- longs 缺少单独的 **absolute strength** 门槛，rotation 也还没有同时要求“相对强 + 绝对强”
- 缺少明确的 **overheat filters**，无法系统性回避晚段扩张、情绪挤兑、追高拥挤
- 止损体系仍偏单一，exit 体系仍偏通用，缺少 setup-aware taxonomy
- short engine 目前只是“防守型占位”，不是成熟的 crypto downside engine
- regime 层没有单独建模 crash / cascade / squeeze 这类会要求立刻压缩风险的极端环境

## 3. Two separate roadmaps

后续实现必须分成两条主线：

1. **Execution-safety track**：解决“系统能否安全自动运行”
2. **Strategy-development track**：解决“系统到底该交易什么、凭什么有边际优势”

这两条主线不能再混在一起排序。

---

## 4. Track A — Execution-safety roadmap

这条线决定系统是否能安全接近真实资金环境。
它不是策略 edge，但它是任何真实执行前的前置条件。

### A1. Real execution boundary and mode separation

- 明确 paper / dry-run / live 的隔离边界
- 禁止任何“隐式 live”路径
- 把执行权限升级成显式配置与显式测试对象

### A2. Hard risk gate before execution

- 在 execution 前加入账户级 reject 逻辑
- 补足 aggregate risk、directional exposure、kill-switch、restart-safe exposure checks
- 让 allocator 之后仍有最后一道 execution risk veto

### A3. Restart-safe state recovery and idempotent replay

- 补齐 crash window、重启恢复、重复执行防护
- 让真实 side effect 与 durable state 的恢复路径一致
- 明确哪些动作在重启后必须 replay、哪些必须 hard stop

### A4. Journal / audit minimum viable truth trail

- 让 entry / stop / target / invalidation / execution result 都有可追溯落盘
- 保证出问题时可以重建“为什么做、做了什么、结果怎样”

### A5. Short execution chain end-to-end

- 这一步属于 execution-safety 线，而不是策略线
- 前提不是“已经有 short candidates”，而是“short 执行、保护单、恢复路径、日志都完整可控”

### A6. Lifecycle and operator reporting completion

- 完成 protective order、management preview、operator summary、incident response 文档
- 目标是让系统不仅能跑，还能被安全接管和排障

**Execution-safety order:**

1. `A1` real execution boundary
2. `A2` hard risk gate
3. `A3` restart-safe recovery
4. `A4` audit trail
5. `A5` short execution chain
6. `A6` lifecycle / operator reporting

---

## 5. Track B — Strategy-development roadmap

这条线决定系统下一步的真实策略方向。
它必须与 execution-safety 线分开评审、分开排期。

### B1. Crypto derivatives and crowding as first-class features

**Objective:** 先解决“为什么它还不像一个 crypto system”。

**Required upgrades:**

- 把 funding / OI change / basis / taker imbalance 从 regime 摘要推进到 candidate-level
- 先覆盖 majors，再覆盖 rotation，再覆盖 short
- 为 trend / rotation / short 分别定义 crowded-long、crowded-short、healthy participation、squeeze-risk 等状态
- 在 runtime summary 中暴露被这些过滤器拦下的原因

**Why first:**

- 这是当前系统与 crypto-native edge 之间最大的结构性缺口
- 不先做这层，后面的 stop / exit / short 仍会建立在过于泛化的价格结构上

### B2. Absolute strength and overheat filters

**Objective:** long 不能只看 relative strength，也不能只看“长得像趋势”。

**Required upgrades:**

- 给 trend / rotation longs 增加独立的 absolute strength floor
- rotation 需要同时满足 relative strength leadership 与 absolute trend health
- 增加 overheat / late-stage extension 过滤：价格扩张过快、funding / basis 过热、单日冲高过猛、追高赔率变差时拒绝新开仓

**Why second:**

- 这一步直接解决“强，但已经太热”和“相对强，但绝对并不健康”的问题

### B3. Richer stop taxonomy

**Objective:** 不同 setup 必须有不同的无效化定义，而不是继续共用单一 EMA 风格止损。

**Required upgrades:**

- breakout、pullback、rotation、short 分别定义 stop families
- 区分 structure stop、volatility stop、squeeze stop、time stop、failure stop
- 让候选与 runtime state 明确记录 stop taxonomy 与 invalidation reason

**Why third:**

- strategy edge 不是只有 entry；真正决定赔率的是 entry 与 invalidation 的匹配方式

### B4. Exit system

**Objective:** 用 setup-aware exits 替代当前过于通用的 lifecycle 阈值驱动。

**Required upgrades:**

- partial take profit、trail、break-even、time-stop、trend fatigue exit、crowding unwind exit
- exit 逻辑按 engine / setup type 区分，而不是只有统一的 lifecycle 状态机
- regime deterioration 进入 exit 决策，而不是只影响新仓 aggressiveness

**Why fourth:**

- 当前系统的退出还不足以承载真正的 crypto trend / rotation / short 行为差异

### B5. Short maturity

**Objective:** 让 short 从“防御型补位”升级成成熟的下跌参与子系统。

**Required upgrades:**

- 区分 breakdown short 与 failed-bounce short
- 加入 squeeze-risk / short-crowding 过滤
- 用 derivatives + overheat + absolute weakness 来确认空头赔率
- 先把 short thesis 做成熟，再谈放开 short execution

**Why fifth:**

- short 在 crypto 中最容易被 squeeze；没有足够成熟度，执行链越完整反而越危险

### B6. Regime crash protection

**Objective:** 让 regime 层能识别真正需要“急速压风险”的环境，而不只是普通 risk-off。

**Required upgrades:**

- 增加 explicit crash / cascade / squeeze regime
- 引入暴跌、去杠杆、异常波动、资金费率极端化时的 exposure compression
- 对新仓、加仓、移动止损、被动持有给出不同级别的强制限制

**Why sixth:**

- 这是系统从“普通策略框架”走向“能理解 crypto 极端环境”的关键一步

### B7. Replay / attribution after the strategy stack is clearer

**Objective:** 等前 6 步明确后，再补 replay / attribution 才不会评估一套方向仍不稳定的系统。

---

## 6. Recommended development order

### Execution-safety order

1. `A1` real execution boundary
2. `A2` hard risk gate
3. `A3` restart-safe recovery
4. `A4` audit trail
5. `A5` short execution chain
6. `A6` lifecycle / operator reporting

### Strategy-development order

1. `B1` crypto derivatives + crowding
2. `B2` absolute strength + overheat filters
3. `B3` richer stop taxonomy
4. `B4` exit system
5. `B5` short maturity
6. `B6` regime crash protection
7. `B7` replay / attribution

## 7. Review focus for the next user checkpoint

Step 2 review 应重点确认：

- 这套系统是否真的要从“price-structure-first”转向“crypto-derivatives-aware”
- `B1 -> B6` 的顺序是否符合老板对 edge 来源的判断
- short 是否应该继续排在 stop / exit 之后，而不是提前变成主线
- regime crash protection 是否应该在 short maturity 前置，或保持当前顺序
- execution-safety 与 strategy-development 这两条线是否已经切分清楚
