from __future__ import annotations

import math
from numbers import Real
from typing import Any

from trading_system.app.config import normalize_engine_names
from trading_system.app.types import RegimeSnapshot

from .breadth import compute_breadth_metrics
from .derivatives import summarize_derivatives_risk

MAJOR_SYMBOLS = {"BTCUSDT", "ETHUSDT"}
_MISSING = object()
_CROWDING_BIAS_VALUES = {"balanced", "crowded_long", "crowded_short"}
_OI_TREND_VALUES = {"flat", "expanding", "contracting"}
_LATE_STAGE_HEAT_VALUES = {"none", "cascade", "squeeze"}

_REGIME_PROFILES: dict[str, dict[str, Any]] = {
    "RISK_ON_TREND": {
        "risk_multiplier": 1.15,
        "bucket_targets": {"trend": 0.7, "rotation": 0.25, "short": 0.05},
        "suppression_rules": [],
    },
    "RISK_ON_ROTATION": {
        "risk_multiplier": 1.05,
        "bucket_targets": {"trend": 0.45, "rotation": 0.45, "short": 0.1},
        "suppression_rules": [],
    },
    "MIXED": {
        "risk_multiplier": 0.9,
        "bucket_targets": {"trend": 0.5, "rotation": 0.3, "short": 0.2},
        "suppression_rules": [],
    },
    "RISK_OFF": {
        "risk_multiplier": 0.7,
        "bucket_targets": {"trend": 0.25, "rotation": 0.05, "short": 0.7},
        "suppression_rules": ["rotation"],
    },
    "HIGH_VOL_DEFENSIVE": {
        "risk_multiplier": 0.55,
        "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8},
        "suppression_rules": ["rotation"],
    },
    "CRASH_DEFENSIVE": {
        "risk_multiplier": 0.45,
        "bucket_targets": {"trend": 0.0, "rotation": 0.0, "short": 0.65},
        "suppression_rules": ["trend", "rotation"],
    },
}


def _coerce_rows(market: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(market, list):
        return market
    if isinstance(market, dict):
        symbols = market.get("symbols", {})
        if isinstance(symbols, dict):
            return [{"symbol": symbol, **payload} for symbol, payload in sorted(symbols.items())]
    return []


def _strict_present_finite_float(
    row: dict[str, Any], field: str, *, symbol: str, timeframe: str
) -> float | None:
    if field not in row:
        return None
    value = row[field]
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"market regime field must be a finite number: {symbol}.{timeframe}.{field}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"market regime field must be finite: {symbol}.{timeframe}.{field}")
    return number


def _strict_derivatives_summary_category(
    summary: dict[str, Any], field: str, *, default: str, allowed: set[str]
) -> str:
    value = summary.get(field, _MISSING)
    if value is _MISSING or value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"derivatives summary {field} must be a string")
    if value.strip() != value or not value:
        raise ValueError(f"derivatives summary {field} must be a canonical string")
    if value not in allowed:
        raise ValueError(f"derivatives summary {field} must be one of {sorted(allowed)}")
    return value


def _major_trend_strength(market_rows: list[dict[str, Any]]) -> float:
    majors = [row for row in market_rows if row.get("symbol") in MAJOR_SYMBOLS]
    if not majors:
        return 0.0

    positives = 0
    for row in majors:
        symbol = row["symbol"]
        daily = row.get("daily", {})
        if not isinstance(daily, dict):
            raise ValueError(f"market regime timeframe must be an object: {symbol}.daily")
        close = _strict_present_finite_float(daily, "close", symbol=symbol, timeframe="daily")
        ema20 = _strict_present_finite_float(daily, "ema_20", symbol=symbol, timeframe="daily")
        ema50 = _strict_present_finite_float(daily, "ema_50", symbol=symbol, timeframe="daily")
        if close is not None and ema20 is not None and ema50 is not None and close > ema20 > ema50:
            positives += 1
    return positives / len(majors)


def _avg_daily_atr_pct(market_rows: list[dict[str, Any]]) -> float:
    if not market_rows:
        return 0.0
    total = 0.0
    for row in market_rows:
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("market regime row symbol must be a non-empty string")
        daily = row.get("daily", {})
        if not isinstance(daily, dict):
            raise ValueError(f"market regime timeframe must be an object: {symbol}.daily")
        total += _strict_present_finite_float(daily, "atr_pct", symbol=symbol, timeframe="daily") or 0.0
    return total / len(market_rows)


