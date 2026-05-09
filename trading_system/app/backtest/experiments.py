from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict
import math
from numbers import Real
from statistics import mean
from typing import Any, Iterable, Mapping

from trading_system.app.config import DEFAULT_CONFIG
from trading_system.app.market_regime.classifier import classify_regime
from trading_system.app.signals import rotation_engine as rotation_signals
from trading_system.app.signals import trend_engine as trend_signals
from trading_system.app.signals.rotation_engine import generate_rotation_candidates
from trading_system.app.signals.short_engine import generate_short_candidates
from trading_system.app.signals.trend_engine import generate_trend_candidates
from trading_system.app.universe.builder import build_universes

from .engine import _allocation_rows, _rank_key, _replay_full_market_baseline_rows, _validated_candidates
from .metrics import expectancy, payoff_ratio, win_rate
from .types import BacktestConfig, BacktestCosts, DatasetSnapshotRow
from .walk_forward import (
    build_walk_forward_windows,
    effective_walk_forward_step_size,
    summarize_parameter_stability,
    summarize_return_scorecard,
    summarize_walk_forward_robustness,
    summarize_walk_forward_window,
)

_REGIME_BASE_RISK_MULTIPLIERS = {
    "RISK_ON_TREND": 1.15,
    "RISK_ON_ROTATION": 1.05,
    "MIXED": 0.9,
    "RISK_OFF": 0.7,
    "HIGH_VOL_DEFENSIVE": 0.55,
    "CRASH_DEFENSIVE": 0.45,
}

_FUNNEL_KEYS = (
    "input_universe",
    "raw_candidates",
    "validated_candidates",
    "allocation_decisions",
    "accepted_allocations",
)

_SYMBOL_FUNNEL_KEYS = (
    "raw_candidates",
    "validated_candidates",
    "allocation_decisions",
    "accepted_allocations",
)

_ROTATION_FILTER_KEYS = (
    "rotation_suppressed",
    "missing_payload",
    "major_filtered",
    "trend_filtered",
    "absolute_strength_filtered",
    "overheat_filtered",
    "overheat_bypassed",
    "crowding_filtered",
    "relative_strength_filtered",
    "score_floor_filtered",
    "score_floor_bypassed",
    "stop_loss_filtered",
    "selected",
)

_TREND_FILTER_KEYS = (
    "missing_payload",
    "eligibility_filtered",
    "eligibility_liquidity_tier_filtered",
    "eligibility_pretrend_filtered",
    "eligibility_daily_return_filtered",
    "eligibility_h4_return_filtered",
    "trend_filtered",
    "absolute_strength_filtered",
    "overheat_filtered",
    "crowding_filtered",
    "score_filtered",
    "stop_loss_filtered",
    "selected",
)

_FRICTION_SCENARIOS: dict[str, BacktestCosts] = {
    "low": BacktestCosts(fee_bps=2.0, slippage_bps=1.0, funding_bps_per_day=0.5),
    "base": BacktestCosts(fee_bps=4.0, slippage_bps=2.0, funding_bps_per_day=1.0),
    "stressed": BacktestCosts(fee_bps=8.0, slippage_bps=4.0, funding_bps_per_day=2.0),
}


def _finite_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite number")
    return number


def _strict_finite_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite number")
    return number


def _strict_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a bool")
    return value


def _optional_strict_finite_number(regime: Mapping[str, Any], field: str, *, default: float = 0.0) -> float:
    value = regime.get(field)
    if value is None:
        return default
    return _strict_finite_number(value, field_name=f"regime.{field}")


def _strict_finite_number_mapping(value: Any, *, path: str) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object")
    result: dict[str, float] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{path} keys must be strings")
        result[key] = _strict_finite_number(item, field_name=f"{path}.{key}")
    return result


def _strict_optional_mapping_number(mapping: Mapping[str, float], key: str, *, path: str, default: float = 0.0) -> float:
    if key not in mapping:
        return default
    return _strict_finite_number(mapping[key], field_name=f"{path}.{key}")


def _strict_daily_finite_number(
    payload: Mapping[str, Any],
    symbol: str,
    field: str,
    *,
    default: float = 0.0,
) -> float:
    daily = trend_signals._tf_row(payload, "daily")
    if field not in daily:
        return default
    return _strict_finite_number(daily[field], field_name=f"{symbol}.daily.{field}")


def _strict_timeframe_finite_number(
    payload: Mapping[str, Any],
    symbol: str,
    timeframe: str,
    field: str,
    *,
    default: float = 0.0,
) -> float:
    timeframe_row = trend_signals._tf_row(payload, timeframe)
    if field not in timeframe_row:
        return default
    return _strict_finite_number(timeframe_row[field], field_name=f"{symbol}.{timeframe}.{field}")


def _trace_candidate_sort_score(candidate: Mapping[str, Any], *, index: int, engine: str) -> float:
    if "score" not in candidate or candidate.get("score") is None:
        return 0.0
    score = candidate.get("score")
    if isinstance(score, bool) or not isinstance(score, int | float):
        raise ValueError(f"{engine} candidates[{index}].score must be numeric")
    if not math.isfinite(score):
        raise ValueError(f"{engine} candidates[{index}].score must be finite")
    return float(score)


def _trace_score_components(value: Any, *, engine: str, index: int) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{engine} candidates[{index}].score_components must be an object")
    for key in value:
        if not isinstance(key, str):
            raise ValueError(f"{engine} candidates[{index}].score_components key must be a string")
    return dict(value)


def _has_forward_window(rows: list[DatasetSnapshotRow], evaluation_window: str) -> bool:
    return any(evaluation_window in row.forward_returns for row in rows)


def _has_drawdown_window(rows: list[DatasetSnapshotRow], evaluation_window: str) -> bool:
    return any(evaluation_window in row.forward_drawdowns for row in rows)


def _has_liquidity_snapshot(rows: list[DatasetSnapshotRow]) -> bool:
    return any(row.instrument_rows for row in rows)


def _has_funding_or_basis_fields(rows: list[DatasetSnapshotRow]) -> bool:
    for row in rows:
        for item in row.derivatives:
            if any(key in item for key in ("basis", "basis_bps", "funding_rate", "funding_rate_8h")):
                return True
    return False


def _mean_numeric(values: list[float]) -> float | None:
    cleaned = []
    for value in values:
        if value is None:
            continue
        try:
            cleaned.append(_finite_number(value, field_name="factor_value"))
        except ValueError:
            continue
    if not cleaned:
        return None
    return mean(cleaned)


def _daily_symbol_values(row: DatasetSnapshotRow, key: str) -> list[float]:
    symbols = row.market.get("symbols", {}) if isinstance(row.market, Mapping) else {}
    if not isinstance(symbols, Mapping):
        return []
    values: list[float] = []
    for symbol, payload in symbols.items():
        if not isinstance(payload, Mapping):
            continue
        daily = payload.get("daily")
        if not isinstance(daily, Mapping) or key not in daily or daily[key] is None:
            continue
        values.append(_strict_finite_number(daily[key], field_name=f"{symbol}.daily.{key}"))
    return values


def _trend_factor_value(row: DatasetSnapshotRow) -> float | None:
    symbols = row.market.get("symbols", {}) if isinstance(row.market, Mapping) else {}
    if not isinstance(symbols, Mapping):
        return None
    values: list[float] = []
    for symbol, payload in symbols.items():
        if not isinstance(payload, Mapping):
            continue
        daily = payload.get("daily")
        if not isinstance(daily, Mapping):
            continue
        if daily.get("close") is None or daily.get("ema_50") is None:
            continue
        close = _strict_finite_number(daily["close"], field_name=f"{symbol}.daily.close")
        ema50 = _strict_finite_number(daily["ema_50"], field_name=f"{symbol}.daily.ema_50")
        if ema50 == 0:
            continue
        values.append((close / ema50) - 1.0)
    return _mean_numeric(values)


def _public_strategy_factor_value(row: DatasetSnapshotRow, family: str) -> float | None:
    if family == "trend_following":
        return _trend_factor_value(row)
    if family == "momentum":
        return _mean_numeric(_daily_symbol_values(row, "return_pct_7d"))
    if family == "mean_reversion":
        value = _mean_numeric(_daily_symbol_values(row, "return_pct_7d"))
        return -value if value is not None else None
    if family == "volatility_breakout":
        return _mean_numeric(_daily_symbol_values(row, "atr_pct"))
    return None


def _pearson_correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    x_mean = mean(xs)
    y_mean = mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True))
    x_var = sum((x - x_mean) ** 2 for x in xs)
    y_var = sum((y - y_mean) ** 2 for y in ys)
    if x_var <= 0 or y_var <= 0:
        return None
    return numerator / ((x_var ** 0.5) * (y_var ** 0.5))


