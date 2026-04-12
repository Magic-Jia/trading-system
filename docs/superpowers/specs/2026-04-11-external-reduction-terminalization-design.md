# External Reduction Terminalization Design

Date: 2026-04-11
Owner: Claw
Status: Draft for written-spec review

## Goal

为现有 `target-scaleout-runner`（目标分批止盈 / runner 管理）主线补齐一个高风险缺口：
**当真实仓位被外部减仓改变后，系统如何把 first target（第一目标）/ second target（第二目标）/ runner（尾仓）状态安全收口，而不重复发动作、不伪造命中、不污染冻结目标位。**

这次设计是对既有 target-management（目标管理）规则的增量补充，不改写既有目标位定义，只补“外部减仓后的终态一致性”。

## Why this slice first

当前最危险的失真不是“目标位算错一档”，而是：
- 真实仓位已经被人工 / 外部系统减掉
- 但程序内部阶段状态仍停留在 `pending`
- 后续评估继续生成新的 partial take profit（部分止盈）/ runner 动作
- 最终出现重复减仓、过度减仓、状态与真实仓位脱节

因此这次优先级高于单纯的报表优化或次级字段整理。

## Selected approach

本次固定采用 **方案 B：保守终态型（conservative terminalization）**。

### Rejected alternatives

1. **激进重算型**
   - 每次同步后按真实仓位直接重算各阶段
   - 拒绝原因：会冲掉已冻结 target state（目标状态）和历史审计语义

2. **纯告警型**
   - 只告警，不自动收口状态
   - 拒绝原因：不能阻止后续重复动作，交易风险仍然暴露

## Scope

### In scope

- LONG（多头）与 SHORT（空头）都覆盖
- 账户同步 / 状态回写后，基于真实剩余仓位做 external reduction（外部减仓）终态判断
- first target / second target / runner 三段的顺序收口
- `satisfied_by_external_reduction` 终态的写入规则
- 阻止后续重复 partial / runner 动作
- 审计 / 报表中区分“价格命中完成”与“外部减仓完成”
- 数量容差（tolerance，容差）与步长（step size，步长）下的防误判

### Out of scope

- 重算 first / second target price（第一 / 第二目标价）
- 因外部减仓而重写 `original_position_qty`
- 把外部减仓伪装成策略自己执行成功
- 新增第三目标、动态 trailing（动态跟踪）或新的仓位管理形态
- 改写既有 thesis invalidation（逻辑失效退出）或价格命中逻辑

## Design principles

1. **真实仓位优先，但冻结目标位不漂移**
   - 账户同步提供“真实剩余仓位”事实
   - 已冻结的 `first_target_price` / `second_target_price` 不因外部减仓而改写

2. **外部减仓不等于目标命中**
   - `satisfied_by_external_reduction` 是独立终态
   - 不能把它写成 `target_hit`
   - 不能补造“价格已经命中”的叙事

3. **只按数量收口，不按方向猜测**
   - LONG / SHORT 在这条规则上尽量共享一套数量驱动逻辑
   - 方向差异留给既有价格命中逻辑处理

4. **顺序收口，不跳级**
   - 永远先看第一阶段，再看第二阶段，最后看 runner
   - 不允许出现第二阶段已终态、第一阶段仍 `pending` 的脏状态

5. **证据不足时宁可保守，不乱补**
   - 脏数据、延迟、舍入抖动、异常放大仓位等场景下，不脑补成功

## Canonical state rules

### Trigger point

external reduction terminalization（外部减仓终态收口）只允许发生在：
- `sync_positions_from_account`（账户同步）
- 或其它等价的“已拿到真实剩余仓位”的状态回写路径

并且时序固定为：
1. 读取真实仓位快照
2. 做数量归一化（step size / 最小数量口径）
3. 执行 external terminalization pass（外部减仓终态收口）
4. 清理本轮已变成终态的 target / runner 待执行意图
5. 再让 `exit_policy`（退出策略）消费 canonical state（规范状态）

不允许在下单意图生成之后、但未重新消费 canonical state 之前，再偷偷补一轮 external terminalization。

### Quantity basis

本次判定使用以下规范数量：

- **数量符号约定（sign convention，符号约定）**
  - LONG / SHORT 在 external reduction 这条规则上，一律先做 side normalization（方向归一化）
  - 进入阈值公式的都是 **绝对仓位大小**，不是带正负号的 signed qty（有符号数量）
  - 若底层仓位在 SHORT 场景里存成负数，必须先取绝对值再参与本 spec 的全部计算

