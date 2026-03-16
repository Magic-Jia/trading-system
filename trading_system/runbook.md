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

## 6. 生成日报

```bash
python3 trading_system/daily_report.py
```

输出：
- `trading_system/data/daily_report.md`

## 7. 记录交易理由或复盘

```bash
python3 trading_system/journal.py note --type rationale --symbol BTCUSDT --side LONG --action OPEN --text "4h 趋势向上，回踩 20EMA 附近，赔率优于账户内弱 alt。"

python3 trading_system/journal.py note --type review --symbol BTCUSDT --side LONG --action CLOSE --text "止盈执行到位，但仓位偏小；下次在同类结构中可略增。"
```

输出：
- `trading_system/data/journal.jsonl`
