from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

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


def _canonical_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string when present")
    canonical = value.strip()
    if not canonical:
        raise ValueError(f"{field} must not be blank when present")
    return canonical


def _optional_canonical_string(payload: Mapping[str, Any], field: str) -> str | None:
    if field not in payload or payload[field] is None:
        return None
    return _canonical_string(payload[field], field=field)


def _extract_rows(market: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    symbols = market.get("symbols")
    if isinstance(symbols, Mapping):
        rows: list[tuple[str, Mapping[str, Any]]] = []
        for symbol, payload in symbols.items():
            symbol = _canonical_string(symbol, field="symbol")
            if isinstance(payload, Mapping):
                rows.append((symbol, payload))
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


def _derivatives_by_symbol(derivatives: Sequence[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    if not derivatives:
        return {}

    rows: dict[str, Mapping[str, Any]] = {}
    for row in derivatives:
        symbol = row.get("symbol")
        if isinstance(symbol, str):
            rows[symbol] = row
    return rows


def _liquidity_inputs(payload: Mapping[str, Any], *, rolling_notional: float | None = None) -> dict[str, Any]:
    tier = (_optional_canonical_string(payload, "liquidity_tier") or "").lower()
    resolved_rolling_notional = _volume_usdt_24h(payload) if rolling_notional is None else rolling_notional
    depth_multiplier = _TIER_TO_DEPTH_MULTIPLIER.get(tier, 0.05)
    slippage_bps = _TIER_TO_SLIPPAGE_BPS.get(tier, 25.0)
    listing_age_days = _TIER_TO_LISTING_AGE_DAYS.get(tier, 90.0)

    daily = payload.get("daily")
    atr_pct = _to_float(daily.get("atr_pct")) if isinstance(daily, Mapping) else 0.0

    return {
        "rolling_notional": resolved_rolling_notional,
        "depth_proxy_notional": resolved_rolling_notional * depth_multiplier,
        "slippage_bps": slippage_bps,
        "listing_age_days": listing_age_days,
        "wick_risk_flag": atr_pct >= 0.12,
    }


def _sort_universe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (-_to_float(row["liquidity_meta"]["rolling_notional"]), row["symbol"]))


def _sector_for_payload(symbol: str, payload: Mapping[str, Any]) -> str:
    sector = _optional_canonical_string(payload, "sector")
    return sector if sector is not None else sector_for_symbol(symbol)


def _canonical_liquidity_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("evaluate_liquidity must return a mapping")

    liquidity: dict[str, Any] = {}
    for key, result_value in value.items():
        try:
            canonical_key = _canonical_string(key, field="evaluate_liquidity key")
        except ValueError as exc:
            raise ValueError("evaluate_liquidity must return a mapping with canonical string keys") from exc
        liquidity[canonical_key] = result_value
    return liquidity


def _strict_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be a bool")


def build_universes(
    market: Mapping[str, Any],
    derivatives: Sequence[Mapping[str, Any]] | None = None,
) -> UniverseBuildResult:
    majors: list[dict[str, Any]] = []
    rotation: list[dict[str, Any]] = []
    shortable_majors: list[dict[str, Any]] = []
    derivatives_rows = _derivatives_by_symbol(derivatives)

    for symbol, payload in _extract_rows(market):
        sector = _sector_for_payload(symbol, payload)
        spot_volume = _volume_usdt_24h(payload)
        derivatives_row = derivatives_rows.get(symbol)
        open_interest_usdt = _to_float(derivatives_row.get("open_interest_usdt")) if derivatives_row else 0.0
        rotation_notional = max(spot_volume, open_interest_usdt)
        liquidity_inputs = _liquidity_inputs(
            payload,
            rolling_notional=rotation_notional if sector != "majors" else None,
        )
        liquidity = _canonical_liquidity_result(evaluate_liquidity(liquidity_inputs))
        liquidity["spot_volume_usdt_24h"] = spot_volume
        liquidity["open_interest_usdt"] = open_interest_usdt
        liquidity["liquidity_source"] = (
            "open_interest_usdt" if sector != "majors" and open_interest_usdt > spot_volume else "volume_usdt_24h"
        )

        row: dict[str, Any] = {
            "symbol": symbol,
            "sector": sector,
            "liquidity_tier": _optional_canonical_string(payload, "liquidity_tier"),
            "passes_liquidity": _strict_bool(liquidity["passes_liquidity"], field="passes_liquidity"),
            "listing_age_ok": _strict_bool(liquidity["listing_age_ok"], field="listing_age_ok"),
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
