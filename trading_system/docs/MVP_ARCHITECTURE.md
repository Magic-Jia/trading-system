# 自动交易程序 MVP 架构（v0.3）

这份文档描述的是 **程序骨架**，不是完整的策略路线图。
当前系统已经进入 partial v2 阶段，但“模块存在”不等于“策略成熟”。

如果想看当前策略缺口与升级顺序，请同时参考：

- `trading_system/docs/STRATEGY_GAPS_AND_UPGRADES.md`
- `docs/superpowers/plans/2026-03-23-trading-system-p0-p1-p2-roadmap.md`

---

## 1. 当前程序骨架

当前版本的主链路是：

```text
load account / market / derivatives
  -> classify regime
  -> build universes
  -> generate trend candidates
  -> generate rotation candidates
  -> generate short candidates
  -> validate candidates
  -> allocate portfolio risk
  -> execute accepted intents (paper only)
  -> evaluate lifecycle + management previews
  -> persist runtime state
  -> print regime / portfolio summary
```

核心原则仍然保持不变：

- 风险先于预测
- 没有清晰 invalidation，就不允许执行
- 同一信号不能重复执行
- 程序重启后不能失忆
- 任何自动动作都必须可追溯

---

## 2. 当前实现状态（partial v2）

### 已经具备的模块

1. `regime`：breadth + derivatives 摘要 + regime classifier
2. `universe`：majors / rotation / short universe 构建
3. `trend engine`：顺势候选
4. `rotation engine`：强势轮动候选
5. `short engine`：防御型 regime 下的 majors short 候选
6. `validator`：候选与信号校验
7. `allocator`：组合预算分配、去重、bucket 约束、major / alt balance
8. `execution`：paper execution + idempotency + management previews
9. `lifecycle / reporting`：仓位管理建议、lifecycle summary、rotation / short summary
10. `storage`：runtime state / journal store

### 仍然明确未完成的部分

- live execution 未启用
- short execution 未启用，当前只生成候选与分配结果
- strategy layer 仍然太依赖价格结构，crypto-specific 特征还不够深入
- stop taxonomy 与 exit system 仍然偏薄
- crash / cascade / squeeze 的 regime protection 仍未单独建模

---

## 3. 目录骨架

```text
trading_system/
├── README.md
├── runbook.md
├── docs/
│   ├── MVP_ARCHITECTURE.md
│   └── STRATEGY_GAPS_AND_UPGRADES.md
├── app/
│   ├── config.py
│   ├── types.py
│   ├── main.py
│   ├── connectors/
│   ├── market_regime/
│   ├── universe/
│   ├── signals/
│   ├── risk/
│   ├── execution/
│   ├── portfolio/
│   ├── storage/
│   └── reporting/
└── data/
```

---

## 4. 模块职责

### `market_regime/`

- 聚合 breadth 与 majors derivatives 摘要
- 输出 regime label、confidence、risk multiplier、bucket targets、suppression rules
- 当前仍缺少 crash / cascade / squeeze 专门保护层

### `universe/`

- 构建 majors、rotation、short universe
- 为 allocator 与各引擎提供可交易候选范围

### `signals/`

- `trend_engine.py`：顺势延续候选
- `rotation_engine.py`：相对强势轮动候选
- `short_engine.py`：防御型空头候选
- 当前问题不是“没有引擎”，而是“引擎的 crypto-specific 特征还不够深”

### `risk/`

- `validator.py`：候选与信号是否合法
- `guardrails.py`：账户级 / 组合级限制
- `position_sizer.py` / `regime_risk.py`：仓位预算与 regime-aware risk

### `portfolio/`

- `allocator.py`：组合分配、bucket 预算、重复 setup crowding penalty、major / alt balance
- `positions.py`：持仓同步
- `lifecycle.py` / `lifecycle_v2.py`：仓位管理与 lifecycle 状态推进

### `execution/`

- `executor.py`：paper / dry-run 执行
- `orders.py`：entry / stop / take-profit payload 与 management preview
- `idempotency.py`：防重复执行与 replay 辅助

### `storage/` / `reporting/`

- 存储 runtime state、journal、management outputs
- 输出 regime / rotation / short / lifecycle 摘要

---

## 5. 下一步原则

后续实现不要再把“加一个价格结构规则”当作主要策略升级。

正确方向是：

1. 先把 execution-safety 与 strategy-development 分开
2. 策略线优先推进 crypto derivatives、crowding、absolute strength、overheat filters
3. 再补 richer stop taxonomy 与 exit system
4. 然后才让 short 与 crash protection 成熟

换句话说，当前系统最缺的不是“更多模块名”，而是 **更像 crypto 的策略判断层**。
