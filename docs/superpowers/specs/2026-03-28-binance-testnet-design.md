# Binance Testnet 接入设计（2026-03-28）

## 目标

把当前交易程序从“本地 paper 模拟成交”为主，扩展到支持 Binance testnet（测试网 / 模拟盘账户）运行。

为避免本轮范围失控，本设计明确拆成两个阶段：

1. **Phase 1：单次跑通（本轮必须完成）**
   - 用测试网凭证连接 Binance futures testnet（合约测试网）；
   - 读取测试网账户；
   - 完成一轮 strategy cycle（策略循环）；
   - 完成安全校验与下单通道验证；
   - 即使当轮没有合格信号，也要输出完整摘要，证明主链路跑通。

   这里的“下单通道验证”在 Phase 1 中有明确定义：
   - 必须至少完成 **signed authenticated API connectivity（签名鉴权接口联通）**；
   - 必须完成 **exchange-rule validation（交易所规则预校验）**；
   - 必须完成 **订单 payload（报文）构造与字段映射检查**；
   - **默认不要求真实提交订单创建请求**，除非老板显式要求把 `TRADING_TESTNET_ORDER_SUBMISSION_ENABLED=1` 打开。
2. **Phase 2：持续运行 + 真实 testnet 下单（后续阶段，不属于本轮交付完成线）**
   - 只有在 Phase 1 单次跑通已经验证通过后，才允许进入；
   - 在 testnet 模式下按固定间隔持续运行；
   - 在满足全部风险门槛与交易所规则校验后，允许把真实订单发到 testnet；
   - 保留状态、日志、错误恢复与对账。

本轮重点是“尽快安全接通并开始跑”，不是做一轮大重构。

## 现状

当前系统已经具备：

- `paper` / `dry-run` / `live` 这三种 execution mode（执行模式）的配置入口；
- paper execution（本地模拟执行）与 ledger（账本）恢复链路；
- testnet 凭证读取基础：`trading_system/binance_client.py` 已支持在 `BINANCE_USE_TESTNET=1` 时优先从 `/home/cn/.local/secrets/binance-testnet.env` 读取 `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_API_SECRET`；
- strategy cycle 主编排与 runtime state（运行状态）输出。

当前缺口：

- 没有明确的 `testnet` execution mode；
- 账户数据仍主要依赖本地 snapshot（快照）输入，而不是直接从 testnet API 拉取；
- 执行层没有真实的 testnet 下单通道；
- 缺少 testnet 的持续运行入口与运行手册。

## 明确范围

### 交易场景范围

本轮 **只做 Binance Futures testnet（U 本位合约测试网）**，不同时覆盖 spot testnet（现货测试网）。

原因：

- 当前系统的账户结构、持仓语义、止损 / 止盈意图、以及 open positions（持仓）主路径明显更接近合约；
- 若本轮同时覆盖现货与合约，配置、订单模型、仓位语义、最小精度与风控边界都会明显变宽；
- 老板当前目标是“尽快安全接通并开始跑”，优先做合约 testnet 更符合现有程序主干。

### v1 下单支持范围

Phase 1 不要求一定成功发单，但必须把下单路径校验通。

Phase 2 的 v1 最小订单集合固定为：

- `MARKET` 开仓
- `STOP_MARKET` 止损
- `TAKE_PROFIT_MARKET` 止盈

本轮 **不做**：

- 限价开仓
- 追踪止损
- 分批止盈
- spot（现货）下单
- 双向持仓模式的复杂支持

## 设计原则

1. **最小改动优先**：尽量复用现有 strategy cycle、state store、reporting、risk gate，不做大重构。
2. **模式边界清楚**：明确区分 paper（本地模拟）与 testnet（交易所测试网），避免语义混淆。
3. **安全优先**：testnet 模式必须显式锁定测试网 endpoint（接口地址），不能在配置模糊时碰到生产环境。
4. **先跑通再常驻**：先确保单次运行稳定，再做持续运行。
5. **失败要可诊断**：报告里明确区分“无信号”“未执行”“执行成功”“接口报错”“配置错误”。

## 方案对比

