from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
SNAP = BASE / "data" / "account_snapshot.json"
SCORED = BASE / "data" / "trade_plan_scored.json"
PAPER = BASE / "data" / "paper_trades.jsonl"
OUT = BASE / "data" / "daily_report.md"


def main() -> None:
    snap = json.loads(SNAP.read_text())
    scored = json.loads(SCORED.read_text())
    paper_lines = []
    if PAPER.exists():
        paper_lines = [json.loads(line) for line in PAPER.read_text().splitlines() if line.strip()]

    cnt = Counter([x["action"] for x in scored["actions"]])
    top = scored["actions"][:5]
    lines = []
    lines.append("# Trading Daily Report")
    lines.append("")
    lines.append(f"- Futures wallet: {snap['futures']['total_wallet_balance']:.4f} USDT")
    lines.append(f"- Unrealized PnL: {snap['futures']['total_unrealized_profit']:.4f} USDT")
    lines.append(f"- Available balance: {snap['futures']['available_balance']:.4f} USDT")
    lines.append("")
    lines.append("## Plan action counts")
    for k, v in cnt.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Top priorities")
    for item in top:
        lines.append(f"- {item['symbol']} | {item['action']} | score={item['priority_score']} | rationale={'; '.join(item['rationale'])}")
    lines.append("")
    lines.append(f"## Paper trades logged: {len(paper_lines)}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(OUT)


if __name__ == '__main__':
    main()
