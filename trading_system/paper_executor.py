from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
PLAN = BASE / "data" / "trade_plan_scored.json"
LOG = BASE / "data" / "paper_trades.jsonl"
BJ = timezone(timedelta(hours=8))


def append(entry: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--mode", choices=["open", "close", "mixed"], default="mixed")
    args = ap.parse_args()

    plan = json.loads(PLAN.read_text())
    actions = plan["actions"][: args.top]
    for item in actions:
        if args.mode == "close" and item["action"] not in {"CLOSE_PRIORITY", "REDUCE_ON_BOUNCE"}:
            continue
        if args.mode == "open" and item["action"] not in {"KEEP_WATCH", "KEEP_PROTECT"}:
            continue
        append(
            {
                "ts_bj": datetime.now(BJ).isoformat(),
                "symbol": item["symbol"],
                "action": item["action"],
                "side": item["side"],
                "paper": True,
                "rationale": item["rationale"],
                "priority_score": item["priority_score"],
            }
        )
    print(LOG)


if __name__ == "__main__":
    main()
