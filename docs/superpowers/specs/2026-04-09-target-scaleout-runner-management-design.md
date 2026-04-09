# Target, Scale-Out, and Runner Management Design

Date: 2026-04-09
Owner: Claw
Status: Draft revised after written-spec review

## Goal

为当前交易系统补齐一块与 `failed-followthrough` 相邻、但边界独立的退出 / 持仓管理设计：
**first target（第一目标） / second target（第二目标） / scale-out（分批止盈） / runner management（尾仓管理） / reporting（报告展示）**。

这份设计不是去改写 thesis invalidation（逻辑失效）或 failed follow-through（后续无力快退）的定义，
而是把“单子走出来以后怎么分批兑现、怎么保护尾仓、报告里给老板看什么”这条线写死。

## Why this slice

当前系统已经具备：
- thesis invalidation（逻辑失效快退）
- failed follow-through（突破后续无力快退）
- 第一目标触发的 partial take profit（部分止盈）雏形
- defensive regime de-risk（防守环境减仓）

但还没有把以下问题明确规格化：
- 第一目标到底怎么定
- 第二目标到底怎么定
- 分批比例是否固定
- runner（尾仓）在什么时点开始受保护
- 报告层到底展示哪些字段，避免“内部状态很多，但看盘时一眼看不懂”

如果这些口径不提前钉死，后面实现时就会出现：
- 回测与实盘解释不一致
- 同一笔单在持仓中目标位漂移
- 过早减仓把强趋势单切碎
- 报告字段混乱、难以快速判断仓位所处阶段

## Scope

### In scope

第一版只覆盖：
- breakout / trend continuation long（突破 / 趋势延续型做多）仓位
- first target / second target 计算口径
- fixed scale-out plan（固定分批计划）
- runner protection（尾仓保护）启动条件与保护价
- runner stop breach（尾仓保护价被击穿）后的退出口径
- 报告层 B 标准版展示口径
- 可解释、可测试、可回测（backtest，可回测）的最小实现

### Out of scope

第一版不做：
- short（做空）侧 target / runner 规则
- 泛 setup（形态）但没有 breakout / continuation 语义的仓位
- 动态目标漂移 / 持仓中重算 target
- 更复杂的 trailing runner（跟踪尾仓）
- 多档 second / third / extended target（第三目标 / 扩展目标）
- 组合层 portfolio exit（组合层统一退出）
- 为不同 symbol（品种）做个性化分批参数

## Current baseline in code

当前代码里，`exit_policy.py` 已有一个最小 partial take profit（部分止盈）行为：
- 读取单一 `take_profit`
- 当价格触及该目标时，输出 `PARTIAL_TAKE_PROFIT`
- 固定减仓 `50%`

但当前实现仍然缺少：
- first target（第一目标）来源定义
- second target（第二目标）定义
- runner（尾仓）数量与保护逻辑
- reporting（报告）字段定义
- 目标位冻结 / 更新规则
- 多次评估下的命中状态持久化与幂等控制

因此这份 spec（规格）要做的，不是凭空发明新概念，而是把现有 partial 行为扩展成一套边界清楚的 target / scale-out / runner contract（目标 / 分批 / 尾仓契约）。

### Legacy take_profit compatibility（旧字段兼容）

第一版兼容口径固定为：
- **新仓位**：以 `first_target_price` 为规范字段
- **旧仓位**：若历史状态里只有 `take_profit` 而没有 `first_target_price`，允许在兼容层一次性映射为 `first_target_price`
- 一旦完成迁移 / 映射，后续新的 target / scale-out / runner 逻辑都只认 `first_target_price`

映射规则必须仍满足与新仓位完全相同的 invariant（不变式）约束：
- 若映射后的旧 `take_profit < 1R` → 回退到 `1R`
- 若映射后的旧 `take_profit >= 2R` → 不得直接认作 first target（第一目标），回退到 `1R`
- 若映射后的值无法满足 `first_target_price < second_target_price` → 回退到 `1R`

同时 `first_target_source` 必须写成明确枚举值之一：
- `structure`
- `fallback_1r`
- `legacy_take_profit_mapped`

### Legacy rollout contract（旧仓位上线兼容契约）

第一版固定采用以下 rollout（上线兼容）口径：
- 由 **lifecycle / position snapshot assembler（生命周期 / 持仓快照组装层）** 负责识别旧仓位并做一次性映射
- 映射时机固定为：**旧仓位第一次进入新 target / scale-out / runner 管线之前**
- 映射完成后，该仓位视为 **已迁移仓位**
- 对已迁移仓位：
  - 新逻辑只认 `first_target_price`
  - 旧的 `take_profit` 触发分支必须被 bypass（旁路 / 绕过）
  - 不允许同一仓位同时经过旧 `take_profit` partial 路径和新 `first_target_price` partial 路径

#### Legacy migration matrix（旧仓位迁移矩阵）

至少固定覆盖以下 4 类：

1. **旧仓位，未走过旧 partial（分批）**
   - 正常映射到新字段
   - 初始化为：
     - `first_target_status = "pending"`
     - `first_target_hit = false`
     - `first_target_filled_qty = 0`

