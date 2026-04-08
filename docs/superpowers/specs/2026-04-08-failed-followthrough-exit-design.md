# Failed Follow-Through Exit Design

Date: 2026-04-08
Owner: Claw
Status: Draft approved in chat, awaiting written-spec review

## Goal

为自动交易程序补下一刀最小但高价值的 exit rule（退出规则）：
**failed follow-through exit（突破后续无力快退）**。

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
- trend / breakout 风格持仓
- long（做多）方向
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
   - 示例表达：`bars_since_entry <= early_window_bars`

2. **尚未达到第一目标位**
   - 若 `first_target_hit = true`，则不触发本规则
   - 理由：已经证明部分延续成立，不应再按“早期后续无力”处理

3. **最小展开不足**
   - 最大有利展开（max favorable excursion, MFE）不足以证明 trade（交易）按预期展开
   - 示例表达：`max_favorable_excursion_r < min_followthrough_r`

4. **价格跌回 breakout support（突破支撑）下方**
   - 这是“结构失速”信号
   - 示例表达：`close < breakout_support`

只有四条同时成立，才发出 failed follow-through exit 建议。

## Position in rule priority

该规则应放在：

1. **硬失效 / 原有 invalidation（逻辑失效）** 之后
2. **time stop（时间止损）** 之前

优先级顺序建议：
- stop / thesis invalidation（止损 / 逻辑失效）
- failed follow-through（突破后续无力快退）
- time stop（时间止损）
- 其他更慢的退出纪律

理由：
- 如果已经正式失效，不需要再说“后续无力”
- failed follow-through 是更前置的“早期认错”
- time stop 是更后置的“拖久了还没走出来再清仓”

## Data / metadata expectations

第一版需要 exit policy 能读到或构造以下信息：
- `bars_since_entry`
- `first_target_hit`
- `max_favorable_excursion_r`
- `breakout_support`
- 当前价格 / bar close

如果现有 position / lifecycle metadata（元数据）里缺少 `breakout_support`，则这一刀的最小实现必须优先评估：
- 是否已有可复用字段
- 若没有，是否可以通过最小扩展在不大面积扩散的前提下补入

原则：
- 优先复用现有字段
- 若必须补字段，也只补到能支撑这条规则为止
- 不顺手扩大到更泛的结构分析系统

## Suggested output semantics

新增退出建议时，输出应清楚区分于 invalidation 和 time stop。

建议：
- action（动作）仍走现有主动退出建议路径
- reason / category（原因 / 分类）明确标识为 `failed_followthrough` 或等价枚举
- explanation（解释）需是人话，可直接进入后续报告层

建议的人话说明：

> 这笔突破在早期阶段没有形成最小有效展开，且价格已跌回突破支撑下方，说明延续失败，建议提前退出。

元数据建议至少带：
- `bars_since_entry`
- `early_window_bars`
- `max_favorable_excursion_r`
- `min_followthrough_r`
- `breakout_support`
- `close`

## Testing strategy

第一版最少覆盖以下场景：

### Should trigger

- 处于早期窗口内
- 未达到第一目标位
- 最大展开不足
- close 已跌回 breakout support 下方
- 预期：触发 failed follow-through exit

### Should not trigger

1. **已明显展开**
   - 早期窗口内，但已经达到最小展开门槛
   - 预期：不触发

2. **结构尚未失守**
   - 展开不足，但仍在 breakout support 上方
   - 预期：不触发

3. **已达到第一目标位**
   - 即便后来回落，也不再按该规则处理
   - 预期：不触发

4. **已有更高优先级 invalidation**
   - 应优先走原有逻辑失效路径
   - 预期：不和 failed follow-through 产生错误优先级冲突

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

**B 口径 + 结构失守型 failed follow-through 规则**

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
