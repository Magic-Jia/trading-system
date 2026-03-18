from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from trading_system.app.config import DEFAULT_CONFIG

from .liquidity_filter import evaluate_liquidity
from .sector_map import sector_for_symbol

_TIER_TO_DEPTH_MULTIPLIER: dict[str, float] = {
    "top": 0.20,
    "high": 0.12,
    "medium": 0.08,
    "low": 0.03,
}

_TIER_TO_SLIPPAGE_BPS: dict[str, float] = {
    "top": 2.0,
    "high": 8.0,
    "medium": 18.0,
    "low": 35.0,
}

_TIER_TO_LISTING_AGE_DAYS: dict[str, float] = {
    "top": 3650.0,
    "high": 1200.0,
    "medium": 365.0,
    "low": 14.0,
}


@dataclass(slots=True)
class UniverseBuildResult:
    major_universe: list[dict[str, Any]] = field(default_factory=list)
    rotation_universe: list[dict[str, Any]] = field(default_factory=list)
    short_universe: list[dict[str, Any]] = field(default_factory=list)


def _to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _extract_rows(market: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    symbols = market.get("symbols")
    if isinstance(symbols, Mapping):
        rows: list[tuple[str, Mapping[str, Any]]] = []
        for symbol, payload in symbols.items():
            if isinstance(payload, Mapping):
                rows.append((str(symbol), payload))
        return rows

    return []


def _volume_usdt_24h(payload: Mapping[str, Any]) -> float:
    daily = payload.get("daily")
    four_hour = payload.get("4h")
    one_hour = payload.get("1h")

    if isinstance(daily, Mapping) and "volume_usdt_24h" in daily:
        return _to_float(daily.get("volume_usdt_24h"))
    if isinstance(four_hour, Mapping) and "volume_usdt_24h" in four_hour:
        return _to_float(four_hour.get("volume_usdt_24h"))
    if isinstance(one_hour, Mapping) and "volume_usdt_24h" in one_hour:
        return _to_float(one_hour.get("volume_usdt_24h"))
    return 0.0


def _liquidity_inputs(payload: Mapping[str, Any]) -> dict[str, Any]:
    tier = str(payload.get("liquidity_tier", "")).lower()
    rolling_notional = _volume_usdt_24h(payload)
    depth_multiplier = _TIER_TO_DEPTH_MULTIPLIER.get(tier, 0.05)
    slippage_bps = _TIER_TO_SLIPPAGE_BPS.get(tier, 25.0)
    listing_age_days = _TIER_TO_LISTING_AGE_DAYS.get(tier, 90.0)

    daily = payload.get("daily")
    atr_pct = _to_float(daily.get("atr_pct")) if isinstance(daily, Mapping) else 0.0

    return {
        "rolling_notional": rolling_notional,
        "depth_proxy_notional": rolling_notional * depth_multiplier,
        "slippage_bps": slippage_bps,
        "listing_age_days": listing_age_days,
        "wick_risk_flag": atr_pct >= 0.12,
    }


def _sort_universe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (-_to_float(row["liquidity_meta"]["rolling_notional"]), row["symbol"]))


def build_universes(market: Mapping[str, Any]) -> UniverseBuildResult:
    majors: list[dict[str, Any]] = []
    rotation: list[dict[str, Any]] = []
    shortable_majors: list[dict[str, Any]] = []

    for symbol, payload in _extract_rows(market):
        sector = str(payload.get("sector") or sector_for_symbol(symbol))
        liquidity = evaluate_liquidity(_liquidity_inputs(payload))

        row: dict[str, Any] = {
            "symbol": symbol,
            "sector": sector,
            "liquidity_tier": payload.get("liquidity_tier"),
            "passes_liquidity": bool(liquidity["passes_liquidity"]),
            "listing_age_ok": bool(liquidity["listing_age_ok"]),
            "liquidity_meta": liquidity,
        }

        if row["passes_liquidity"] and sector == "majors":
            majors.append(row)
            shortable_majors.append(dict(row))
            continue

        if row["passes_liquidity"] and row["listing_age_ok"] and sector != "majors":
            if _to_float(liquidity["rolling_notional"]) >= DEFAULT_CONFIG.universe.min_liquidity_usdt_24h:
                rotation.append(row)

    return UniverseBuildResult(
        major_universe=_sort_universe(majors),
        rotation_universe=_sort_universe(rotation),
        short_universe=_sort_universe(shortable_majors),
    )