2. **旧仓位，旧 50% partial 已完整完成**
   - 迁移时直接写：
     - `first_target_status = "filled"`
     - `first_target_hit = true`
     - `first_target_filled_qty = 0.50 * original_position_qty`
   - 不得再次触发新的 first-stage partial（第一阶段分批）

3. **旧仓位，旧 partial 只成交了一部分**
   - 迁移时写：
     - `first_target_status = "pending"`
     - `first_target_hit = false`
     - `first_target_filled_qty = legacy_partial_filled_qty`
   - 后续只补第一阶段未完成部分，不得把已成交部分重卖

4. **旧仓位，已被外部减仓显著削减或基本清空**
   - 若第一阶段理论目标已因历史外部减仓而不可达
   - 则直接按 `first_target_status = "satisfied_by_external_reduction"` 收口
   - 不再继续补第一阶段

## Design decisions

### 1) First target ownership

第一版 first target（第一目标）采用：
**structure target（结构目标）优先 + 1R floor（1R 地板价） + entry-time freeze（入场即冻结）**。

#### 1.1 Structure target（结构目标）定义

这里的 structure target（结构目标）指：
- 不是单纯用固定 R multiple（R 倍数）硬算出来的价位
- 而是来自 entry/setup time（入场 / 建仓时）已经识别出的最近有效结构阻力 / 量度目标 / 明确阻力带

对第一版来说，`exit_policy` 不负责自己重新识别结构。
它只消费上游已经写入 position metadata（持仓元数据）的目标字段。

#### 1.2 First target selection rule（第一目标选取规则）

第一版固定采用以下规则：

1. 若存在清晰的 `structure_target_price`
2. 且该目标相对 entry（入场）距离满足 **>= 1R 且 < 2R**
3. 则把它作为 first target（第一目标）
4. 否则回退到固定 **1R** 目标

也就是：
- **结构位优先**
- 但**低于 1R 不认**
- **高于或等于 2R 也不认作 first target（第一目标）**，以避免和 second target（第二目标）重叠或倒挂
- 没有结构位也回退到 **1R**

#### 1.2.1 Ordering invariant（目标顺序不变式）

第一版强制满足以下顺序：
- `stop_loss < entry_price < first_target_price < second_target_price`（long，做多）

因此：
- 若 `structure_target_price` 满足 `>= 2R`，它不能直接成为 first target（第一目标）
- 此时 first target（第一目标）回退到 **1R**
- second target（第二目标）仍固定为 **2R**

这样可以保证：
- first / second stage（第一 / 第二阶段）顺序稳定
- 不会出现“第二目标先于第一目标”的语义冲突
- second target（第二目标）命中后把 runner stop（尾仓止损）抬到 first target（第一目标）价时，保护价始终合理

#### 1.3 1R definition（1R 定义）

对 long（做多）仓位：
- `risk_unit = entry_price - stop_loss`
- `first_target_1r = entry_price + risk_unit`

若 `risk_unit <= 0` 或关键字段缺失，则第一版安全降级：
- 不计算新 target
- 不触发 target-based partial / runner logic（基于目标位的分批 / 尾仓逻辑）

#### 1.4 Freeze rule（冻结规则）

第一版明确采用：
- first target（第一目标）在 **entry（入场）时确定一次**
- 持仓过程中**不动态更新**
- 不允许随着后续价格结构变化而漂移

原因：
- 保证回测与实盘口径一致
- 避免“单子拿着拿着，目标自己变了”的解释歧义
- 降低实现复杂度

### 2) Second target definition

第一版 second target（第二目标）固定为：
- **2R**

对 long（做多）仓位：
- `second_target_price = entry_price + 2 * risk_unit`

价格触达语义固定为：
- `mark_price >= first_target_price` 视为**价格已触达** first target（第一目标）
- `mark_price >= second_target_price` 视为**价格已触达** second target（第二目标）
- equality（相等）算触达

注意：
- 上面定义的是 **price-reach event（价格触达事件）**
- 不是持久化状态字段 `first_target_hit` / `second_target_hit` 的定义
- `first_target_hit` / `second_target_hit` 在本 spec（规格）里专指：对应阶段动作已经完成并写回状态

第一版不对 second target（第二目标）做结构化推导。
它的来源固定标记为：
- `second_target_source = "fixed_2r"`

这样做的原因：
- 第一版先把层级和行为钉死
- 避免 first target（第一目标）已经结构化、second target（第二目标）也结构化后，把问题一下扩得太宽
- 让 reporting（报告）里能清楚告诉老板：第二目标来自固定 2R，而不是模糊内部算法

### 3) Scale-out plan

第一版 scale-out plan（分批计划）固定为：
- first target（第一目标）出 **50%**
- second target（第二目标）出 **25%**
- runner（尾仓）保留 **25%**

对应 contract（契约）为：
- `first_scale_out_fraction = 0.50`
- `second_scale_out_fraction = 0.25`
- `runner_fraction = 0.25`

**关键语义：以上三个比例都以 original position size（原始开仓数量）为基准，而不是以当前剩余仓位为基准。**