def _public_strategy_factor_effectiveness(
    rows: list[DatasetSnapshotRow],
    *,
    family: str,
    evaluation_window: str,
    minimum_sample_count: int,
) -> dict[str, Any] | None:
    pairs: list[tuple[float, float]] = []
    for row in rows:
        if evaluation_window not in row.forward_returns:
            continue
        factor_value = _public_strategy_factor_value(row, family)
        if factor_value is None:
            continue
        try:
            finite_factor_value = _finite_number(factor_value, field_name="factor_value")
        except ValueError:
            continue
        forward_return = _finite_number(
            row.forward_returns[evaluation_window],
            field_name=f"forward_returns.{evaluation_window}",
        )
        pairs.append((finite_factor_value, forward_return))

    if not pairs:
        return None

    ordered = sorted(pairs, key=lambda item: item[0])
    bucket_size = max(1, len(ordered) // 2)
    bottom_returns = [forward_return for _factor_value, forward_return in ordered[:bucket_size]]
    top_returns = [forward_return for _factor_value, forward_return in ordered[-bucket_size:]]
    factor_values = [factor_value for factor_value, _forward_return in pairs]
    forward_returns = [forward_return for _factor_value, forward_return in pairs]
    information_coefficient = _pearson_correlation(factor_values, forward_returns)
    top_bucket_avg = mean(top_returns) if top_returns else 0.0
    bottom_bucket_avg = mean(bottom_returns) if bottom_returns else 0.0
    top_bucket_hit_rate = sum(1 for value in top_returns if value > 0) / len(top_returns) if top_returns else 0.0
    effectiveness_status = "insufficient_sample"
    if (
        len(pairs) >= minimum_sample_count
        and information_coefficient is not None
        and information_coefficient >= 0.2
        and top_bucket_avg > bottom_bucket_avg
        and top_bucket_hit_rate >= 0.5
    ):
        effectiveness_status = "promising_research"
    elif len(pairs) >= minimum_sample_count:
        effectiveness_status = "not_promising"

    return {
        "sample_count": len(pairs),
        "minimum_sample_count": minimum_sample_count,
        "information_coefficient": round(information_coefficient, 6) if information_coefficient is not None else None,
        "top_bucket_avg_forward_return": round(top_bucket_avg, 6),
        "bottom_bucket_avg_forward_return": round(bottom_bucket_avg, 6),
        "top_minus_bottom_forward_return": round(top_bucket_avg - bottom_bucket_avg, 6),
        "top_bucket_hit_rate": round(top_bucket_hit_rate, 6),
        "effectiveness_status": effectiveness_status,
    }


def run_public_strategy_factor_experiment(
    rows: Iterable[DatasetSnapshotRow],
    *,
    evaluation_window: str = "3d",
    strategy_families: Iterable[str] = (),
    minimum_effectiveness_sample_count: int = 30,
) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=lambda row: (row.timestamp, row.run_id))
    families = tuple(strategy_families) or (
        "trend_following",
        "momentum",
        "mean_reversion",
        "volatility_breakout",
        "liquidity_volume",
        "funding_basis",
        "onchain_flow",
    )

    forward_supported = _has_forward_window(ordered_rows, evaluation_window)
    drawdown_supported = _has_drawdown_window(ordered_rows, evaluation_window)
    liquidity_supported = _has_liquidity_snapshot(ordered_rows)
    funding_supported = _has_funding_or_basis_fields(ordered_rows)

    family_specs: dict[str, dict[str, Any]] = {
        "trend_following": {
            "factor_name": f"trend_proxy_{evaluation_window}",
            "required_fields": [f"forward_returns.{evaluation_window}"],
            "supported": forward_supported,
            "unsupported_reason": None if forward_supported else "missing_forward_return_window",
        },
        "momentum": {
            "factor_name": f"momentum_{evaluation_window}",
            "required_fields": [f"forward_returns.{evaluation_window}"],
            "supported": forward_supported,
            "unsupported_reason": None if forward_supported else "missing_forward_return_window",
        },
        "mean_reversion": {
            "factor_name": f"reversal_proxy_{evaluation_window}",
            "required_fields": [f"forward_returns.{evaluation_window}"],
            "supported": forward_supported,
            "unsupported_reason": None if forward_supported else "missing_forward_return_window",
        },
        "volatility_breakout": {
            "factor_name": f"drawdown_volatility_proxy_{evaluation_window}",
            "required_fields": [f"forward_drawdowns.{evaluation_window}"],
            "supported": drawdown_supported,
            "unsupported_reason": None if drawdown_supported else "missing_forward_drawdown_window",
        },
        "liquidity_volume": {
            "factor_name": "liquidity_volume_filter",
            "required_fields": ["instrument_rows.quote_volume_usdt_24h", "instrument_rows.liquidity_tier"],
            "supported": liquidity_supported,
            "unsupported_reason": None if liquidity_supported else "missing_instrument_snapshot_rows",
        },
        "funding_basis": {
            "factor_name": "funding_basis",
            "required_fields": ["derivatives.funding_rate", "derivatives.basis"],
            "supported": funding_supported and liquidity_supported,
            "unsupported_reason": None if funding_supported and liquidity_supported else "insufficient_funding_or_basis_fields",
        },
        "onchain_flow": {
            "factor_name": "onchain_flow_confirmation",
            "required_fields": ["onchain.exchange_flow", "onchain.stablecoin_supply"],
            "supported": False,
            "unsupported_reason": "missing_onchain_dataset",
        },
    }

    factors = []
    for index, family in enumerate(families):
        if not isinstance(family, str):
            raise ValueError(f"strategy_families[{index}] must be a string")
        spec = dict(family_specs.get(family, {}))
        if not spec:
            spec = {
                "factor_name": family,
                "required_fields": [],
                "supported": False,
                "unsupported_reason": "unknown_strategy_family",
            }
        factor = {
            "source_strategy_family": family,
            "factor_name": spec["factor_name"],
            "required_fields": list(spec["required_fields"]),
            "supported": _strict_bool(spec["supported"], field_name=f"family_specs.{family}.supported"),
            "unsupported_reason": spec["unsupported_reason"],
            "sample_count": len(ordered_rows),
            "evaluation_window": evaluation_window,
        }
        if factor["supported"]:
            effectiveness = _public_strategy_factor_effectiveness(
                ordered_rows,
                family=family,
                evaluation_window=evaluation_window,
                minimum_sample_count=minimum_effectiveness_sample_count,
            )
            if effectiveness is not None:
                factor["effectiveness"] = effectiveness
        factors.append(factor)

    supported = [factor for factor in factors if factor["supported"]]
    unsupported = [factor for factor in factors if not factor["supported"]]
    evaluated = [factor for factor in supported if isinstance(factor.get("effectiveness"), Mapping)]
    effective = [
        factor
        for factor in evaluated
        if dict(factor.get("effectiveness", {})).get("effectiveness_status") == "promising_research"
    ]
    return {
        "metadata": {
            "snapshot_count": len(ordered_rows),
            "evaluation_window": evaluation_window,
            "strategy_family_count": len(families),
            "minimum_effectiveness_sample_count": minimum_effectiveness_sample_count,
        },
        "factors": factors,
        "summary": {
            "supported_factor_count": len(supported),
            "unsupported_factor_count": len(unsupported),
            "data_gap_count": len(unsupported),
            "evaluated_factor_count": len(evaluated),
            "effective_factor_count": len(effective),
        },
    }


def _regime_for_row(row: DatasetSnapshotRow) -> dict[str, Any]:
    override = row.meta.get("regime_override")
    if override is not None and not isinstance(override, Mapping):
        raise ValueError("regime_override must be an object")
    if isinstance(override, Mapping):
        for key in override:
            if not isinstance(key, str):
                raise ValueError("regime_override key must be a string")
        return dict(override)
    return asdict(classify_regime(row.market, row.derivatives))


def _regime_label(regime: Mapping[str, Any], *, default: str) -> str:
    label = regime.get("label")
    if label is None:
        return default
    if not isinstance(label, str):
        raise ValueError("regime.label must be a string when present")
    return label


def _aggression_from_regime(regime: Mapping[str, Any]) -> float:
    label = str(regime.get("label", ""))
    base = _REGIME_BASE_RISK_MULTIPLIERS.get(label)
    if not base:
        return 0.0
    return round(_optional_strict_finite_number(regime, "risk_multiplier") / base, 6)


def _confidence_bucket(confidence: float) -> str:
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.55:
        return "mid"
    return "low"


def _aggression_bucket(aggression: float) -> str:
    if aggression >= 0.95:
        return "full"
    if aggression >= 0.7:
        return "reduced"
    return "defensive"


def _mean_mapping(values: list[dict[str, Any]], *, path: str) -> dict[str, float]:
    if not values:
        return {}
    keys = sorted({key for item in values for key in item})
    return {
        key: mean(
            _strict_finite_number(item[key], field_name=f"{path}.{key}") if key in item else 0.0
            for item in values
        )
        for key in keys
    }


def _duration_stats(labels: list[str]) -> dict[str, dict[str, float]]:
    durations: dict[str, list[int]] = defaultdict(list)
    current_label = None
    current_run = 0
    for label in labels:
        if label == current_label:
            current_run += 1
            continue
        if current_label is not None:
            durations[current_label].append(current_run)
        current_label = label
        current_run = 1
    if current_label is not None:
        durations[current_label].append(current_run)

    return {
        label: {
            "count": len(runs),
            "avg_duration_bars": mean(runs),
            "max_duration_bars": max(runs),
        }
        for label, runs in durations.items()
    }


def run_regime_predictive_power_experiment(rows: Iterable[DatasetSnapshotRow]) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=lambda row: (row.timestamp, row.run_id))
    grouped: dict[str, dict[str, list[Any]]] = defaultdict(lambda: {"returns": [], "drawdowns": [], "confidence": [], "aggression": []})
    confidence_aggression_summary: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "forward_return_3d": []})
    labels: list[str] = []

    for row in ordered_rows:
        regime = _regime_for_row(row)
        label = _regime_label(regime, default="UNKNOWN")
        labels.append(label)
        aggression = _aggression_from_regime(regime)
        confidence = _optional_strict_finite_number(regime, "confidence")
        forward_returns = _strict_finite_number_mapping(row.forward_returns, path="forward_returns")
        forward_drawdowns = _strict_finite_number_mapping(row.forward_drawdowns, path="forward_drawdowns")
        grouped[label]["returns"].append(forward_returns)
        grouped[label]["drawdowns"].append(forward_drawdowns)
        grouped[label]["confidence"].append(confidence)
        grouped[label]["aggression"].append(aggression)

        bucket_name = f"{_confidence_bucket(confidence)}|{_aggression_bucket(aggression)}"
        confidence_aggression_summary[bucket_name]["count"] += 1
        confidence_aggression_summary[bucket_name]["forward_return_3d"].append(
            _strict_optional_mapping_number(forward_returns, "3d", path="forward_returns")
        )

    by_regime = {
        label: {
            "count": len(values["returns"]),
            "forward_return_by_window": _mean_mapping(values["returns"], path="forward_returns"),
            "forward_drawdown_by_window": _mean_mapping(values["drawdowns"], path="forward_drawdowns"),
            "avg_confidence": mean(values["confidence"]) if values["confidence"] else 0.0,
            "avg_aggression": mean(values["aggression"]) if values["aggression"] else 0.0,
        }
        for label, values in grouped.items()
    }

    confidence_summary = {
        bucket: {
            "count": values["count"],
            "avg_forward_return_3d": mean(values["forward_return_3d"]) if values["forward_return_3d"] else 0.0,
        }
        for bucket, values in confidence_aggression_summary.items()
    }

    return {
        "metadata": {"snapshot_count": len(ordered_rows), "regime_count": len(by_regime)},
        "by_regime": by_regime,
        "duration_stats": _duration_stats(labels),
        "confidence_aggression_summary": confidence_summary,
    }


def _rotation_regime_variant(regime: Mapping[str, Any], *, policy: str) -> dict[str, Any]:
    variant = dict(regime)
    raw_rules = variant.get("suppression_rules", [])
    if isinstance(raw_rules, list):
        for index, rule in enumerate(raw_rules):
            if not isinstance(rule, str):
                raise ValueError(f"regime.suppression_rules[{index}] must be a string")
        rules = [rule for rule in raw_rules if rule.lower() != "rotation"]
    else:
        rules = []
    if policy == "current":
        variant["suppression_rules"] = list(raw_rules) if isinstance(raw_rules, list) else raw_rules
    else:
        variant["suppression_rules"] = rules
    return variant


def _rotation_forward_return(row: DatasetSnapshotRow, symbol: str, evaluation_window: str) -> float:
    candidate_returns = row.meta.get("candidate_forward_returns")
    if isinstance(candidate_returns, Mapping):
        rotation_returns = candidate_returns.get("rotation")
        if isinstance(rotation_returns, Mapping) and symbol in rotation_returns:
            return _strict_finite_number(
                rotation_returns[symbol],
                field_name=f"candidate_forward_returns.rotation.{symbol}",
            )
    return _strict_optional_mapping_number(row.forward_returns, evaluation_window, path="forward_returns")


