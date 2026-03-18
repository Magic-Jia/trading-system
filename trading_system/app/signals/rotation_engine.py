from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from trading_system.app.config import DEFAULT_CONFIG
from trading_system.app.signals.scoring import score_rotation_candidate
from trading_system.app.types import EngineCandidate, RegimeSnapshot

_ROTATION_SCORE_FLOOR = 0.60


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _tf_row(payload: Mapping[str, Any], timeframe: str) -> Mapping[str, Any]:
    row = payload.get(timeframe)
    if isinstance(row, Mapping):
        return row
    return {}


def _regime_value(regime: RegimeSnapshot | Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    if regime is None:
        return default
    if isinstance(regime, Mapping):
        return regime.get(key, default)
    return getattr(regime, key, default)


def _rotation_suppressed(regime: RegimeSnapshot | Mapping[str, Any] | None) -> bool:
    rules = _regime_value(regime, "suppression_rules", [])
    if not isinstance(rules, list):
        return False
    return "rotation" in {str(rule).lower().strip() for rule in rules}


def _rotation_symbols(rotation_universe: Sequence[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    if rotation_universe is None:
        return rows
    for row in rotation_universe:
        symbol = str(row.get("symbol", "")).upper().strip()
        if symbol:
            rows[symbol] = row
    return rows


def _major_proxy_returns(market_context: Mapping[str, Any]) -> dict[str, float]:
    symbols = market_context.get("symbols")
    if not isinstance(symbols, Mapping):
        return {"daily": 0.0, "4h": 0.0, "1h": 0.0}

    majors = [payload for symbol, payload in symbols.items() if str(symbol).upper() in {"BTCUSDT", "ETHUSDT"}]
    if not majors:
        return {"daily": 0.0, "4h": 0.0, "1h": 0.0}

    total_daily = 0.0
    total_h4 = 0.0
    total_h1 = 0.0
    for payload in majors:
        total_daily += _to_float(_tf_row(payload, "daily").get("return_pct_7d"))
        total_h4 += _to_float(_tf_row(payload, "4h").get("return_pct_3d"))
        total_h1 += _to_float(_tf_row(payload, "1h").get("return_pct_24h"))

    count = float(len(majors))
    return {"daily": total_daily / count, "4h": total_h4 / count, "1h": total_h1 / count}


def _trend_intact(payload: Mapping[str, Any]) -> bool:
    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    return (
        _to_float(daily.get("close")) > _to_float(daily.get("ema_20")) > _to_float(daily.get("ema_50"))
        and _to_float(h4.get("close")) >= _to_float(h4.get("ema_20")) >= _to_float(h4.get("ema_50"))
        and _to_float(h1.get("close")) >= _to_float(h1.get("ema_20")) >= _to_float(h1.get("ema_50"))
    )


def _relative_strength_features(payload: Mapping[str, Any], proxy: Mapping[str, float]) -> dict[str, float]:
    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")

    daily_spread = _to_float(daily.get("return_pct_7d")) - _to_float(proxy.get("daily"))
    h4_spread = _to_float(h4.get("return_pct_3d")) - _to_float(proxy.get("4h"))
    h1_spread = _to_float(h1.get("return_pct_24h")) - _to_float(proxy.get("1h"))

    average_spread = (daily_spread + h4_spread + h1_spread) / 3.0
    relative_strength_rank = max(min((average_spread + 0.03) / 0.06, 1.0), 0.0)
    persistence = sum(1 for spread in (daily_spread, h4_spread, h1_spread) if spread >= -0.01) / 3.0

    return {
        "daily_spread": daily_spread,
        "h4_spread": h4_spread,
        "h1_spread": h1_spread,
        "relative_strength_rank": relative_strength_rank,
        "persistence": persistence,
    }


def _pullback_quality(payload: Mapping[str, Any]) -> float:
    h1 = _tf_row(payload, "1h")
    close = _to_float(h1.get("close"))
    ema20 = _to_float(h1.get("ema_20"))
    ema50 = _to_float(h1.get("ema_50"))
    if close <= 0 or ema20 <= 0 or ema50 <= 0 or close < ema20 or ema20 < ema50:
        return 0.0

    extension = max(close - ema20, 0.0)
    extension_band = max(close * 0.03, 1e-9)
    quality = 1.0 - min(extension / extension_band, 1.0)
    return max(min(quality, 1.0), 0.0)


def _liquidity_quality(payload: Mapping[str, Any], universe_row: Mapping[str, Any]) -> float:
    daily = _tf_row(payload, "daily")
    volume = _to_float(daily.get("volume_usdt_24h"))
    rolling_notional = _to_float(dict(universe_row.get("liquidity_meta", {})).get("rolling_notional"))
    normalized_volume = min(max(volume, rolling_notional) / 1_200_000_000.0, 1.0)
    slippage_bps = _to_float(dict(universe_row.get("liquidity_meta", {})).get("slippage_bps"),)
    slippage_quality = 1.0 - min(slippage_bps / 25.0, 1.0)
    return max(min((normalized_volume * 0.8) + (slippage_quality * 0.2), 1.0), 0.0)


def _volatility_quality(payload: Mapping[str, Any]) -> float:
    daily = _tf_row(payload, "daily")
    atr_pct = _to_float(daily.get("atr_pct"))
    if atr_pct <= 0.0:
        return 0.0
    return max(0.0, min(1.0 - (abs(atr_pct - 0.055) / 0.04), 1.0))


def _setup_type(payload: Mapping[str, Any]) -> str:
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    if _to_float(h4.get("return_pct_3d")) >= 0.024 and _to_float(h1.get("return_pct_24h")) >= 0.007:
        return "RS_REACCELERATION"
    return "RS_PULLBACK"


def generate_rotation_candidates(
    market_context: Mapping[str, Any],
    *,
    rotation_universe: Sequence[Mapping[str, Any]] | None = None,
    regime: RegimeSnapshot | Mapping[str, Any] | None = None,
) -> list[EngineCandidate]:
    if _rotation_suppressed(regime):
        return []

    symbols = market_context.get("symbols")
    if not isinstance(symbols, Mapping):
        return []

    eligible = _rotation_symbols(rotation_universe)
    if not eligible:
        return []

    proxy = _major_proxy_returns(market_context)
    candidates: list[EngineCandidate] = []
    for symbol, universe_row in eligible.items():
        payload_value = symbols.get(symbol)
        if not isinstance(payload_value, Mapping):
            continue
        payload = payload_value
        if str(payload.get("sector", "")).lower() == "majors":
            continue
        if not _trend_intact(payload):
            continue

        rs_features = _relative_strength_features(payload, proxy)
        if rs_features["relative_strength_rank"] < 0.38 or rs_features["persistence"] < (2.0 / 3.0):
            continue

        scored = score_rotation_candidate(
            {
                "relative_strength_rank": rs_features["relative_strength_rank"],
                "persistence": rs_features["persistence"],
                "pullback_quality": _pullback_quality(payload),
                "liquidity_quality": _liquidity_quality(payload, universe_row),
                "volatility_quality": _volatility_quality(payload),
            }
        )
        total_score = _to_float(scored.get("total"))
        if total_score < _ROTATION_SCORE_FLOOR:
            continue

        daily = _tf_row(payload, "daily")
        liquidity_meta = dict(universe_row.get("liquidity_meta", {})) if isinstance(universe_row, Mapping) else {}
        liquidity_meta.setdefault("liquidity_tier", payload.get("liquidity_tier"))
        liquidity_meta["volume_usdt_24h"] = _to_float(daily.get("volume_usdt_24h"))

        candidates.append(
            EngineCandidate(
                engine="rotation",
                setup_type=_setup_type(payload),
                symbol=symbol,
                side="LONG",
                score=total_score,
                timeframe_meta={
                    "daily_bias": "relative_strength_leader",
                    "h4_structure": "leader_persistence",
                    "h1_trigger": "pullback_hold_or_reacceleration",
                    "relative_strength": {
                        "daily_spread": round(rs_features["daily_spread"], 6),
                        "h4_spread": round(rs_features["h4_spread"], 6),
                        "h1_spread": round(rs_features["h1_spread"], 6),
                    },
                    "score_components": scored.get("components", {}),
                },
                sector=str(payload.get("sector") or universe_row.get("sector") or ""),
                liquidity_meta=liquidity_meta,
            )
        )

    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.symbol))