第一版要求 position（持仓）里持久化一个明确的原始数量字段：
- `original_position_qty`（或同义但唯一的 canonical field，规范字段）

也就是：
- first target（第一目标）命中时，减仓原始仓位的 50%
- second target（第二目标）命中时，再减仓原始仓位的 25%
- 最终 runner（尾仓）剩余原始仓位的 25%

该方案的含义：
- 第一段先兑现一半，降低持仓心理压力与回吐风险
- 第二段再兑现一部分，确认延续已走到 2R
- 保留一小段尾仓去吃更大趋势

第一版不允许：
- 不同 setup（形态）使用不同分批比率
- 持仓过程中修改分批比例
- 把 second target（第二目标）的 25% 解释成“对剩余仓位再卖 25%”

### 4) Runner protection

第一版 runner protection（尾仓保护）采用：
- **second target（第二目标）命中后才启动**

固定规则：
1. first target（第一目标）命中时，仅执行第一段 partial（部分止盈）
2. 在 second target（第二目标）命中之前，不因为 first target（第一目标）命中就自动把 runner stop（尾仓止损）抬紧
3. 一旦 second target（第二目标）命中：
   - 执行第二段 partial（部分止盈）
   - 将 runner stop（尾仓止损）抬到 **first target（第一目标）价**
4. 当 `runner_protected = true` 后，若 `mark_price <= runner_stop_price`（long，做多）
   - 输出剩余 runner（尾仓）的退出建议
   - 由现有 exit path（退出路径）执行清仓剩余仓位

#### 4.1 Why not protect at first target

第一版明确不采用“first target 一到就立刻把 runner stop 抬到 break-even（保本）或更紧”的做法。

原因：
- 对 breakout / continuation（突破 / 延续）单来说，太早保护容易被正常回踩洗掉
- 用户明确选择的是更专业、更成熟的趋势交易口径：
  - **让强单先呼吸**
  - 到了 2R 再正式接入尾仓保护

#### 4.2 Protection reference（保护参考价）

第一版固定：
- second target（第二目标）命中后，`runner_stop_price = first_target_price`

第一版不做：
- ATR trail（ATR 跟踪）
- swing low trail（摆动低点跟踪）
- EMA trail（均线跟踪）
- 随新结构不断上移 runner stop（尾仓止损）

## Reporting contract

第一版 reporting（报告）采用：
- **B 标准版**

默认展示以下字段：
- `first_target_hit`：bool（布尔）—— 第一目标阶段是否以标准成交方式完成并写回状态
- `second_target_hit`：bool（布尔）—— 第二目标阶段是否以标准成交方式完成并写回状态
- `runner_protected`：bool（布尔）—— 当前 runner（尾仓）是否已受保护
- `runner_stop_price`：number | null（数值或空）—— 当前 runner stop（尾仓止损）价
- `scale_out_plan`：object（对象）—— `{first: 0.50, second: 0.25, runner: 0.25, basis: "original_position"}`
- `second_target_source`：string（字符串）—— 第一版固定为 `fixed_2r`

B 标准版默认不展示阶段状态枚举，但 audit / debug / backtest replay（审计 / 调试 / 回测回放）视图必须能看到：
- `first_target_status`
- `second_target_status`

### Not shown by default

第一版 B 标准版默认不展示：
- 所有内部中间字段
- 更详细的 target 计算过程
- 未被用户要求的扩展元数据

这样做的原因：
- 让报告一眼能看懂仓位处在哪一段
- 不把本该内部使用的字段全部扔给老板

## Data / metadata expectations

第一版需要 position / lifecycle（持仓 / 生命周期）层向 exit / reporting 层提供以下字段：
- `side`
- `entry_price`
- `stop_loss`
- `mark_price`
- `original_position_qty`
- `remaining_position_qty`
- `symbol_step_size`
- `min_order_qty`（如该交易所 / 品种需要）
- `first_target_price`
- `first_target_source`
- `first_target_status`
- `first_target_hit`
- `first_target_filled_qty`
- `second_target_price`
- `second_target_source`
- `second_target_status`
- `second_target_hit`
- `second_target_filled_qty`
- `runner_stop_price`
- `runner_protected`
- `scale_out_plan`

### State ownership and persistence（状态归属与持久化）

第一版固定采用以下状态归属：
- lifecycle / execution state（生命周期 / 执行状态）负责持久化：
  - `first_target_status`
  - `first_target_hit`
  - `first_target_filled_qty`
  - `second_target_status`
  - `second_target_hit`
  - `second_target_filled_qty`
  - `runner_protected`
  - `runner_stop_price`
- `exit_policy` 负责读取这些状态并决定当前是否需要发出动作
- `exit_policy` **不直接修改持仓状态**；状态更新发生在动作被执行 / 应用到持仓之后

默认初始值：
- `first_target_status = "pending"`
- `first_target_hit = false`
- `first_target_filled_qty = 0`
- `second_target_status = "pending"`
- `second_target_hit = false`
- `second_target_filled_qty = 0`
- `runner_protected = false`
- `runner_stop_price = null`