### 方案 1：在现有主链路上扩展 testnet 模式（推荐）

在配置层、账户数据层、执行层、运行层各补一层 testnet 分支，复用现有主程序。

**优点**：改动最小，最快上线，最符合“先开始跑”。
**缺点**：短期内 paper 与 testnet 会并存，需要在 runbook（运行手册）中清晰说明区别。

### 方案 2：单独做 testnet runner

保持现有主程序不动，新增专门的 testnet 入口与执行路径。

**优点**：隔离好。
**缺点**：入口与维护成本变高，容易形成重复代码。

### 方案 3：重构 execution layer 为四态统一模型

把 `paper / dry-run / testnet / live` 一次性抽象完整。

**优点**：架构最整齐。
**缺点**：这轮过重，不符合“尽快跑起来”的目标。

**结论**：采用方案 1。

## 目标架构

### 1. 配置层

扩展 execution mode：

- `paper`：本地模拟成交，不接交易所下单；
- `dry-run`：只生成意图，不执行；
- `testnet`：真实调用 Binance testnet API；
- `live`：真实生产环境（本轮不启用）。

新增 / 明确的环境变量：

- `TRADING_EXECUTION_MODE=testnet`
- `BINANCE_USE_TESTNET=1`
- `BINANCE_FAPI_URL=https://testnet.binancefuture.com`
- `TRADING_LOOP_INTERVAL_SECONDS=<轮询秒数>`
- `TRADING_TESTNET_ORDER_SUBMISSION_ENABLED=0|1`
- `TRADING_TESTNET_ALLOWED_SYMBOLS=BTCUSDT,ETHUSDT,...`
- `TRADING_TESTNET_MAX_ORDER_NOTIONAL_USDT=<单笔上限>`
- `TRADING_TESTNET_MAX_OPEN_POSITIONS=<最大持仓数>`
- 继续支持 `/home/cn/.local/secrets/binance-testnet.env`

说明：

- 本轮范围固定为 futures testnet，因此不要求同时引入 spot testnet base URL；
- `TRADING_TESTNET_ORDER_SUBMISSION_ENABLED` 默认应为 `0`，先完成连接与安全校验后，再显式打开真实 testnet 发单。

保护规则：

- 当 `TRADING_EXECUTION_MODE=testnet` 时，若 endpoint（接口地址）不是 testnet 域名，直接拒绝启动；
- 若 key（密钥）缺失或只配了一半，也直接拒绝启动；
- 若 `TRADING_TESTNET_ORDER_SUBMISSION_ENABLED` 未显式打开，则只允许做连接 / 账户 / 信号 / 下单预校验，不允许真实 testnet 发单；
- 即使显式打开了发单开关，也必须先通过 permissions preflight（权限预检）：确认该 key 在 Binance Futures testnet 上具备账户读取与下单所需权限，且账户状态可用于下单；
- 若 symbol（交易对）不在 allowlist（允许名单）内，直接拒绝；
- 若单笔 notional（名义价值）、单 symbol 最大数量、最大持仓数、最大杠杆上限、最小可用保证金缓冲不满足约束，直接拒绝；
- v1 可额外启用“仅当当前无历史 testnet 仓位时才允许新开仓”的保守开关；若检测到未确认的历史脏仓位，默认先阻断新开仓；
- runtime state（运行状态）与日志中必须明确写 `mode=testnet`。

### 账户模式前提

为了把 v1 范围收紧到可安全实现，本轮明确只支持一种账户模式组合：

- **one-way mode（单向持仓模式）**
- **cross margin（全仓）**
- single-asset 模式（不支持 multi-assets mode）
- 使用系统既有的保守 sizing（仓位计算）结果，不额外做动态杠杆切换

不支持的情况：

- hedge / dual-side mode（双向持仓模式）
- isolated margin（逐仓）
- 运行时自动切换杠杆或保证金模式

处理规则：

- 启动或首轮运行时必须检查账户模式；
- 账户模式 / 保证金模式的真相源以 Binance Futures API 返回为准；
- v1 必须显式列出并实现用于判定的接口 / 字段来源；
- 若 API 响应缺字段、语义不清、或无法明确验证 one-way + cross + single-asset，则直接视为 unsupported（不支持）并 fail-fast（快速失败）；
- 若发现不是支持的组合，直接 fail-fast（快速失败），不给出模糊“继续尝试”；
- v1 不负责自动替用户改账户模式。

