# 自动交易程序 MVP 架构（v0.2）

面向老板当前这套 Binance 交易系统，下一步不是继续堆扫描脚本，而是把它整理成一个可自动执行的程序骨架：

1. 信号输入
2. 风险校验
3. 下单执行
4. 持仓管理
5. 复盘记录

核心原则：
- 风险先于预测
- 没有止损，就不允许下单
- 同一信号只能执行一次
- 程序重启后不能失忆
- 任何自动动作都必须可追溯

---

## v2 P0 当前实现状态（partial v2）

当前版本是 **partial v2**，已落地 v2 主链路中的关键骨架：

1. `regime`：市场状态识别
2. `universe`：动态候选池
3. `trend engine`：趋势候选
4. `validator`：风险校验
5. `allocator`：组合预算与候选接受/降配决策
6. `execution`：paper 执行 + 幂等标记
7. `lifecycle/reporting`：持仓建议与摘要输出

当前引擎覆盖说明：
- 已实现：trend engine
- 未实现（保留到后续阶段）：rotation engine、short engine
- 运行时要求：`rotation_candidates` 与 `short_candidates` 显式为空列表，并通过 `partial_v2_coverage=true` 标识当前覆盖范围。

v2 P0 的运行顺序（main cycle）：

```text
load account/market/derivatives
  -> classify regime
  -> build universe
  -> generate trend candidates
  -> validate candidates
  -> allocate risk (allocator)
  -> execute accepted intents (paper + idempotency)
  -> evaluate lifecycle + persist runtime state
  -> print regime/portfolio summary
```

测试与运行预期：
- `pytest trading_system/tests -v` 全量通过。
- 手动执行 `python -m trading_system.app.main`（配套 runtime 输入）应输出 `regime` + `portfolio` 摘要，且无 traceback。
- `runtime_state.json` 保留旧字段并新增/刷新 `latest_regime`、`latest_allocations` 等 v2 字段。

---

## 1. 目录骨架（建议落地结构）

```text
trading_system/
├── README.md
├── runbook.md
├── docs/
│   └── MVP_ARCHITECTURE.md
├── app/
│   ├── __init__.py
│   ├── config.py               # 配置加载：风控阈值、交易参数、环境变量
│   ├── types.py                # 统一数据结构定义（signal, order_intent, position）
│   ├── main.py                 # 程序入口：串联一次完整交易循环
│   ├── connectors/
│   │   ├── __init__.py
│   │   └── binance.py          # 交易所接口封装（账户、订单、持仓、行情）
│   ├── signals/
│   │   ├── __init__.py
│   │   ├── market_scan.py      # 市场扫描
│   │   └── strategy_trend.py   # 第一版简单趋势策略
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── position_sizer.py   # 仓位 sizing
│   │   ├── guardrails.py       # 风险闸门、熔断、相关性限制
│   │   └── validator.py        # 信号是否合法、是否允许开仓
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── executor.py         # 下单执行器
│   │   ├── orders.py           # 下单/撤单/改价
│   │   └── idempotency.py      # 防重复下单
│   ├── portfolio/
│   │   ├── __init__.py
│   │   ├── positions.py        # 持仓同步
│   │   └── lifecycle.py        # 开仓后管理：止损、减仓、移动保护
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── state_store.py      # 程序状态落库
│   │   └── journal_store.py    # 交易日志/复盘落库
│   └── reporting/
│       ├── __init__.py
│       └── daily_report.py     # 日报、异常提醒、策略表现摘要
└── data/
    ├── account_snapshot.json
    ├── market_scan.json
    ├── trade_plan.json
    ├── trade_plan_scored.json
    ├── paper_trades.jsonl
    ├── journal.jsonl
    └── runtime_state.json
```

这个结构是为了把当前偏脚本化的流程，收束成一个真正能自动执行的程序。

---

## 2. MVP 运行主链路

第一版只做一条简单、可验证、可审计的自动交易闭环：

```text
行情/账户读取
  -> 生成信号
  -> 风险校验
  -> 计算仓位
  -> 生成订单意图
  -> 执行下单
  -> 写入状态与日志
  -> 持仓跟踪与退出管理
```

### 每一步要回答的问题

#### A. 生成信号
- 有没有符合条件的交易机会？
- 方向是什么？
- 入场逻辑是什么？
- 失效点在哪里？