### State model（状态模型）

第一版明确区分两类东西：

1. **价格触达事件（非持久化语义）**
   - `price_reaches_first_target := mark_price >= first_target_price`
   - `price_reaches_second_target := mark_price >= second_target_price`

2. **阶段完成状态（持久化语义）**
   - `first_target_status` / `second_target_status` 的 canonical enum（规范枚举）固定为：
     - `pending`
     - `filled`
     - `satisfied_by_external_reduction`
   - `first_target_hit = true` 表示：第一目标阶段以**标准成交方式**完成并写回状态
   - `first_target_filled_qty` 表示：第一目标阶段累计已成交数量
   - `second_target_hit = true` 表示：第二目标阶段以**标准成交方式**完成并写回状态
   - `second_target_filled_qty` 表示：第二目标阶段累计已成交数量
   - `runner_protected = true` 表示：尾仓保护状态已生效并写回状态

Idempotency（幂等）规则：
- 只有当 `first_target_status = "pending"` 且价格已触达 first target（第一目标）时，才允许发 first partial（第一段分批）
- 一旦第一阶段状态不再是 `pending`，后续评估即使价格继续高于 first target（第一目标），也不得重复发 first partial
- `second_target_status` 同理
- 一旦 `runner_protected = true`，后续只允许等待 runner stop（尾仓止损）被击穿或其他更高优先级退出，不得重复“再保护一次”

### Same-round multi-action writeback contract（同轮多动作写回契约）

第一版固定采用**逐动作写回**，而不是“同轮全成或全不成”的原子整包写回。

也就是：
- 若同轮先发 first partial（第一段分批），后发 second partial（第二段分批）
- 则每个动作都在其自身真正执行成功后，分别写回对应状态

明确规则：
- first partial 完成后 → `first_target_status = "filled"` 且 `first_target_hit = true`
- first partial 每次有成交 → 累加写回 `first_target_filled_qty`
- second partial 完成后 → `second_target_status = "filled"` 且 `second_target_hit = true`
- second partial 每次有成交 → 累加写回 `second_target_filled_qty`
- second partial 完成后，才允许写回：
  - `runner_protected = true`
  - `runner_stop_price = first_target_price`
- 若同轮 first partial 只发生部分成交、尚未完成第一阶段目标数量：
  - second partial **本轮不得继续执行**
  - 必须等待后续轮次先补齐第一阶段，再进入第二阶段

若出现同轮多动作但不是全成功，例如：
- first partial 成功，second partial 失败 / 被拒 / 未完整执行

则必须写回：
- `first_target_status = "filled"`
- `first_target_hit = true`
- `second_target_status = "pending"`
- `second_target_hit = false`
- `first_target_filled_qty =` 第一阶段累计已成交数量
- `second_target_filled_qty =` 第二阶段累计已成交数量（若第二阶段完全失败则保持原值，通常仍为 0）
- `runner_protected = false`
- `runner_stop_price = null`

后续评估时：
- 不得重复 first partial
- 允许在价格仍满足条件、且仓位状态仍允许时，继续尝试 second partial

**阶段完成判据固定为：**
- 第一阶段目标数量：`first_stage_target_qty = original_position_qty * 0.50`
- 第二阶段目标数量：`second_stage_target_qty = original_position_qty * 0.25`
- 只有当某阶段的累计已成交数量达到该阶段目标数量时，才允许把该阶段写回为 `*_target_hit = true`
- 若只成交了部分、尚未达到该阶段目标数量，则该阶段仍视为未完成，`*_target_hit` 不得置为 `true`

### Upstream ownership

第一版固定职责边界：
- **上游 lifecycle / position snapshot assembler（生命周期 / 持仓快照组装层）** 负责：
  - 在入场时写入 `side`
  - 在入场时写入 `original_position_qty`
  - 在每轮持仓状态中提供最新 `remaining_position_qty`
  - 提供 `symbol_step_size`，必要时再提供 `min_order_qty`
  - 在入场时选定并冻结 `first_target_price`
  - 写入 `first_target_source`
  - 在写入 `first_target_price` 时同步校验 invariant（不变式），不得把不满足 `1R <= first_target_price < 2R` 与 `first_target_price < second_target_price` 的无效目标写入仓位状态
  - 计算并写入 `second_target_price = 2R`
  - 写入 `second_target_source = fixed_2r`
  - 写入 `scale_out_plan`
  - 初始化命中 / 保护状态字段
- **exit policy（退出策略）** 负责：
  - 消费上述字段
  - 根据价格触达事件与阶段完成状态，返回**有序动作列表**
  - 不直接做数量 clamp（截断），也不直接修改持仓状态
- **execution / state apply（执行 / 状态回写层）** 负责：
  - 用 `original_position_qty`、`remaining_position_qty`、`first_target_filled_qty`、`second_target_filled_qty`、`symbol_step_size`、`min_order_qty` 做数量对账
  - 按返回列表顺序执行动作
  - 在动作真正完成后写回 `first_target_status` / `first_target_hit` / `first_target_filled_qty` / `second_target_status` / `second_target_hit` / `second_target_filled_qty` / `runner_protected` / `runner_stop_price` / `remaining_position_qty`
  - 负责执行 **reconciliation / terminalization pass（对账 / 终态收口步骤）**：
    - 该步骤在本轮动作执行后运行；若本轮没有动作，也必须运行一次
    - 专门用于检测并写回无新动作的终态变化，例如 `*_target_status = "satisfied_by_external_reduction"`
