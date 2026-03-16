from __future__ import annotations

import json
from pathlib import Path

from binance_client import FUTURES_BASE, SPOT_BASE, env_ready, signed_get, signed_params

OUT = Path(__file__).resolve().parent / "data" / "account_snapshot.json"


def main() -> None:
    if not env_ready():
        raise SystemExit("Binance env missing")

    params = signed_params()
    fut = signed_get(FUTURES_BASE, "/fapi/v2/account", params)
    pos = signed_get(FUTURES_BASE, "/fapi/v2/positionRisk", params)
    spot = signed_get(SPOT_BASE, "/api/v3/account", params)

    active_positions = []
    for p in pos:
        amt = float(p["positionAmt"])
        if amt == 0:
            continue
        entry = float(p["entryPrice"]) if float(p["entryPrice"]) else 0.0
        mark = float(p["markPrice"]) if float(p["markPrice"]) else 0.0
        roi = ((mark - entry) / entry * 100) if entry else 0.0
        active_positions.append(
            {
                "symbol": p["symbol"],
                "side": p["positionSide"],
                "amt": amt,
                "entry": entry,
                "mark": mark,
                "upl": float(p["unRealizedProfit"]),
                "notional": abs(float(p["notional"])),
                "roi_pct": roi,
                "leverage": int(float(p["leverage"])),
                "liquidation_price": p["liquidationPrice"],
            }
        )

    active_positions.sort(key=lambda x: x["upl"])
    nonzero_spot = []
    for b in spot["balances"]:
        free = float(b["free"])
        locked = float(b["locked"])
        if free or locked:
            nonzero_spot.append({"asset": b["asset"], "free": free, "locked": locked, "total": free + locked})

    snapshot = {
        "spot": {"nonzero_balances": nonzero_spot},
        "futures": {
            "total_wallet_balance": float(fut["totalWalletBalance"]),
            "total_unrealized_profit": float(fut["totalUnrealizedProfit"]),
            "total_margin_balance": float(fut["totalMarginBalance"]),
            "available_balance": float(fut["availableBalance"]),
            "total_initial_margin": float(fut["totalInitialMargin"]),
            "total_maint_margin": float(fut["totalMaintMargin"]),
            "positions": active_positions,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    print(OUT)


if __name__ == "__main__":
    main()
