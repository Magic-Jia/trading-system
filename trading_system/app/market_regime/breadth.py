from __future__ import annotations

import math
from numbers import Real
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


def _strict_finite_float(row: dict[str, Any], field: str, *, symbol: str) -> float:
    if field not in row:
        raise ValueError(f"missing required market breadth field: {symbol}.4h.{field}")
    value = row[field]
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"market breadth field must be a finite number: {symbol}.4h.{field}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"market breadth field must be finite: {symbol}.4h.{field}")
    return number


def compute_breadth_metrics(market: dict[str, Any] | list[dict[str, Any]]) -> dict[str, float]:
    rows = _coerce_rows(market)
    total = len(rows)

    above_4h_ema20 = 0
    ema20_above_ema50 = 0
    positive_momentum = 0

    for row in rows:
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("market breadth row symbol must be a non-empty string")
        tf_4h = row.get("4h")
        if not isinstance(tf_4h, dict):
            raise ValueError(f"market breadth timeframe must be an object: {symbol}.4h")
        close = _strict_finite_float(tf_4h, "close", symbol=symbol)
        ema20 = _strict_finite_float(tf_4h, "ema_20", symbol=symbol)
        ema50 = _strict_finite_float(tf_4h, "ema_50", symbol=symbol)
        momentum = _strict_finite_float(tf_4h, "return_pct_3d", symbol=symbol)

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
