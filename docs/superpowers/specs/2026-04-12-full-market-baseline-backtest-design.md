# Full-Market Baseline Backtest Design

Date: 2026-04-12
Owner: Claw
Status: Draft revised after written-spec review

## Goal

为当前自动交易程序补齐一套**全市场、全周期、接近实盘口径**的 baseline backtest（基线回测）设计，先回答三个问题：

1. 当前系统在 **spot（现货）+ futures（合约）**、**共享资金池**、**真实摩擦成本** 下到底表现如何。
2. 当前系统的问题主要出在 **signal quality（信号质量）**、**exit / position management（出场 / 仓位管理）**、**portfolio constraints（组合约束）**，还是 **cost drag（成本拖累）**。
3. 下一轮优化应该优先改哪一层，而不是继续凭感觉改策略逻辑。

本设计不是直接追求“把回测收益做漂亮”，而是先建立一套**可复现、可审计、可归因**的回测基线，为后续优化自动交易程序提供可靠参照。

## Why this slice

当前系统已经具备策略逻辑、持仓管理、paper execution（模拟执行）、reporting（报告）等主链路，但缺少一套能回答“在真实组合约束下，这个系统到底行不行”的统一回测框架。

如果现在直接继续优化出场 / 仓位管理，会有三个风险：

- 不知道当前瓶颈到底在哪，容易优化错方向。
- 缺少全市场、全周期的统一基线，改动前后无法公平比较。
- 容易把策略 edge（优势）、组合约束、成本拖累混成一个结果，导致错误结论。

因此第一阶段先做 baseline backtest（基线回测），先把**真实问题分层拆开**，再决定下一轮优化切哪一刀。

## User-confirmed constraints

本设计基于老板已确认的约束：

- 回测目标：优先看 **return / drawdown balance（收益 / 回撤平衡）**，不是只看名义总收益。
- 市场范围：**spot（现货）+ futures（合约）一起**。
- 时间范围：采用能覆盖 bull / bear / chop（三类市场环境）的统一口径；第一阶段固定为 **`2021-01-01` 到回测执行时的最新可用数据**。
- 成本口径：采用**尽量真实**口径，统一计入：
  - fee（手续费）
  - slippage（滑点）
  - funding（资金费率，仅合约）
- 标的池：采用**流动性过滤后的全市场**，不是无过滤地把所有可见标的都塞进回测。
- 资金模型：采用 **shared capital pool（共享资金池）**，所有标的一起竞争同一笔资金。
- 持仓约束：采用 **dynamic position cap（动态持仓上限）**，不写死固定最大持仓数。
- 单笔仓位：采用 **fixed risk per trade（固定单笔风险）**，按止损距离反推仓位大小，而不是固定资金占比。
- 第一阶段不做参数暴力寻优，不在回测过程中顺手改策略逻辑。

## Scope

### In scope

第一阶段只覆盖以下内容：

- 统一 spot（现货）与 futures（合约）的历史回测入口。
- 流动性过滤后的全市场 universe（标的池）构建。
- 共享资金池下的组合级信号竞争、资金分配与开仓裁决。
- 动态持仓上限、总风险预算、单笔固定风险 sizing（仓位计算）。
- 成本模型：手续费、滑点、资金费率。
- 组合级结果输出：收益、回撤、胜率、盈亏比、资金曲线。
- 结果拆解：按年份、市场类型、标的、策略阶段做归因。
- 交易流水（trade ledger，交易流水）和拒单 / 剔除原因可追溯。
- 为下一轮优化输出明确优先级建议。

### Out of scope

第一阶段不做以下事项，避免 scope（范围）失控：

- 参数寻优、网格搜索、walk-forward（滚动前瞻）调参。
- 高频 / 分钟级撮合仿真。
- order book（订单簿）级冲击成本模拟。
- 新策略逻辑发明或现有信号逻辑重写。
- 机器学习打分层。
- 组合层更复杂的跨资产相关性优化器。
- 以“回测好看”为目标调整过滤参数。