def _rotation_candidates_for_policy(
    row: DatasetSnapshotRow,
    *,
    policy: str,
    soft_score_floor: float,
) -> list[dict[str, Any]]:
    floor = _strict_finite_number(soft_score_floor, field_name="soft_score_floor")
    regime = _rotation_regime_variant(_regime_for_row(row), policy=policy)
    universes = build_universes(row.market, derivatives=row.derivatives)
    candidates = [
        asdict(candidate)
        for candidate in generate_rotation_candidates(
            row.market,
            rotation_universe=universes.rotation_universe,
            derivatives=row.derivatives,
            regime=regime,
        )
    ]
    if policy == "soft_suppression":
        return [
            candidate
            for index, candidate in enumerate(candidates)
            if _trace_candidate_sort_score(candidate, index=index, engine="rotation") >= floor
        ]
    return candidates


def _rotation_candidate_symbol(candidate: Mapping[str, Any], *, field_name: str) -> str:
    symbol = candidate["symbol"]
    if not isinstance(symbol, str):
        raise ValueError(f"{field_name} must be a string")
    return symbol


def _rotation_policy_candidate_map(policy: str, candidates: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {
        _rotation_candidate_symbol(candidate, field_name=f"{policy}.candidate.symbol"): candidate
        for candidate in candidates
    }


def _policy_summary(returns: list[float]) -> dict[str, float]:
    return {
        "bucket_level_pnl": round(sum(returns), 6),
        "hit_rate": round(win_rate(returns), 6),
        "payoff_ratio": round(payoff_ratio(returns), 6),
        "expectancy": round(expectancy(returns), 6),
        "trade_count": len(returns),
    }


def run_rotation_suppression_experiment(
    rows: Iterable[DatasetSnapshotRow],
    *,
    evaluation_window: str = "3d",
    soft_score_floor: float = 0.72,
) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=lambda row: (row.timestamp, row.run_id))
    policy_returns: dict[str, list[float]] = {"current": [], "no_suppression": [], "soft_suppression": []}
    comparison_rows: list[dict[str, Any]] = []
    suppressed_candidate_returns: list[float] = []

    for row in ordered_rows:
        current_candidates = _rotation_candidates_for_policy(row, policy="current", soft_score_floor=soft_score_floor)
        no_suppression_candidates = _rotation_candidates_for_policy(row, policy="no_suppression", soft_score_floor=soft_score_floor)
        soft_candidates = _rotation_candidates_for_policy(row, policy="soft_suppression", soft_score_floor=soft_score_floor)

        policy_maps = {
            "current": _rotation_policy_candidate_map("current", current_candidates),
            "no_suppression": _rotation_policy_candidate_map("no_suppression", no_suppression_candidates),
            "soft_suppression": _rotation_policy_candidate_map("soft_suppression", soft_candidates),
        }
        all_symbols = sorted(set().union(*[set(policy_map) for policy_map in policy_maps.values()]))
        for symbol in all_symbols:
            forward_return = _rotation_forward_return(row, symbol, evaluation_window)
            if symbol in policy_maps["current"]:
                policy_returns["current"].append(forward_return)
            if symbol in policy_maps["no_suppression"]:
                policy_returns["no_suppression"].append(forward_return)
            if symbol in policy_maps["soft_suppression"]:
                policy_returns["soft_suppression"].append(forward_return)
            if symbol in policy_maps["no_suppression"] and symbol not in policy_maps["current"]:
                suppressed_candidate_returns.append(forward_return)

            comparison_rows.append(
                {
                    "timestamp": row.timestamp.isoformat(),
                    "run_id": row.run_id,
                    "symbol": symbol,
                    "forward_return": round(forward_return, 6),
                    "current": "selected" if symbol in policy_maps["current"] else "suppressed",
                    "no_suppression": "selected" if symbol in policy_maps["no_suppression"] else "rejected",
                    "soft_suppression": "selected" if symbol in policy_maps["soft_suppression"] else "filtered",
                }
            )

    suppressed_total = len(suppressed_candidate_returns)
    positive_suppressed = sum(1 for value in suppressed_candidate_returns if value > 0)
    non_positive_suppressed = sum(1 for value in suppressed_candidate_returns if value <= 0)

    return {
        "policies": {policy: _policy_summary(returns) for policy, returns in policy_returns.items()},
        "opportunity_kill_rate": (positive_suppressed / suppressed_total) if suppressed_total else 0.0,
        "avoid_loss_rate": (non_positive_suppressed / suppressed_total) if suppressed_total else 0.0,
        "rotation_comparison_rows": comparison_rows,
    }


def _candidate_forward_return(
    row: DatasetSnapshotRow,
    *,
    engine: str,
    symbol: str,
    evaluation_window: str,
) -> float:
    candidate_returns = row.meta.get("candidate_forward_returns")
    if isinstance(candidate_returns, Mapping):
        engine_returns = candidate_returns.get(engine)
        if isinstance(engine_returns, Mapping) and symbol in engine_returns:
            return _strict_finite_number(
                engine_returns[symbol],
                field_name=f"candidate_forward_returns.{engine}.{symbol}",
            )
    if evaluation_window not in row.forward_returns:
        return 0.0
    return _strict_finite_number(
        row.forward_returns[evaluation_window],
        field_name=f"forward_returns.{evaluation_window}",
    )


def _account_context(row: DatasetSnapshotRow) -> dict[str, Any]:
    default_account = {
        "equity": 0.0,
        "available_balance": 0.0,
        "futures_wallet_balance": 0.0,
        "open_positions": [],
    }
    if not row.account:
        return default_account
    if not isinstance(row.account, Mapping):
        raise ValueError("row.account must be an object")
    for key in row.account:
        if not isinstance(key, str):
            raise ValueError("row.account key must be a string")
    account = dict(row.account)
    account.setdefault("open_positions", [])
    return account


def _accepted_allocation_returns(
    row: DatasetSnapshotRow,
    allocations: list[dict[str, Any]],
    *,
    evaluation_window: str,
) -> list[float]:
    returns: list[float] = []
    for index, allocation in enumerate(allocations):
        if _allocation_status(allocation, index=index).upper() == "REJECTED":
            continue
        returns.append(
            _candidate_forward_return(
                row,
                engine=_allocation_string_field(allocation, "engine", index=index),
                symbol=_allocation_string_field(allocation, "symbol", index=index),
                evaluation_window=evaluation_window,
            )
        )
    return returns


