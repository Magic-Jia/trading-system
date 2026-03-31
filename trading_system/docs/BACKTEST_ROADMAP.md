# Trading System Backtest Roadmap

这份文档回答 6 个核心问题：

1. 为什么现在必须做回测
2. 回测不该只围绕 `rotation suppression`，但为什么它适合作为第一枪
3. 整套系统应该按什么顺序回测
4. 每一阶段要回答什么问题、产出什么结果
5. 如何避免把回测做成“参数炼丹”
6. 什么时候可以进入下一轮策略开发，什么时候必须先停下来修正

---

## 1. Why backtesting is now mandatory

当前系统已经不是“看几张图、解释几次结果”就够的阶段了。

它已经形成一条会真实改变输出与执行结果的完整链路：

1. `regime`
2. `confidence / aggression`
3. `suppression rules`
4. `universe`
5. `candidate engines`
6. `validator / allocator`
7. `paper execution / lifecycle`

这意味着：

- 任何一层都可能改善收益，也可能只是让系统更复杂
- “逻辑自洽”不等于“长期有边际优势”
- 单次样本看起来合理，不代表多周期、多环境也成立
- 如果没有系统回测，后续所有优化都容易退化成主观解释

**结论：回测已经是主链路，不是附属分析。**

---

## 2. Scope: what the backtest must cover

回测范围不能只盯某一个单点规则。

### 2.1 What should NOT happen

不要把回测缩成：

- 只测 `rotation suppression on/off`
- 只测某个 score threshold
- 只测某个 EMA 条件
- 只看总收益，不看候选漏斗和错杀率

那样最多只能回答一个局部问题，不能回答系统级问题。

### 2.2 What SHOULD happen

回测必须覆盖整条策略链：

1. **Regime layer**
   - 分类本身是否有预测力
   - `confidence / aggression` 是否真能区分“该放大”和“该缩小”

2. **Suppression layer**
   - `rotation` / `trend` / `short` 的桶级压制是否有效
   - 是在避坑，还是在错杀机会

3. **Candidate layer**
   - 各引擎各自有没有 alpha
   - 过滤器是在改善质量，还是在过度阉割信号

4. **Allocation / execution layer**
   - 分配、缩仓、保护逻辑是否提高风险调整后收益
   - execution friction（fee / spread / slippage / funding drag）是否改变结论

5. **Attribution layer**
   - 系统最终收益来自哪一层
   - 哪些规则在制造价值，哪些规则只是让系统看起来更“稳重”

---

## 3. Backtest philosophy

整套回测路线图遵守 5 个原则。

### 3.1 Answer structural questions before tuning parameters

先回答制度问题，再回答参数问题。

顺序必须是：

1. 这条规则方向对不对
2. 这条规则是否有统计价值
3. 只有前两步成立，才允许调阈值

### 3.2 Run ablations, not stories

每次新增判断，都要做 A/B 或 ablation（消融对照）。

不是问：

- “这个规则听起来合理吗？”

而是问：

- “拿掉它以后，收益、回撤、换手、错杀率如何变化？”

### 3.3 Judge by distributions, not screenshots

不要用单次窗口、单次行情、单个样本解释系统。

必须看：

- 多个时间窗口
- 不同市场环境
- 不同波动区间
- 不同拥挤状态

### 3.4 Separate research from optimization

研究阶段回答：

- 有没有边际价值
- 价值从哪里来

优化阶段才回答：

- 0.7 要不要改成 0.65
- score floor 要不要从 0.60 改到 0.58

### 3.5 No forward contamination

任何回测都必须避免：

- 用未来数据算当前信号
- 用后验最佳参数反推历史
- 用同一数据既做发现又做最终结论

最少也要分成：

- 研究样本（in-sample）
- 验证样本（out-of-sample）

如果以后规模够大，再做 rolling / walk-forward（滚动 / 步进）验证。

---

## 4. Backtest order: recommended roadmap

建议分成 6 个阶段，不要乱序。

---

## Phase 0 — Backtest infrastructure and truth baseline

### Goal

先把“怎么测”这件事做对，避免后面一边研究一边修统计口径。

### Questions to answer