### 2. 数据层

新增 testnet 账户 / 基础市场数据读取能力：

- 当处于 `testnet` 模式时，优先从 Binance testnet API 拉账户状态，而不是只依赖本地 JSON snapshot；
- 若部分市场 / 衍生品数据在 testnet 不完整，允许降级：
  - 能继续跑策略的，使用公共行情接口补齐；
  - 不能安全跑的，明确报缺失，不静默跳过关键依赖。

输出结果仍尽量复用现有 `AccountSnapshot`、market context（市场上下文）、derivatives snapshot（衍生品快照）结构，减少主流程改动。

#### 混源数据边界

若 testnet 环境里缺少部分分析数据，允许有限混源，但边界必须写死：

- **交易关键校验数据**（exchange rules / filters / order-type 规则 / 账户模式判断）必须来自目标 testnet 交易环境对应的 metadata（元数据）；
- **非交易关键分析数据** 才允许由公共行情接口补齐；
- 任何会影响真实 testnet 下单合法性的判断，不允许基于另一套非目标环境规则。

### 3. 执行层

扩展执行器：

- `paper`：继续走本地 paper executor；
- `dry-run`：继续只返回预览；
- `testnet`：调用 Binance futures testnet 下单接口；
- `live`：继续保持禁用或显式未启用。

testnet execution（测试网执行）v1 固定支持：

- `MARKET` 开仓
- `STOP_MARKET` 止损
- `TAKE_PROFIT_MARKET` 止盈

Futures 保护单字段映射在 v1 中必须固定，不允许实现时自由发挥：

- entry（开仓）：`MARKET`
- stop（止损）：`STOP_MARKET`
- take profit（止盈）：`TAKE_PROFIT_MARKET`
- protective orders（保护单）默认使用 `closePosition=true`
- `workingType` 固定为实现中统一支持的一种（默认优先 `MARK_PRICE`），并在 runbook 中写明
- v1 明确采用“**先确认 entry 已成交 / 已产生仓位，再挂保护单**”的顺序
- v1 不走 quantity-based / reduce-only 保护单替代路径，避免首版出现多套保护语义
- 若 Binance testnet 对某些字段组合不接受，必须在策略输出中明确标出“字段映射不兼容”，而不是静默删字段继续下单

若 testnet 某些订单能力、字段或 close-position（平仓）语义与当前内部意图不完全一致，必须显式降级并记录，不得假装完全等价。

#### 交易所规则预校验

真实发单前，必须先用 exchange metadata（交易所元数据）完成预校验，包括但不限于：

- price precision（价格精度）
- quantity precision / lot size（数量步长）
- min notional（最小名义价值）
- 允许的订单类型
- 当前账户 / symbol 所需的基础限制

原则：**先本地校验，再发 API**，不要把交易所拒单当成常规参数检查器。

#### 防重、幂等与对账

testnet 模式必须定义明确的 intent（意图）生命周期：

1. 生成稳定的 `intent_id` / `client_order_id`；
2. 在本地下单日志 / 状态中预写一条 `pending_submit` 记录；
3. 再向 testnet 提交；
4. 收到结果后更新为 `submitted` / `accepted` / `rejected`；
5. 下一轮运行先按 `client_order_id` 做 reconciliation（对账），再决定是否补发。

必须覆盖的异常场景：

- 提交后、持久化前进程崩溃；
- API 超时，但交易所可能已接单；
- 同一 signal（信号）在连续轮次重复出现；
- entry（开仓）已成交或部分成交，但 protective orders（保护单）挂单失败。

处理规则：