- `normalized_original_qty`
  - `original_position_qty` 的绝对值按 symbol step size（品种步长）向下归一化后的数量
- `raw_remaining_abs_qty`
  - 当前真实剩余仓位先取绝对值、但尚未按步长归一化的原始数量
- `normalized_remaining_qty`
  - `raw_remaining_abs_qty` 按同一步长向下归一化后的数量
- `stage_target_qty(stage)`
  - 每阶段理论应减数量，按 `normalized_original_qty * stage_fraction` 后再按步长向下归一化
- `cumulative_remaining_threshold(stage)`
  - 若该阶段及其之前阶段都已完成后，理论上最多允许剩余的仓位数量
  - first stage 后：`normalized_original_qty - stage_target_qty(first)`
  - second stage 后：`normalized_original_qty - stage_target_qty(first) - stage_target_qty(second)`
  - runner 终态后：`0`
- `min_actionable_qty`
  - 用于判断剩余仓位是否仍足以合法保留 runner（尾仓）的最小可动作数量
  - 定义为：交易所 `min_order_qty`（若有）与一个 step size（一步步长）两者中的较大值
- `qty_epsilon`
  - 数量容差
  - 定义为：`max(step_size / 2, dust_buffer)`；若当前代码没有独立 `dust_buffer`，第一版固定取 `step_size / 2`

### Zero-sized stage rule

若任一阶段出现：
- `stage_target_qty(first) <= qty_epsilon`，或
- `stage_target_qty(second) <= qty_epsilon`

则该仓位的 target / runner 管理视为 **non-actionable due to size（因仓位过小不可动作）**，并进入冻结路径，而不是自动判成 external reduction 成功。

冻结后要求：
- 不生成新的 first / second / runner 动作
- 不把任何阶段写成 `filled` 或 `satisfied_by_external_reduction`
- 记录机器可读原因：`stage_qty_rounded_to_zero`
- 报表 / 审计上明确显示这是“小仓位 / 粗步长导致不可执行”，不是目标已完成

### Terminal status set

本 spec 在顺序收口里承认的**唯一合法正式终态**固定为：
- `filled`
- `satisfied_by_external_reduction`

顺序门控（first → second → runner）只认这两个状态。

若实现里已经存在其他类似终态名称（如 canceled / inactive / skipped 等），在本 slice 中处理规则固定为：
- 不能直接拿来当顺序门控终态使用
- 若 active position（活动持仓）上出现它们，默认按 abnormal state（异常状态）处理并进入冻结路径
- 只有 `filled` 与 `satisfied_by_external_reduction` 能驱动后续阶段继续评估

### Hard predicate

阶段被 external reduction（外部减仓）满足的硬判定固定为：

- first stage 已被 external reduction 满足，当且仅当：
  - 该阶段当前不是正式终态；且
  - `raw_remaining_abs_qty <= cumulative_remaining_threshold(first) + qty_epsilon`

- second stage 已被 external reduction 满足，当且仅当：
  - first stage 已经是正式终态；且
  - second stage 当前不是正式终态；且
  - `raw_remaining_abs_qty <= cumulative_remaining_threshold(second) + qty_epsilon`

- runner 已被 external reduction 关闭，当且仅当：
  - first / second 都已经是正式终态；且
  - `raw_remaining_abs_qty <= qty_epsilon`

- runner 只剩 dust（碎量，不能再合法动作），当且仅当：
  - first / second 都已经是正式终态；且
  - `qty_epsilon < raw_remaining_abs_qty < min_actionable_qty`

### Role of `*_target_filled_qty`

`first_target_filled_qty` / `second_target_filled_qty` **不参与** external reduction 的核心阈值公式；
它们的职责固定为：
- 保留历史上“系统自己已经成交过多少”的审计事实
- 判定该阶段是否已是 `filled` 等正式终态，避免被改写成 external reduction
- 在阶段“部分成交后又被外部减仓补齐”的场景中，保留已有部分成交量，不清零、不补造额外 filled qty

也就是说：
- external reduction 是否成立，按 **累计剩余仓位阈值** 判定
- 历史已成交多少，按 `*_target_filled_qty` 保留
- 二者不能互相覆盖

### Stage terminalization order

#### 1. First target terminalization

当以下条件同时满足时，first target（第一目标）可从 `pending` 收口为 `satisfied_by_external_reduction`：
- 该阶段当前尚未是正式终态
- `normalized_remaining_qty <= cumulative_remaining_threshold(first) + qty_epsilon`

