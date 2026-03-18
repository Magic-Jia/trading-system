from __future__ import annotations

from typing import Any


def _coerce_rows(market: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(market, list):
        return market
    if isinstance(market, dict):
        symbols = market.get("symbols", {})
        if isinstance(symbols, dict):
            return [{"symbol": symbol, **payload} for symbol, payload in sorted(symbols.items())]
    return []


def _ratio(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return part / total


def compute_breadth_metrics(market: dict[str, Any] | list[dict[str, Any]]) -> dict[str, float]:
    rows = _coerce_rows(market)
    total = len(rows)

    above_4h_ema20 = 0
    ema20_above_ema50 = 0
    positive_momentum = 0

    for row in rows:
        tf_4h = row.get("4h", {})
        close = float(tf_4h.get("close", 0.0))
        ema20 = float(tf_4h.get("ema_20", 0.0))
        ema50 = float(tf_4h.get("ema_50", 0.0))
        momentum = float(tf_4h.get("return_pct_3d", 0.0))

        if close > ema20:
            above_4h_ema20 += 1
        if ema20 > ema50:
            ema20_above_ema50 += 1
        if momentum > 0:
            positive_momentum += 1

    return {
        "universe_size": float(total),
        "pct_above_4h_ema20": _ratio(above_4h_ema20, total),
        "pct_4h_ema20_above_ema50": _ratio(ema20_above_ema50, total),
        "positive_momentum_share": _ratio(positive_momentum, total),
    }