def _telemetry_counter(value: Any, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer counter")
    return value


def _merge_counts(target: dict[str, int], source: Mapping[str, int], *, path: str) -> None:
    for key, value in source.items():
        if not isinstance(key, str):
            raise ValueError(f"{path} key must be a string")
        source_count = _telemetry_counter(value, path=f"{path}.{key}")
        target_count = _telemetry_counter(target.get(key, 0), path=f"{path}.{key}")
        target[key] = target_count + source_count


def _with_zero_defaults(counts: Mapping[str, int], keys: tuple[str, ...], *, path: str) -> dict[str, int]:
    return {key: _telemetry_counter(counts.get(key, 0), path=f"{path}.{key}") for key in keys}


def _symbol_telemetry_row(symbol_rows: dict[str, dict[str, Any]], symbol: str) -> dict[str, Any]:
    telemetry = symbol_rows.get(symbol)
    if telemetry is None:
        telemetry = {"snapshot_count": 1, "funnel": {}, "filter_counts": {}}
        symbol_rows[symbol] = telemetry
    return telemetry


def _bump_symbol_filter(symbol_rows: dict[str, dict[str, Any]], symbol: str, key: str) -> None:
    telemetry = _symbol_telemetry_row(symbol_rows, symbol)
    filter_counts = telemetry["filter_counts"]
    field_path = f"symbol_rows.{symbol}.filter_counts.{key}"
    filter_counts[key] = _telemetry_counter(filter_counts.get(key, 0), path=field_path) + 1


def _bump_symbol_funnel(symbol_rows: dict[str, dict[str, Any]], symbol: str, key: str) -> None:
    telemetry = _symbol_telemetry_row(symbol_rows, symbol)
    funnel = telemetry["funnel"]
    field_path = f"symbol_rows.{symbol}.funnel.{key}"
    funnel[key] = _telemetry_counter(funnel.get(key, 0), path=field_path) + 1


def _telemetry_symbol_row_key(symbol: Any, *, path: str = "symbol_rows") -> str:
    if not isinstance(symbol, str):
        raise ValueError(f"{path} key must be a string")
    return symbol


def _telemetry_mapping(value: Any, *, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object")
    return value


def _telemetry_optional_mapping(payload: Mapping[str, Any], key: str, *, path: str) -> Mapping[str, Any]:
    value = payload.get(key, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"{path}.{key} must be an object")
    return value


def _telemetry_optional_count_mapping(payload: Mapping[str, Any], key: str, *, path: str) -> dict[str, int]:
    value = _telemetry_optional_mapping(payload, key, path=path)
    result: dict[str, int] = {}
    count_path = f"{path}.{key}"
    for count_key, count_value in value.items():
        if not isinstance(count_key, str):
            raise ValueError(f"{count_path} key must be a string")
        result[count_key] = _telemetry_counter(count_value, path=f"{count_path}.{count_key}")
    return result


def _traced_filter_counts(traced: Mapping[str, Any]) -> Mapping[str, Any]:
    filter_counts = traced.get("filter_counts", {})
    if not isinstance(filter_counts, Mapping):
        raise ValueError("traced.filter_counts must be an object")
    return filter_counts


def _normalize_symbol_rows(symbol_rows: Any) -> dict[str, dict[str, Any]]:
    rows = _telemetry_mapping(symbol_rows, path="symbol_rows")
    normalized: dict[str, dict[str, Any]] = {}
    for symbol, payload in rows.items():
        symbol_key = _telemetry_symbol_row_key(symbol)
        row_path = f"symbol_rows.{symbol_key}"
        row_payload = _telemetry_mapping(payload, path=row_path)
        snapshot_count = 0
        if "snapshot_count" in row_payload:
            snapshot_count = _telemetry_integer_counter(
                row_payload["snapshot_count"],
                path=f"{row_path}.snapshot_count",
            )
        normalized[symbol_key] = {
            "snapshot_count": snapshot_count,
            "funnel": _telemetry_optional_count_mapping(row_payload, "funnel", path=row_path),
            "filter_counts": _telemetry_optional_count_mapping(row_payload, "filter_counts", path=row_path),
        }
    return normalized


def _trace_candidate_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("candidates", [])
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("candidates must be a list")
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(value):
        if not isinstance(candidate, Mapping):
            raise ValueError(f"candidates[{index}] must be an object")
        for key in candidate:
            if not isinstance(key, str):
                raise ValueError(f"candidates[{index}] key must be a string")
        rows.append(dict(candidate))
    return rows


def _trace_input_universe(payload: Mapping[str, Any]) -> int:
    value = payload.get("input_universe", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("input_universe must be an integer")
    return value


def _strict_input_universe(payload: Mapping[str, Any], *, path: str) -> int:
    value = payload["input_universe"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path}.input_universe must be an integer")
    return value


def _strict_candidate_rows(payload: Mapping[str, Any], *, path: str) -> list[dict[str, Any]]:
    value = payload["candidates"]
    if not isinstance(value, list):
        raise ValueError(f"{path}.candidates must be a list")
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(value):
        if not isinstance(candidate, Mapping):
            raise ValueError(f"{path}.candidates[{index}] must be an object")
        for key in candidate:
            if not isinstance(key, str):
                raise ValueError(f"{path}.candidates[{index}] key must be a string")
        rows.append(dict(candidate))
    return rows


def _strict_mapping_field(payload: Mapping[str, Any], field: str, *, path: str) -> dict[str, Any]:
    value = payload[field]
    if not isinstance(value, Mapping):
        raise ValueError(f"{path}.{field} must be an object")
    for key in value:
        if not isinstance(key, str):
            raise ValueError(f"{path}.{field} key must be a string")
    return dict(value)


def _telemetry_integer_counter(value: Any, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    return value


def _pipeline_funnel_counts(pipeline: Mapping[str, Any]) -> dict[str, int]:
    funnel = pipeline["funnel"]
    if not isinstance(funnel, Mapping):
        raise ValueError("pipeline.funnel must be an object")
    counts: dict[str, int] = {}
    for key in _FUNNEL_KEYS:
        if key in funnel:
            counts[key] = _telemetry_integer_counter(funnel[key], path=f"pipeline.funnel.{key}")
    return _with_zero_defaults(counts, _FUNNEL_KEYS, path="pipeline.funnel")


def _pipeline_row_mappings(pipeline: Mapping[str, Any], field: str) -> list[Mapping[str, Any]]:
    if field not in pipeline:
        return []
    rows = pipeline[field]
    if not isinstance(rows, list):
        raise ValueError(f"pipeline.{field} must be a list")
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"pipeline.{field}[{index}] must be an object")
    return rows


def _validated_candidate_symbol(candidate: Mapping[str, Any], *, index: int) -> str:
    symbol = candidate.get("symbol", "")
    if not isinstance(symbol, str):
        raise ValueError(f"validated_candidates[{index}].symbol must be a string")
    return symbol


def _merge_symbol_breakdown(target: dict[str, dict[str, Any]], source: Mapping[str, Any]) -> None:
    for symbol, payload in source.items():
        symbol_key = _telemetry_symbol_row_key(symbol, path="symbol_breakdown")
        row_path = f"symbol_breakdown.{symbol_key}"
        row_payload = _telemetry_mapping(payload, path=row_path)
        target_row = target.setdefault(symbol_key, {"snapshot_count": 0, "funnel": {}, "filter_counts": {}})
        snapshot_count = 0
        if "snapshot_count" in row_payload:
            snapshot_count = _telemetry_integer_counter(
                row_payload["snapshot_count"],
                path=f"{row_path}.snapshot_count",
            )
        target_row["snapshot_count"] += snapshot_count
        _merge_counts(
            target_row["funnel"],
            _telemetry_optional_mapping(row_payload, "funnel", path=row_path),
            path=f"{row_path}.funnel",
        )
        _merge_counts(
            target_row["filter_counts"],
            _telemetry_optional_mapping(row_payload, "filter_counts", path=row_path),
            path=f"{row_path}.filter_counts",
        )


def _finalize_returns(value: Any, *, path: str) -> list[float]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be a list")
    returns: list[float] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if isinstance(item, bool) or not isinstance(item, Real):
            raise ValueError(f"{item_path} must be numeric")
        number = float(item)
        if not math.isfinite(number):
            raise ValueError(f"{item_path} must be finite")
        returns.append(number)
    return returns


def _finalize_engine_results(
    aggregates: Mapping[str, Any],
    engines: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for engine_name, spec in engines.items():
        engine_path = f"engines.{engine_name}"
        aggregate = _telemetry_mapping(aggregates[engine_name], path=engine_path)
        result[engine_name] = {
            "funnel": _with_zero_defaults(
                _telemetry_optional_mapping(aggregate, "funnel_counts", path=engine_path),
                _FUNNEL_KEYS,
                path=f"{engine_path}.funnel_counts",
            ),
            "filter_counts": _with_zero_defaults(
                _telemetry_optional_mapping(aggregate, "filter_counts", path=engine_path),
                tuple(spec["filter_keys"]),
                path=f"{engine_path}.filter_counts",
            ),
            "performance": _policy_summary(
                _finalize_returns(
                    aggregate.get("accepted_returns", []),
                    path=f"{engine_path}.accepted_returns",
                )
            ),
        }
    return result


def _finalize_symbol_breakdown(symbols: Mapping[str, Any], *, filter_keys: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for symbol, payload in sorted(symbols.items()):
        symbol_key = _telemetry_symbol_row_key(symbol, path="symbol_breakdown")
        row_path = f"symbol_breakdown.{symbol_key}"
        row_payload = _telemetry_mapping(payload, path=row_path)
        snapshot_count = 0
        if "snapshot_count" in row_payload:
            snapshot_count = _telemetry_integer_counter(
                row_payload["snapshot_count"],
                path=f"{row_path}.snapshot_count",
            )
        result[symbol_key] = {
            "snapshot_count": snapshot_count,
            "funnel": _with_zero_defaults(
                _telemetry_optional_mapping(row_payload, "funnel", path=row_path),
                _SYMBOL_FUNNEL_KEYS,
                path=f"{row_path}.funnel",
            ),
            "filter_counts": _with_zero_defaults(
                _telemetry_optional_mapping(row_payload, "filter_counts", path=row_path),
                filter_keys,
                path=f"{row_path}.filter_counts",
            ),
        }
    return result


def _finalize_regime_breakdown(
    regimes: Mapping[str, Any],
    engines: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for regime_label, payload in regimes.items():
        if not isinstance(regime_label, str):
            raise ValueError("regime_breakdown key must be a string")
        row_path = f"regime_breakdown.{regime_label}"
        row_payload = _telemetry_mapping(payload, path=row_path)
        snapshot_count = 0
        if "snapshot_count" in row_payload:
            snapshot_count = _telemetry_integer_counter(
                row_payload["snapshot_count"],
                path=f"{row_path}.snapshot_count",
            )
        engine_payloads = _telemetry_optional_mapping(row_payload, "engines", path=row_path)
        finalized_engines: dict[str, Any] = {}
        for engine_name, spec in engines.items():
            engine_path = f"{row_path}.engines.{engine_name}"
            engine_payload = _telemetry_mapping(engine_payloads.get(engine_name, {}), path=engine_path)
            finalized_engines[engine_name] = {
                "funnel": _with_zero_defaults(
                    _telemetry_optional_mapping(engine_payload, "funnel_counts", path=engine_path),
                    _FUNNEL_KEYS,
                    path=f"{engine_path}.funnel_counts",
                ),
                "filter_counts": _with_zero_defaults(
                    _telemetry_optional_mapping(engine_payload, "filter_counts", path=engine_path),
                    tuple(spec["filter_keys"]),
                    path=f"{engine_path}.filter_counts",
                ),
                "performance": _policy_summary(
                    _finalize_returns(
                        engine_payload.get("accepted_returns", []),
                        path=f"{engine_path}.accepted_returns",
                    )
                ),
            }
        result[regime_label] = {
            "snapshot_count": snapshot_count,
            "engines": finalized_engines,
        }
    return result


def _run_candidate_pipeline(
    row: DatasetSnapshotRow,
    *,
    regime: Mapping[str, Any],
    input_universe: int,
    candidates: list[dict[str, Any]],
    evaluation_window: str,
) -> dict[str, Any]:
    account = _account_context(row)
    validated_candidates = _validated_candidates(candidates, account)
    allocations = _allocation_rows(account, validated_candidates, regime, app_config=None)
    return {
        "funnel": {
            "input_universe": input_universe,
            "raw_candidates": len(candidates),
            "validated_candidates": len(validated_candidates),
            "allocation_decisions": len(allocations),
            "accepted_allocations": sum(
                1
                for index, allocation in enumerate(allocations)
                if _allocation_status(allocation, index=index).upper() != "REJECTED"
            ),
        },
        "validated_candidates": validated_candidates,
        "allocation_rows": allocations,
        "returns": _accepted_allocation_returns(row, allocations, evaluation_window=evaluation_window),
    }


def _accepted_allocations(allocations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    for index, allocation in enumerate(allocations):
        if _allocation_status(allocation, index=index).upper() == "REJECTED":
            continue
        if _allocation_final_risk_budget(allocation, path=f"allocations[{index}].final_risk_budget") > 0.0:
            accepted.append(allocation)
    return accepted


def _allocation_string_field(allocation: Mapping[str, Any], field: str, *, index: int) -> str:
    if field not in allocation:
        return ""
    value = allocation[field]
    if not isinstance(value, str):
        raise ValueError(f"allocations[{index}].{field} must be a string")
    return value


def _allocation_status(allocation: Mapping[str, Any], *, index: int) -> str:
    return _allocation_string_field(allocation, "status", index=index)


def _allocation_final_risk_budget(allocation: Mapping[str, Any], *, path: str) -> float:
    if "final_risk_budget" not in allocation or allocation.get("final_risk_budget") is None:
        return 0.0
    value = allocation["final_risk_budget"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a finite number")
    budget = float(value)
    if not math.isfinite(budget):
        raise ValueError(f"{path} must be a finite number")
    return budget


def _baseline_rank_score(candidate: Mapping[str, Any]) -> float:
    if "score" not in candidate or candidate.get("score") is None:
        return 0.0
    score = candidate.get("score")
    if isinstance(score, bool) or not isinstance(score, int | float):
        raise ValueError("candidate.score must be numeric")
    if not math.isfinite(score):
        raise ValueError("candidate.score must be finite")
    return float(score)


def _baseline_allocation_reasons(reasons: list[str] | None) -> list[str]:
    if reasons is None:
        return []
    if not isinstance(reasons, list):
        raise ValueError("reasons must be a list")
    for index, reason in enumerate(reasons):
        if not isinstance(reason, str):
            raise ValueError(f"reasons[{index}] must be a string")
    return list(reasons)


def _baseline_allocation_row(
    candidate: Mapping[str, Any],
    *,
    rank: int,
    status: str,
    final_risk_budget: float,
    reasons: list[str] | None = None,
    baseline_name: str,
) -> dict[str, Any]:
    if isinstance(final_risk_budget, bool) or not isinstance(final_risk_budget, int | float):
        raise ValueError("final_risk_budget must be a finite number")
    budget = _finite_number(final_risk_budget, field_name="final_risk_budget")
    return {
        "symbol": candidate.get("symbol"),
        "engine": candidate.get("engine"),
        "setup_type": candidate.get("setup_type"),
        "score": candidate.get("score"),
        "status": status,
        "rank": rank,
        "reasons": _baseline_allocation_reasons(reasons),
        "final_risk_budget": round(budget, 6),
        "meta": {
            "baseline_name": baseline_name,
            "rank_score": _baseline_rank_score(candidate),
        },
    }


def _equal_weight_allocations(validated_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(validated_candidates, key=_rank_key)
    if not ranked:
        return []

    max_positions = max(int(DEFAULT_CONFIG.risk.max_open_positions), 0)
    selected_count = min(len(ranked), max_positions)
    if selected_count == 0:
        return [
            _baseline_allocation_row(
                candidate,
                rank=index,
                status="REJECTED",
                final_risk_budget=0.0,
                reasons=["equal-weight baseline has no available slots"],
                baseline_name="equal_weight_baseline",
            )
            for index, candidate in enumerate(ranked, start=1)
        ]

    equal_budget = float(DEFAULT_CONFIG.risk.max_total_risk_pct) / selected_count
    allocations: list[dict[str, Any]] = []
    for index, candidate in enumerate(ranked, start=1):
        if index > selected_count:
            allocations.append(
                _baseline_allocation_row(
                    candidate,
                    rank=index,
                    status="REJECTED",
                    final_risk_budget=0.0,
                    reasons=["equal-weight baseline max positions reached"],
                    baseline_name="equal_weight_baseline",
                )
            )
            continue

        allocations.append(
            _baseline_allocation_row(
                candidate,
                rank=index,
                status="ACCEPTED",
                final_risk_budget=equal_budget,
                baseline_name="equal_weight_baseline",
            )
        )

    return allocations


def _fixed_risk_allocations(validated_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(validated_candidates, key=_rank_key)
    if not ranked:
        return []

    max_positions = max(int(DEFAULT_CONFIG.risk.max_open_positions), 0)
    remaining_budget = float(DEFAULT_CONFIG.risk.max_total_risk_pct)
    per_trade_budget = max(float(DEFAULT_CONFIG.risk.default_risk_pct), 0.0)
    allocations: list[dict[str, Any]] = []
    for index, candidate in enumerate(ranked, start=1):
        if index > max_positions:
            allocations.append(
                _baseline_allocation_row(
                    candidate,
                    rank=index,
                    status="REJECTED",
                    final_risk_budget=0.0,
                    reasons=["fixed-risk baseline max positions reached"],
                    baseline_name="fixed_risk_baseline",
                )
            )
            continue

        if remaining_budget <= 0.0:
            allocations.append(
                _baseline_allocation_row(
                    candidate,
                    rank=index,
                    status="REJECTED",
                    final_risk_budget=0.0,
                    reasons=["fixed-risk baseline portfolio budget exhausted"],
                    baseline_name="fixed_risk_baseline",
                )
            )
            continue

        final_budget = min(per_trade_budget, remaining_budget)
        remaining_budget -= final_budget
        status = "DOWNSIZED" if final_budget < per_trade_budget else "ACCEPTED"
        reasons = ["fixed-risk baseline downsized to fit portfolio cap"] if status == "DOWNSIZED" else []
        allocations.append(
            _baseline_allocation_row(
                candidate,
                rank=index,
                status=status,
                final_risk_budget=final_budget,
                reasons=reasons,
                baseline_name="fixed_risk_baseline",
            )
        )

    return allocations


def _window_holding_days(evaluation_window: str) -> float:
    normalized = evaluation_window.strip().lower()
    if len(normalized) < 2:
        return 0.0
    unit = normalized[-1]
    raw_value = normalized[:-1]
    try:
        value = float(raw_value)
    except ValueError:
        return 0.0
    if unit == "d":
        return max(value, 0.0)
    if unit == "h":
        return max(value / 24.0, 0.0)
    return 0.0


def _allocation_performance_rows(
    row: DatasetSnapshotRow,
    allocations: list[dict[str, Any]],
    *,
    evaluation_window: str,
    costs: BacktestCosts,
) -> list[dict[str, Any]]:
    holding_days = _window_holding_days(evaluation_window)
    performance_rows: list[dict[str, Any]] = []
    for allocation_index, allocation in enumerate(allocations):
        if _allocation_status(allocation, index=allocation_index).upper() == "REJECTED":
            continue
        risk_budget = _allocation_final_risk_budget(
            allocation,
            path=f"allocations[{allocation_index}].final_risk_budget",
        )
        if risk_budget <= 0.0:
            continue
        gross_return = _candidate_forward_return(
            row,
            engine=_allocation_string_field(allocation, "engine", index=allocation_index),
            symbol=_allocation_string_field(allocation, "symbol", index=allocation_index),
            evaluation_window=evaluation_window,
        )
        gross_pnl = risk_budget * gross_return
        fee_drag = risk_budget * ((2.0 * costs.fee_bps) / 10_000.0)
        slippage_drag = risk_budget * ((2.0 * costs.slippage_bps) / 10_000.0)
        funding_drag = risk_budget * ((holding_days * costs.funding_bps_per_day) / 10_000.0)
        total_drag = fee_drag + slippage_drag + funding_drag
        performance_rows.append(
            {
                "symbol": allocation.get("symbol"),
                "engine": allocation.get("engine"),
                "status": allocation.get("status"),
                "risk_budget": risk_budget,
                "gross_pnl": gross_pnl,
                "net_pnl": gross_pnl - total_drag,
                "fee_drag": fee_drag,
                "slippage_drag": slippage_drag,
                "funding_drag": funding_drag,
            }
        )
    return performance_rows


def _allocation_summary(allocations: list[dict[str, Any]]) -> dict[str, Any]:
    budgets: list[float] = []
    for index, allocation in enumerate(allocations):
        if _allocation_status(allocation, index=index).upper() == "REJECTED":
            continue
        budget = _allocation_final_risk_budget(allocation, path=f"allocations[{index}].final_risk_budget")
        if budget > 0.0:
            budgets.append(budget)
    status_breakdown = {
        "accepted": sum(
            1
            for index, allocation in enumerate(allocations)
            if _allocation_status(allocation, index=index).upper() == "ACCEPTED"
        ),
        "downsized": sum(
            1
            for index, allocation in enumerate(allocations)
            if _allocation_status(allocation, index=index).upper() == "DOWNSIZED"
        ),
        "rejected": sum(
            1
            for index, allocation in enumerate(allocations)
            if _allocation_status(allocation, index=index).upper() == "REJECTED"
        ),
    }
    return {
        "accepted_allocations": len(budgets),
        "total_risk_budget": round(sum(budgets), 6),
        "avg_risk_budget": round(mean(budgets), 6) if budgets else 0.0,
        "max_risk_budget": round(max(budgets), 6) if budgets else 0.0,
        "status_breakdown": status_breakdown,
    }


def _friction_summary(
    performance_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    gross_pnls = [
        _performance_row_finite_number(row, "gross_pnl", index=index)
        for index, row in enumerate(performance_rows)
    ]
    net_pnls = [
        _performance_row_finite_number(row, "net_pnl", index=index)
        for index, row in enumerate(performance_rows)
    ]
    fee_drag = sum(
        _performance_row_finite_number(row, "fee_drag", index=index)
        for index, row in enumerate(performance_rows)
    )
    slippage_drag = sum(
        _performance_row_finite_number(row, "slippage_drag", index=index)
        for index, row in enumerate(performance_rows)
    )
    funding_drag = sum(
        _performance_row_finite_number(row, "funding_drag", index=index)
        for index, row in enumerate(performance_rows)
    )
    cost_drag = fee_drag + slippage_drag + funding_drag
    return {
        "gross_bucket_pnl": round(sum(gross_pnls), 6),
        "net_bucket_pnl": round(sum(net_pnls), 6),
        "trade_count": len(performance_rows),
        "hit_rate": round(win_rate(net_pnls), 6),
        "payoff_ratio": round(payoff_ratio(net_pnls), 6),
        "expectancy": round(expectancy(net_pnls), 6),
        "cost_drag": round(cost_drag, 6),
        "cost_attribution": {
            "fee_drag": round(fee_drag, 6),
            "slippage_drag": round(slippage_drag, 6),
            "funding_drag": round(funding_drag, 6),
        },
    }


def _performance_row_finite_number(row: Mapping[str, Any], field: str, *, index: int) -> float:
    value = row[field]
    path = f"performance_rows[{index}].{field}"
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{path} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{path} must be a finite number")
    return number


def _rotation_candidate_sector(
    symbol: str,
    payload: Mapping[str, Any],
    universe_row: Mapping[str, Any],
) -> str:
    payload_sector = payload.get("sector")
    if payload_sector is not None:
        if not isinstance(payload_sector, str):
            raise ValueError(f"{symbol}.sector must be a string when present")
        if payload_sector.strip():
            return payload_sector.strip()

    universe_sector = universe_row.get("sector")
    if universe_sector is not None:
        if not isinstance(universe_sector, str):
            raise ValueError(f"{symbol}.rotation_universe.sector must be a string when present")
        if universe_sector.strip():
            return universe_sector.strip()

    return ""


def _rotation_universe_liquidity_meta(symbol: str, universe_row: Mapping[str, Any]) -> dict[str, Any]:
    liquidity_meta = universe_row.get("liquidity_meta")
    if liquidity_meta is None:
        return {}
    if not isinstance(liquidity_meta, Mapping):
        raise ValueError(f"{symbol}.rotation_universe.liquidity_meta must be an object")
    for key in liquidity_meta:
        if not isinstance(key, str):
            raise ValueError(f"{symbol}.rotation_universe.liquidity_meta key must be a string")
    return dict(liquidity_meta)


def _validate_rotation_payload_sectors(symbols: Mapping[Any, Any]) -> None:
    for symbol, payload in symbols.items():
        symbol_name = rotation_signals._market_symbol_key(symbol)
        if not isinstance(payload, Mapping) or "sector" not in payload or payload.get("sector") is None:
            continue
        if not isinstance(payload.get("sector"), str):
            raise ValueError(f"{symbol_name}.sector must be a string when present")


def _rotation_candidates_with_trace(
    row: DatasetSnapshotRow,
    *,
    disabled_filters: frozenset[str],
) -> dict[str, Any]:
    regime = _regime_for_row(row)
    if rotation_signals._rotation_suppressed(regime):
        return {"input_universe": 0, "candidates": [], "filter_counts": {"rotation_suppressed": 1}}

    symbols = row.market.get("symbols")
    if not isinstance(symbols, Mapping):
        return {"input_universe": 0, "candidates": [], "filter_counts": {}}
    for symbol in symbols:
        rotation_signals._market_symbol_key(symbol)
    _validate_rotation_payload_sectors(symbols)
    for symbol, payload_value in symbols.items():
        symbol_name = rotation_signals._market_symbol_key(symbol)
        if isinstance(payload_value, Mapping):
            _strict_daily_finite_number(payload_value, symbol_name, "volume_usdt_24h")

    universes = build_universes(row.market, derivatives=row.derivatives)
    eligible = rotation_signals._rotation_symbols(universes.rotation_universe)
    proxy = rotation_signals._major_proxy_returns(row.market)
    filter_counts: dict[str, int] = defaultdict(int)
    candidates: list[dict[str, Any]] = []
    symbol_rows: dict[str, dict[str, Any]] = {}
    for symbol, universe_row in eligible.items():
        symbol_name = rotation_signals._market_symbol_key(symbol)
        payload_value = symbols.get(symbol)
        if not isinstance(payload_value, Mapping):
            filter_counts["missing_payload"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "missing_payload")
            continue

        payload = payload_value
        sector = _rotation_candidate_sector(symbol_name, payload, universe_row)
        if sector.lower() == "majors":
            filter_counts["major_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "major_filtered")
            continue
        if not rotation_signals._trend_accepted(payload, regime):
            filter_counts["trend_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "trend_filtered")
            continue
        if not rotation_signals._passes_absolute_strength_gate(payload):
            filter_counts["absolute_strength_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "absolute_strength_filtered")
            continue

        overheat_rejected = rotation_signals._reject_price_extension_overheat(payload)
        if overheat_rejected and "overheat" not in disabled_filters:
            filter_counts["overheat_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "overheat_filtered")
            continue
        if overheat_rejected and "overheat" in disabled_filters:
            filter_counts["overheat_bypassed"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "overheat_bypassed")

        derivatives_features = rotation_signals.symbol_derivatives_features(row.derivatives, symbol_name)
        if rotation_signals._reject_overheated_crowded_leader(symbol_name, derivatives_features, payload):
            filter_counts["crowding_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "crowding_filtered")
            continue

        rs_features = rotation_signals._relative_strength_features(payload, proxy)
        if rs_features["relative_strength_rank"] < 0.38 or rs_features["persistence"] < (2.0 / 3.0):
            filter_counts["relative_strength_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "relative_strength_filtered")
            continue

        scored = rotation_signals.score_rotation_candidate(
            {
                "relative_strength_rank": rs_features["relative_strength_rank"],
                "persistence": rs_features["persistence"],
                "pullback_quality": rotation_signals._pullback_quality(payload),
                "liquidity_quality": rotation_signals._liquidity_quality(
                    payload,
                    {**universe_row, "liquidity_meta": _rotation_universe_liquidity_meta(symbol_name, universe_row)},
                ),
                "volatility_quality": rotation_signals._volatility_quality(payload),
            }
        )
        total_score = _strict_finite_number(scored.get("total"), field_name="rotation score total")
        score_components = _trace_score_components(
            scored.get("components"),
            engine="rotation",
            index=len(candidates),
        )
        if total_score < rotation_signals._ROTATION_SCORE_FLOOR and "score_floor" not in disabled_filters:
            filter_counts["score_floor_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "score_floor_filtered")
            continue
        if total_score < rotation_signals._ROTATION_SCORE_FLOOR and "score_floor" in disabled_filters:
            filter_counts["score_floor_bypassed"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "score_floor_bypassed")

        stop_loss = rotation_signals._rotation_stop_loss(payload)
        if stop_loss <= 0.0:
            filter_counts["stop_loss_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "stop_loss_filtered")
            continue

        daily = rotation_signals._tf_row(payload, "daily")
        liquidity_meta = _rotation_universe_liquidity_meta(symbol_name, universe_row)
        liquidity_meta.setdefault("liquidity_tier", payload.get("liquidity_tier"))
        liquidity_meta["volume_usdt_24h"] = _strict_daily_finite_number(payload, symbol_name, "volume_usdt_24h")

        candidates.append(
            {
                "engine": "rotation",
                "setup_type": rotation_signals._setup_type(payload),
                "symbol": symbol,
                "side": "LONG",
                "score": total_score,
                "stop_loss": stop_loss,
                "invalidation_source": "rotation_pullback_failure_below_1h_ema50",
                "timeframe_meta": {
                    "daily_bias": "relative_strength_leader",
                    "h4_structure": "soft_daily_reclaim" if not rotation_signals._trend_intact(payload) else "leader_persistence",
                    "h1_trigger": "pullback_hold_or_reacceleration",
                    "relative_strength": {
                        "daily_spread": round(rs_features["daily_spread"], 6),
                        "h4_spread": round(rs_features["h4_spread"], 6),
                        "h1_spread": round(rs_features["h1_spread"], 6),
                    },
                    "score_components": score_components,
                },
                "sector": sector,
                "liquidity_meta": liquidity_meta,
            }
        )
        filter_counts["selected"] += 1
        _bump_symbol_filter(symbol_rows, symbol_name, "selected")
        _bump_symbol_funnel(symbol_rows, symbol_name, "raw_candidates")

    for index, candidate in enumerate(candidates):
        if not isinstance(candidate.get("symbol"), str):
            raise ValueError(f"rotation candidates[{index}].symbol must be a string")

    return {
        "input_universe": len(eligible),
        "candidates": [
            candidate
            for _, candidate in sorted(
                enumerate(candidates),
                key=lambda row: (-_trace_candidate_sort_score(row[1], index=row[0], engine="rotation"), row[1]["symbol"]),
            )
        ],
        "filter_counts": dict(filter_counts),
        "symbol_rows": symbol_rows,
    }


def _trend_eligibility_reasons(
    payload: Mapping[str, Any],
    *,
    symbol: str = "trend_payload",
    regime: Mapping[str, Any] | None = None,
    liquidity_tier: str | None = None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if liquidity_tier is None:
        _, liquidity_tier = trend_signals._payload_categories("trend_payload", payload)
    tier = liquidity_tier.lower()
    daily = trend_signals._tf_row(payload, "daily")
    h4 = trend_signals._tf_row(payload, "4h")
    h1 = trend_signals._tf_row(payload, "1h")
    if tier not in trend_signals._HIGH_LIQUIDITY_TIERS:
        reasons.append("eligibility_liquidity_tier_filtered")
    if not trend_signals._is_uptrend(daily, h4, h1) and not trend_signals._is_supportive_non_major_soft_pretrend(
        payload,
        regime,
        liquidity_tier,
    ):
        reasons.append("eligibility_pretrend_filtered")
    if _strict_timeframe_finite_number(payload, symbol, "daily", "return_pct_7d") <= 0.0:
        reasons.append("eligibility_daily_return_filtered")
    if _strict_timeframe_finite_number(payload, symbol, "4h", "return_pct_3d") <= 0.0:
        reasons.append("eligibility_h4_return_filtered")
    return tuple(reasons)


def _trend_daily_reclaim_gap_pct(payload: Mapping[str, Any], *, symbol: str = "trend_payload") -> float | None:
    daily_close = _strict_timeframe_finite_number(payload, symbol, "daily", "close")
    daily_ema50 = _strict_timeframe_finite_number(payload, symbol, "daily", "ema_50")
    if daily_close <= 0.0 or daily_ema50 <= 0.0:
        return None
    return (daily_close / daily_ema50) - 1.0


def _trend_structure_intact(
    payload: Mapping[str, Any],
    *,
    symbol: str = "trend_payload",
    sector: str | None = None,
    soft_daily_for_majors: bool = False,
    majors_reclaim_max_gap_pct: float | None = None,
) -> bool:
    daily_close = _strict_timeframe_finite_number(payload, symbol, "daily", "close")
    daily_ema20 = _strict_timeframe_finite_number(payload, symbol, "daily", "ema_20")
    daily_ema50 = _strict_timeframe_finite_number(payload, symbol, "daily", "ema_50")
    h4_intact = (
        _strict_timeframe_finite_number(payload, symbol, "4h", "close")
        >= _strict_timeframe_finite_number(payload, symbol, "4h", "ema_20")
        >= _strict_timeframe_finite_number(payload, symbol, "4h", "ema_50")
    )
    h1_intact = (
        _strict_timeframe_finite_number(payload, symbol, "1h", "close")
        >= _strict_timeframe_finite_number(payload, symbol, "1h", "ema_20")
        >= _strict_timeframe_finite_number(payload, symbol, "1h", "ema_50")
    )
    if sector is None:
        sector, _ = trend_signals._payload_categories(symbol, payload)
    if majors_reclaim_max_gap_pct is not None and sector == trend_signals._MAJOR_SECTOR:
        reclaim_gap_pct = _trend_daily_reclaim_gap_pct(payload, symbol=symbol)
        daily_intact = (
            daily_close > daily_ema20
            and reclaim_gap_pct is not None
            and reclaim_gap_pct <= majors_reclaim_max_gap_pct
        )
    elif soft_daily_for_majors and sector == trend_signals._MAJOR_SECTOR:
        daily_intact = daily_close > daily_ema20
    else:
        daily_intact = daily_close > daily_ema20 > daily_ema50
    return daily_intact and h4_intact and h1_intact



def _trend_candidates_with_trace(
    row: DatasetSnapshotRow,
    *,
    majors_only: bool = False,
    soft_daily_for_majors: bool = False,
    majors_reclaim_max_gap_pct: float | None = None,
) -> dict[str, Any]:
    regime = _regime_for_row(row)
    symbols = row.market.get("symbols")
    if not isinstance(symbols, Mapping):
        return {"input_universe": 0, "candidates": [], "filter_counts": {}}

    filter_counts: dict[str, int] = defaultdict(int)
    candidates: list[dict[str, Any]] = []
    symbol_rows: dict[str, dict[str, Any]] = {}
    input_universe = 0
    for symbol, payload_value in symbols.items():
        if not isinstance(payload_value, Mapping):
            symbol_name = trend_signals._market_symbol_key(symbol)
            filter_counts["missing_payload"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "missing_payload")
            continue

        symbol_name = trend_signals._market_symbol_key(symbol)
        payload = payload_value
        sector, liquidity_tier = trend_signals._payload_categories(symbol_name, payload)
        is_major = sector == trend_signals._MAJOR_SECTOR
        if majors_only and not is_major:
            continue

        input_universe += 1
        soft_non_major_pretrend = (
            not is_major and trend_signals._is_supportive_non_major_soft_pretrend(payload, regime, liquidity_tier)
        )
        if not is_major and not trend_signals._is_high_liquidity_strong_name(payload, liquidity_tier) and not soft_non_major_pretrend:
            filter_counts["eligibility_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "eligibility_filtered")
            for reason in _trend_eligibility_reasons(payload, symbol=symbol_name, regime=regime, liquidity_tier=liquidity_tier):
                filter_counts[reason] += 1
                _bump_symbol_filter(symbol_rows, symbol_name, reason)
            continue

        daily = trend_signals._tf_row(payload, "daily")
        h4 = trend_signals._tf_row(payload, "4h")
        h1 = trend_signals._tf_row(payload, "1h")
        if not _trend_structure_intact(
            payload,
            symbol=symbol_name,
            sector=sector,
            soft_daily_for_majors=soft_daily_for_majors,
            majors_reclaim_max_gap_pct=majors_reclaim_max_gap_pct,
        ) and not soft_non_major_pretrend:
            filter_counts["trend_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "trend_filtered")
            continue
        if not trend_signals._passes_absolute_strength_gate(payload):
            filter_counts["absolute_strength_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "absolute_strength_filtered")
            continue
        if trend_signals._reject_price_extension_overheat(payload):
            filter_counts["overheat_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "overheat_filtered")
            continue

        derivatives_features = trend_signals._strict_derivatives_trend_features(
            symbol_name,
            trend_signals.symbol_derivatives_features(row.derivatives, symbol_name),
        )
        if trend_signals._reject_crowded_long(derivatives_features, payload):
            filter_counts["crowding_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "crowding_filtered")
            continue

        scored = trend_signals.score_trend_candidate(
            {
                "daily_bias": "up",
                "h4_structure": "intact",
                "h1_trigger": "confirmed",
                "volume_quality": trend_signals._volume_quality(payload),
            }
        )
        total_score = _strict_finite_number(scored.get("total"), field_name="trend score total")
        if total_score <= 0.0:
            filter_counts["score_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "score_filtered")
            continue

        stop_loss = trend_signals._trend_stop_loss(payload)
        if stop_loss <= 0.0:
            filter_counts["stop_loss_filtered"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "stop_loss_filtered")
            continue

        timeframe_meta = {
            "daily_bias": "supportive_soft_pretrend" if soft_non_major_pretrend else "up",
            "h4_structure": "intact",
            "h1_trigger": "confirmed",
        }
        if row.derivatives is not None:
            timeframe_meta["derivatives"] = {
                "crowding_bias": derivatives_features["crowding_bias"],
                "basis_bps": derivatives_features["basis_bps"],
            }

        candidates.append(
            {
                "engine": "trend",
                "setup_type": trend_signals._setup_type(payload),
                "symbol": symbol_name,
                "side": "LONG",
                "score": total_score,
                "stop_loss": stop_loss,
                "invalidation_source": "trend_structure_loss_below_4h_ema50",
                "timeframe_meta": timeframe_meta,
                "sector": sector or None,
                "liquidity_meta": {
                    "liquidity_tier": payload.get("liquidity_tier"),
                    "volume_usdt_24h": _strict_daily_finite_number(payload, symbol_name, "volume_usdt_24h"),
                },
            }
        )
        filter_counts["selected"] += 1
        _bump_symbol_filter(symbol_rows, symbol_name, "selected")
        _bump_symbol_funnel(symbol_rows, symbol_name, "raw_candidates")

    _validate_trend_candidate_symbols(candidates)
    return {
        "input_universe": input_universe,
        "candidates": [
            candidate
            for _, candidate in sorted(
                enumerate(candidates),
                key=lambda row: (-_trace_candidate_sort_score(row[1], index=row[0], engine="trend"), row[1]["symbol"]),
            )
        ],
        "filter_counts": dict(filter_counts),
        "symbol_rows": symbol_rows,
    }


def _validate_trend_candidate_symbols(candidates: Iterable[Mapping[str, Any]]) -> None:
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate.get("symbol"), str):
            raise ValueError(f"candidates[{index}].symbol must be a string")


def _candidate_symbol(candidate: Mapping[str, Any], *, index: int) -> str:
    symbol = candidate.get("symbol", "")
    if not isinstance(symbol, str):
        raise ValueError(f"candidates[{index}].symbol must be a string")
    return symbol


def _engine_only_candidates(row: DatasetSnapshotRow, *, engine: str) -> dict[str, Any]:
    regime = _regime_for_row(row)
    universes = build_universes(row.market, derivatives=row.derivatives)
    if engine == "trend":
        symbols = row.market.get("symbols")
        input_universe = len(symbols) if isinstance(symbols, Mapping) else 0
        candidates = [
            asdict(candidate)
            for candidate in generate_trend_candidates(
                row.market,
                derivatives=row.derivatives,
                regime=regime,
            )
        ]
        return {"regime": regime, "input_universe": input_universe, "candidates": candidates, "filter_counts": {}}
    if engine == "rotation":
        traced = _rotation_candidates_with_trace(row, disabled_filters=frozenset())
        return {
            "regime": regime,
            "input_universe": traced["input_universe"],
            "candidates": traced["candidates"],
            "filter_counts": traced["filter_counts"],
        }
    if engine == "short":
        candidates = [
            asdict(candidate)
            for candidate in generate_short_candidates(
                row.market,
                short_universe=universes.short_universe,
                derivatives=row.derivatives,
                regime=regime,
            )
        ]
        return {
            "regime": regime,
            "input_universe": len(universes.short_universe),
            "candidates": candidates,
            "filter_counts": {},
        }
    raise ValueError(f"unsupported engine variant: {engine}")


def _all_engine_candidates(row: DatasetSnapshotRow) -> dict[str, Any]:
    regime = _regime_for_row(row)
    input_universe = 0
    candidates: list[dict[str, Any]] = []
    for engine in ("trend", "rotation", "short"):
        engine_only = _engine_only_candidates(row, engine=engine)
        input_universe += _strict_input_universe(engine_only, path="engine_only")
        candidates.extend(_strict_candidate_rows(engine_only, path="engine_only"))
    return {
        "regime": regime,
        "input_universe": input_universe,
        "candidates": candidates,
    }


def run_allocator_friction_experiment(
    rows: Iterable[DatasetSnapshotRow],
    *,
    evaluation_window: str = "3d",
) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=lambda row: (row.timestamp, row.run_id))
    variant_builders = {
        "current_allocator": lambda account, validated, regime: _allocation_rows(
            account,
            validated,
            regime,
            app_config=DEFAULT_CONFIG,
        ),
        "equal_weight_baseline": lambda _account, validated, _regime: _equal_weight_allocations(validated),
        "fixed_risk_baseline": lambda _account, validated, _regime: _fixed_risk_allocations(validated),
    }
    variant_allocations: dict[str, list[dict[str, Any]]] = {name: [] for name in variant_builders}
    comparison_rows: list[dict[str, Any]] = []

    for row in ordered_rows:
        candidate_bundle = _all_engine_candidates(row)
        regime = _strict_mapping_field(candidate_bundle, "regime", path="candidate_bundle")
        account = _account_context(row)
        validated_candidates = _validated_candidates(
            _strict_candidate_rows(candidate_bundle, path="candidate_bundle"),
            account,
        )

        for variant_name, builder in variant_builders.items():
            allocations = builder(account, validated_candidates, regime)
            variant_allocations[variant_name].extend(allocations)
            for friction_name, costs in _FRICTION_SCENARIOS.items():
                performance_rows = _allocation_performance_rows(
                    row,
                    allocations,
                    evaluation_window=evaluation_window,
                    costs=costs,
                )
                summary = _friction_summary(performance_rows)
                comparison_rows.append(
                    {
                        "run_id": row.run_id,
                        "timestamp": row.timestamp.isoformat(),
                        "allocator_variant": variant_name,
                        "friction_scenario": friction_name,
                        "accepted_allocations": len(performance_rows),
                        "total_risk_budget": round(
                            sum(
                                _performance_row_finite_number(item, "risk_budget", index=index)
                                for index, item in enumerate(performance_rows)
                            ),
                            6,
                        ),
                        "gross_bucket_pnl": summary["gross_bucket_pnl"],
                        "net_bucket_pnl": summary["net_bucket_pnl"],
                        "cost_drag": summary["cost_drag"],
                    }
                )

    variants: dict[str, dict[str, Any]] = {}
    for variant_name, allocations in variant_allocations.items():
        frictions: dict[str, Any] = {}
        for friction_name, costs in _FRICTION_SCENARIOS.items():
            all_performance_rows: list[dict[str, Any]] = []
            for row in ordered_rows:
                candidate_bundle = _all_engine_candidates(row)
                regime = _strict_mapping_field(candidate_bundle, "regime", path="candidate_bundle")
                account = _account_context(row)
                validated_candidates = _validated_candidates(
                    _strict_candidate_rows(candidate_bundle, path="candidate_bundle"),
                    account,
                )
                allocations_for_row = variant_builders[variant_name](account, validated_candidates, regime)
                all_performance_rows.extend(
                    _allocation_performance_rows(
                        row,
                        allocations_for_row,
                        evaluation_window=evaluation_window,
                        costs=costs,
                    )
                )
            frictions[friction_name] = _friction_summary(all_performance_rows)

        variants[variant_name] = {
            "allocation_summary": _allocation_summary(allocations),
            "frictions": frictions,
        }

    return {
        "metadata": {
            "snapshot_count": len(ordered_rows),
            "variant_count": len(variants),
            "evaluation_window": evaluation_window,
        },
        "variants": variants,
        "comparison_rows": comparison_rows,
    }


def run_engine_filter_ablation_experiment(
    rows: Iterable[DatasetSnapshotRow],
    *,
    evaluation_window: str = "3d",
) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=lambda row: (row.timestamp, row.run_id))
    variants: dict[str, dict[str, Any]] = {
        "trend_only": {
            "builder": lambda row: _trend_candidates_with_trace(row),
            "filter_keys": _TREND_FILTER_KEYS,
        },
        "majors_only_trend": {
            "builder": lambda row: _trend_candidates_with_trace(row, majors_only=True),
            "filter_keys": _TREND_FILTER_KEYS,
        },
        "majors_soft_trend": {
            "builder": lambda row: _trend_candidates_with_trace(
                row,
                majors_only=True,
                soft_daily_for_majors=True,
            ),
            "filter_keys": _TREND_FILTER_KEYS,
        },
        "majors_reclaim_band_0pct": {
            "builder": lambda row: _trend_candidates_with_trace(
                row,
                majors_only=True,
                soft_daily_for_majors=True,
                majors_reclaim_max_gap_pct=0.0,
            ),
            "filter_keys": _TREND_FILTER_KEYS,
        },
        "majors_reclaim_band_1pct": {
            "builder": lambda row: _trend_candidates_with_trace(
                row,
                majors_only=True,
                soft_daily_for_majors=True,
                majors_reclaim_max_gap_pct=0.01,
            ),
            "filter_keys": _TREND_FILTER_KEYS,
        },
        "majors_reclaim_band_2pct": {
            "builder": lambda row: _trend_candidates_with_trace(
                row,
                majors_only=True,
                soft_daily_for_majors=True,
                majors_reclaim_max_gap_pct=0.02,
            ),
            "filter_keys": _TREND_FILTER_KEYS,
        },
        "rotation_only": {
            "builder": lambda row: _rotation_candidates_with_trace(row, disabled_filters=frozenset()),
            "filter_keys": _ROTATION_FILTER_KEYS,
        },
        "short_only": {
            "builder": lambda row: _engine_only_candidates(row, engine="short"),
            "filter_keys": _ROTATION_FILTER_KEYS,
        },
        "rotation_without_overheat_filter": {
            "builder": lambda row: _rotation_candidates_with_trace(
                row,
                disabled_filters=frozenset({"overheat"}),
            ),
            "filter_keys": _ROTATION_FILTER_KEYS,
        },
    }
    results: dict[str, dict[str, Any]] = {}

    for variant_name, variant in variants.items():
        funnel_counts: dict[str, int] = {}
        filter_counts: dict[str, int] = {}
        accepted_returns: list[float] = []
        selected_symbols: set[str] = set()
        accepted_symbols: set[str] = set()

        for row in ordered_rows:
            regime = _regime_for_row(row)
            traced = variant["builder"](row)
            regime = traced.get("regime", regime)
            candidate_rows = _trace_candidate_rows(traced)
            input_universe = _trace_input_universe(traced)
            _merge_counts(filter_counts, _traced_filter_counts(traced), path="traced.filter_counts")
            selected_symbols.update(
                symbol
                for index, candidate in enumerate(candidate_rows)
                if (symbol := _candidate_symbol(candidate, index=index))
            )

            pipeline = _run_candidate_pipeline(
                row,
                regime=regime,
                input_universe=input_universe,
                candidates=candidate_rows,
                evaluation_window=evaluation_window,
            )
            _merge_counts(funnel_counts, pipeline["funnel"], path="pipeline.funnel")
            accepted_returns.extend(pipeline["returns"])
            accepted_symbols.update(
                symbol
                for index, allocation in enumerate(_pipeline_row_mappings(pipeline, "allocation_rows"))
                if _allocation_status(allocation, index=index).upper() != "REJECTED"
                and _allocation_final_risk_budget(allocation, path=f"allocations[{index}].final_risk_budget") > 0.0
                if (symbol := _allocation_string_field(allocation, "symbol", index=index))
            )

        results[variant_name] = {
            "funnel": _with_zero_defaults(funnel_counts, _FUNNEL_KEYS, path=f"variants.{variant_name}.funnel"),
            "filter_counts": _with_zero_defaults(
                filter_counts,
                tuple(variant["filter_keys"]),
                path=f"variants.{variant_name}.filter_counts",
            ),
            "performance": _policy_summary(accepted_returns),
            "selected_symbols": sorted(selected_symbols),
            "accepted_symbols": sorted(accepted_symbols),
        }

    return {
        "metadata": {
            "snapshot_count": len(ordered_rows),
            "variant_count": len(results),
            "evaluation_window": evaluation_window,
        },
        "variants": results,
    }


def run_long_gate_telemetry_experiment(
    rows: Iterable[DatasetSnapshotRow],
    *,
    evaluation_window: str = "3d",
) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=lambda row: (row.timestamp, row.run_id))
    engines: dict[str, dict[str, Any]] = {
        "trend_long": {
            "builder": _trend_candidates_with_trace,
            "filter_keys": _TREND_FILTER_KEYS,
        },
        "rotation_long": {
            "builder": lambda row: _rotation_candidates_with_trace(row, disabled_filters=frozenset()),
            "filter_keys": _ROTATION_FILTER_KEYS,
        },
    }
    aggregates: dict[str, dict[str, Any]] = {
        engine_name: {
            "funnel_counts": {},
            "filter_counts": {},
            "accepted_returns": [],
        }
        for engine_name in engines
    }
    symbol_breakdown_aggregates: dict[str, dict[str, Any]] = {engine_name: {} for engine_name in engines}
    regime_breakdown_aggregates: dict[str, dict[str, Any]] = {}
    snapshot_rows: list[dict[str, Any]] = []

    for row in ordered_rows:
        regime = _regime_for_row(row)
        regime_label = _regime_label(regime, default="UNKNOWN")
        regime_bucket = regime_breakdown_aggregates.setdefault(
            regime_label,
            {
                "snapshot_count": 0,
                "engines": {
                    engine_name: {"funnel_counts": {}, "filter_counts": {}, "accepted_returns": []}
                    for engine_name in engines
                },
            },
        )
        regime_bucket["snapshot_count"] += 1
        engine_rows: dict[str, Any] = {}
        total_raw_candidates = 0
        total_accepted_allocations = 0

        for engine_name, spec in engines.items():
            traced = spec["builder"](row)
            traced_filter_counts = _traced_filter_counts(traced)
            pipeline = _run_candidate_pipeline(
                row,
                regime=regime,
                input_universe=_trace_input_universe(traced),
                candidates=_trace_candidate_rows(traced),
                evaluation_window=evaluation_window,
            )
            funnel = _pipeline_funnel_counts(pipeline)
            aggregate = aggregates[engine_name]
            _merge_counts(aggregate["funnel_counts"], funnel, path=f"engines.{engine_name}.funnel_counts")
            _merge_counts(aggregate["filter_counts"], traced_filter_counts, path=f"engines.{engine_name}.filter_counts")
            aggregate["accepted_returns"].extend(pipeline["returns"])

            regime_engine_bucket = regime_bucket["engines"][engine_name]
            _merge_counts(
                regime_engine_bucket["funnel_counts"],
                funnel,
                path=f"regime_breakdown.{regime_label}.engines.{engine_name}.funnel_counts",
            )
            _merge_counts(
                regime_engine_bucket["filter_counts"],
                traced_filter_counts,
                path=f"regime_breakdown.{regime_label}.engines.{engine_name}.filter_counts",
            )
            regime_engine_bucket["accepted_returns"].extend(pipeline["returns"])

            symbol_rows = _normalize_symbol_rows(traced.get("symbol_rows", {}))
            for candidate_index, candidate in enumerate(_pipeline_row_mappings(pipeline, "validated_candidates")):
                symbol = _validated_candidate_symbol(candidate, index=candidate_index)
                if symbol:
                    _bump_symbol_funnel(symbol_rows, symbol, "validated_candidates")
            for allocation_index, allocation in enumerate(_pipeline_row_mappings(pipeline, "allocation_rows")):
                symbol = _allocation_string_field(allocation, "symbol", index=allocation_index)
                if not symbol:
                    continue
                _bump_symbol_funnel(symbol_rows, symbol, "allocation_decisions")
                if _allocation_status(allocation, index=allocation_index).upper() != "REJECTED":
                    _bump_symbol_funnel(symbol_rows, symbol, "accepted_allocations")
            _merge_symbol_breakdown(symbol_breakdown_aggregates[engine_name], symbol_rows)

            filter_counts = _with_zero_defaults(
                traced_filter_counts,
                tuple(spec["filter_keys"]),
                path=f"snapshot.engines.{engine_name}.filter_counts",
            )
            total_raw_candidates += int(funnel["raw_candidates"])
            total_accepted_allocations += int(funnel["accepted_allocations"])
            engine_rows[engine_name] = {
                "funnel": funnel,
                "filter_counts": filter_counts,
            }

        snapshot_rows.append(
            {
                "timestamp": row.timestamp.isoformat(),
                "run_id": row.run_id,
                "regime_label": regime_label,
                "total_long_raw_candidates": total_raw_candidates,
                "total_long_accepted_allocations": total_accepted_allocations,
                "engines": engine_rows,
            }
        )

    engine_results = _finalize_engine_results(aggregates, engines)

    symbol_breakdown = {
        engine_name: _finalize_symbol_breakdown(
            engine_symbols,
            filter_keys=tuple(engines[engine_name]["filter_keys"]),
        )
        for engine_name, engine_symbols in symbol_breakdown_aggregates.items()
    }

    regime_breakdown = _finalize_regime_breakdown(regime_breakdown_aggregates, engines)

    return {
        "metadata": {
            "snapshot_count": len(ordered_rows),
            "engine_count": len(engine_results),
            "evaluation_window": evaluation_window,
        },
        "engines": engine_results,
        "symbol_breakdown": symbol_breakdown,
        "regime_breakdown": regime_breakdown,
        "snapshot_rows": snapshot_rows,
    }


def _summarize_strategy_walk_forward_segment(
    rows: tuple[DatasetSnapshotRow, ...],
    *,
    config: BacktestConfig,
) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=lambda row: (row.timestamp, row.run_id))
    if not ordered_rows:
        return {
            "run_ids": [],
            "snapshot_count": 0,
            "start_timestamp": None,
            "end_timestamp": None,
            "scorecard": summarize_return_scorecard(()),
        }

    replay = _replay_full_market_baseline_rows(config, ordered_rows)
    scorecard = summarize_return_scorecard(replay.net_period_returns)
    scorecard["trade_count"] = int(replay.portfolio_summary.trade_count)
    return {
        "run_ids": [row.run_id for row in ordered_rows],
        "snapshot_count": len(ordered_rows),
        "start_timestamp": ordered_rows[0].timestamp.isoformat(),
        "end_timestamp": ordered_rows[-1].timestamp.isoformat(),
        "scorecard": scorecard,
    }


def _summarize_walk_forward_window_with_strategy(
    window: Any,
    *,
    config: BacktestConfig,
) -> dict[str, Any]:
    return {
        "window_index": window.window_index,
        "in_sample": _summarize_strategy_walk_forward_segment(window.in_sample, config=config),
        "out_of_sample": _summarize_strategy_walk_forward_segment(window.out_of_sample, config=config),
    }


def run_walk_forward_validation_experiment(
    rows: Iterable[DatasetSnapshotRow],
    *,
    evaluation_window: str = "3d",
    in_sample_size: int,
    out_of_sample_size: int,
    step_size: int | None = None,
    config: BacktestConfig | None = None,
) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=lambda row: (row.timestamp, row.run_id))
    effective_step_size = effective_walk_forward_step_size(
        out_of_sample_size=out_of_sample_size,
        step_size=step_size,
    )
    windows = build_walk_forward_windows(
        ordered_rows,
        in_sample_size=in_sample_size,
        out_of_sample_size=out_of_sample_size,
        step_size=effective_step_size,
    )
    strategy_mode = config is not None and config.capital is not None and config.universe is not None
    window_summaries = []
    for window in windows:
        if strategy_mode:
            window_summaries.append(_summarize_walk_forward_window_with_strategy(window, config=config))
        else:
            window_summaries.append(
                summarize_walk_forward_window(
                    window,
                    evaluation_window=evaluation_window,
                )
            )

    return {
        "metadata": {
            "snapshot_count": len(ordered_rows),
            "window_count": len(windows),
            "evaluation_window": evaluation_window,
            "in_sample_size": in_sample_size,
            "out_of_sample_size": out_of_sample_size,
            "step_size": effective_step_size,
        },
        "windows": window_summaries,
        "robustness_summary": summarize_walk_forward_robustness(window_summaries),
        "parameter_stability": summarize_parameter_stability(window_summaries),
    }