- 历史 market / derivatives / account context 数据是否足够完整
- 当前 runtime state 能否反推每一层决策
- 手续费、滑点、funding drag 是否能纳入统一口径
- 回测输出是否能稳定复现

### Required outputs

- 明确的回测输入数据规范
- 明确的信号时间戳与撮合时间戳规范
- 统一指标定义表
- 统一实验命名和结果落盘格式

### Minimum deliverables

1. 历史样本目录结构
2. 固定回测配置 schema
3. 统一绩效指标模块
4. 统一 attribution 输出模块
5. 统一实验摘要模板

### Decision gate

只有当以下条件满足时，才进入 Phase 1：

- 同一输入重复运行结果一致
- 手续费 / 滑点口径固定
- 候选、过滤、分配、执行结果可逐层重建

---

## Phase 1 — Regime predictive power backtest

### Goal

验证 `regime` 不是“解释器”，而是真有分层价值。

### Questions to answer

1. 不同 regime 后续收益分布是否显著不同
2. 不同 regime 后续回撤分布是否显著不同
3. `confidence / aggression` 是否真能把高质量环境和低质量环境区分开
4. `MIXED`、`RISK_OFF`、`RISK_ON_*` 的未来表现是否真的不同

### Core experiments

#### Experiment 1: label predictive power

按每次 regime 打标签后，统计未来：

- 1d
- 3d
- 7d

的：

- major basket return
- alt basket return
- cross-sectional breadth change
- realized volatility
- drawdown

#### Experiment 2: confidence / aggression monotonicity

按 `confidence` 或 `aggression` 分桶，查看：

- 更高 aggression 是否对应更好的未来收益分布
- 更低 aggression 是否对应更差的回撤 / 波动环境

#### Experiment 3: regime stability

查看 regime 是否：

- 过度抖动
- 持续时间太短
- 大量停留在无法区分的模糊区间

### Key metrics

- forward return by regime
- forward drawdown by regime
- realized vol by regime
- regime duration
- regime transition matrix
- calibration of confidence buckets

### What counts as success

至少要看到：

- 各 regime 后续分布有可区分差异
- aggression 分层与收益 / 风险存在大致单调关系
- `MIXED` 不是一个吞掉一切解释的垃圾桶标签

### Failure modes

如果 Phase 1 失败，说明问题可能在：

- regime 定义本身太弱
- confidence 计算没有预测力
- 现在的 suppression / sizing 逻辑缺乏上游统计基础

**如果失败，不应继续盲调 suppression 阈值。**

---

## Phase 2 — Suppression policy backtest

### Goal

验证桶级压制规则是否真的在创造价值。

### Why this is Phase 2, not the whole roadmap

`suppression` 很重要，但它只是 regime 之后的一道闸门。

要先确认 regime 本身有分层价值，再看 suppression 是否把这种分层转化成更好的交易结果。

### Questions to answer

1. `rotation suppression` 是在避坑还是错杀
2. `trend suppression` / `short suppression` 是否有类似价值
3. suppress 和 downsize 的边界是否合理
4. 桶级关停是不是过于粗暴

### Core experiments

#### Experiment 1: current policy vs no suppression

比较：

- 当前 suppression 规则
- 完全不 suppression，只保留 normal / downsize

#### Experiment 2: current policy vs soft suppression

比较：

- 当前：直接禁止某桶出信号
- 软处理：不禁止，但进一步缩仓或提高门槛

#### Experiment 3: threshold sensitivity

比较：

- `aggression < 0.7`
- `aggression < 0.65`
- `aggression < 0.6`

对 `rotation suppression` 的影响。

### Key metrics

- bucket-level PnL
- bucket-level hit rate
- bucket-level payoff ratio
- opportunity kill rate（被抑制但原本盈利的比例）
- avoid-loss rate（被抑制且原本亏损的比例）
- total portfolio return / drawdown / Sharpe / Calmar

### What counts as success

成功不是“总收益更高”这么简单。

更关心：

- 回撤是否显著改善
- 错杀是否小于避坑收益
- suppression 是否真正改善风险调整后收益

### Important note

`rotation suppression` 是第一枪，但不该成为唯一回测对象。

---

## Phase 3 — Candidate engine and filter backtest

### Goal

确认各候选引擎本身有没有 alpha，并拆清每一道过滤器的价值。