- 同一 strategy intent（策略意图）重复出现时，先查本地状态和 testnet open orders / recent orders，不得直接重复发单；
- API 或网络超时后，先按 `client_order_id` 查询是否已存在订单，再判定该轮失败；
- reconciliation（对账）流程必须有界：立即查询一次；若仍未确认，则在一个短时间窗口内有限重试；若仍无法确认，标记为 `reconciliation_unresolved`（对账未决），并阻止该 intent 的后续重复提交；
- 若 entry 已成交或部分成交，但 protective orders 没有全部挂上，系统必须把该状态标记为 **critical_unprotected_position（关键未保护持仓）**；
- 对 `critical_unprotected_position`，v1 默认策略是：停止继续开新仓、记录高优先级错误，并在同轮做一次有限的 protection retry（保护单重试）；若仍失败，则保留持仓风险告警，等待人工处理，不静默继续；
- v1 对保护单失败后的选择明确为：**不自动强平，不继续补开新仓，不静默保留为正常状态**；若已有部分保护单创建成功，需先按查询结果记录现状，不做盲目全撤；
- 下一轮若发现 `critical_unprotected_position` 尚未解决，只允许进入 **protection-repair path（补保护路径）**，不再为同一 symbol 重新生成新 entry intent（开仓意图）；
- `runtime_state` 不是唯一真相源，testnet reconciliation 结果优先于本地“未知”状态。

执行结果要在 runtime state / logs（日志）中明确包含：

- 订单意图；
- 是否真正发到 testnet；
- 返回的 order id / client id；
- 成功 / 失败原因；
- 是否由 reconciliation（对账）恢复出的最终状态；
- entry 的成交状态、成交数量、均价；
- protective orders 是否全部成功挂出。

### 4. 运行层

增加两类入口：

#### 单次运行

提供一个明确的 testnet 单次运行方式，用来验证：

- 凭证可读；
- endpoint 正确；
- 账户读取成功；
- strategy cycle 可执行；
- Phase 1 的默认成功 **不依赖真实发单**，只依赖签名联通、规则校验、payload 映射与结构化订单预览；
- 只有在显式打开真实提交开关后，才进入额外的真实 testnet 发单验证路径；
- 无信号时能干净退出并给出摘要。

#### 持续运行

先做最稳妥的轮询循环：

- 固定间隔执行一轮；
- 每轮更新 runtime state 与日志；
- 单轮失败不导致整个进程永久失效；
- 保留最近一次成功 / 失败时间与错误摘要；
- 支持手动启动、停止。

失败节流规则必须明确：

- 连续网络 / 认证 / 限频错误时，不做紧密重试；
- 至少进入下一轮冷却，或执行一个有上限的 backoff（退避）；
- order submission（订单提交）同一轮内禁止快速重复提交；
- 若交易所返回限频信息或可识别的重试提示，必须优先遵守；
- 认证 / 配置错误与瞬时网络错误要分开处理，前者优先 fail-fast，后者才进入冷却 / 重试；
- v1 采用简单确定规则：**按轮次级别冷却**，单轮一旦触发提交相关错误，本轮不再重复提交，等待下一轮；
- 若连续多轮都卡在认证 / 权限 / production-assertion（生产环境断言）失败，必须退出自动运行并进入人工干预状态，不允许无限刷错；
- 日志里要区分“正常空仓 / 无信号”和“接口 / 权限异常”，避免刷屏误判。

本轮不强制绑定 systemd service（系统服务）；先保留为 CLI / 脚本入口，验证稳定后再决定是否提升为服务。

## 用户可见行为

### 单次跑通的成功标准

Phase 1 的一次 testnet 运行满足以下条件即视为成功：

1. 成功识别并加载 testnet 凭证；
2. 成功连接 futures testnet endpoint；
3. 成功取得账户状态；
4. 成功完成一轮策略分析；
5. 产出明确摘要：候选、分配、执行预检 / 未执行原因；
6. 若本轮没有任何可下单信号，输出“无执行”而不是报错；
7. 若本轮存在信号，也允许只完成“安全校验 + 下单路径预检”而不真实发单。

也就是说，**单次跑通成功 ≠ 当轮必须真实成交**；只要连接、账户、策略循环、风控与下单路径预校验都通过，就算主目标达成。

为了让“下单通道验证”更可验证，Phase 1 的摘要 / 日志里必须产出一份结构化 `validated_order_preview`（已验证订单预览），至少包含：

