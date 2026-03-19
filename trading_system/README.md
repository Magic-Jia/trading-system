# Trading System v1 / v2 P1 Rotation

面向 Claw 的加密交易工作流骨架。

## 目标

- 读取 Binance 账户（当前默认只读）
- 扫描市场与当前持仓
- 生成交易计划
- 对每一笔计划中的开仓/平仓给出充分依据
- 记录复盘与系统改进
- 支持模拟执行（paper trading）

## v2 P1（rotation phase on top of P0）范围

当前仓库已在 v2 P0 主链路基础上接入 rotation engine，但仍是 **partial v2** 覆盖：

1. `regime`：市场状态识别（risk-on / risk-off / neutral）
2. `universe`：动态候选池构建
3. `trend engine`：趋势候选生成
4. `rotation engine`：强势轮动候选生成与评分
5. `validator`：候选风控校验
6. `allocator`：组合风险预算分配
7. `execution`：paper 执行 + 幂等防重
8. `lifecycle/reporting`：仓位生命周期建议与运行摘要输出

当前阶段明确约束：
- `short` 引擎尚未实现，当前运行时保持显式空输出（`short_candidates=[]`）。
- `rotation_candidates` 已接入 runtime state，用于暴露 rotation engine 输出。
- `runtime_state.json` 中 `partial_v2_coverage=true` 仍用于标记当前不是完整 v2，剩余缺口主要是 short engine。
- 保持 paper execution 行为，不扩展到 short 执行链路。

## 目录

### 现有脚本层

- `binance_client.py`：Binance 读取客户端（兼容本机变量名）
- `market_scan.py`：市场快扫
- `account_snapshot.py`：账户快照与持仓摘要
- `generate_plan.py`：生成交易计划 JSON
- `journal.py`：交易理由/复盘记录
- `data/`：本地状态、快照、计划输出
- `runbook.md`：运行说明

### 新的程序骨架层

- `docs/MVP_ARCHITECTURE.md`：自动交易程序 MVP 架构、模块清单、开发顺序
- `app/`：模块化自动交易程序骨架
  - `connectors/`：交易所接口
  - `signals/`：信号与策略
  - `risk/`：风险校验与仓位管理
  - `execution/`：订单执行与幂等保护
  - `portfolio/`：持仓生命周期管理
  - `storage/`：状态与日志落库
  - `reporting/`：日报与异常输出

## 环境变量

兼容以下任一组：
- `BINANCE_API_KEY` / `BINANCE_API_SECRET`
- `BINANCE_APIKEY` / `BINANCE_SECRET`

测试网约定：
- 默认优先从 `/home/cn/.local/secrets/binance-testnet.env` 读取测试网凭证。
- 建议在该文件中维护 `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_API_SECRET`。
- 若测试网文件不可用，再回退到旧的环境变量来源。

## 当前边界

- 当前版本不自动在真实账户下单。
- 当前版本可用于：读账户、做计划、做模拟执行、记录复盘。
- 如后续接入模拟盘执行器，可在 `paper_trades.jsonl` 中持续跟踪。

## 测试与运行（v2 P0）

- 单测：`pytest trading_system/tests/test_main_v2_cycle.py -v`
- 全量测试：`pytest trading_system/tests -v`
- 手动跑一次 paper cycle：
  `TRADING_ACCOUNT_SNAPSHOT_FILE=trading_system/data/account_snapshot.json TRADING_MARKET_CONTEXT_FILE=trading_system/data/market_context.json TRADING_DERIVATIVES_SNAPSHOT_FILE=trading_system/data/derivatives_snapshot.json python -m trading_system.app.main`

运行期预期：
- 标准输出包含 `regime` 与 `portfolio` 两段摘要，其中 `regime.rotation` 会给出 rotation 的紧凑报告（候选/接受/执行符号与 leader 元数据）。
- `trading_system/data/runtime_state.json` 至少包含：
  `positions`、`management_suggestions`、`latest_regime`、`latest_allocations`、`rotation_summary`。
