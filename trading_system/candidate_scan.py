from __future__ import annotations

import json
from pathlib import Path

from binance_client import SPOT_BASE, public_get

OUT = Path(__file__).resolve().parent / "data" / "candidate_scan.json"
WATCH = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "LINKUSDT",
    "XRPUSDT",
    "AUCTIONUSDT",
    "COWUSDT",
]


def klines(symbol: str, interval: str = "4h", limit: int = 60):
    return public_get(SPOT_BASE, "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})


def atr(rows, period: int = 14) -> float:
    trs = []
    prev_close = None
    for r in rows[-period:]:
        high = float(r[2]); low = float(r[3]); close = float(r[4])
        if prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        prev_close = close
    return sum(trs) / len(trs)


def summary(symbol: str):
    rows = klines(symbol)
    closes = [float(r[4]) for r in rows]
    last = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50
    momentum = (last / closes[-10] - 1) * 100
    vol = atr(rows) / last * 100
    score = 0
    if last > ma20: score += 1
    if ma20 > ma50: score += 1
    if momentum > 0: score += 1
    return {
        "symbol": symbol,
        "last": last,
        "ma20_4h": ma20,
        "ma50_4h": ma50,
        "momentum_10bars_pct": momentum,
        "atr_pct": vol,
        "trend_score": score,
        "bias": "LONG_CANDIDATE" if score >= 2 else "NEUTRAL_OR_WEAK",
    }


def main() -> None:
    data = [summary(s) for s in WATCH]
    data.sort(key=lambda x: (x["trend_score"], x["momentum_10bars_pct"]), reverse=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(OUT)


if __name__ == "__main__":
    main()
