# Failed Follow-Through Exit Design

Date: 2026-04-08
Owner: Claw
Status: Draft revised after written-spec review

## Goal

为自动交易程序补下一刀最小但高价值的 exit rule（退出规则）：
**failed follow-through exit（突破后续无力快退）**。

本文档里的 **B 口径** 明确定义为：
**只有“展开不足 + 结构失守”同时成立，才触发快退。**

目标不是替代已有的 stop（止损）或 time stop（时间止损），而是补一层更前置的职业交易员式认错：
当一笔 breakout / trend continuation（突破 / 趋势延续）交易在早期阶段没有形成最小有效展开，且价格重新跌破 breakout support（突破支撑）时，系统应提前退出，而不是继续死等硬止损或拖到 time stop。

## Why this slice

当前主线已经有：
- fail-fast invalidation（逻辑失效快退）
- time stop（时间止损）
- partial take profit（分批止盈）
- defensive regime de-risk（防守环境降档减仓）

但中间仍缺一层“这笔突破看起来没有走出来，应尽早承认后续无力”的纪律。

这类单在实战里最伤：
- 它们往往没有立即 hit stop（打止损）
- 但也没有形成应有的延续
- 会占用资金、拖慢周转、消耗注意力
- 最后要么拖成垃圾仓位，要么回落后被动离场

从专业交易员角度，这一层优先级高于 break-even（保本保护），因为它更贴近真实亏损来源，也更不容易误杀本来能走成的大单。

## Scope

### In scope

第一版只覆盖：
- **breakout-continuation long（突破延续型做多）** 持仓
- 不覆盖 generic trend（泛趋势）但没有 breakout 语义的 long
- 纸上执行主链的 exit policy（退出策略）
- 明确、可解释、可测试的最小规则

### Out of scope

本刀不做：
- short（做空）侧 follow-through 规则
- break-even / stop-to-breakeven（保本保护）
- runner management（尾仓管理）
- crowding-aware exit（拥挤退出）
- multi-signal score（多信号打分）
- 组合层 portfolio exit（组合层统一退出）

## Rule definition

第一版 failed follow-through exit 固定采用以下四条件同时成立才触发：

1. **早期窗口内**
   - 持仓仍处于定义好的 early window（早期观察窗口）内
   - `bars_since_entry` 采用 **1-based（从 1 开始）** 计数：入场后的第一个完整评估 bar 记为 1
   - 固定表达：`bars_since_entry <= early_window_bars`
   - 边界定义：`bars_since_entry == early_window_bars` 仍算 early（早期窗口内）

2. **尚未达到第一目标位**
   - 若 `first_target_hit = true`，则不触发本规则
   - 理由：已经证明部分延续成立，不应再按“早期后续无力”处理

3. **最小展开不足**
   - 最大有利展开（max favorable excursion, MFE）不足以证明 trade（交易）按预期展开
   - 固定表达：`max_favorable_excursion_r < min_followthrough_r`
   - 边界定义：`max_favorable_excursion_r == min_followthrough_r` 不算“展开不足”，因此不触发

4. **价格跌回 breakout support（突破支撑）下方**
   - 这是“结构失速”信号
   - 第一版固定使用当前系统已有的 `mark_price` 作为判断价格
   - 固定表达：`mark_price < breakout_support`
   - 边界定义：`mark_price == breakout_support` 不算跌破，因此不触发

### Eligibility gate（适用资格）

第一版 failed follow-through 不是对所有 long（做多）仓位生效，而是只对已有 **breakout-continuation** 语义的仓位生效。

最小资格判断固定为：
- `side == "LONG"`
- `invalidation_source` 必须满足精确 v1 谓词：`str(invalidation_source).startswith("trend_breakout_")`

明确纳入：
- `trend_breakout_failure_below_4h_ema20` 及其他同前缀来源

明确排除：
- generic trend 但没有 breakout 语义的来源（例如 `trend_structure_loss_*` 这类泛趋势失效）
- short（做空）仓位
- 没有 `invalidation_source` 或来源语义不明确的仓位

实现边界：
- 第一版允许 `exit_policy` 只做**轻量字符串资格判断**
- 不允许为这一刀新增新的 setup classifier（形态分类器）
- 若仓位不满足该资格门槛，则本规则直接跳过，不触发

只有四条同时成立，才发出 failed follow-through exit 建议。