## Design summary

第一阶段固定采用**双阶段路线**：

1. **Stage 1: Auditable baseline（可审计基线）**
   - 先建立一套全市场、共享资金池、真实摩擦成本的统一回测框架。
   - 输出可信基线与问题定位结果。

2. **Stage 2: Realism upgrades（真实度增强）**
   - 在基线框架跑通后，再增量增强更重的真实约束，例如更细的滑点层级、更复杂的 crowding（拥挤）规则等。
   - 这一阶段不是本 spec（规格）的实现范围，但会作为下一轮可选扩展方向保留接口。

本 spec 只定义 **Stage 1** 的完整边界与交付标准。

## Architecture

第一阶段回测架构固定拆成 5 层，每层只做一件事，避免把数据问题、信号问题、组合问题、成本问题搅成一团。

### 1) Market data layer（市场数据层）

职责：

- 提供统一时间轴上的 OHLCV（开高低收量）历史数据。
- 提供 futures（合约）所需 funding（资金费率）时间序列。
- 提供 symbol metadata（标的元数据），至少包括：
  - 上市时间
  - 市场类型（spot / futures）
  - 最小下单单位
  - 价格与数量精度
- 提供 universe filtering（标的池过滤）所需的流动性与可交易性输入。

边界：

- 只负责“把干净、可用的数据交给上游”，不负责生成交易信号。
- 缺失数据、不完整 funding、精度信息缺失，应在此层完成识别并打上可审计标记。

### 2) Signal generation layer（信号生成层）

职责：

- 复用当前自动交易程序已有的策略逻辑。
- 在历史时间轴上生成 candidate intents（候选交易意图）。
- 输出结构化字段，至少包括：
  - symbol
  - market type（spot / futures）
  - side
  - entry timestamp
  - entry reference price
  - stop loss
  - strategy leg / setup type（策略腿 / 形态类型）
  - any sizing inputs needed downstream（供下游 sizing 使用的必要字段）

边界：

- 此层不做组合层资金竞争。
- 此层不做最终开仓裁决。
- 此层不偷偷加入“为了让回测更好看”的过滤器；组合与风险约束必须放在下游统一处理。

### 3) Portfolio execution layer（组合执行层）

职责：

- 把所有候选信号放进 shared capital pool（共享资金池）里统一竞争。
- 决定候选交易是：
  - accepted（接受开仓）
  - resized（缩小仓位）
  - deferred（延后）
  - rejected（拒绝）
- 统一施加：
  - total open risk budget（总开仓风险预算）
  - capital availability（可用资金约束）
  - dynamic position cap（动态持仓上限）
  - market-type coexistence rules（现货 / 合约并存规则）
  - crowding rules（拥挤规则）

边界：

- 此层不生成策略信号。
- 此层不估算滑点与手续费；它只决定“能不能开、开多大、为什么”。

### 4) Cost and fill simulation layer（成本与成交仿真层）

职责：

- 对被接受的交易统一应用：
  - fee（手续费）
  - slippage（滑点）
  - funding（资金费率，仅 futures）
- 把 candidate intent（候选意图）转成 backtest fill（回测成交）与持仓变动。
- 保证同一成本输入反复执行结果一致，可复现、可审计。

边界：

- 第一阶段只做 bar-based（K 线级）保守模拟，不做 order book（订单簿）级撮合。
- 若成本输入缺失，应拒绝产出“伪真实结果”；要么明确降级，要么中止相关子市场回测并写出原因。

### 5) Attribution and reporting layer（归因与报告层）

职责：

- 输出组合级 summary（总览）指标。
- 输出 trade ledger（交易流水）。
- 输出 rejection / resize ledger（拒单 / 缩仓流水）。
- 输出按时间、市场、标的、策略阶段拆分的归因报告。
- 输出下一轮优化建议。

边界：

- 此层不做策略修改。
- 此层不回写交易状态。
- 此层只负责解释结果，不负责重新定义结果。

## Data flow

