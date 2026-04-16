# trading-system

A Binance-oriented crypto trading research and paper-trading system.

这个仓库当前以 **paper-first** 为边界，聚焦于：
- 市场扫描与账户快照
- 趋势 / 轮动 / short 候选生成
- 风险校验、仓位分配、组合管理
- paper trading 执行与运行状态持久化
- backtest / dataset / archive 研究链路

> 当前默认边界：**不自动进行真实资金下单**。live execution 不是这个仓库当前默认启用的模式。

---

## 仓库现在有什么

主代码位于：`trading_system/`

核心能力包括：
- **market regime**：市场状态识别、breadth / derivatives 摘要
- **signal engines**：trend / rotation / short 候选与评分
- **risk & portfolio**：validator、position sizing、allocator、lifecycle
- **execution**：paper execution、幂等防重、ledger 记录
- **reporting**：daily / regime / lifecycle 摘要
- **research**：backtest engine、archive capture / importer、实验与指标统计

部署与调度示例位于：
- `deploy/systemd/`
- `deploy/cron/`

设计文档与计划位于：
- `docs/`

---

## 目录概览

```text
.
├── trading_system/          # 主程序与测试
│   ├── app/                 # 模块化交易系统骨架
│   ├── docs/                # 子系统文档
│   ├── tests/               # pytest 测试
│   ├── run_cycle.py         # 单次 paper/runtime cycle 入口
│   └── README.md            # 更详细的子目录说明
├── deploy/
│   ├── systemd/             # systemd service/timer 示例
│   └── cron/                # cron 脚本示例
├── docs/                    # 设计 / 计划 / 说明文档
└── README.md                # 当前文件
```

---

## 快速开始

### 1. 准备环境
仓库中的测试与运行命令目前主要按现有文档使用 `uv` + `python` / `pytest`。

你至少需要：
- Python 3
- `uv`
- Binance API 凭证（只在需要读取账户或市场接口时）

兼容的环境变量命名：
- `BINANCE_API_KEY` / `BINANCE_API_SECRET`
- `BINANCE_APIKEY` / `BINANCE_SECRET`

测试网约定（如果你走 testnet）：
- 默认优先读取 `/home/cn/.local/secrets/binance-testnet.env`
- 建议在其中维护：
  - `BINANCE_TESTNET_API_KEY`
  - `BINANCE_TESTNET_API_SECRET`

### 2. 跑测试
```bash
uv run --with pytest python -m pytest trading_system/tests -v
```

### 3. 手动跑一次 paper cycle
```bash
python -m trading_system.run_cycle --mode paper
```

如果当前 paper bucket 缺少输入快照，程序会优先在运行目录下自动补齐所需快照后再执行。

### 4. 运行 backtest research CLI
```bash
python -m trading_system.app.backtest.cli run \
  --config trading_system/tests/fixtures/backtest/minimal_config.json \
  --output-dir /tmp/backtest-research
```

---

## 运行与部署

### systemd
最小可用模板：
- `deploy/systemd/trading-system-paper.service`
- `deploy/systemd/trading-system-paper.timer`

该模板的执行入口是：
```bash
python -m trading_system.run_cycle --mode paper
```

### cron
可用于临时或轻量调度：
- `deploy/cron/trading-system-paper-cron.sh`
- `deploy/cron/install-trading-system-paper-crontab.sh`

默认固定在 paper runtime bucket 下读写运行数据，并带文件锁避免重复执行。

---

## 当前状态与边界

这个仓库已经不是只有单一脚本，而是一个逐步成型的 **partial v2 trading stack**：
- regime
- universe construction
- trend / rotation / short engines
- validator / allocator / risk gates
- paper execution
- lifecycle / reporting
- backtest research pipeline

当前明确边界：
- **paper-first**
- short 候选可进入分配与报告，但执行层仍可显式跳过
- 更偏向研究、验证、模拟执行与运行编排，而不是“直接全自动实盘”

---

## 进一步阅读

建议从这些文件开始：
- `trading_system/README.md`
- `trading_system/app/README.md`
- `docs/superpowers/specs/`
- `docs/superpowers/plans/`
- `trading_system/tests/`

---

## 适合谁

这个仓库更适合：
- 想搭建 **crypto trading research + paper trading** 工作流的人
- 想把脚本式交易工具逐步演进成模块化系统的人
- 需要回测、运行状态持久化、周期调度与报告输出的人

如果你需要，我下一步可以继续补：
- 更正式的英文 README
- `LICENSE`
- GitHub Actions CI
- 更完整的部署说明
