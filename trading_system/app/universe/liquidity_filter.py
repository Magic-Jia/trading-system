from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping


def _env_float(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


def _env_bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no"}


def _to_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True, slots=True)
class LiquidityFilterConfig:
    min_rolling_notional: float = field(
        default_factory=lambda: _env_float("TRADING_UNIVERSE_MIN_ROLLING_NOTIONAL", "1000000")
    )
    min_depth_proxy_notional: float = field(
        default_factory=lambda: _env_float("TRADING_UNIVERSE_MIN_DEPTH_PROXY_NOTIONAL", "100000")
    )
    max_slippage_bps: float = field(default_factory=lambda: _env_float("TRADING_UNIVERSE_MAX_SLIPPAGE_BPS", "20"))
    min_listing_age_days: float = field(
        default_factory=lambda: _env_float("TRADING_UNIVERSE_MIN_LISTING_AGE_DAYS", "30")
    )
    reject_wick_risk: bool = field(default_factory=lambda: _env_bool("TRADING_UNIVERSE_REJECT_WICK_RISK", "1"))


def evaluate_liquidity(
    metrics: Mapping[str, Any], config: LiquidityFilterConfig | None = None
) -> dict[str, bool | float]:
    cfg = config or LiquidityFilterConfig()

    rolling_notional = _to_float(metrics.get("rolling_notional"), 0.0)
    depth_proxy_notional = _to_float(metrics.get("depth_proxy_notional"), rolling_notional)
    slippage_bps = _to_float(metrics.get("slippage_bps"), float("inf"))
    listing_age_days = _to_float(metrics.get("listing_age_days"), float("inf"))

    wick_risk_flags = metrics.get("wick_risk_flags")
    has_wick_risk = bool(metrics.get("wick_risk_flag", False))
    if isinstance(wick_risk_flags, (list, tuple, set)):
        has_wick_risk = has_wick_risk or len(wick_risk_flags) > 0

    rolling_notional_ok = rolling_notional >= cfg.min_rolling_notional
    depth_proxy_ok = depth_proxy_notional >= cfg.min_depth_proxy_notional
    slippage_ok = slippage_bps <= cfg.max_slippage_bps
    listing_age_ok = listing_age_days >= cfg.min_listing_age_days
    wick_risk_ok = (not cfg.reject_wick_risk) or (not has_wick_risk)

    passes_liquidity = all((rolling_notional_ok, depth_proxy_ok, slippage_ok, listing_age_ok, wick_risk_ok))
    return {
        "passes_liquidity": passes_liquidity,
        "rolling_notional": rolling_notional,
        "depth_proxy_notional": depth_proxy_notional,
        "slippage_bps": slippage_bps,
        "listing_age_days": listing_age_days,
        "rolling_notional_ok": rolling_notional_ok,
        "depth_proxy_ok": depth_proxy_ok,
        "slippage_ok": slippage_ok,
        "listing_age_ok": listing_age_ok,
        "wick_risk_ok": wick_risk_ok,
    }


def passes_liquidity_filter(metrics: Mapping[str, Any], config: LiquidityFilterConfig | None = None) -> bool:
    evaluation = evaluate_liquidity(metrics, config=config)
    return bool(evaluation["passes_liquidity"])
