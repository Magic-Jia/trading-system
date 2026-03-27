# Strategy Gaps and Upgrade Order

这份文档专门回答 4 个问题：

1. 当前策略结构到底是什么
2. 当前系统的主要缺陷是什么
3. 为什么它仍然太 price-structure-heavy、还不够 crypto-specific
4. 下一步最重要的策略升级顺序是什么

## 1. Current strategy structure

当前系统的真实结构是：

1. `regime`
2. `universe`
3. `trend engine`
4. `rotation engine`
5. `short engine`
6. `allocator`
7. `risk gate`
8. `paper execution`
9. `lifecycle / reporting`

这意味着：

- 系统已经不再只是单引擎 trend 脚本
- 当前问题更多是 **edge 质量** 与 **策略成熟度**，而不是“有没有模块”
- short 已经在结构上占位，但执行与策略质量都还没有完成

## 2. Current defects and limitations

### 2.1 Strategy defects

- `trend` 仍主要依赖 EMA 对齐、阶段涨幅、量能确认
- `rotation` 已经引入 relative strength，但仍偏向价格延续判断
- `short` 目前更像防御型补位，而不是成熟的 crypto short playbook
- `regime` 已经读取 majors derivatives 摘要，但作用还偏粗粒度
- `allocator` 和 `risk gate` 已经有组合框架，但还不够 **edge-aware**：更像风险预算器，而不是把 setup 质量、crowding、流动性、赔率差异翻译成 aggressiveness 的分配器
- 系统还没有明确把 **execution friction**（fee / spread / slippage / funding drag）写进策略层；现在更多是执行约束，而不是 candidate 质量的一部分
- `rotation` 还缺 **turnover / signal stability** 约束，容易在“相对强但不稳定”的候选之间高频切换
- 文档里还没有明确的 **alpha validation discipline**：新特征如何做 ablation、怎么判断它真的提升策略而不是只让规则更复杂，目前没有被提到主线里
- `paper execution` 使主链路可验证，但并不会让策略自动变得更 crypto-native

### 2.2 Operational limitations that still matter

- live execution 仍未打开
- short execution 仍未打通
- stop taxonomy / exit system 仍偏薄，而且当前 entry 体系明显比 exit 体系更成熟
- crash-protection regime 仍未单独建模

## 3. Why the current system is still too price-structure-heavy

### 3.1 Most entries still come from generic structure logic

当前多数候选来自：

- close 与 EMA20 / EMA50 的相对位置
- 近期 return window
- breakout / pullback / breakdown 这些结构描述

这些在股票、外汇、加密里都能用一点，但它们不是这套系统最该拥有的差异化优势。

### 3.2 Derivatives data is still underused

当前系统已经读取 funding、OI、basis、taker ratio，但主要还是：

- 用在 majors 聚合层面的 regime 摘要
- 作为“环境描述”，而不是“具体候选过滤器”

这意味着系统还没有真正回答这些 crypto-native 问题：

- 这个 breakout 是健康趋势延续，还是拥挤 late-long？
- 这个 rotation leader 是真强，还是短期情绪挤上去的假强？
- 这个 short 是顺着去杠杆，还是在 crowded-short 条件下追着送给 squeeze？

### 3.3 Stops and exits are not expressive enough

当前止损和退出更多还是“有总比没有强”的阶段：

- 多个 setup 仍共用相近的 EMA / 结构止损锚点
- lifecycle 仍偏统一阈值推进
- 缺少 crowding unwind、trend fatigue、time-stop、failure exit 等 crypto 常见退出理由

## 4. The new strategy direction

下一步要把系统从：

- **price-structure-first**

推进到：

- **crypto-derivatives-aware**
- **crowding-aware**
- **absolute-strength-aware**
- **setup-aware for stops and exits**

这不是否定现有 regime / trend / rotation / short 架构；
而是给这套架构补上更像 crypto 的判断层。

## 5. Prioritized strategy upgrade order

### Phase 1 — Crypto derivatives + crowding

优先把以下特征推进到 candidate-level：

- funding
- open interest change
- basis
- taker buy / sell imbalance
- 可能的话加入更直接的 overheat / squeeze proxy