收口后必须满足：
- `first_target_status = "satisfied_by_external_reduction"`
- `first_target_hit = false`
- `first_target_filled_qty` 保持原值，不补造到阶段目标量
- 不再生成该阶段新的 `PARTIAL_TAKE_PROFIT`
- 不重写 `first_target_price`

#### 2. Second target terminalization

只有在第一阶段已经是正式终态后，才允许评估 second target（第二目标）。

若以下条件同时满足：
- second 当前尚未是正式终态
- `normalized_remaining_qty <= cumulative_remaining_threshold(second) + qty_epsilon`

则：
- `second_target_status = "satisfied_by_external_reduction"`
- `second_target_hit = false`
- `second_target_filled_qty` 保持原值，不补造到阶段目标量
- 不再生成第二阶段新的 partial

#### 3. Partial-filled then externally-reduced rule

若某阶段此前已经部分成交，但尚未 `filled`，之后又发生外部减仓，则：
- 仍然按上面的累计剩余仓位阈值判断是否可收口为 `satisfied_by_external_reduction`
- 该阶段已有的 `*_target_filled_qty` 保留原值
- 不得把该阶段改写成 `filled`
- 不得把“外部补齐的剩余量”塞进 `*_target_filled_qty`

这条规则对 first / second 两阶段都适用。

#### 4. Runner terminalization

runner 的合同固定为：

- 若 `raw_remaining_abs_qty <= qty_epsilon`
  - 视为真实仓位已清空
  - 写回：
    - `runner_protected = false`
    - `runner_stop_price = null`
    - `remaining_position_qty = 0`
    - `runner_terminal_reason = "external_reduction_flat"`
  - 报表语义固定为“runner 被外部减仓彻底结束”
  - 且不再允许新的 runner 动作

- 若 `qty_epsilon < raw_remaining_abs_qty < min_actionable_qty`
  - 视为只剩 dust runner（碎量尾仓）
  - 写回：
    - `runner_protected = false`
    - `runner_stop_price = null`
    - `remaining_position_qty` 保留该 dust 数量本身，不强行写成 `0`
  - 审计原因固定标记为 `runner_terminal_reason = "external_reduction_dust"`
  - 报表语义固定为“仍有碎量残仓，但不可再合法发 runner 动作”，不是 flat（已清仓）
  - 不再允许新的 runner 动作

- 若 `normalized_remaining_qty >= min_actionable_qty`
  - 视为仍有合法 runner 余量
  - 不因 external reduction 自动补写新的 `runner_protected`
  - 不猜测新的 `runner_stop_price`
  - 只保留当前真实存在的 runner 状态

### Canonical postconditions

| 场景 | first | second | runner | 允许后续动作 |
|---|---|---|---|---|
| 剩余仓位 `<= qty_epsilon` | 若未终态则顺序收口到 external reduction | 若未终态则顺序收口到 external reduction | 清空并关闭保护字段 | 不允许任何新的 target / runner 动作 |
| 只剩 dust runner | first / second 顺序收口完成 | first / second 顺序收口完成 | 关闭保护字段，记录 dust 原因 | 不允许新的 runner 动作 |
| 仍有合法 runner 余量 | 已完成阶段按顺序收口 | 已完成阶段按顺序收口 | 保留真实 runner，不补造保护 | 只允许与真实 remaining 匹配的后续逻辑 |
| 阶段部分成交后又外部减仓 | 可收口为 external reduction，但保留已有 filled qty | 同左 | 按真实剩余判断 | 不允许对已收口阶段重复发 partial |
| 重复 sync 同一真实仓位 | 幂等，不重复改写终态 | 幂等，不重复改写终态 | 幂等 | 不重复生成被抑制的动作 |

## Tolerance and normalization rules

为了避免误判，本次设计要求 external reduction 判断复用现有数量归一化口径：
- 与 symbol step size（品种步长）一致的数量归一化
- 与执行链一致的 rounding（舍入）规则
- 明确的 `qty_epsilon`
- 明确的 `min_actionable_qty`

第一版不允许“看起来差不多”这种主观判断；所有 external reduction 判定都必须统一落到：
- `raw_remaining_abs_qty`
- 归一化后的累计剩余阈值
- `qty_epsilon`
- `min_actionable_qty`

