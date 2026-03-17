from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
PLAN = BASE / "data" / "trade_plan_scored.json"
PAPER = BASE / "data" / "paper_trades.jsonl"
OUT = BASE / "data" / "review_score.json"


def main() -> None:
    plan = json.loads(PLAN.read_text()) if PLAN.exists() else {"actions": []}
    paper = [json.loads(line) for line in PAPER.read_text().splitlines() if line.strip()] if PAPER.exists() else []

    action_count = Counter([x["action"] for x in plan.get("actions", [])])
    paper_count = Counter([x["action"] for x in paper])

    review = {
        "plan_action_count": dict(action_count),
        "paper_action_count": dict(paper_count),
        "top_plan_symbols": [x["symbol"] for x in plan.get("actions", [])[:10]],
        "notes": [
            "当前评分器仍是规则驱动，不是统计学习模型。",
            "后续可接入真实结果字段，统计每类信号的胜率、盈亏比与回撤。",
            "当前 review_score 更像系统状态摘要，用于给下一版优化提供方向。",
        ],
    }
    OUT.write_text(json.dumps(review, ensure_ascii=False, indent=2))
    print(OUT)


if __name__ == "__main__":
    main()