- **reporting（报告层）** 负责：
  - 按 B 标准版筛选与展示字段

### Explicit non-goals

第一版不允许在 `exit_policy` 中：
- 临时推导结构目标
- 回看历史 K 线现场识别阻力位
- 动态重算 first target / second target

### Side gating（方向门禁）

第一版新 target / scale-out / runner 规则只对 `side == "LONG"` 生效。
若 `side != "LONG"`：
- 直接跳过本 spec（规格）新增逻辑
- 保留系统当前其他既有行为

## Action semantics

第一版把动作语义固定为四段：

### Fraction basis（比例基准）

- 对 `PARTIAL_TAKE_PROFIT` 来说，`qty_fraction` 一律表示 **original position size（原始开仓数量）占比**
- 对 `EXIT` 来说，`qty_fraction = 1.0` 继续表示 **退出当前剩余全部仓位**

### Multi-action output contract（多动作输出契约）

第一版沿用当前 `exit_policy` 的基本形态：
- **每轮评估返回一个有序 `list[ExitDecision]`（动作列表）**

这份列表就是 exit_policy（退出策略）与 execution / state apply（执行 / 状态回写层）之间的 canonical interface（规范接口）。

固定规则：
- 列表顺序就是执行顺序
- execution（执行层）必须按列表顺序处理，不得重排
- 同一轮里若前一个动作失败 / 被拒 / 未尝试，则后续动作本轮不得自动越过执行
- 本轮不做“执行一次再回到 exit_policy 重新评估”的内部递归；需要下一轮时，交给下一轮正常评估

### First target hit

命中 first target（第一目标）时，且 `first_target_status = "pending"`：
- `action = "PARTIAL_TAKE_PROFIT"`
- `qty_fraction = 0.50`
- `meta.exit_trigger = "first_target_hit"`
- `meta.target_stage = "first"`
- `meta.fraction_basis = "original_position"`

### Second target hit

命中 second target（第二目标）时，且 `second_target_status = "pending"`：
- `action = "PARTIAL_TAKE_PROFIT"`
- `qty_fraction = 0.25`
- `meta.exit_trigger = "second_target_hit"`
- `meta.target_stage = "second"`
- `meta.fraction_basis = "original_position"`
- `meta.runner_stop_price = first_target_price`
- `meta.runner_protected = true`

### Gap-through / skip-stage behavior（跳空穿越 / 跨阶段行为）

若同一轮首次观察到的 `mark_price` 已经满足：
- `mark_price >= second_target_price`
- 且 `first_target_status = "pending"`
- 且 `second_target_status = "pending"`

则第一版固定按**阶段顺序**在同一轮返回有序动作列表：
1. 先返回 first target（第一目标）对应的 50% partial
2. 再返回 second target（第二目标）对应的 25% partial
3. 只有第二段动作真正执行成功后，才把 runner（尾仓）状态切到 protected（已保护）

也就是：
- 不允许“直接只做 second partial（第二段分批），跳过 first partial（第一段分批）”
- 不把两段合并成一个 75% 动作
- 统一保持 stage order（阶段顺序）与回测 / 实盘口径一致
- 若第一段成功、第二段失败，则只写回第一段状态，第二段与 runner protection（尾仓保护）留待后续轮次处理

### Runner protected state

second target（第二目标）命中后，系统需要能明确表达：
- `runner_protected = true`
- `runner_stop_price = first_target_price`

这里的“runner 受保护”是**状态**，不是独立 `EXIT` 动作。
第一版不为“抬 runner stop”单独发明新的交易动作类型。

### Runner stop breach（尾仓保护价被击穿）

当 `runner_protected = true` 且 `mark_price <= runner_stop_price`（long，做多）时：
- `action = "EXIT"`
- `qty_fraction = 1.0`
- `meta.exit_trigger = "runner_stop_hit"`
- `meta.runner_stop_price = runner_stop_price`

这表示：
- 退出当前剩余全部 runner（尾仓）仓位
- 不再追加新的 partial（分批）动作

### State transition table（状态流转表）

**说明：下表只描述 fully-completed stage（已完整完成阶段）的写回结果；若出现部分成交、跨轮补单、或阶段未完整完成，以前文 Same-round multi-action writeback contract（同轮多动作写回契约）与 Quantity reconciliation（数量对账规则）为准。尤其在 gap-through（直接跳到第二目标）场景下，只要第一阶段尚未完整完成，第二阶段本轮就不得继续执行。**