第一阶段数据流固定为：

1. 原始历史数据进入 market data layer（市场数据层）。
2. 数据层完成清洗、对齐、过滤，并形成可回测 universe（标的池）。
3. signal generation layer（信号生成层）按时间轴生成候选交易意图。
4. portfolio execution layer（组合执行层）在共享资金池里决定哪些信号被允许执行，以及仓位大小。
5. cost and fill simulation layer（成本与成交仿真层）对成交应用真实摩擦成本，并更新模拟持仓与权益曲线。
6. attribution and reporting layer（归因与报告层）输出结果总览、交易流水、拆解报告和优化建议。

该数据流必须支持 deterministic replay（确定性复现）：同样的输入数据、同样的配置、同样的时间范围，多次执行应得到同样结果。

## Universe definition

第一阶段 universe（标的池）固定采用“流动性过滤后的全市场”，具体规则如下。

### 1) Listing age filter（上市时长过滤）

- 新上市标的在上市初期不纳入回测。
- 第一阶段固定要求：symbol（标的）只有在上市后累积达到最小可观测窗口后才可参与回测。
- 最小可观测窗口不由临时人工判断，而由统一配置控制，并在报告中可见。

目标：避免把极端不稳定、历史极短的标的混进全市场样本。

### 2) Liquidity filter（流动性过滤）

- 标的必须满足最小成交连续性与最小成交额要求。
- 流动性过滤必须按 market type（spot / futures）分开计算，不得用同一阈值粗暴套用到两个市场。
- 被过滤掉的标的必须可追溯到具体原因：
  - 成交额过低
  - 活跃度不足
  - 时间序列断裂

目标：剔除无法真实执行的冷门标的，避免回测利润被少数不可交易样本污染。

### 3) Data completeness filter（数据完整性过滤）

- 缺 OHLCV 的标的不得参与该时间段回测。
- futures（合约）缺 funding（资金费率）时，不得默默按 0 填充后冒充真实回测；必须显式标记为：
  - excluded（排除），或
  - degraded mode（降级模式）
- 第一阶段默认优先选择 **excluded（排除）**，避免结果被错误 funding 稀释。

### 4) Tradeability filter（可执行性过滤）

- 缺失 price / quantity precision（价格 / 数量精度）信息的标的不得纳入真实口径回测。
- 最小下单量、最小名义价值不满足的候选单应在组合层或成交层被拒绝，并写入 rejection ledger（拒单流水）。

## Time window

第一阶段固定回测窗口为：

- start: `2021-01-01`
- end: 回测运行时的最新可用历史数据截止点

该窗口的设计目的不是“越长越好”，而是覆盖当前 crypto（加密）市场中最重要的三类环境：

- bull market（牛市）
- bear market（熊市）
- choppy / range market（震荡 / 区间市场）

更早历史不默认纳入第一阶段基线，原因是：

- 更早期很多标的并不存在。
- spot / futures 的市场结构与流动性质量不一致。
- funding、精度、交易元数据更容易缺失。

## Capital model

第一阶段资金模型固定采用 **shared capital pool（共享资金池）**。

### Shared pool contract（共享资金池契约）

- 所有 spot（现货）与 futures（合约）候选交易共享同一套组合级资金约束。
- 某个标的是否能开仓，不只取决于它自己的信号质量，也取决于当时组合层是否还有可用风险预算与资金空间。
- 回测结果必须体现“好信号之间互相竞争资金”的现实，而不是把每个标的都当成独立账户。

### Why not isolated capital（为什么不用独立资金）

第一阶段不采用“每个标的各自独立资金”的原因：

- 那会系统性高估真实收益。
- 无法暴露组合层拥挤、相关性集中、同时开仓过多等实盘核心问题。
- 无法回答“当前程序能不能真实上组合层实盘”。

## Position sizing

第一阶段单笔仓位固定采用 **fixed risk per trade（固定单笔风险）**。

### Sizing rule（仓位规则）

