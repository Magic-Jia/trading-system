# Runbook

## 1. 生成账户快照

```bash
python3 trading_system/account_snapshot.py
```

输出：
- `trading_system/data/account_snapshot.json`

## 2. 生成市场快扫

```bash
python3 trading_system/market_scan.py
```

输出：
- `trading_system/data/market_scan.json`

## 3. 生成交易计划

```bash
python3 trading_system/generate_plan.py
python3 trading_system/score_plan.py
```

输出：
- `trading_system/data/trade_plan.json`
- `trading_system/data/trade_plan_scored.json`

## 4. 模拟执行高优先级动作

```bash
python3 trading_system/paper_executor.py --top 5 --mode mixed
```

输出：
- `trading_system/data/paper_trades.jsonl`

## 5. 扫描候选新开仓标的并生成交易参数

```bash
python3 trading_system/candidate_scan.py
python3 trading_system/entry_templates.py
```

输出：
- `trading_system/data/candidate_scan.json`
- `trading_system/data/entry_templates.json`

## 6. 生成仓位 sizing 与复盘评分

```bash
python3 trading_system/position_sizing.py
python3 trading_system/review_score.py
```

输出：
- `trading_system/data/sized_entries.json`
- `trading_system/data/review_score.json`

## 6.5 运行自动交易程序 MVP 主流程（当前默认 paper execution）

```bash
python3 -m trading_system.run_cycle --mode paper
```

输出：
- 控制台打印信号执行结果，以及当前持仓的管理建议与 action previews（ADD_PROTECTIVE_STOP 补保护止损预览、BREAK_EVEN 止损上调、PARTIAL_TAKE_PROFIT 减仓预览、EXIT 平仓预览）
- management preview 中会附带按 symbol 过滤后的 `open_protective_orders`，以及 connector-ready payload：`upsert_protective_stop` / `reduce_only_close`
- `trading_system/data/runtime/paper/paper/runtime_state.json`
- 若有实际通过风控并进入执行层，还会写入 `trading_system/data/execution_log.jsonl`

说明：
- 运行前先确保上游已经把三份输入快照写到 `trading_system/data/runtime/paper/paper/`
- 管理动作当前仅生成 preview payload，不会触发 live 写入
- `ADD_PROTECTIVE_STOP` 仅在 preview 中给出建议止损位：默认取开仓价外侧 2%，若当前价格已越过该水平，则改为在当前价格外侧保留 0.5% 缓冲，避免生成已触发的止损
- 执行层仍仅支持 `paper` / `dry-run`，`live` 会被拒绝

## 7. 生成日报

```bash
python3 trading_system/daily_report.py
```

输出：
- `trading_system/data/daily_report.md`

## 8. 记录交易理由或复盘

```bash
python3 trading_system/journal.py note --type rationale --symbol BTCUSDT --side LONG --action OPEN --text "4h 趋势向上，回踩 20EMA 附近，赔率优于账户内弱 alt。"

python3 trading_system/journal.py note --type review --symbol BTCUSDT --side LONG --action CLOSE --text "止盈执行到位，但仓位偏小；下次在同类结构中可略增。"
```

输出：
- `trading_system/data/journal.jsonl`