`normalized_remaining_qty` 只用于：
- 状态写回
- 报表展示
- 与历史快照对比时的稳定化口径

它**不**作为 external reduction 是否成立的最终判定量。

## Safety rules for abnormal states

遇到以下异常时，默认走保守路径：
- `original_position_qty` 缺失或非正数
- 剩余仓位异常增大，与阶段历史冲突
- 阶段状态本身互相打架
- runner 保护字段与剩余仓位不一致

保守路径细则固定为：
- 对 `original_position_qty` 缺失 / 非正数：
  - 不做 external terminalization
  - 禁止新的 target / runner 动作
  - 仅记录 warning（告警）
- 对剩余仓位异常增大：
  - 不把任何阶段改写成 external reduction
  - 不清空已有 `*_target_filled_qty`
  - 仅记录 warning
- 对阶段状态互相冲突：
  - 优先保留已存在的正式终态
  - 禁止新增 target 动作，直到状态重新一致
- 对 runner 字段与剩余仓位不一致：
  - 若剩余仓位 `<= qty_epsilon` 或只剩 dust，则允许清理 `runner_protected` / `runner_stop_price`
  - 否则仅禁止新的 runner 动作，不猜测新的保护价

所有异常路径都必须满足：
- 不伪造 `target_hit`
- 不补造虚假 filled qty（已成交数量）
- 不新增新的 partial / runner 动作
- 保持可审计：能看出是“外部减仓收口失败”还是“仅因异常被保守冻结”

### Operational abnormal predicates

以下异常条件必须有明确可测谓词：
- **剩余仓位异常增大，与阶段历史冲突**
  - `previous_normalized_remaining_qty` 固定来自：当前 sync（同步）前一版已持久化的 canonical position state（规范持仓状态）
  - 若这是 first sync（首次同步）或重启后历史快照缺失，则该谓词本轮不触发，当前快照只用于建立新 baseline（基线）
  - “有显式加仓解释”固定指：同一 reconciliation cycle（对账周期）里存在已知的 top-up / add-position / reverse-intent / side-flip 事件
  - 因此，只有当：
    - 存在 `previous_normalized_remaining_qty`；且
    - `normalized_remaining_qty > previous_normalized_remaining_qty + qty_epsilon`；且
    - 本轮不存在上述显式事件
    - 才进入 abnormal-increase freeze（异常增仓冻结）
- **阶段状态本身互相打架**
  - 至少包括：
    - second 是合法正式终态，但 first 不是合法正式终态
    - `*_target_hit = true`，但对应 status 不是 `filled`
    - `runner_protected = true`，但 first / second 并未都进入合法正式终态
    - `runner_stop_price` 非空，但 `runner_protected = false`

## Architecture impact

### Primary unit boundary

本次逻辑应继续收口在 `target_management.py`（目标管理辅助模块）这一层，而不是散落到：
- `exit_policy.py`
- `positions.py`
- `reporting.py`

建议边界：
- `target_management.py`
  - 负责：阶段数量阈值计算、容差判断、顺序终态收口、异常保守降级
  - 必填输入固定为：
    - 当前 position state（当前持仓状态）
    - account snapshot 中的真实剩余仓位
    - symbol quantity rules（step size / min_order_qty）
    - previous canonical position state（上一版规范持仓状态，可为空）
    - same-cycle explainable events（本轮可解释事件，如 top-up / add-position / reverse-intent，可为空）
  - 并返回结构化结果，最少包含：
    - `position_patch`：要写回状态的字段补丁
    - `closed_stages`：本轮新收口的 stage 名单，取值只允许 `first` / `second` / `runner`
    - `suppressed_intents`：本轮必须抑制的本地待执行动作，按 stage 标识，不含已到交易所的 live orders
    - `freeze_target_actions`：是否冻结新的 target 动作
    - `freeze_runner_actions`：是否冻结新的 runner 动作
    - `freeze_reason`：机器可读冻结原因，单值或规范枚举
    - `warnings`：机器可读 warning code 列表
    - `normalized_quantities`：至少含 original / raw_remaining / normalized_remaining / min_actionable / qty_epsilon