- 每笔交易先由 stop distance（止损距离）定义 risk unit（风险单位）。
- 目标仓位大小由“单笔允许承受的最大亏损”反推得出。
- 止损远的交易，仓位自动变小。
- 止损近的交易，仓位可以更大。

### Why this rule（为什么采用这条规则）

- 能跨越不同波动率、不同价格级别的标的做公平比较。
- 比固定资金占比更符合真实交易逻辑。
- 能和共享资金池、动态持仓限制自然衔接。

### Guardrails（护栏）

组合层必须在 sizing（仓位计算）后再做至少三层保护：

- 若计算结果低于最小下单规模，则拒单并记原因。
- 若计算结果会突破总风险预算，则缩仓或拒单。
- 若计算结果会导致资金占用超出允许范围，则缩仓或拒单。

## Dynamic position cap and portfolio constraints

第一阶段不采用固定“最多开 N 个仓位”的硬编码，而采用 **dynamic position cap（动态持仓上限）**。

### Decision order（决策顺序）

组合层对候选信号必须按以下顺序做裁决：

1. 检查 total open risk budget（总开仓风险预算）是否允许新增风险。
2. 检查当前 capital availability（可用资金）是否支持新增或扩张仓位。
3. 检查当前持仓结构是否已过度拥挤。
4. 只有前三项都通过，才允许该信号进入最终成交层。

### Dynamic cap inputs（动态上限输入）

第一阶段动态持仓上限由以下输入共同决定：

- 当前已占用的 open risk（开仓风险）
- 当前已占用资金
- 当前活跃持仓数
- 当前是否已有同一 base asset（基础币种）的 spot / futures 暴露

### First-stage crowding rule（第一阶段拥挤规则）

第一阶段拥挤规则采用保守且可解释的最小版本：

- 默认不允许同一 base asset（基础币种）在同方向同时持有重复暴露的 spot + futures 头寸。
- 若组合层已经持有某一 base asset 的方向暴露，新候选单默认作为 crowding conflict（拥挤冲突）处理，需拒绝或显式缩减。

第一阶段不做更复杂的 sector / theme（板块 / 主题）级相关性建模；那属于后续增强范围。

## Cost model

第一阶段成本模型固定为“保守但稳定”，宁可略高估，也不低估。

### 1) Fees（手续费）

- fee（手续费）必须按 market type（spot / futures）分别建模。
- 第一阶段允许使用统一默认费率表，但费率表必须显式可配置、可审计。
- 不允许把 spot 与 futures 强行混成同一费率。

### 2) Slippage（滑点）

- slippage（滑点）不得统一为一个对所有标的都一样的死值。
- 第一阶段采用**按流动性分层**的保守滑点模型：
  - 流动性更差的标的，滑点更高
  - 流动性更好的标的，滑点更低
- 分层规则必须写入输出报告，可追溯每笔交易用的是哪一层滑点假设。

### 3) Funding（资金费率）

- funding（资金费率）只适用于 futures（合约）持仓。
- spot（现货）持仓不得错误计入 funding。
- funding 必须按真实时间序列累计到持仓层，而不是一次性粗略扣减。

## Entry, hold, and exit semantics in backtest

第一阶段不发明新策略，只回放当前自动交易程序已有的逻辑。为了确保可审计性，回测必须清晰区分：

- signal emitted（信号产生）
- trade accepted / rejected（交易被接受 / 拒绝）
- fill simulated（成交被模拟）
- position updated（持仓被更新）
- exit triggered（退出被触发）

回测输出必须能回答每一笔交易：

- 为什么进
- 为什么没有进
- 为什么这个仓位大小
- 为什么这个时点出
- 退出收益里有多少被成本吃掉

## Outputs

第一阶段交付固定为 4 类输出。

### 1) Backtest entrypoint（回测入口）

- 一条稳定、可重复执行的命令或配置化入口。
- 同样输入多次执行，结果一致。

### 2) Portfolio summary（组合总览）

至少包含：

