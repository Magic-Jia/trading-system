# Trading System v1

面向 Claw 的加密交易工作流骨架。

## 目标

- 读取 Binance 账户（当前默认只读）
- 扫描市场与当前持仓
- 生成交易计划
- 对每一笔计划中的开仓/平仓给出充分依据
- 记录复盘与系统改进
- 支持模拟执行（paper trading）

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
