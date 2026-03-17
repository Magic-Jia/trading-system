from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
SNAP = BASE / "data" / "account_snapshot.json"
ENTRIES = BASE / "data" / "entry_templates.json"
OUT = BASE / "data" / "sized_entries.json"

DEFAULT_RISK_PCT = 0.01  # 1% of futures wallet per idea
MAX_NOTIONAL_PCT = 0.12  # cap single idea notional to 12% of wallet for small account


def main() -> None:
    snap = json.loads(SNAP.read_text())
    entries = json.loads(ENTRIES.read_text())
    wallet = float(snap["futures"]["total_wallet_balance"])
    risk_budget = wallet * DEFAULT_RISK_PCT
    max_notional = wallet * MAX_NOTIONAL_PCT

    out = []
    for row in entries:
        entry = sum(row["entry_zone"]) / 2
        stop = row["stop_loss"]
        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            continue
        raw_qty = risk_budget / stop_dist
        capped_qty = min(raw_qty, max_notional / entry)
        planned_notional = capped_qty * entry
        planned_loss = capped_qty * stop_dist
        rr1 = (row["take_profit_1"] - entry) / stop_dist
        rr2 = (row["take_profit_2"] - entry) / stop_dist
        out.append(
            {
                **row,
                "wallet_balance": round(wallet, 4),
                "risk_budget_usdt": round(risk_budget, 4),
                "suggested_qty": round(capped_qty, 6),
                "planned_notional_usdt": round(planned_notional, 4),
                "planned_max_loss_usdt": round(planned_loss, 4),
                "rr_tp1": round(rr1, 2),
                "rr_tp2": round(rr2, 2),
                "sizing_rationale": [
                    "单笔默认风险预算为账户权益的 1%",
                    "单笔名义仓位上限默认为账户权益的 12%",
                    "仓位由止损距离反推，而不是凭主观信心决定",
                ],
            }
        )

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(OUT)


if __name__ == "__main__":
    main()