def _classify_label(
    breadth: dict[str, float], derivatives: dict[str, Any], trend_strength: float, avg_daily_atr_pct: float
) -> str:
    breadth_strong = (
        breadth["pct_above_4h_ema20"] >= 0.7
        and breadth["pct_4h_ema20_above_ema50"] >= 0.65
        and breadth["positive_momentum_share"] >= 0.6
    )
    breadth_weak = (
        breadth["pct_above_4h_ema20"] < 0.4
        and breadth["pct_4h_ema20_above_ema50"] < 0.4
        and breadth["positive_momentum_share"] < 0.4
    )
    high_volatility = avg_daily_atr_pct >= 0.06
    crowding_bias = _strict_derivatives_summary_category(
        derivatives,
        "crowding_bias",
        default="balanced",
        allowed=_CROWDING_BIAS_VALUES,
    )
    oi_trend = _strict_derivatives_summary_category(
        derivatives,
        "oi_trend",
        default="flat",
        allowed=_OI_TREND_VALUES,
    )
    late_stage_heat = _strict_derivatives_summary_category(
        derivatives,
        "late_stage_heat",
        default="none",
        allowed=_LATE_STAGE_HEAT_VALUES,
    )

    if late_stage_heat != "none":
        return "CRASH_DEFENSIVE"
    if high_volatility:
        return "HIGH_VOL_DEFENSIVE"
    if breadth_strong and trend_strength >= 0.7 and crowding_bias != "crowded_short":
        return "RISK_ON_TREND"
    if breadth_strong and trend_strength < 0.7 and crowding_bias != "crowded_short":
        return "RISK_ON_ROTATION"
    if breadth_weak or crowding_bias == "crowded_short" or oi_trend == "contracting":
        return "RISK_OFF"
    return "MIXED"


def _base_confidence(label: str) -> float:
    if label == "RISK_ON_TREND":
        return 0.82
    if label == "RISK_ON_ROTATION":
        return 0.76
    if label == "RISK_OFF":
        return 0.78
    if label == "HIGH_VOL_DEFENSIVE":
        return 0.74
    if label == "CRASH_DEFENSIVE":
        return 0.3
    return 0.58


def _aggression_scale(confidence: float) -> float:
    if confidence >= 0.75:
        return 1.0
    if confidence >= 0.55:
        return 0.85
    if confidence >= 0.4:
        return 0.65
    return 0.45


def classify_regime(
    market: dict[str, Any] | list[dict[str, Any]],
    derivatives: dict[str, Any] | list[dict[str, Any]],
    *,
    force_low_confidence: bool = False,
    disabled_engines: tuple[str, ...] | list[str] | set[str] | frozenset[str] | None = None,
) -> RegimeSnapshot:
    market_rows = _coerce_rows(market)
    breadth = compute_breadth_metrics(market_rows)
    derivatives_summary = summarize_derivatives_risk(derivatives)
    trend_strength = _major_trend_strength(market_rows)
    avg_daily_atr_pct = _avg_daily_atr_pct(market_rows)

    label = _classify_label(breadth, derivatives_summary, trend_strength, avg_daily_atr_pct)
    confidence = _base_confidence(label)

    confidence += (breadth["pct_above_4h_ema20"] - 0.5) * 0.2
    confidence += (trend_strength - 0.5) * 0.15
    crowding_score = float(derivatives_summary.get("crowding_score", 0.0))
    confidence -= max(crowding_score, 0.0) * 0.02
    confidence = max(0.05, min(confidence, 0.98))

    if force_low_confidence:
        confidence = min(confidence, 0.35)

    aggression = _aggression_scale(confidence)
    crowding_bias = _strict_derivatives_summary_category(
        derivatives_summary,
        "crowding_bias",
        default="balanced",
        allowed=_CROWDING_BIAS_VALUES,
    )
    late_stage_heat = _strict_derivatives_summary_category(
        derivatives_summary,
        "late_stage_heat",
        default="none",
        allowed=_LATE_STAGE_HEAT_VALUES,
    )
    if crowding_bias == "crowded_long":
        aggression = max(0.3, aggression * 0.8)
    if late_stage_heat != "none":
        aggression = min(aggression, 0.45)

    execution_policy = "normal"
    if aggression <= 0.5:
        execution_policy = "suppress"
    elif aggression < 0.95:
        execution_policy = "downsize"

    profile = _REGIME_PROFILES[label]
    base_bucket_targets = profile["bucket_targets"]
    bucket_targets = {
        bucket: round(float(weight) * aggression, 6) for bucket, weight in base_bucket_targets.items()
    }
    risk_multiplier = round(float(profile["risk_multiplier"]) * aggression, 6)

    suppression_rules = list(profile["suppression_rules"])
    if aggression < 0.7 and "rotation" not in suppression_rules:
        suppression_rules.append("rotation")

    for engine in normalize_engine_names(disabled_engines):
        bucket_targets[engine] = 0.0
        if engine not in suppression_rules:
            suppression_rules.append(engine)

    return RegimeSnapshot(
        label=label,
        confidence=round(confidence, 6),
        risk_multiplier=risk_multiplier,
        execution_policy=execution_policy,
        bucket_targets=bucket_targets,
        suppression_rules=suppression_rules,
    )