### Questions to answer

1. `trend`、`rotation`、`short` 各自是否独立有边际价值
2. 哪些过滤器在提升质量
3. 哪些过滤器在过度减少样本
4. 候选数量下降是否真的换来 payoff 提升

### Required decomposition

每个引擎都要拆成漏斗：

1. raw universe
2. engine eligibility
3. feature gates
4. score floor
5. validator / allocator survival
6. final executed set

### Core experiments

#### Experiment 1: engine standalone viability

分别测：

- trend only
- rotation only
- short only

#### Experiment 2: filter ablations

逐个移除这些过滤器，观察变化：

- absolute strength gate
- trend intact gate
- crowding / derivatives filter
- overheat filter
- score floor
- stop viability gate

#### Experiment 3: funnel attribution

统计每层：

- 进来多少
- 出去多少
- 被砍掉的样本后来表现怎样
- 留下来的样本后来表现怎样

### Key metrics

- hit rate
- expectancy
- average win / average loss
- median trade return
- tail loss
- hold time distribution
- turnover
- filter keep rate / reject rate
- reject bucket forward return distribution

### What counts as success

成功意味着：

- 至少有一个引擎在可接受交易成本下有稳定边际优势
- 至少部分过滤器能被证明不是“形式主义”
- 系统知道自己在哪一层丢掉了机会，在哪一层避开了垃圾交易

---

## Phase 4 — Allocation, sizing, and execution policy backtest

### Goal

验证组合层与执行层不是在“消耗 alpha”。

### Questions to answer

1. `downsize` 是否改善风险调整后收益
2. allocator 是否比简单等权更优
3. bucket targets 是否合理
4. execution friction 是否吞掉策略优势
5. 当前 lifecycle / management 规则是否改善持仓结果

### Core experiments

#### Experiment 1: allocator vs simple baseline

比较：

- 当前 allocator
- 等权配置
- 固定单笔风险配置

#### Experiment 2: policy comparison

比较：

- `normal`
- `downsize`
- `suppress`

在同样信号上的表现差异。

#### Experiment 3: friction stress test

在不同交易成本假设下跑：

- low friction
- baseline friction
- stressed friction

### Key metrics

- net return after costs
- max drawdown
- Sharpe / Sortino / Calmar
- capital efficiency
- concentration
- turnover-adjusted return
- cost drag attribution

### What counts as success

必须证明：

- allocator / downsize 至少没有显著破坏 alpha
- 交易成本下结论仍成立
- 不是“毛收益看起来很漂亮，净收益几乎没了”

---

## Phase 5 — Robustness, walk-forward, and anti-overfit validation

### Goal

验证前面看到的优势不是样本内幻觉。

### Questions to answer

1. 不同年份 / 不同波动阶段结果是否稳定
2. 滚动训练 / 滚动验证下效果是否还能保留
3. 轻微参数扰动后结论是否仍成立
4. 是否存在高度依赖某几段行情的伪优势

### Core experiments

#### Experiment 1: market regime slices

切片对比：

- 强趋势阶段
- 震荡阶段
- 恐慌去杠杆阶段
- 低波动无聊阶段

#### Experiment 2: walk-forward validation

滚动窗口：

- 用前一段样本定规则 / 定阈值
- 用下一段样本验证
- 重复滚动

#### Experiment 3: parameter stability

轻微改动：

- suppression threshold
- score floor
- stop 规则细节
- 强度阈值

看结果是否崩掉。

### Key metrics

- out-of-sample Sharpe / Calmar
- performance dispersion by period
- sensitivity bands
- worst-window performance
- parameter stability score

### What counts as success

成功意味着：

- 优势不是只来自少数窗口
- 参数不是“一碰就死”
- 样本外仍能保留合理比例的边际优势

---

## Phase 6 — Research-to-production promotion gate

### Goal

定义什么时候一项规则可以从研究结论进入正式主系统。

### Promotion requirements

一项新规则或新参数，至少要满足：

1. 有明确研究问题
2. 有 A/B 或 ablation 证据
3. 有样本外验证
4. 有交易成本后结果
5. 有失败条件与回滚条件
6. 有对 runtime / reporting 的可观测输出

### Do NOT promote if