## Threshold ownership and defaults

第一版为避免配置扩散，核心阈值归属固定如下：

- `early_window_bars = 6`
  - 归属：`exit_policy.py` 内部的保守默认常量
  - 第一版不要求 position metadata（持仓元数据）动态传入
  - 含义：入场后的前 6 个评估 bar（K 线）仍视为 early window（早期窗口）

- `min_followthrough_r = 0.25`
  - 归属：`exit_policy.py` 内部的保守默认常量
  - 第一版同样不做外部配置化
  - 含义：若最大有利展开连 0.25R 都没有达到，则视为“最小展开不足”

选择这组保守默认值的原因：
- 比 time stop（时间止损）更早，但还不至于把正常回踩的强单过早赶出去
- 0.25R 只是“是否有最小有效跟随”的门槛，不是要求趋势单必须立即大幅盈利
- 这一刀是最小退出纪律补丁，不应先引入新的全局配置面
- 先把行为、优先级与测试边界钉死，比先做参数体系更重要

这样 planner（实现计划）可以把当前切片收敛在 `exit_policy` + 测试层，而不是额外扩展配置系统

## Position in rule priority

该规则应放在：

1. **硬失效 / 原有 invalidation（逻辑失效）** 之后
2. **time stop（时间止损）** 之前

优先级顺序固定为：
- stop / thesis invalidation（止损 / 逻辑失效）
- failed follow-through（突破后续无力快退）
- time stop（时间止损）
- 其他更慢的退出纪律

同一评估周期的冲突规则固定为：
- 若 `thesis_invalidation` 成立，则直接输出 invalidation exit，不再评估 failed follow-through
- 若 `failed_followthrough` 成立，则它在同轮里是**排他型退出**：
  - 抑制 `time_stop`
  - 抑制 `defensive_regime_de_risk`
- 第一版不允许同一轮同时输出 `failed_followthrough` 与 `time_stop` / `defensive_regime_de_risk`

理由：
- 如果已经正式失效，不需要再说“后续无力”
- failed follow-through 是更前置的“早期认错”
- time stop 是更后置的“拖久了还没走出来再清仓”

## Data / metadata expectations

第一版需要 exit policy 读取以下输入：
- `bars_since_entry`
- `first_target_hit`
- `max_favorable_excursion_r`
- `breakout_support`
- `mark_price`
- `invalidation_source`

最小接口边界固定如下：

- `exit_policy` **只消费，不负责重新推导 breakout 结构**
  - 也就是说，`exit_policy` 不在这一刀里自行回看历史 K 线推断 `breakout_support`
  - 它只读取 position / lifecycle 已经提供好的数值字段

- 上游职责
  - `bars_since_entry`、`first_target_hit`、`max_favorable_excursion_r` 继续沿用现有持仓生命周期数据来源
  - `breakout_support` 的权威来源固定为：**position payload（持仓载荷）上的 `breakout_support` 数值字段**
  - 该字段由**开仓时的持仓快照组装层（position snapshot / lifecycle assembler）**负责写入 `position["breakout_support"]`
  - 对本规则而言，`breakout_support` 是**entry/setup time（入场/建仓时）冻结的结构参考值**，后续 lifecycle 不允许在持仓过程中漂移更新该字段

- 第一版不接受的替代方案
  - 不在 `exit_policy` 内复用 `stop_loss` 冒充 `breakout_support`
  - 不在 `exit_policy` 内临时用其他 reference anchor（参考锚点）推导近似值

- 本刀不允许做的事
  - 不在 `exit_policy` 内新增通用结构识别器
  - 不为了这一刀把整个 position schema（持仓结构）重构
  - 不扩展到更泛的 setup taxonomy（形态分类体系）

### Evaluation order note

- `first_target_hit` 读取的是**当前评估周期里已经更新后的 lifecycle 状态**
- 也就是说，若同一评估轮次里上游已先确认第一目标位命中，则本规则应把该仓位视为 `first_target_hit = true`，直接不触发 failed follow-through

### Missing / invalid data behavior

第一版缺字段时采用**安全降级，不触发**原则：

