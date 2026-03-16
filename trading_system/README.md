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

- `binance_client.py`：Binance 读取客户端（兼容本机变量名）
- `market_scan.py`：市场快扫
- `account_snapshot.py`：账户快照与持仓摘要
- `generate_plan.py`：生成交易计划 JSON
- `journal.py`：交易理由/复盘记录
- `data/`：本地状态、快照、计划输出
- `runbook.md`：运行说明

## 环境变量

兼容以下任一组：
- `BINANCE_API_KEY` / `BINANCE_API_SECRET`
- `BINANCE_APIKEY` / `BINANCE_SECRET`

## 当前边界

- 当前版本不自动在真实账户下单。
- 当前版本可用于：读账户、做计划、做模拟执行、记录复盘。
- 如后续接入模拟盘执行器，可在 `paper_trades.jsonl` 中持续跟踪。
