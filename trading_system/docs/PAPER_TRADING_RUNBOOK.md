# Paper Trading Runbook

## 目的

- 使用 `paper` 模式执行 strategy cycle，不触发 live 下单。
- 将每次模拟成交写入 `paper_ledger.jsonl`。
- 当 `runtime_state.json` 丢失或被清空时，允许主循环从 ledger 恢复已执行 intent，避免重复下单。

## 路径约定

- `TRADING_STATE_FILE` 决定 runtime state 输出位置。
- `paper_ledger.jsonl` 始终写在 `TRADING_STATE_FILE` 的同级目录。
- 示例：若 state file 为 `/tmp/runtime_state.json`，ledger 路径即 `/tmp/paper_ledger.jsonl`。

## 单次运行

```bash
TRADING_EXECUTION_MODE=paper \
TRADING_ACCOUNT_SNAPSHOT_FILE=trading_system/data/account_snapshot.json \
TRADING_MARKET_CONTEXT_FILE=trading_system/data/market_context.json \
TRADING_DERIVATIVES_SNAPSHOT_FILE=trading_system/data/derivatives_snapshot.json \
python -m trading_system.app.main
```

## 运行期检查点

- 标准输出 `portfolio.paper_trading.mode` 应为 `paper`。
- `portfolio.paper_trading.ledger_path` 应指向当前 state file 同目录下的 `paper_ledger.jsonl`。
- `portfolio.paper_trading.emitted_count` 表示本次 cycle 新写入 ledger 的 paper fills。
- `portfolio.paper_trading.replayed_count` 表示本次 cycle 从 ledger 恢复、未重复执行的 intents。

## 重启恢复

1. 保留 `paper_ledger.jsonl`。
2. 删除或重置 `runtime_state.json`。
3. 用相同 market/account 输入再次运行 `python -m trading_system.app.main`。
4. 验证 `portfolio.paper_trading.replayed_count` 大于 `0`，且对应 allocation 的 `execution` 仍为原 intent id。

## focused verification

- `PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_paper_executor.py`
- `PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_main_v2_cycle.py -k 'paper_cycle_emits_paper_trading_summary_and_records_ledger or paper_cycle_replays_from_ledger_when_state_is_missing'`