| 阶段 | 触发条件 | 动作 | 写回状态 |
|---|---|---|---|
| first target hit | `first_target_status = "pending"` 且 `mark_price >= first_target_price` | `PARTIAL_TAKE_PROFIT 0.50` | `first_target_status = "filled"`, `first_target_hit = true` |
| second target hit | `second_target_status = "pending"` 且 `mark_price >= second_target_price` | `PARTIAL_TAKE_PROFIT 0.25` | `second_target_status = "filled"`, `second_target_hit = true`；若仍有剩余尾仓，则再写回 `runner_protected = true`, `runner_stop_price = first_target_price` |
| external reduction terminalization | `*_target_status = "pending"` 且阶段目标已不可达 | 无新的 partial 动作 | `*_target_status = "satisfied_by_external_reduction"`, `*_target_hit = false` |
| runner stop breach | `runner_protected = true` 且 `mark_price <= runner_stop_price` | `EXIT 1.0` | 剩余仓位关闭 |

### Quantity reconciliation（数量对账规则）

虽然 first / second partial（第一 / 第二段分批）的比例以 `original_position_qty` 为基准，但执行时仍必须先和**当前剩余可卖数量**对账。

固定规则：
- `requested_qty = original_position_qty * stage_fraction`
- `already_filled_qty = stage_filled_qty`
- `stage_remaining_qty = max(requested_qty - already_filled_qty, 0)`
- `raw_executable_qty = min(stage_remaining_qty, remaining_position_qty)`
- `executable_qty = floor_to_symbol_step(raw_executable_qty)`（按该 symbol（品种）的最小下单单位向下舍入）
- 若存在 `min_order_qty` 且 `executable_qty < min_order_qty`，则该动作本轮不执行
- 若 `executable_qty <= 0`，则该动作本轮不执行

数量比较与完成判定允许使用一个小容差：
- `qty_epsilon = max(symbol_step_size / 2, 1e-12)`
- 当 `filled_qty + qty_epsilon >= target_qty` 时，可视为达到该阶段目标数量

### Terminal rule for externally reduced positions（外部减仓后的终态规则）

若某阶段因非本阶段动作导致仓位被提前削减，例如：
- defensive de-risk（防守减仓）
- manual reduction（手动减仓）
- 其他外部卖出

从而使该阶段剩余理论目标数量已经**数学上不可达**，则第一版固定采用：
- 该阶段记为 `satisfied_by_external_reduction`（因外部减仓视为满足）
- 具体写回到：
  - `first_target_status` 或 `second_target_status`
- 对外不再当作标准成交完成，因此：
  - `*_target_hit = false`
- 对该阶段停止继续请求新的 partial
- reporting（报告）默认 B 版可不展示该枚举，但 audit / debug / backtest replay（审计 / 调试 / 回测回放）必须能看出该阶段并非标准成交完成，而是被外部减仓提前满足

**不可达的硬判定公式固定为：**
对任一阶段：
- `stage_target_qty = original_position_qty * stage_fraction`
- `stage_remaining_qty = max(stage_target_qty - stage_filled_qty, 0)`
- `raw_executable_qty = min(stage_remaining_qty, remaining_position_qty)`
- `rounded_executable_qty = floor_to_symbol_step(raw_executable_qty)`

当同时满足以下条件时，该阶段进入 `satisfied_by_external_reduction`：
1. `stage_remaining_qty > qty_epsilon`
2. 且以下至少一条成立：
   - `remaining_position_qty <= qty_epsilon`
   - `rounded_executable_qty <= qty_epsilon`
   - 存在 `min_order_qty` 且 `rounded_executable_qty < min_order_qty`

翻译成人话就是：
- 这个阶段理论上还差一点
- 但按当前剩余仓位、最小下单单位、最小下单量，已经根本下不出有效补单
- 那就正式终态化，不再无限重试

含义：
- 不允许因为 defensive de-risk（防守减仓）、手动减仓、舍入、历史部分成交等原因而 oversell（超卖）
- stage action（阶段动作）默认先按原始基准计算，再向当前剩余仓位做 clamp（截断）

状态写回补充规则：
- 若某阶段动作有成交，则先累加写回对应 `*_target_filled_qty`
- 若累计已成交数量在容差范围内达到该阶段目标数量，则该阶段可写回为已完成（`*_target_status = "filled"`, `*_target_hit = true`）
- 若累计已成交数量仍未达到该阶段目标数量，则该阶段保持未完成（`*_target_status = "pending"`, `*_target_hit = false`），后续轮次继续只补未完成部分
- 若满足前述“不可达”硬判定公式，则该阶段写回为 `*_target_status = "satisfied_by_external_reduction"`, `*_target_hit = false`
- 对 second target（第二目标）来说，只有在动作完成后**仍有剩余仓位**时，才写回 `runner_protected = true`
- 若 second target（第二目标）动作完成后仓位已清空，则：
  - `second_target_status = "filled"`
  - `second_target_hit = true`
  - `runner_protected = false`
  - `runner_stop_price = null`

## Rule interaction and priority

第一版优先级固定如下：
1. thesis invalidation（逻辑失效）/ hard invalidation（硬失效）
2. failed follow-through（后续无力快退）
3. runner stop breach（尾仓保护价被击穿）
4. target-based partials（目标位分批止盈）
5. defensive regime de-risk（防守环境减仓）
6. runner protected state update（尾仓保护状态更新）