- 若 `breakout_support` 缺失 → 本规则跳过，不报错，不触发
- 若 `max_favorable_excursion_r` 缺失或不是有效数值 → 本规则跳过，不触发
- 若 `first_target_hit` 缺失 → 视为本规则输入不完整，跳过，不触发
- 若 `bars_since_entry` 缺失或非法 → 跳过，不触发
- 若 `mark_price` 缺失或非法 → 跳过，不触发
- 若 `invalidation_source` 不具备 breakout / continuation 语义 → 跳过，不触发

原因：
- exit 规则在生产里应优先保证安全，不因元数据缺口制造错误清仓
- 这一刀的目标是补一条高价值规则，不是顺手引入新的强校验失败面

测试层必须覆盖至少一条“关键字段缺失时安全跳过”的场景。

## Required output semantics

新增退出建议时，输出必须清楚区分于 invalidation 和 time stop。

第一版强制输出契约固定为：
- `action = "EXIT"`
- `qty_fraction = 1.0`
- `priority = "MEDIUM"`
- reason / category（原因 / 分类）固定标识为 `failed_followthrough`
- `meta.exit_trigger = "failed_followthrough"`
- `reference_price` 固定使用当前 `mark_price`
- explanation（解释）需是人话，可直接进入后续报告层

建议的人话说明：

> 这笔突破在早期阶段没有形成最小有效展开，且价格已跌回突破支撑下方，说明延续失败，建议提前退出。

第一版要求的最小元数据为：
- `exit_trigger = "failed_followthrough"`
- `bars_since_entry`
- `early_window_bars`
- `max_favorable_excursion_r`
- `min_followthrough_r`
- `breakout_support`
- `mark_price`

## Testing strategy

第一版最少覆盖以下场景：

### Should trigger

- 处于早期窗口内
- 未达到第一目标位
- 最大展开不足
- `mark_price` 已跌回 breakout support 下方
- 预期：触发 failed follow-through exit

### Should not trigger

1. **已明显展开**
   - 早期窗口内，但已经达到最小展开门槛
   - 预期：不触发

2. **结构尚未失守**
   - 展开不足，但 `mark_price` 仍在 breakout support 上方
   - 预期：不触发

3. **已达到第一目标位**
   - 即便后来回落，也不再按该规则处理
   - 预期：不触发

4. **已有更高优先级 invalidation**
   - 应优先走原有逻辑失效路径
   - 预期：不和 failed follow-through 产生错误优先级冲突

5. **字段缺失或非法**
   - 如 `breakout_support` 缺失、MFE 不是数值、`first_target_hit` 不可用
   - 预期：安全跳过，不触发

6. **同轮出现 defensive de-risk 条件**
   - 当 failed follow-through 已成立时，即便 defensive regime 也成立
   - 预期：只输出 failed follow-through，不叠加 defensive de-risk

7. **同轮出现 time stop 条件**
   - 当 failed follow-through 与 time stop 同时满足
   - 预期：只输出 failed follow-through，不叠加 time stop

### Boundary checks

测试里应显式写死以下边界：
- `bars_since_entry == early_window_bars` → 仍属于 early window（可继续评估）
- `max_favorable_excursion_r == min_followthrough_r` → 不算展开不足，因此不触发
- `mark_price == breakout_support` → 不算跌破，因此不触发
- `bars_since_entry > early_window_bars` → 超出早期窗口，因此不触发

## Minimal implementation plan boundary

第一刀代码改动尽量控制在：
- `trading_system/app/portfolio/exit_policy.py`
- `trading_system/tests/test_exit_policy.py`
- 若确实需要，再最小补一处 metadata 输入来源

不把这刀扩展成：
- 新的通用 price-structure 引擎
- 新的多级 target 系统
- 新的 break-even 管理器
- 新的组合层退出框架

## Recommended approach

本设计明确推荐：

**B 口径（只有“展开不足 + 结构失守”同时成立才快退） + 结构失守型 failed follow-through 规则**

也就是：
- 不做纯时间版
- 不做只要跌回去就退的粗暴版
- 不做多条件打分版
- 而是做“展开不足 + 结构失守”双条件触发

这是当前最符合专业交易员直觉、同时又能保持实现边界收敛的方案。

## Success criteria

当本刀完成时，应满足：
- exit policy 能新增一条 failed follow-through 建议
- 这条建议与 invalidation / time stop 的边界清楚
- 测试能证明触发和不触发场景
- 改动保持在小包范围
- 为后续 runner management / break-even / crowding-aware exit 打下更合理的退出层次
