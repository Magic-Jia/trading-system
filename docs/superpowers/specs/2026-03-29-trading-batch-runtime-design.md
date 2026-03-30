# Trading Batch Runtime Design

## Goal

把当前交易系统落成一个适合中低频策略的持续运行方案：使用定时跑批驱动单轮 cycle（单轮执行），严格隔离 `paper` / `testnet` / `live` 三种运行环境的状态目录，并补上最小可用的自动恢复、日志与监控输出。

## Why this shape fits the current system

当前系统已经是典型的快照驱动 + 单轮主循环架构：读取账户 / 市场 / 衍生品快照，生成候选，做风险校验与分配，执行 paper intents，最后持久化 runtime state。它不是高频 websocket 常驻引擎，更像 5m / 15m / 1h 级别的中低频策略系统，因此最稳的持续运行方式不是把 `app.main` 改造成永不退出的事件循环，而是把它包进一个可重入、可恢复的单轮 runner，再由 systemd timer 周期性拉起。

## Non-goals

这次不做：

- live execution 正式启用
- websocket 常驻行情引擎
- 分布式队列 / 多进程微服务拆分
- 复杂外部监控系统（Prometheus / Grafana）
- 新策略逻辑

## Required outcomes

### 1. 定时跑批

系统支持通过 `systemd timer` 定时触发一个 `oneshot` service。每轮执行都应：

1. 解析运行环境（`paper` / `testnet` / `live`）
2. 准备当前环境目录
3. 刷新输入快照（先留统一入口，即使部分数据刷新仍复用现有脚本）
4. 执行主循环
5. 写结构化运行摘要
6. 返回明确退出码

### 2. 状态与路径严格分桶

必须消除任何“不同运行环境共用同一状态文件”的路径。最低要求：

- `runtime_state.json`
- `paper_ledger.jsonl` / 后续 execution ledger
- `account_snapshot.json`
- `market_context.json`
- `derivatives_snapshot.json`
- `latest.json` / 历史 run summary

都必须以运行环境为一级隔离维度。

推荐目录结构：

```text
<base>/<env>/
  state/
    runtime_state.json
    paper_ledger.jsonl
  snapshots/
    account_snapshot.json
    market_context.json
    derivatives_snapshot.json
  logs/
    latest.json
    runs/
```

其中 `<env>` 为 `paper` / `testnet` / `live`。

### 3. 单轮 runner 统一入口

新增统一运行入口，例如 `trading_system/run_cycle.py`，负责一轮任务编排，而不是把 systemd 直接绑在一串 shell 命令上。runner 的职责是：

- 解析环境变量
- 解析当前环境对应的目录路径
- 确保目录存在
- 调用或复用现有脚本刷新快照
- 调用 `trading_system.app.main`
- 记录结构化运行结果
- 在异常时保留足够上下文用于告警 / 调试

runner 本身不负责策略判断，也不应该重新实现 `app.main` 的交易逻辑。

### 4. 结构化运行摘要

除 systemd journal 原始日志外，每轮还应落一份结构化摘要，例如：

```json
{
  "env": "paper",
  "mode": "paper",
  "started_at": "2026-03-29T15:00:00+08:00",
  "finished_at": "2026-03-29T15:00:03+08:00",
  "status": "ok",
  "accepted": 0,
  "rejected": 2,
  "executed": 0,
  "state_file": ".../paper/state/runtime_state.json",
  "account_snapshot_file": ".../paper/snapshots/account_snapshot.json",
  "risk_context_source": "paper"
}
```

这份摘要一方面用于人工排查，一方面为未来接 OpenClaw 主动巡检、失败告警和简单 dashboard 做输入。

### 5. 自动恢复与失败处理

因为方案是“周期性单轮任务”，不使用常驻进程，所以失败恢复策略应当是：

- 单轮失败时本轮退出非 0
- timer 下一轮仍继续调起
- `latest.json` 与 journal 能看出失败原因
- 若 `runtime_state.json` 丢失，已有的 paper ledger replay 机制继续工作

这次不做复杂“重试 N 次”的 runner 内循环，因为那会把批处理语义重新变脏。

### 6. systemd 作为正式调度方式

正式落地采用：

- `trading-system-paper.service`
- `trading-system-paper.timer`

后续 `testnet` / `live` 复用同一模板，只换环境变量和值，不复制业务逻辑。

### 7. 监控与告警最小闭环

第一版至少做到：

- `systemctl status` / `journalctl` 可检查运行状态
- `latest.json` 可检查最近一轮成功 / 失败
- 出错时日志中明确包含运行环境、状态路径、快照路径

告警第一版可以先留为“结构化失败摘要 + OpenClaw/Feishu 后续接入点”，不要求本包必须打通外部通知。

## Design decisions

### Decision A: use systemd timer instead of cron

原因：

- journal 原生日志更好查
- 支持 `Persistent=true` 补跑错过周期
- 更容易看 service 最后一次退出码
- 更适合正式长期运行

### Decision B: use a short-lived runner instead of a daemon

原因：

- 更符合当前主循环结构
- 更容易保证幂等与恢复
- 更容易排错
- 避免常驻进程长期持有脏状态

### Decision C: make runtime env the first-class axis

这次的 paper/live 串账 bug 已经证明，“执行模式”与“状态目录”必须绑定成一等公民。之后所有状态、快照、日志、ledger 与监控输出都必须先按 runtime env 定位，再继续业务逻辑。

## Files likely involved

### New

- `trading_system/run_cycle.py` — 单轮 runner 入口
- `trading_system/app/runtime_paths.py` — 统一管理运行环境到目录路径的解析
- `trading_system/tests/test_run_cycle.py` — runner 层测试
- `trading_system/tests/test_runtime_paths.py` — 环境分桶与路径推导测试
- `deploy/systemd/trading-system-paper.service` — paper service 模板
- `deploy/systemd/trading-system-paper.timer` — paper timer 模板
- `trading_system/docs/BATCH_RUNTIME_RUNBOOK.md` — 新运行手册

### Modify

- `trading_system/app/config.py` — 加入 runtime env / base dir 相关配置
- `trading_system/app/main.py` — 复用统一路径解析，减少手填路径入口
- `trading_system/README.md` — 补持续运行说明
- `trading_system/docs/PAPER_TRADING_RUNBOOK.md` — 指向新的 batch runtime 使用方式

## Acceptance criteria

1. 在 `paper` 环境下执行 runner 后，所有 state / snapshot / log 文件都落在 `paper/` 目录树内。
2. 在 `testnet` 环境下执行 runner 后，不会读写 `paper/` 或 `live/` 目录。
3. `app.main` 继续支持现有单轮运行，但优先通过统一路径解析获取默认文件路径。
4. systemd service + timer 模板存在，并能按文档启动 `paper` 周期任务。
5. 结构化运行摘要能明确展示当前环境、路径、执行结果和关键计数。
6. 相关回归测试覆盖：环境分桶、runner 成功路径、runner 失败路径、paper replay 未被破坏。

## Rollout order

1. 先完成 `paper` 路径
2. 验证稳定后复制到 `testnet`
3. `live` 只预留路径与模板，不在本包开启真实执行