- `positions.py` / `sync_positions_from_account`
  - 负责：在拿到真实仓位后调用上述 helper（辅助函数）
  - 应用 `position_patch`
  - 并把以下字段**持久化写入 canonical position state（规范持仓状态）**：
    - `freeze_target_actions`
    - `freeze_runner_actions`
    - `freeze_reason`
    - `warnings`
    - `runner_terminal_reason`
  - 再按 `suppressed_intents` 抑制同 stage 的 stale intents（过期待执行意图）
  - 若本轮已发现该 stage 存在本地 live reduce-only order（已发往交易所但未完成的本地减仓单），则：
    - 该 order reconciliation / cancel（订单对账 / 撤单）必须进入现有订单边界处理
    - 本 slice 至少要把仓位冻结成 `freeze_reason = "external_reduction_live_orders_unreconciled"`
    - 在 live order 未被现有边界确认处理前，不得生成新的同 stage 动作
- `exit_policy.py`
  - 只消费最终规范状态，不自己重新推断 external reduction
  - 直接读取持久化后的 `freeze_target_actions` / `freeze_runner_actions` / `freeze_reason`
  - 对被冻结仓位不再重新生成对应动作
- `reporting.py`
  - 只展示终态原因，不参与写状态
  - 直接读取持久化后的 `freeze_reason` / `warnings` / `runner_terminal_reason`

这样能保证：
- 状态只在一个地方被定义
- LONG / SHORT 共享数量逻辑
- 执行层和报表层不再各自偷偷推断“是不是外部减仓”

## Reporting contract

报表 / 审计层必须能明确区分：
- `filled` / `target_hit`：价格命中并由系统阶段动作完成
- `satisfied_by_external_reduction`：阶段并非价格命中，而是被外部减仓提前满足

禁止把这两类完成原因折叠成同一种“已完成”。

## Test and acceptance plan

### Core regression coverage

必须覆盖以下最小回归集：

#### LONG
- 外部减仓刚好吃掉第一阶段 → 第一阶段收口为 `satisfied_by_external_reduction`
- 外部减仓继续吃掉第二阶段 → 第二阶段也正确收口
- 外部减仓把仓位直接减到只剩 runner 或直接清空 → 状态顺序收口，不跳级

#### SHORT
- 与 LONG 对应的同组数量驱动场景全部复跑
- 验证 SHORT 没有夹带 LONG 价格方向假设

### Anti-regression coverage

- 步长 / 舍入附近不误关阶段
- 原始仓位缺失或异常状态时，不脑补成功
- 已进入 `satisfied_by_external_reduction` 的阶段，后续不会重复发相同 partial
- 不会借 external reduction 收口去改写冻结目标价
- 阶段部分成交后再发生外部减仓，仍能按净剩余阈值正确收口
- second 已完成后只剩 dust runner 时，runner 只关闭、不伪造保护
- runner 被外部减仓彻底打平时，`runner_terminal_reason = "external_reduction_flat"`
- 重复同步同一真实仓位是幂等的，不重复写状态、不重复抑制同一动作
- dirty-state（脏状态）下只告警 / 冻结，不脑补成功
- 有上一次快照且无显式加仓事件时，异常增仓会触发 freeze；首次同步 / 缺历史时不会误触发
- `stage_target_qty == 0` 时进入 `stage_qty_rounded_to_zero` 冻结路径，不误判成 external reduction 成功

### Execution-side acceptance

验收必须用人话证明 5 件事：
1. 外部已经减掉的仓位，程序不会再重复减一次
2. 冻结目标位不会被借机改乱
3. 报表能明确区分“价格命中完成”和“外部减仓完成”
4. account sync（账户同步）之后，旧的 target / runner 待执行意图会先被抑制，再由 `exit_policy` 读新状态，不会出现 stale intents（过期待执行意图）继续落地
5. 若同 stage 已有本地 live reduce-only order（交易所侧仍存活的本地减仓单），系统不会再继续生成同方向重复单；至少会冻结并把问题交给现有订单对账 / 撤单边界处理

## Implementation-ready decisions

本 spec（设计）正式锁死以下实现口径：
- 采用保守终态型，不做激进重算
- LONG + SHORT 同时覆盖
- external reduction 只在真实仓位同步后判断
- 只按数量收口，不按价格方向猜测
- `satisfied_by_external_reduction` 不得写成 `target_hit`
- 顺序收口 first → second → runner
- external reduction 的唯一判定量固定为 `raw_remaining_abs_qty`
- `normalized_remaining_qty` 只用于写回 / 展示 / 历史对比，不用于最终判定
- 判断必须带数量容差与归一化
- 小仓位导致 `stage_target_qty == 0` 时进入 `stage_qty_rounded_to_zero` 冻结路径
- 异常状态宁可保守，不做脑补式修复

## Open questions

无。