- total return（总收益）
- annualized return（年化收益）
- max drawdown（最大回撤）
- return / drawdown ratio（收益 / 回撤比）
- win rate（胜率）
- profit factor 或 average win / average loss（盈亏效率指标）
- equity curve（资金曲线）

### 3) Breakdown reports（拆解报告）

至少支持以下拆分：

- 按年份
- 按 market type（现货 / 合约）
- 按 symbol（标的）
- 按 strategy leg / setup family（策略腿 / 形态家族）

### 4) Audit outputs（审计输出）

至少包含：

- trade ledger（交易流水）
- rejection ledger（拒单流水）
- resize / cap ledger（缩仓 / 上限裁决流水）
- universe inclusion / exclusion ledger（标的纳入 / 排除流水）

## Verification strategy

第一阶段验证固定分为 3 层。

### 1) Data validation（数据验证）

必须验证：

- OHLCV、funding、symbol metadata 时间轴对齐。
- funding 只作用于 futures（合约）。
- 上市 / 退市 / 缺数据标的被正确过滤或排除。
- 精度、最小下单量、名义价值限制能正确影响成交模拟。

### 2) Engine validation（引擎验证）

必须验证：

- 同一输入反复跑结果一致。
- 共享资金池不会超配。
- fixed risk per trade（固定单笔风险）与止损距离对应正确。
- 动态持仓上限在风险预算与资金占用层面真实生效。
- fee / slippage / funding 三类成本都能单独审计。

### 3) Result sanity validation（结果合理性验证）

必须验证：

- 抽样交易流水后，能解释每笔交易的进出逻辑与仓位大小。
- 没有因为少数异常标的、缺失 funding、错误精度处理而出现“虚高业绩”。
- 组合结果的收益与回撤来源可解释，而不是只能看一条净值线。

## Error handling and failure modes

第一阶段必须明确处理以下失败模式，而不是默认吞掉：

- spot / futures 历史数据时间轴错位。
- funding（资金费率）缺失或不连续。
- 价格 / 数量精度元数据缺失。
- universe filtering（标的过滤）输入缺失。
- 仓位计算后低于最小下单规模。
- 成本模型输入缺失导致“伪真实”结果。

默认原则：

- 能安全排除的，明确排除并记录原因。
- 不能安全降级的，明确中止该子任务，而不是悄悄继续。
- 所有排除 / 中止原因必须进入可审计输出。

## Success criteria

第一阶段完成标准固定为：

1. 能稳定复现同一结果。
2. 能在 spot（现货）+ futures（合约）全市场、共享资金池口径下给出可信基线。
3. 能拆出收益与回撤的主要来源。
4. 能明确告诉老板：下一轮优化应优先改信号、出场、仓位还是组合约束。

如果只得到一堆图表，却回答不了“下一轮先改什么”，则视为第一阶段没有真正完成。

## Explicit non-goals

为防止 spec（规格）膨胀，第一阶段明确不承担以下目标：

- 证明当前策略已经可以直接上线实盘。
- 把所有真实世界摩擦一次建模到极致。
- 为所有市场环境调出最优参数。
- 在同一轮里同时做回测框架建设和策略重写。

## Final design decision

本 spec 最终固定采用：

- 全市场但经过流动性过滤的 universe（标的池）
- `2021-01-01` 至最新可用数据的统一回测窗口
- spot（现货）+ futures（合约）联合回测
- fee（手续费）+ slippage（滑点）+ funding（资金费率）的真实摩擦成本口径
- shared capital pool（共享资金池）
- dynamic position cap（动态持仓上限）
- fixed risk per trade（固定单笔风险）
- 以 return / drawdown balance（收益 / 回撤平衡）为核心目标函数
- 先交付 auditable baseline（可审计基线），再决定第二阶段真实度增强项

这意味着第一阶段的正确产物不是“一个更漂亮的收益数字”，而是：

- 一条可信的组合级基线
- 一份能解释系统优缺点的审计输出
- 一张明确的下一轮优化优先级清单