- 只在单一窗口好看
- 只改善总收益，不改善风险调整后收益
- 只靠少数极端赢家支撑
- 解释很多，但 attribution 不清楚
- 样本外显著变差

---

## 5. Recommended execution order for current system

结合当前系统状态，建议按下面顺序执行：

### Step 1
**先做 Phase 0：回测基础设施与口径统一**

### Step 2
**做 Phase 1：Regime predictive power**

这是上游真值检查。
如果这一步不成立，后面 suppression 与 sizing 的讨论都容易失去基础。

### Step 3
**做 Phase 2：Suppression policy backtest**

优先查：

- `rotation suppression`
- 再扩展到其他桶

### Step 4
**做 Phase 3：Candidate engines + filter ablations**

尤其要拆清：

- `rotation` 到底有没有独立 alpha
- `trend` 是否只是吃 beta
- `short` 在当前系统里是不是还只是防守占位

### Step 5
**做 Phase 4：Allocator / execution policy / friction**

### Step 6
**做 Phase 5：样本外与稳健性验证**

### Step 7
**达到 promotion gate 后，才允许把结论写回策略主线**

---

## 6. Backtest scorecard: what every experiment must report

以后每次正式回测，最少都要报下面这些东西。

### 6.1 Experiment metadata

- experiment name
- code / config version
- sample period
- symbols / universe scope
- fee / slippage / funding assumptions
- baseline version
- variant version

### 6.2 Portfolio metrics

- total return
- annualized return
- max drawdown
- Sharpe
- Sortino
- Calmar
- volatility
- turnover

### 6.3 Trade metrics

- trade count
- win rate
- payoff ratio
- expectancy
- median trade return
- p95 loss / tail loss
- average holding time

### 6.4 Attribution metrics

- by regime
- by engine
- by filter
- by suppression decision
- by execution policy
- by cost drag

### 6.5 Decision summary

每次回测最后必须强制给出一句话结论：

- 保留
- 修改
- 下线
- 暂缓，等更多样本

不能只贴图，不下判断。

---

## 7. What to backtest first: current priority list

如果当前资源有限，优先级如下。

### Priority 1 — Regime predictive power

先确认上游分层有没有统计价值。

### Priority 2 — Rotation suppression policy

这是当前最明确、最值钱、最容易做对照实验的单点规则。

### Priority 3 — Rotation engine standalone + filter funnel

如果 suppression 被证明过严，就必须继续拆：

- rotation 自身到底强不强
- 是 suppression 砍掉了机会
- 还是 engine 本来就不够好

### Priority 4 — Downsize / allocator / cost drag

这决定系统是不是把已有 alpha 又磨没了。

### Priority 5 — Walk-forward robustness

确保前面看到的东西不是样本内幻觉。

---

## 8. Anti-patterns: how this roadmap can go wrong

### 8.1 Parameter mining before structural validation

在没证明规则方向成立前，就开始大规模扫参数。

### 8.2 Evaluating only total return

忽略：

- 回撤
- 样本数量
- 成本
- 换手
- 错杀率

### 8.3 Treating suppressions as automatically good

压制规则天然会让曲线看起来更稳，
但那不代表它创造了真正的风险调整后价值。

### 8.4 Ignoring opportunity-kill analysis

只统计“避开了多少亏损”，不统计“错杀了多少本该赚钱的交易”。

### 8.5 Letting one lucky window dominate conclusions

如果结论只靠一段单边行情支撑，就不能进入正式规则。

---

## 9. Bottom line

当前系统需要的不是“做不做回测”的讨论，
而是：

- **按什么顺序回测**
- **每一层要回答什么问题**
- **什么证据足以支持保留 / 修改 / 下线一条规则**

最短结论：

1. 回测必须覆盖 `regime -> suppression -> candidate -> allocation -> execution` 整条链
2. `rotation suppression` 适合作为第一枪，但不该成为唯一对象
3. 先做制度验证，再做参数优化
4. 任何规则升级都必须经过 ablation + 样本外验证 + 成本检验

如果后续要落地执行，建议下一份实施文档直接按本路线图拆成：

- 回测基础设施实施计划
- regime 回测实施计划
- suppression / engine ablation 实施计划
- robustness / walk-forward 实施计划
