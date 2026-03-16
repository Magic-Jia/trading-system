from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
SNAP = BASE / "data" / "account_snapshot.json"
SCAN = BASE / "data" / "market_scan.json"
OUT = BASE / "data" / "trade_plan.json"


def main() -> None:
    snap = json.loads(SNAP.read_text())
    scan = {row["symbol"]: row for row in json.loads(SCAN.read_text())}
    positions = snap["futures"]["positions"]

    plan = {
        "meta": {
            "style": "top crypto trader",
            "principle": "cut weak fragmented losers, keep relative strength, no new weak alt longs",
        },
        "actions": [],
    }

    for p in positions:
        symbol = p["symbol"]
        roi = p["roi_pct"]
        action = "HOLD"
        rationale = []

        if roi <= -45 and p["notional"] > 2:
            action = "CLOSE_PRIORITY"
            rationale.append("亏损过深且剩余名义价值仍值得回收")
            rationale.append("继续持有的机会成本高于留仓价值")
        elif roi <= -25:
            action = "REDUCE_ON_BOUNCE"
            rationale.append("结构偏弱，适合反弹减仓而非继续加码")
        elif symbol in scan and scan[symbol]["bias"] == "UP":
            action = "KEEP_WATCH"
            rationale.append("4h 相对强于 20MA，可作为保留观察仓")
        elif roi > 0:
            action = "KEEP_PROTECT"
            rationale.append("当前盈利仓，优先保护利润，不让盈利翻亏")
        else:
            rationale.append("暂不新增动作，等待更清晰结构")

        plan["actions"].append(
            {
                "symbol": symbol,
                "action": action,
                "side": p["side"],
                "roi_pct": round(roi, 2),
                "upl": round(p["upl"], 4),
                "notional": round(p["notional"], 4),
                "rationale": rationale,
            }
        )

    OUT.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    print(OUT)


if __name__ == "__main__":
    main()