#### B. 风险校验
- 当前账户还能不能继续承担风险？
- 这笔交易会不会让总风险暴露超标？
- 该标的是否与已持仓高度相关？
- 有没有缺止损、止损太近、流动性太差等问题？

#### C. 计算仓位
- 按账户权益和止损距离，最大可开多大？
- 在高波动环境下是否自动降仓？
- 是否需要限制单笔名义仓位？

#### D. 执行下单
- 是市价、限价，还是分批挂单？
- 失败时是否重试？
- 如何确保同一信号不会下两次？
- 下单后是否同步挂止损/止盈？

#### E. 持仓管理
- 开仓后何时移动止损？
- 何时部分止盈？
- 信号失效时是减仓还是直接退出？

#### F. 日志与复盘
- 这笔单为什么做？
- 是否符合系统？
- 实际结果如何？
- 哪个模块做对了，哪个模块做错了？

---

## 3. MVP 必须先完成的模块清单

### P0：没有这些，别上自动执行

1. **Binance 连接器**
   - 账户读取
   - 持仓读取
   - 订单查询
   - 下单/撤单
   - 兼容本机现有环境变量名

2. **Risk Engine（风险引擎）**
   - 单笔风险上限
   - 总风险敞口上限
   - 相关性限制
   - 波动率降仓
   - 没有止损则拒绝开仓

3. **Position Sizer（仓位计算）**
   - 根据账户权益、入场价、止损价反推仓位
   - 单笔名义仓位上限
   - 输出计划亏损金额与理论盈亏比

4. **Order Executor（执行器）**
   - 下单
   - 止损单
   - 止盈单
   - 失败重试
   - 防重复下单

5. **State Store（状态存储）**
   - 当前活跃订单
   - 当前持仓
   - 最近执行的信号 ID
   - 冷却时间
   - 熔断状态

6. **Journal / Audit Log（日志）**
   - 每笔单的原因、执行动作、返回结果、退出结果

---

## 4. 当前已有脚本如何映射到新架构

现有脚本不是废掉，而是拆入新结构：

- `binance_client.py` -> `app/connectors/binance.py`
- `market_scan.py` -> `app/signals/market_scan.py`
- `position_sizing.py` -> `app/risk/position_sizer.py`
- `paper_executor.py` -> `app/execution/executor.py` 的模拟模式
- `journal.py` -> `app/storage/journal_store.py`
- `daily_report.py` -> `app/reporting/daily_report.py`

这样做的好处：
- 先保留现有成果
- 再逐步把脚本拼装成系统
- 避免一把重写导致回退

---

## 5. 开发顺序（建议 backlog）

### Phase 1：先把“底盘”做出来
1. `connectors/binance.py`
2. `risk/validator.py`
3. `risk/guardrails.py`
4. `risk/position_sizer.py`
5. `storage/state_store.py`
6. `execution/idempotency.py`
7. `execution/orders.py`
8. `execution/executor.py`

### Phase 2：再把“持仓生命周期”做出来
9. `portfolio/positions.py`
10. `portfolio/lifecycle.py`
11. `storage/journal_store.py`
12. `reporting/daily_report.py`

### Phase 3：最后才扩展策略
13. `signals/strategy_trend.py`
14. 多周期过滤
15. 候选池轮动
16. 结构化回测与表现归因

---

## 6. 第一版只做一套简单策略

不要一开始就搞多策略切换、AI 预测、多因子融合。

第一版只建议：
- 趋势过滤
- 回踩/突破其中一种触发
- 固定的止损逻辑
- 固定的分批止盈逻辑

原因很简单：
- 先验证执行系统是否稳定
- 先验证风险引擎是否真能控损
- 先验证日志是否能支持复盘

自动交易系统先求**稳**，再求**花**。

---

## 7. 里程碑定义

### Milestone A：能安全读、能安全判
- 读取账户/持仓/订单
- 接收单一策略信号
- 输出“允许/拒绝开仓”与建议仓位

### Milestone B：能模拟执行
- 执行 paper trade
- 写入状态
- 防重复
- 记录完整日志

### Milestone C：能小规模实盘
- 真单执行
- 止损/止盈联动
- 熔断生效
- 异常可恢复

---

## 8. 一句话决策

下一步自动交易程序不该先做更复杂的择时，而是：

**先把 Risk Engine + Position Sizer + Order Executor + State Store 这一层做实。**

这是把当前“分析脚本集合”变成“自动交易程序”的分水岭。