- symbol（交易对）
- side（方向）
- qty（数量）
- 计划使用的订单类型集合
- 本地规则校验结果（通过 / 拒绝）
- 若拒绝，明确拒绝原因
- 是否满足真实提交前置条件

### 持续运行的成功标准

1. 可按设定间隔连续运行多轮；
2. 每轮有清晰日志；
3. 中间单次失败后可继续下一轮；
4. 状态文件能反映最新轮次；
5. 不会误连生产环境。

## 错误处理

### 配置错误

- key 缺失
- secret 缺失
- mode=testnet 但 base URL 指向生产
- testnet 相关 env（环境变量）未打开
- 未显式启用 testnet 发单却试图提交真实订单

处理：启动前 fail-fast（快速失败），报明确错误。

### 接口错误

- 网络超时
- API 权限不足
- 测试网余额 / 保证金不足
- 订单参数不被接受
- 提交结果不明确（超时 / 断连，无法立即确认是否已接单）

处理：

- 本轮先标记为 `submission_unknown`（提交结果未知）或明确失败；
- 立即进入 reconciliation（对账）：按 `client_order_id` 查询 open orders / recent orders；
- 确认无单后，才允许下一轮重新评估是否补发；
- 持续运行模式进入下一轮或按 backoff（退避）规则冷却；
- 若错误属于 timestamp skew / recvWindow（时间偏移 / 请求窗口）问题，必须先做 server-time recheck（服务器时间复检）后再继续。

### 数据不足

- testnet 不提供完整衍生品上下文
- 某些 symbol（交易对）数据缺失

处理：
- 能降级则降级；
- 不能安全降级则直接说明该轮无法完成，而不是输出伪完整结果。

## 测试策略

### 自动验证

新增 / 更新测试覆盖：

1. `config`：支持 `testnet` mode，并校验非法 mode / endpoint；
2. `binance client`：testnet 凭证优先级与 endpoint 选择；
3. `account loader`：testnet API 响应映射为内部 `AccountSnapshot`；
4. `executor`：testnet 模式分支是否正确触发，错误是否正确上报；
5. `exchange validation`：quantity / precision / min notional / 订单类型约束能在提交前拦截；
6. `idempotency + reconciliation`：重复 signal、提交超时、重启恢复时不会重复发单；
7. `runner`：持续运行遇到单轮异常时是否继续下一轮，并按冷却 / 退避规则运行。

### 手工验证

分两步：

1. **单次跑通**：验证连接、账户读取、单轮 cycle；
2. **持续运行**：验证连续多轮执行、日志更新、错误恢复。

runbook（运行手册）里必须明确列出以下检查项：

- 实际使用的 env file（环境文件）路径
- 生效的 futures testnet endpoint
- 账户标识 / 权限检查结果
- server time（服务器时间）检查
- timestamp skew / recvWindow 检查结果
- 账户模式检查结果（one-way / cross）
- “production endpoint assertion passed”（生产环境断言已通过）
- 是否开启真实 testnet 发单
- 当前 allowlist、单笔 notional 上限、最大持仓数、最大杠杆上限

## 实施边界

本轮明确做：

- 引入 `testnet` 模式；
- 接 Binance testnet 凭证和 endpoint；
- 支持单次运行；
- 支持持续运行；
- 增加相应测试和 runbook。

本轮明确不做：

- 生产 live execution（真实资金自动执行）；
- 一次性重构整个 execution architecture（执行架构）；
- systemd 常驻服务化；
- 高阶监控 / 告警系统。

## 推荐实施顺序

1. 扩展配置层，加入 `testnet` mode 与防呆校验；
2. 实现 testnet 账户读取与最小 API 适配；
3. 把执行层接出 `testnet` 分支；
4. 先完成单次跑通；
5. 再加持续运行循环；
6. 补 runbook 与 focused verification（聚焦验证）。

## 预期结果

完成后，交易程序将能在 Binance testnet（测试网 / 模拟盘）下真实接入测试账户运行，先完成单次验证，再进入持续轮询运行；同时保留现有 paper 模式，不混淆两者边界，也不误碰生产环境。
