from __future__ import annotations

import json
from pathlib import Path

from binance_client import SPOT_BASE, public_get

OUT = Path(__file__).resolve().parent / "data" / "market_scan.json"
WATCH = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "COWUSDT", "AUCTIONUSDT"]


def klines(symbol: str, interval: str = "4h", limit: int = 30):
    return public_get(SPOT_BASE, "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})


def summary(symbol: str):
    rows = klines(symbol)
    closes = [float(r[4]) for r in rows]
    last = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    return {
        "symbol": symbol,
        "last": last,
        "ma20_4h": ma20,
        "bias": "UP" if last > ma20 else "DOWN",
    }


def main() -> None:
    data = [summary(s) for s in WATCH]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(OUT)


if __name__ == "__main__":
    main()
