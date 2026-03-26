from __future__ import annotations

from collections.abc import Mapping
from typing import Any

MAJOR_SYMBOLS = {"BTCUSDT", "ETHUSDT"}
_LATE_STAGE_LONG_BLOWOFF_FUNDING_RATE = 0.0002
_LATE_STAGE_LONG_BLOWOFF_BASIS_BPS = 25.0


def _coerce_all_rows(derivatives: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(derivatives, list):
        rows = derivatives
    elif isinstance(derivatives, dict):
        rows = derivatives.get("rows", [])
        if not isinstance(rows, list):
            return []
    else:
        return []

    return [row for row in rows if isinstance(row, dict) and row.get("symbol")]


def _coerce_rows(derivatives: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in _coerce_all_rows(derivatives) if row.get("symbol") in MAJOR_SYMBOLS]


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _classify_funding_heat(avg_funding: float) -> str:
    if avg_funding >= 0.0002:
        return "hot"
    if avg_funding >= 0.00008:
        return "warm"
    if avg_funding <= -0.0002:
        return "hot"
    if avg_funding <= -0.00008:
        return "warm"
    return "cool"


def _classify_price_oi_interaction(avg_price_change: float, avg_oi_change: float) -> str:
    if avg_price_change > 0 and avg_oi_change > 0:
        return "long_build"
    if avg_price_change < 0 and avg_oi_change > 0:
        return "short_build"
    if avg_price_change > 0 and avg_oi_change < 0:
        return "short_covering"
    if avg_price_change < 0 and avg_oi_change < 0:
        return "long_unwind"
    return "flat"


def _crowding_score(
    funding_rate: float,
    open_interest_change_24h_pct: float,
    taker_buy_sell_ratio: float,
    basis_bps: float,
) -> float:
    crowding_score = 0.0
    crowding_score += 1.0 if funding_rate > 0.00005 else -1.0 if funding_rate < -0.00005 else 0.0
    crowding_score += (
        1.0 if open_interest_change_24h_pct > 0.02 else -1.0 if open_interest_change_24h_pct < -0.02 else 0.0
    )
    crowding_score += 1.0 if taker_buy_sell_ratio > 1.02 else -1.0 if taker_buy_sell_ratio < 0.98 else 0.0
    crowding_score += 1.0 if basis_bps > 15 else -1.0 if basis_bps < -15 else 0.0
    return crowding_score


def _crowding_bias(crowding_score: float) -> str:
    if crowding_score >= 3:
        return "crowded_long"
    if crowding_score <= -3:
        return "crowded_short"
    return "balanced"


def symbol_derivatives_features(
    derivatives: dict[str, Any] | list[dict[str, Any]] | None,
    symbol: str,
) -> dict[str, Any]:
    if derivatives is None:
        rows: list[dict[str, Any]] = []
    else:
        rows = _coerce_all_rows(derivatives)

    normalized_symbol = str(symbol).upper()
    row = next((item for item in rows if str(item.get("symbol", "")).upper() == normalized_symbol), {})

    funding_rate = float(row.get("funding_rate", 0.0) or 0.0)
    open_interest_change_24h_pct = float(row.get("open_interest_change_24h_pct", 0.0) or 0.0)
    mark_price_change_24h_pct = float(row.get("mark_price_change_24h_pct", 0.0) or 0.0)
    taker_buy_sell_ratio = float(row.get("taker_buy_sell_ratio", 1.0) or 1.0)
    basis_bps = float(row.get("basis_bps", 0.0) or 0.0)
    crowding_score = _crowding_score(
        funding_rate=funding_rate,
        open_interest_change_24h_pct=open_interest_change_24h_pct,
        taker_buy_sell_ratio=taker_buy_sell_ratio,
        basis_bps=basis_bps,
    )

    return {
        "funding_rate": funding_rate,
        "open_interest_change_24h_pct": open_interest_change_24h_pct,
        "mark_price_change_24h_pct": mark_price_change_24h_pct,
        "taker_buy_sell_ratio": taker_buy_sell_ratio,
        "basis_bps": basis_bps,
        "funding_heat": _classify_funding_heat(funding_rate),
        "price_oi_interaction": _classify_price_oi_interaction(
            mark_price_change_24h_pct,
            open_interest_change_24h_pct,
        ),
        "crowding_bias": _crowding_bias(crowding_score),
        "crowding_score": crowding_score,
    }


def is_late_stage_long_blowoff(features: Mapping[str, Any]) -> bool:
    return (
        float(features.get("funding_rate", 0.0) or 0.0) >= _LATE_STAGE_LONG_BLOWOFF_FUNDING_RATE
        and float(features.get("basis_bps", 0.0) or 0.0) >= _LATE_STAGE_LONG_BLOWOFF_BASIS_BPS
    )


def summarize_derivatives_risk(derivatives: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
    rows = _coerce_rows(derivatives)
    if not rows:
        return {
            "majors_count": 0,
            "avg_funding_rate": 0.0,
            "avg_open_interest_change_24h_pct": 0.0,
            "avg_mark_price_change_24h_pct": 0.0,
            "avg_taker_buy_sell_ratio": 1.0,
            "avg_basis_bps": 0.0,
            "funding_heat": "cool",
            "oi_trend": "flat",
            "price_oi_interaction": "flat",
            "crowding_bias": "balanced",
            "crowding_score": 0.0,
        }

    funding_values = [float(row["funding_rate"]) for row in rows]
    oi_change_values = [float(row["open_interest_change_24h_pct"]) for row in rows]
    price_change_values = [float(row["mark_price_change_24h_pct"]) for row in rows]
    taker_values = [float(row["taker_buy_sell_ratio"]) for row in rows]
    basis_values = [float(row["basis_bps"]) for row in rows]

    avg_funding = _avg(funding_values)
    avg_oi_change = _avg(oi_change_values)
    avg_price_change = _avg(price_change_values)
    avg_taker = _avg(taker_values)
    avg_basis = _avg(basis_values)

    crowding_score = _crowding_score(avg_funding, avg_oi_change, avg_taker, avg_basis)
    crowding_bias = _crowding_bias(crowding_score)

    if avg_oi_change >= 0.03:
        oi_trend = "expanding"
    elif avg_oi_change <= -0.03:
        oi_trend = "contracting"
    else:
        oi_trend = "flat"

    return {
        "majors_count": len(rows),
        "avg_funding_rate": avg_funding,
        "avg_open_interest_change_24h_pct": avg_oi_change,
        "avg_mark_price_change_24h_pct": avg_price_change,
        "avg_taker_buy_sell_ratio": avg_taker,
        "avg_basis_bps": avg_basis,
        "funding_heat": _classify_funding_heat(avg_funding),
        "oi_trend": oi_trend,
        "price_oi_interaction": _classify_price_oi_interaction(avg_price_change, avg_oi_change),
        "crowding_bias": crowding_bias,
        "crowding_score": crowding_score,
    }