目标：

- trend 不再只看结构，也看是否是健康参与还是拥挤追涨
- rotation 不再只看相对强弱，也看是否已进入 crowding / blow-off 区域
- short 不再只看 breakdown，也看是否存在 squeeze / crowded-short 风险

### Phase 2 — Absolute strength + overheat filters

目标：

- 给 longs 增加绝对强弱门槛
- rotation 同时要求“relative strength + absolute strength”
- 新开仓前明确过滤过热、过度扩张、赔率变差的候选

### Phase 3 — Regime crash protection

目标：

- 新增 explicit crash / cascade / squeeze regime
- 在极端环境下强制收缩风险与执行权限
- 让系统能区分“普通 risk-off”与“需要立刻压缩仓位的市场故障态”

原因：

- 在 crypto 里，crowding / derivatives 与 crash / cascade / squeeze 是连在一起的
- 如果这层放得太后，前面的 crowding 判断很难真正转化成风险压缩动作

### Phase 4 — Edge-aware sizing, execution friction, and turnover control

目标：

- 把 candidate quality、crowding、流动性、赔率差异翻译成 aggressiveness
- 明确把 fee / spread / slippage / funding drag 纳入策略层，而不只当执行噪音
- 给 rotation / short 建立 turnover 与 signal stability 控制，避免高换手吞掉 edge

### Phase 5 — Richer stop taxonomy

目标：

- breakout / pullback / rotation / short 各自有不同 stop family
- 明确记录结构止损、波动止损、挤压止损、时间止损、失败止损
- 让 invalidation 不再只是“跌破某条 EMA”

### Phase 6 — Exit system

目标：

- partial take profit
- trailing protection
- break-even promotion
- time-stop
- failure exit
- crowding unwind exit
- regime deterioration exit

### Phase 7 — Short maturity

目标：

- 完成 breakdown short 与 failed-bounce short 的区分
- 引入 squeeze / crowding 过滤
- 让 short 真正具备独立策略逻辑，而不是防守补位

当前状态（2026-03-27）：

- 这个阶段已经先以 **bounded package** 形式落地一包：`short engine` 已区分 `BREAKDOWN_SHORT` / `FAILED_BOUNCE_SHORT`，并加入 crowded-short squeeze risk 过滤。
- short 的 setup-specific stop / invalidation 语义已经进入共享 stop taxonomy，并能在 runtime state / reporting 里直接看到，不再只是 entry-only 变化。
- 这次落地仍然**不包含** live short execution plumbing、交易所 short 下单链路、以及更完整的 execution-safety 配套；这些继续属于 execution-safety track。
- 因为 short entry / stop 语义已经先成熟了一步，而 exit 体系仍偏薄，所以下一包策略建议仍优先回到 **Phase 6 — Exit system**。

### Phase 8 — Strategy evaluation / ablation / attribution

目标：

- 为新增特征建立 ablation discipline，避免规则只增不减
- 区分“提升了 signal quality”与“只是让系统更复杂”
- 让 attribution 聚焦策略 edge，而不是只记录执行流水

## 6. What should stay separate from this strategy order

下面这些依然很重要，但它们不属于策略 edge 升级顺序：

- live execution boundary
- hard execution risk gate
- restart-safe state recovery
- audit trail / journal truth
- short execution plumbing
- operator reporting

这些属于 **execution-safety track**。
它们决定系统能不能安全运行；
而上面的 6 个 phase 才决定系统有没有更强的策略方向。

## 7. What the next review should decide

Step 2 review 应重点确认：

- 这套系统的 edge 是否应该明确转向 crypto derivatives / crowding
- regime crash protection 是否应该前置到 derivatives / overheat 之后
- edge-aware sizing / execution friction / turnover control 是否应该被明确写成一整段主线，而不是散在 allocator 里
- short maturity 已经完成一包受控落地后，下一包是否明确回到 exit system（推荐）
- strategy evaluation / ablation / attribution 是否应该在文档里被单列成最后一个策略阶段
- 这套顺序是否足够清楚，能直接指导 step 3 implementation