### Collision rules

- 若同一轮 `thesis_invalidation` 成立：
  - 直接走 invalidation exit（失效退出）
  - 不再输出 first / second target partial（第一 / 第二目标分批）
  - 不再处理 runner stop（尾仓止损）
- 若同一轮 `failed_followthrough` 成立：
  - 直接走 failed follow-through exit（后续无力快退）
  - 不再输出 target-based partials
  - 不再处理 runner stop（尾仓止损）
- 若 `runner_protected = true` 且同一轮价格已击穿 `runner_stop_price`：
  - 优先输出 runner stop exit（尾仓保护退出）
  - 不再追加新的 partial（分批）动作
- 若同一轮先命中 second target（第二目标）又满足 defensive de-risk（防守减仓）：
  - 优先保留 second target partial（第二目标分批）
  - runner protection（尾仓保护）照常更新
  - 第一版不叠加额外 defensive de-risk 动作，避免同轮重复减仓
- 若同一轮先命中 first target（第一目标）又满足 defensive de-risk（防守减仓）：
  - 优先保留 first target partial（第一目标分批）
  - 第一版不叠加额外 defensive de-risk 动作，避免同轮重复减仓
- 若某阶段只完成了部分成交、仍处于未完成状态时又遇到 defensive de-risk（防守减仓）：
  - 第一版优先继续补齐当前未完成的 target stage（目标阶段）
  - 不额外插入新的 defensive de-risk 动作
  - 若该阶段随后因外部减仓已数学上不可达，则按前述 `satisfied_by_external_reduction` 终态规则收口，并把该阶段状态写回到对应 `*_target_status`
  - 目的仍是避免同一轮或连续轮次对同一剩余仓位重复减仓

## Missing / invalid data behavior

第一版缺字段时采用**安全降级，不触发**原则：

- 若 `entry_price` / `mark_price` 缺失或非法 → 跳过 target / runner 逻辑
- 若 `stop_loss` 缺失或无法形成有效 `risk_unit` → 跳过 target / runner 逻辑
- 若 `side` 缺失 → 跳过新的 target / scale-out / runner 逻辑
- 若 `original_position_qty` 缺失或非法 → 跳过新的 partial scale-out（分批止盈）逻辑
- 若 `first_target_price` 缺失 → 不触发 first target partial
- 若 `second_target_price` 缺失 → 不触发 second target partial，也不启动 runner protection
- 若 `first_target_price` 存在但 `second_target_price` 缺失 → 允许 first target partial，但不允许 second target / runner 逻辑
- 若 `second_target_price` 存在但 `first_target_price` 缺失 → 视为无效状态，second target / runner 逻辑一律跳过，避免生成无效 `runner_stop_price`
- 若 `runner_protected = true` 但 `runner_stop_price` 缺失或非法 → 视为 invalid runner state（无效尾仓状态）；本轮不得生成 runner stop exit，也不得猜测保护价，改为在 reporting / debug（报告 / 调试）中暴露该异常状态
- 若 `scale_out_plan` 缺失 → 不影响执行动作，但 reporting（报告）层应展示为 unavailable（不可用）而不是瞎猜

原因：
- 真实系统里，错误退出比错过一次 partial（部分止盈）更危险
- 第一版目标是补纪律，不是放大元数据缺口风险

## Testing strategy

第一版至少覆盖以下场景：

### Should trigger

1. **结构目标 >= 1R 且 < 2R，first target 正常采用结构位**
   - 预期：first_target_price 使用结构目标

2. **结构目标 < 1R，回退到 1R**
   - 预期：first_target_price 使用 1R，而不是太近的结构位

3. **结构目标 >= 2R，因顺序冲突回退到 1R**
   - 预期：first_target_price 使用 1R，second_target_price 仍为 2R

4. **无结构目标，回退到 1R**
   - 预期：first_target_price = 1R

5. **命中 first target（第一目标）**
   - 预期：输出 50% partial take profit（部分止盈）
   - 且该 50% 以 original position size（原始仓位）为基准

6. **命中 second target（第二目标）**
   - 预期：输出 25% partial take profit（部分止盈）
   - 且该 25% 以 original position size（原始仓位）为基准
   - 且 runner_protected = true
   - 且 runner_stop_price = first_target_price

7. **首次观察价格已直接到 second target（第二目标）上方**
   - 预期：同轮顺序输出 first partial + second partial
   - 且 runner_protected = true

8. **runner stop（尾仓止损）被击穿**
   - 预期：输出剩余 runner（尾仓）全平 EXIT

9. **首次观察价格直接跳到 second target（第二目标）上方时，exit_policy 返回有序双动作列表**
   - 预期：列表顺序固定为 first partial → second partial

10. **当前剩余仓位低于原始阶段目标数量**
   - 预期：动作数量按 remaining_position_qty（剩余仓位）clamp（截断），不超卖

11. **gap-through（直接跳到第二目标）后，第一段成功、第二段未完整成交**
   - 预期：只写回 `first_target_status = "filled"`, `first_target_hit = true`
   - `second_target_status = "pending"`, `second_target_hit = false`
   - `runner_protected = false`
   - 下一轮只重试仍未完成的第二阶段工作

