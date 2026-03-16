from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
PLAN = BASE / "data" / "trade_plan.json"
OUT = BASE / "data" / "trade_plan_scored.json"

ACTION_SCORE = {
    "CLOSE_PRIORITY": 90,
    "REDUCE_ON_BOUNCE": 65,
    "KEEP_PROTECT": 55,
    "KEEP_WATCH": 50,
    "HOLD": 35,
}


def score_item(item: dict) -> dict:
    score = ACTION_SCORE.get(item["action"], 0)
    score += min(10, max(0, int(abs(item.get("upl", 0)))))
    item = dict(item)
    item["priority_score"] = score
    return item


def main() -> None:
    plan = json.loads(PLAN.read_text())
    actions = [score_item(a) for a in plan["actions"]]
    actions.sort(key=lambda x: x["priority_score"], reverse=True)
    scored = {**plan, "actions": actions}
    OUT.write_text(json.dumps(scored, ensure_ascii=False, indent=2))
    print(OUT)


if __name__ == "__main__":
    main()
