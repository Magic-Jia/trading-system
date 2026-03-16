from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
CAND = BASE / "data" / "candidate_scan.json"
OUT = BASE / "data" / "entry_templates.json"


def build_template(row: dict) -> dict:
    last = row["last"]
    atr_pct = row["atr_pct"] / 100
    entry_low = last * (1 - 0.25 * atr_pct)
    entry_high = last * (1 + 0.10 * atr_pct)
    stop = last * (1 - 1.2 * atr_pct)
    tp1 = last * (1 + 1.5 * atr_pct)
    tp2 = last * (1 + 3.0 * atr_pct)
    return {
        "symbol": row["symbol"],
        "bias": row["bias"],
        "entry_zone": [round(entry_low, 6), round(entry_high, 6)],
        "stop_loss": round(stop, 6),
        "take_profit_1": round(tp1, 6),
        "take_profit_2": round(tp2, 6),
        "rationale": [
            "4h 趋势评分较高",
            "价格位于短中期均线强势结构中或附近",
            "用 ATR 推导出适合小资金试错的进出场框架",
        ],
    }


def main() -> None:
    rows = json.loads(CAND.read_text())
    out = [build_template(r) for r in rows if r["bias"] == "LONG_CANDIDATE"]
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(OUT)


if __name__ == "__main__":
    main()