12. **跨轮部分成交**
   - 预期：依赖 `first_target_filled_qty` / `second_target_filled_qty` 只补未完成数量
   - 不得重复卖出此前已成交部分

13. **非整手 / 小数仓位**
   - 预期：按最小下单单位向下舍入
   - 且阶段完成判定使用容差，不因极小尾差导致永远无法完成

14. **first target（第一目标）与 defensive（防守减仓）同轮相遇**
   - 预期：只执行 first target partial，不再额外叠加 defensive de-risk

15. **gap-through（直接跳到第二目标）但第一阶段只部分成交**
   - 预期：第二阶段本轮不得执行
   - 必须等待第一阶段完整完成后，后续轮次再进入第二阶段

16. **阶段目标因外部减仓已不可达**
   - 预期：该阶段按 `*_target_status = "satisfied_by_external_reduction"` 收口
   - `*_target_hit = false`
   - 不再无限重试

17. **旧 `take_profit`（止盈价）仓位迁移**
   - 预期：由 snapshot assembler（持仓快照组装层）完成一次性映射
   - 映射后旧 `take_profit` 分支对该仓位旁路，不得与新分支双触发

18. **旧仓位已完成旧 50% partial 后再迁移**
   - 预期：直接写成 `first_target_status = "filled"`, `first_target_hit = true`
   - 且 `first_target_filled_qty = 0.50 * original_position_qty`
   - 不得再次触发新的第一阶段分批

19. **status-only terminalization（仅状态终态化）**
   - 预期：即使本轮没有新动作，reconciliation / terminalization pass（对账 / 终态收口步骤）仍会运行
   - 并能把不可达阶段写成 `*_target_status = "satisfied_by_external_reduction"`

20. **runner 脏状态**
   - 预期：当 `runner_protected = true` 但 `runner_stop_price` 缺失 / 非法时
   - 不生成 runner stop exit
   - 只暴露 invalid runner state（无效尾仓状态）

### Should not trigger

1. **命中 first target 前，不应提前保护 runner（尾仓）**
   - 预期：runner_protected = false

2. **只命中 first target，不应自动执行 second partial**
   - 预期：只减 50%

3. **`risk_unit <= 0`**
   - 预期：不生成 target / runner 动作

4. **invalidation 已触发**
   - 预期：优先输出 invalidation exit，不输出 partials

5. **failed follow-through 已触发**
   - 预期：优先输出 failed follow-through exit，不输出 partials

6. **字段缺失**
   - 预期：安全跳过，不误触发

### Boundary checks

测试必须显式覆盖：
- `structure_target_r == 1.0` → 允许采用结构目标
- `structure_target_r < 1.0` → 必须回退到 1R
- `structure_target_r >= 2.0` → 不得作为 first target（第一目标），必须回退到 1R
- second target（第二目标）命中前 → `runner_protected = false`
- second target（第二目标）命中后 → `runner_protected = true`
- `runner_stop_price == first_target_price` → 保护价精确等于第一目标位，而不是 break-even（保本位）
- `first_target_hit = true` 后重复评估 → 不得重复发第一段 partial
- `second_target_hit = true` 后重复评估 → 不得重复发第二段 partial

## Minimal implementation boundary

第一刀代码改动应尽量控制在：
- `trading_system/app/portfolio/exit_policy.py`
- target / runner metadata 组装层（若当前不存在对应字段，则最小补一处）
- execution / state apply（执行 / 状态回写层），以支持 `original_position_qty`、阶段 filled qty（阶段已成交数量）与逐动作写回
- `trading_system/tests/test_exit_policy.py`
- 若需要，再最小补一处 reporting（报告）测试
- 若需要，再最小补一处 lifecycle / apply 测试，验证同轮多动作部分成功时的写回状态
- 若需要，再最小补一处 execution / quantity reconciliation（执行 / 数量对账）测试，验证 remaining qty（剩余仓位）不足时不超卖
- 若需要，再最小补一处 partial-fill-across-rounds（跨轮部分成交）测试，验证不会重复卖出已成交阶段数量

第一版不应扩展成：
- 通用 target engine（通用目标引擎）
- 多形态多参数目标系统
- 复杂 trailing stop framework（复杂跟踪止损框架）

## Success criteria

当这刀完成时，应满足：
- 系统能明确区分 first target（第一目标）与 second target（第二目标）
- first target（第一目标）满足“结构位优先 + 1R 地板价 + 入场冻结”，且严格小于 second target（第二目标）
- second target（第二目标）固定为 2R
- scale-out plan（分批计划）固定为 50 / 25 / 25，且以 original position size（原始仓位）为基准
- runner（尾仓）只在 second target（第二目标）命中后受保护
- runner_stop_price 固定抬到 first_target_price
- runner stop（尾仓止损）被击穿后能退出剩余 runner（尾仓）
- reporting（报告）默认按 B 标准版展示
- 与 invalidation / failed follow-through 的优先级边界清楚
- 测试能够证明触发、不触发与边界场景
