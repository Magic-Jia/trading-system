from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

MAJOR_SYMBOLS = {"BTCUSDT", "ETHUSDT"}
_LATE_STAGE_LONG_BLOWOFF_FUNDING_RATE = 0.0002
_LATE_STAGE_LONG_BLOWOFF_BASIS_BPS = 25.0
_LATE_STAGE_LONG_ACCELERATION_H4_EXTENSION_PCT = 0.025
_LATE_STAGE_LONG_ACCELERATION_H1_EXTENSION_PCT = 0.008
_LATE_STAGE_LONG_ACCELERATION_OI_CHANGE_PCT = 0.04
_LATE_STAGE_LONG_ACCELERATION_MARK_PRICE_CHANGE_PCT = 0.02


def _canonical_symbol_identity(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.upper() or value != value.strip():
        raise ValueError("invalid derivatives symbol identity")
    return value


def _coerce_all_rows(derivatives: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(derivatives, list):
        rows = derivatives
    elif isinstance(derivatives, dict):
        rows = derivatives.get("rows", [])
        if not isinstance(rows, list):
            return []
    else:
        return []

    normalized: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"derivatives rows[{idx}] must be an object")
        if "symbol" in row:
            _canonical_symbol_identity(row["symbol"])
            normalized.append(row)
    return normalized


def _coerce_rows(derivatives: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in _coerce_all_rows(derivatives) if row.get("symbol") in MAJOR_SYMBOLS]


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _strict_number_field(row: Mapping[str, Any], field: str) -> float:
    value = row[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid derivatives numeric field {field}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"invalid derivatives numeric field {field}")
    return number


def _optional_strict_number_field(row: Mapping[str, Any], field: str, *, default: float) -> float:
    if field not in row:
        return default
    return _strict_number_field(row, field)


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


def _classify_late_stage_heat(
    avg_funding: float,
    avg_oi_change: float,
    avg_price_change: float,
    avg_taker: float,
    avg_basis: float,
) -> str:
    cascade = (
        avg_price_change <= -0.05
        and avg_oi_change <= -0.08
        and avg_taker <= 0.9
        and avg_basis <= -15.0
        and avg_funding <= -0.00005
    )
    if cascade:
        return "cascade"

    squeeze = (
        avg_price_change >= 0.05
        and avg_oi_change <= -0.05
        and avg_taker >= 1.1
        and avg_basis <= -15.0
        and avg_funding <= -0.00008
    )
    if squeeze:
        return "squeeze"

    return "none"


def _execution_hazard(late_stage_heat: str) -> str:
    if late_stage_heat in {"cascade", "squeeze"}:
        return "compress_risk"
    return "none"


def symbol_derivatives_features(
    derivatives: dict[str, Any] | list[dict[str, Any]] | None,
    symbol: str,
) -> dict[str, Any]:
    if derivatives is None:
        rows: list[dict[str, Any]] = []
    else:
        rows = _coerce_all_rows(derivatives)

    normalized_symbol = _canonical_symbol_identity(symbol)
    row = next((item for item in rows if item.get("symbol") == normalized_symbol), None)

    if row is None:
        funding_rate = 0.0
        open_interest_change_24h_pct = 0.0
        mark_price_change_24h_pct = 0.0
        taker_buy_sell_ratio = 1.0
        basis_bps = 0.0
    else:
        funding_rate = _optional_strict_number_field(row, "funding_rate", default=0.0)
        open_interest_change_24h_pct = _optional_strict_number_field(row, "open_interest_change_24h_pct", default=0.0)
        mark_price_change_24h_pct = _optional_strict_number_field(row, "mark_price_change_24h_pct", default=0.0)
        taker_buy_sell_ratio = _optional_strict_number_field(row, "taker_buy_sell_ratio", default=1.0)
        basis_bps = _optional_strict_number_field(row, "basis_bps", default=0.0)
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


def is_late_stage_long_blowoff(
    features: Mapping[str, Any],
    *,
    h4_extension_pct: float = 0.0,
    h1_extension_pct: float = 0.0,
) -> bool:
    funding_basis_blowoff = (
        float(features.get("funding_rate", 0.0) or 0.0) >= _LATE_STAGE_LONG_BLOWOFF_FUNDING_RATE
        and float(features.get("basis_bps", 0.0) or 0.0) >= _LATE_STAGE_LONG_BLOWOFF_BASIS_BPS
    )
    price_oi_acceleration_blowoff = (
        h4_extension_pct >= _LATE_STAGE_LONG_ACCELERATION_H4_EXTENSION_PCT
        and h1_extension_pct >= _LATE_STAGE_LONG_ACCELERATION_H1_EXTENSION_PCT
        and float(features.get("open_interest_change_24h_pct", 0.0) or 0.0) >= _LATE_STAGE_LONG_ACCELERATION_OI_CHANGE_PCT
        and float(features.get("mark_price_change_24h_pct", 0.0) or 0.0) >= _LATE_STAGE_LONG_ACCELERATION_MARK_PRICE_CHANGE_PCT
    )
    return funding_basis_blowoff or price_oi_acceleration_blowoff


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
            "late_stage_heat": "none",
            "execution_hazard": "none",
        }

    funding_values = [_strict_number_field(row, "funding_rate") for row in rows]
    oi_change_values = [_strict_number_field(row, "open_interest_change_24h_pct") for row in rows]
    price_change_values = [_strict_number_field(row, "mark_price_change_24h_pct") for row in rows]
    taker_values = [_strict_number_field(row, "taker_buy_sell_ratio") for row in rows]
    basis_values = [_strict_number_field(row, "basis_bps") for row in rows]

    avg_funding = _avg(funding_values)
    avg_oi_change = _avg(oi_change_values)
    avg_price_change = _avg(price_change_values)
    avg_taker = _avg(taker_values)
    avg_basis = _avg(basis_values)

    crowding_score = _crowding_score(avg_funding, avg_oi_change, avg_taker, avg_basis)
    crowding_bias = _crowding_bias(crowding_score)
    late_stage_heat = _classify_late_stage_heat(avg_funding, avg_oi_change, avg_price_change, avg_taker, avg_basis)

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
        "late_stage_heat": late_stage_heat,
        "execution_hazard": _execution_hazard(late_stage_heat),
    }
