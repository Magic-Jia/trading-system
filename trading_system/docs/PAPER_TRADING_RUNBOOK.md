# Paper Trading Runbook

## 目的

- 使用 `paper` 模式执行 strategy cycle，不触发 live 下单。
- 将每次模拟成交写入 `paper_ledger.jsonl`。
- 当 `runtime_state.json` 丢失或被清空时，允许主循环从 ledger 恢复已执行 intent，避免重复下单。

## 路径约定

- paper 入口默认读 `trading_system/data/runtime/paper/paper/` 这个 bucket。
- 其中输入快照的 canonical 路径是：
  - `trading_system/data/runtime/paper/paper/account_snapshot.json`
  - `trading_system/data/runtime/paper/paper/market_context.json`
  - `trading_system/data/runtime/paper/paper/derivatives_snapshot.json`
- 同一个 bucket 下会自动产出：
  - `trading_system/data/runtime/paper/paper/runtime_state.json`
  - `trading_system/data/runtime/paper/paper/paper_ledger.jsonl`
  - `trading_system/data/runtime/paper/paper/latest.json`
  - `trading_system/data/runtime/paper/paper/error.json`
- 若要切到别的隔离环境，用 `--runtime-env <name>`；例如 `paper/testnet/`。

## 单次运行

```bash
python -m trading_system.run_cycle --mode paper
```

若当前 bucket 缺少三份输入快照，入口会先在 `trading_system/data/runtime/paper/paper/` 内自动补齐：

- `account_snapshot.json`：生成安全的 paper 账户快照（默认空持仓、空挂单，不读取根目录 live 快照）
- `market_context.json`：按当前 symbol 列表从公开行情接口拉取并写入 paper bucket
- `derivatives_snapshot.json`：按当前 symbol 列表从公开合约接口拉取并写入 paper bucket

如果公开行情/API 拉取失败，当前轮次会 fail-fast，并把错误写入同目录 `error.json` / `latest.json`。

## 定时跑批约定

- 若要把 `paper` cycle 接到 `systemd timer`，优先直接复用 `deploy/systemd/trading-system-paper.service` 与 `deploy/systemd/trading-system-paper.timer`。
- 详细安装和巡检步骤见 `trading_system/docs/BATCH_RUNTIME_RUNBOOK.md`。
- timer 模式默认也走同一个 paper bucket；部署到 `/opt/trading-system` 后，对应绝对路径是 `/opt/trading-system/trading_system/data/runtime/paper/paper/`。
- `paper_ledger.jsonl` 会跟随 `runtime_state.json` 落在同级目录，所以不要把整个 runtime bucket 放到临时目录，除非你接受 ledger 丢失。
- 当前 batch 模板只负责“定时执行一轮”；若 bucket 内缺少输入快照，paper 入口会先在本 bucket 内自动补齐，补不出来就直接报错退出。

## 运行期检查点

- 标准输出 `portfolio.paper_trading.mode` 应为 `paper`。
- `portfolio.paper_trading.ledger_path` 应指向当前 state file 同目录下的 `paper_ledger.jsonl`。
- `portfolio.paper_trading.emitted_count` 表示本次 cycle 新写入 ledger 的 paper fills。
- `portfolio.paper_trading.replayed_count` 表示本次 cycle 从 ledger 恢复、未重复执行的 intents。

## 重启恢复

1. 保留 `paper_ledger.jsonl`。
2. 删除或重置 `runtime_state.json`。
3. 用相同 market/account 输入再次运行 `python -m trading_system.run_cycle --mode paper`。
4. 验证 `portfolio.paper_trading.replayed_count` 大于 `0`，且对应 allocation 的 `execution` 仍为原 intent id。

## focused verification

- `PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_paper_executor.py`
- `PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_main_v2_cycle.py -k 'paper_cycle_emits_paper_trading_summary_and_records_ledger or paper_cycle_replays_from_ledger_when_state_is_missing'`
- `systemd-analyze verify deploy/systemd/trading-system-paper.service deploy/systemd/trading-system-paper.timer`
