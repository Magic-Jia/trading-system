from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
import math
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
    for payload in symbols.values():
        if not isinstance(payload, Mapping):
            continue
        daily = payload.get("daily")
        if not isinstance(daily, Mapping) or daily.get(key) is None:
            continue
        try:
            values.append(float(daily[key]))
        except (TypeError, ValueError):
            continue
    return values


def _trend_factor_value(row: DatasetSnapshotRow) -> float | None:
    symbols = row.market.get("symbols", {}) if isinstance(row.market, Mapping) else {}
    if not isinstance(symbols, Mapping):
        return None
    values: list[float] = []
    for payload in symbols.values():
        if not isinstance(payload, Mapping):
            continue
        daily = payload.get("daily")
        if not isinstance(daily, Mapping):
            continue
        try:
            close = float(daily.get("close"))
            ema50 = float(daily.get("ema_50"))
        except (TypeError, ValueError):
            continue
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
    for family in families:
        spec = dict(family_specs.get(family, {}))
        if not spec:
            spec = {
                "factor_name": str(family),
                "required_fields": [],
                "supported": False,
                "unsupported_reason": "unknown_strategy_family",
            }
        factor = {
            "source_strategy_family": str(family),
            "factor_name": spec["factor_name"],
            "required_fields": list(spec["required_fields"]),
            "supported": bool(spec["supported"]),
            "unsupported_reason": spec["unsupported_reason"],
            "sample_count": len(ordered_rows),
            "evaluation_window": evaluation_window,
        }
        if factor["supported"]:
            effectiveness = _public_strategy_factor_effectiveness(
                ordered_rows,
                family=str(family),
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
    if isinstance(override, Mapping):
        return dict(override)
    return asdict(classify_regime(row.market, row.derivatives))


def _aggression_from_regime(regime: Mapping[str, Any]) -> float:
    label = str(regime.get("label", ""))
    base = _REGIME_BASE_RISK_MULTIPLIERS.get(label)
    if not base:
        return 0.0
    return round(float(regime.get("risk_multiplier", 0.0) or 0.0) / base, 6)


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


def _mean_mapping(values: list[dict[str, float]]) -> dict[str, float]:
    if not values:
        return {}
    keys = sorted({key for item in values for key in item})
    return {
        key: mean(float(item.get(key, 0.0)) for item in values)
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
        label = str(regime.get("label", "UNKNOWN"))
        labels.append(label)
        aggression = _aggression_from_regime(regime)
        grouped[label]["returns"].append(dict(row.forward_returns))
        grouped[label]["drawdowns"].append(dict(row.forward_drawdowns))
        grouped[label]["confidence"].append(float(regime.get("confidence", 0.0)))
        grouped[label]["aggression"].append(aggression)

        bucket_name = f"{_confidence_bucket(float(regime.get('confidence', 0.0)))}|{_aggression_bucket(aggression)}"
        confidence_aggression_summary[bucket_name]["count"] += 1
        confidence_aggression_summary[bucket_name]["forward_return_3d"].append(float(row.forward_returns.get("3d", 0.0)))

    by_regime = {
        label: {
            "count": len(values["returns"]),
            "forward_return_by_window": _mean_mapping(values["returns"]),
            "forward_drawdown_by_window": _mean_mapping(values["drawdowns"]),
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
    rules = [str(rule) for rule in list(variant.get("suppression_rules", []))]
    rules = [rule for rule in rules if rule.lower() != "rotation"]
    if policy == "current":
        variant["suppression_rules"] = list(regime.get("suppression_rules", []))
    else:
        variant["suppression_rules"] = rules
    return variant


def _rotation_forward_return(row: DatasetSnapshotRow, symbol: str, evaluation_window: str) -> float:
    candidate_returns = row.meta.get("candidate_forward_returns")
    if isinstance(candidate_returns, Mapping):
        rotation_returns = candidate_returns.get("rotation")
        if isinstance(rotation_returns, Mapping) and symbol in rotation_returns:
            return float(rotation_returns[symbol])
    return float(row.forward_returns.get(evaluation_window, 0.0))


def _rotation_candidates_for_policy(
    row: DatasetSnapshotRow,
    *,
    policy: str,
    soft_score_floor: float,
) -> list[dict[str, Any]]:
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
        return [candidate for candidate in candidates if float(candidate.get("score", 0.0)) >= soft_score_floor]
    return candidates


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
            "current": {str(candidate["symbol"]): candidate for candidate in current_candidates},
            "no_suppression": {str(candidate["symbol"]): candidate for candidate in no_suppression_candidates},
            "soft_suppression": {str(candidate["symbol"]): candidate for candidate in soft_candidates},
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
            return float(engine_returns[symbol])
    return float(row.forward_returns.get(evaluation_window, 0.0))


def _account_context(row: DatasetSnapshotRow) -> dict[str, Any]:
    default_account = {
        "equity": 0.0,
        "available_balance": 0.0,
        "futures_wallet_balance": 0.0,
        "open_positions": [],
    }
    if not row.account:
        return default_account
    account = dict(row.account)
    account.setdefault("open_positions", [])
    return account


def _accepted_allocation_returns(
    row: DatasetSnapshotRow,
    allocations: list[dict[str, Any]],
    *,
    evaluation_window: str,
) -> list[float]:
    return [
        _candidate_forward_return(
            row,
            engine=str(allocation.get("engine", "")),
            symbol=str(allocation.get("symbol", "")),
            evaluation_window=evaluation_window,
        )
        for allocation in allocations
        if str(allocation.get("status", "")).upper() != "REJECTED"
    ]


def _merge_counts(target: dict[str, int], source: Mapping[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + int(value)


def _with_zero_defaults(counts: Mapping[str, int], keys: tuple[str, ...]) -> dict[str, int]:
    return {key: int(counts.get(key, 0)) for key in keys}


def _symbol_telemetry_row(symbol_rows: dict[str, dict[str, Any]], symbol: str) -> dict[str, Any]:
    telemetry = symbol_rows.get(symbol)
    if telemetry is None:
        telemetry = {"snapshot_count": 1, "funnel": {}, "filter_counts": {}}
        symbol_rows[symbol] = telemetry
    return telemetry


def _bump_symbol_filter(symbol_rows: dict[str, dict[str, Any]], symbol: str, key: str) -> None:
    telemetry = _symbol_telemetry_row(symbol_rows, symbol)
    filter_counts = telemetry["filter_counts"]
    filter_counts[key] = int(filter_counts.get(key, 0)) + 1


def _bump_symbol_funnel(symbol_rows: dict[str, dict[str, Any]], symbol: str, key: str) -> None:
    telemetry = _symbol_telemetry_row(symbol_rows, symbol)
    funnel = telemetry["funnel"]
    funnel[key] = int(funnel.get(key, 0)) + 1


def _merge_symbol_breakdown(target: dict[str, dict[str, Any]], source: Mapping[str, Any]) -> None:
    for symbol, payload in source.items():
        target_row = target.setdefault(str(symbol), {"snapshot_count": 0, "funnel": {}, "filter_counts": {}})
        target_row["snapshot_count"] += int(dict(payload).get("snapshot_count", 0))
        _merge_counts(target_row["funnel"], dict(payload).get("funnel", {}))
        _merge_counts(target_row["filter_counts"], dict(payload).get("filter_counts", {}))


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
            "accepted_allocations": sum(1 for row in allocations if str(row.get("status", "")).upper() != "REJECTED"),
        },
        "validated_candidates": validated_candidates,
        "allocation_rows": allocations,
        "returns": _accepted_allocation_returns(row, allocations, evaluation_window=evaluation_window),
    }


def _accepted_allocations(allocations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    for index, allocation in enumerate(allocations):
        if str(allocation.get("status", "")).upper() == "REJECTED":
            continue
        if _allocation_final_risk_budget(allocation, path=f"allocations[{index}].final_risk_budget") > 0.0:
            accepted.append(allocation)
    return accepted


def _allocation_final_risk_budget(allocation: Mapping[str, Any], *, path: str) -> float:
    if "final_risk_budget" not in allocation or allocation.get("final_risk_budget") is None:
        return 0.0
    value = allocation["final_risk_budget"]
    if isinstance(value, bool):
        raise ValueError(f"{path} must be a finite number")
    try:
        budget = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be a finite number") from exc
    if not math.isfinite(budget):
        raise ValueError(f"{path} must be a finite number")
    return budget


def _baseline_allocation_row(
    candidate: Mapping[str, Any],
    *,
    rank: int,
    status: str,
    final_risk_budget: float,
    reasons: list[str] | None = None,
    baseline_name: str,
) -> dict[str, Any]:
    return {
        "symbol": candidate.get("symbol"),
        "engine": candidate.get("engine"),
        "setup_type": candidate.get("setup_type"),
        "score": candidate.get("score"),
        "status": status,
        "rank": rank,
        "reasons": list(reasons or []),
        "final_risk_budget": round(float(final_risk_budget), 6),
        "meta": {
            "baseline_name": baseline_name,
            "rank_score": float(candidate.get("score", 0.0) or 0.0),
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
    for allocation in _accepted_allocations(allocations):
        risk_budget = float(allocation.get("final_risk_budget", 0.0) or 0.0)
        gross_return = _candidate_forward_return(
            row,
            engine=str(allocation.get("engine", "")),
            symbol=str(allocation.get("symbol", "")),
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
    accepted = _accepted_allocations(allocations)
    budgets = [float(allocation.get("final_risk_budget", 0.0) or 0.0) for allocation in accepted]
    status_breakdown = {
        "accepted": sum(1 for allocation in allocations if str(allocation.get("status", "")).upper() == "ACCEPTED"),
        "downsized": sum(1 for allocation in allocations if str(allocation.get("status", "")).upper() == "DOWNSIZED"),
        "rejected": sum(1 for allocation in allocations if str(allocation.get("status", "")).upper() == "REJECTED"),
    }
    return {
        "accepted_allocations": len(accepted),
        "total_risk_budget": round(sum(budgets), 6),
        "avg_risk_budget": round(mean(budgets), 6) if budgets else 0.0,
        "max_risk_budget": round(max(budgets), 6) if budgets else 0.0,
        "status_breakdown": status_breakdown,
    }


def _friction_summary(
    performance_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    gross_pnls = [float(row["gross_pnl"]) for row in performance_rows]
    net_pnls = [float(row["net_pnl"]) for row in performance_rows]
    fee_drag = sum(float(row["fee_drag"]) for row in performance_rows)
    slippage_drag = sum(float(row["slippage_drag"]) for row in performance_rows)
    funding_drag = sum(float(row["funding_drag"]) for row in performance_rows)
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


def _rotation_candidates_with_trace(
    row: DatasetSnapshotRow,
    *,
    disabled_filters: frozenset[str],
) -> dict[str, Any]:
    regime = _regime_for_row(row)
    if rotation_signals._rotation_suppressed(regime):
        return {"input_universe": 0, "candidates": [], "filter_counts": {"rotation_suppressed": 1}}

    universes = build_universes(row.market, derivatives=row.derivatives)
    eligible = rotation_signals._rotation_symbols(universes.rotation_universe)
    symbols = row.market.get("symbols")
    if not isinstance(symbols, Mapping):
        return {"input_universe": 0, "candidates": [], "filter_counts": {}}

    proxy = rotation_signals._major_proxy_returns(row.market)
    filter_counts: dict[str, int] = defaultdict(int)
    candidates: list[dict[str, Any]] = []
    symbol_rows: dict[str, dict[str, Any]] = {}
    for symbol, universe_row in eligible.items():
        symbol_name = str(symbol)
        payload_value = symbols.get(symbol)
        if not isinstance(payload_value, Mapping):
            filter_counts["missing_payload"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "missing_payload")
            continue

        payload = payload_value
        if str(payload.get("sector", "")).lower() == "majors":
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

        derivatives_features = rotation_signals.symbol_derivatives_features(row.derivatives, str(symbol))
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
                "liquidity_quality": rotation_signals._liquidity_quality(payload, universe_row),
                "volatility_quality": rotation_signals._volatility_quality(payload),
            }
        )
        total_score = rotation_signals._to_float(scored.get("total"))
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
        liquidity_meta = dict(universe_row.get("liquidity_meta", {})) if isinstance(universe_row, Mapping) else {}
        liquidity_meta.setdefault("liquidity_tier", payload.get("liquidity_tier"))
        liquidity_meta["volume_usdt_24h"] = rotation_signals._to_float(daily.get("volume_usdt_24h"))

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
                    "score_components": scored.get("components", {}),
                },
                "sector": str(payload.get("sector") or universe_row.get("sector") or ""),
                "liquidity_meta": liquidity_meta,
            }
        )
        filter_counts["selected"] += 1
        _bump_symbol_filter(symbol_rows, symbol_name, "selected")
        _bump_symbol_funnel(symbol_rows, symbol_name, "raw_candidates")

    return {
        "input_universe": len(eligible),
        "candidates": sorted(
            candidates,
            key=lambda row: (-float(row.get("score", 0.0) or 0.0), str(row.get("symbol", ""))),
        ),
        "filter_counts": dict(filter_counts),
        "symbol_rows": symbol_rows,
    }


def _trend_eligibility_reasons(
    payload: Mapping[str, Any],
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
    if trend_signals._to_float(daily.get("return_pct_7d")) <= 0.0:
        reasons.append("eligibility_daily_return_filtered")
    if trend_signals._to_float(h4.get("return_pct_3d")) <= 0.0:
        reasons.append("eligibility_h4_return_filtered")
    return tuple(reasons)


def _trend_daily_reclaim_gap_pct(payload: Mapping[str, Any]) -> float | None:
    daily = trend_signals._tf_row(payload, "daily")
    daily_close = trend_signals._to_float(daily.get("close"))
    daily_ema50 = trend_signals._to_float(daily.get("ema_50"))
    if daily_close <= 0.0 or daily_ema50 <= 0.0:
        return None
    return (daily_close / daily_ema50) - 1.0



def _trend_structure_intact(
    payload: Mapping[str, Any],
    *,
    soft_daily_for_majors: bool = False,
    majors_reclaim_max_gap_pct: float | None = None,
) -> bool:
    daily = trend_signals._tf_row(payload, "daily")
    h4 = trend_signals._tf_row(payload, "4h")
    h1 = trend_signals._tf_row(payload, "1h")
    daily_close = trend_signals._to_float(daily.get("close"))
    daily_ema20 = trend_signals._to_float(daily.get("ema_20"))
    daily_ema50 = trend_signals._to_float(daily.get("ema_50"))
    h4_intact = (
        trend_signals._to_float(h4.get("close")) >= trend_signals._to_float(h4.get("ema_20")) >= trend_signals._to_float(h4.get("ema_50"))
    )
    h1_intact = (
        trend_signals._to_float(h1.get("close")) >= trend_signals._to_float(h1.get("ema_20")) >= trend_signals._to_float(h1.get("ema_50"))
    )
    sector = str(payload.get("sector", ""))
    if majors_reclaim_max_gap_pct is not None and sector == trend_signals._MAJOR_SECTOR:
        reclaim_gap_pct = _trend_daily_reclaim_gap_pct(payload)
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
        symbol_name = str(symbol)
        if not isinstance(payload_value, Mapping):
            filter_counts["missing_payload"] += 1
            _bump_symbol_filter(symbol_rows, symbol_name, "missing_payload")
            continue

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
            for reason in _trend_eligibility_reasons(payload, regime, liquidity_tier):
                filter_counts[reason] += 1
                _bump_symbol_filter(symbol_rows, symbol_name, reason)
            continue

        daily = trend_signals._tf_row(payload, "daily")
        h4 = trend_signals._tf_row(payload, "4h")
        h1 = trend_signals._tf_row(payload, "1h")
        if not _trend_structure_intact(
            payload,
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

        derivatives_features = trend_signals.symbol_derivatives_features(row.derivatives, str(symbol))
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
        total_score = trend_signals._to_float(scored.get("total"))
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
                "crowding_bias": str(derivatives_features.get("crowding_bias", "balanced")),
                "basis_bps": trend_signals._to_float(derivatives_features.get("basis_bps")),
            }

        candidates.append(
            {
                "engine": "trend",
                "setup_type": trend_signals._setup_type(payload),
                "symbol": str(symbol),
                "side": "LONG",
                "score": total_score,
                "stop_loss": stop_loss,
                "invalidation_source": "trend_structure_loss_below_4h_ema50",
                "timeframe_meta": timeframe_meta,
                "sector": sector or None,
                "liquidity_meta": {
                    "liquidity_tier": payload.get("liquidity_tier"),
                    "volume_usdt_24h": trend_signals._to_float(daily.get("volume_usdt_24h")),
                },
            }
        )
        filter_counts["selected"] += 1
        _bump_symbol_filter(symbol_rows, symbol_name, "selected")
        _bump_symbol_funnel(symbol_rows, symbol_name, "raw_candidates")

    return {
        "input_universe": input_universe,
        "candidates": sorted(
            candidates,
            key=lambda row: (-float(row.get("score", 0.0) or 0.0), str(row.get("symbol", ""))),
        ),
        "filter_counts": dict(filter_counts),
        "symbol_rows": symbol_rows,
    }


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
        input_universe += int(engine_only["input_universe"])
        candidates.extend(list(engine_only["candidates"]))
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
        regime = dict(candidate_bundle["regime"])
        account = _account_context(row)
        validated_candidates = _validated_candidates(list(candidate_bundle["candidates"]), account)

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
                            sum(float(item["risk_budget"]) for item in performance_rows),
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
                regime = dict(candidate_bundle["regime"])
                account = _account_context(row)
                validated_candidates = _validated_candidates(list(candidate_bundle["candidates"]), account)
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
            candidate_rows = list(traced.get("candidates", []))
            input_universe = int(traced.get("input_universe", 0))
            _merge_counts(filter_counts, dict(traced.get("filter_counts", {})))
            selected_symbols.update(
                str(candidate.get("symbol", ""))
                for candidate in candidate_rows
                if str(candidate.get("symbol", ""))
            )

            pipeline = _run_candidate_pipeline(
                row,
                regime=regime,
                input_universe=input_universe,
                candidates=candidate_rows,
                evaluation_window=evaluation_window,
            )
            _merge_counts(funnel_counts, pipeline["funnel"])
            accepted_returns.extend(pipeline["returns"])
            accepted_symbols.update(
                str(allocation.get("symbol", ""))
                for allocation in _accepted_allocations(list(pipeline["allocation_rows"]))
                if str(allocation.get("symbol", ""))
            )

        results[variant_name] = {
            "funnel": _with_zero_defaults(funnel_counts, _FUNNEL_KEYS),
            "filter_counts": _with_zero_defaults(filter_counts, tuple(variant["filter_keys"])),
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
        regime_label = str(regime.get("label", "UNKNOWN"))
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
            pipeline = _run_candidate_pipeline(
                row,
                regime=regime,
                input_universe=int(traced["input_universe"]),
                candidates=list(traced["candidates"]),
                evaluation_window=evaluation_window,
            )
            aggregate = aggregates[engine_name]
            _merge_counts(aggregate["funnel_counts"], pipeline["funnel"])
            _merge_counts(aggregate["filter_counts"], traced["filter_counts"])
            aggregate["accepted_returns"].extend(pipeline["returns"])

            regime_engine_bucket = regime_bucket["engines"][engine_name]
            _merge_counts(regime_engine_bucket["funnel_counts"], pipeline["funnel"])
            _merge_counts(regime_engine_bucket["filter_counts"], traced["filter_counts"])
            regime_engine_bucket["accepted_returns"].extend(pipeline["returns"])

            symbol_rows = {
                str(symbol): {
                    "snapshot_count": int(dict(payload).get("snapshot_count", 0)),
                    "funnel": dict(dict(payload).get("funnel", {})),
                    "filter_counts": dict(dict(payload).get("filter_counts", {})),
                }
                for symbol, payload in dict(traced.get("symbol_rows", {})).items()
            }
            for candidate in list(pipeline.get("validated_candidates", [])):
                symbol = str(candidate.get("symbol", ""))
                if symbol:
                    _bump_symbol_funnel(symbol_rows, symbol, "validated_candidates")
            for allocation in list(pipeline.get("allocation_rows", [])):
                symbol = str(allocation.get("symbol", ""))
                if not symbol:
                    continue
                _bump_symbol_funnel(symbol_rows, symbol, "allocation_decisions")
                if str(allocation.get("status", "")).upper() != "REJECTED":
                    _bump_symbol_funnel(symbol_rows, symbol, "accepted_allocations")
            _merge_symbol_breakdown(symbol_breakdown_aggregates[engine_name], symbol_rows)

            funnel = _with_zero_defaults(dict(pipeline["funnel"]), _FUNNEL_KEYS)
            filter_counts = _with_zero_defaults(dict(traced["filter_counts"]), tuple(spec["filter_keys"]))
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
                "regime_label": str(regime.get("label", "")),
                "total_long_raw_candidates": total_raw_candidates,
                "total_long_accepted_allocations": total_accepted_allocations,
                "engines": engine_rows,
            }
        )

    engine_results: dict[str, Any] = {}
    for engine_name, spec in engines.items():
        aggregate = aggregates[engine_name]
        engine_results[engine_name] = {
            "funnel": _with_zero_defaults(dict(aggregate["funnel_counts"]), _FUNNEL_KEYS),
            "filter_counts": _with_zero_defaults(dict(aggregate["filter_counts"]), tuple(spec["filter_keys"])),
            "performance": _policy_summary(list(aggregate["accepted_returns"])),
        }

    symbol_breakdown = {
        engine_name: {
            symbol: {
                "snapshot_count": int(payload.get("snapshot_count", 0)),
                "funnel": _with_zero_defaults(dict(payload.get("funnel", {})), _SYMBOL_FUNNEL_KEYS),
                "filter_counts": _with_zero_defaults(dict(payload.get("filter_counts", {})), tuple(engines[engine_name]["filter_keys"])),
            }
            for symbol, payload in sorted(engine_symbols.items())
        }
        for engine_name, engine_symbols in symbol_breakdown_aggregates.items()
    }

    regime_breakdown = {
        regime_label: {
            "snapshot_count": int(payload.get("snapshot_count", 0)),
            "engines": {
                engine_name: {
                    "funnel": _with_zero_defaults(
                        dict(dict(payload.get("engines", {})).get(engine_name, {}).get("funnel_counts", {})),
                        _FUNNEL_KEYS,
                    ),
                    "filter_counts": _with_zero_defaults(
                        dict(dict(payload.get("engines", {})).get(engine_name, {}).get("filter_counts", {})),
                        tuple(engines[engine_name]["filter_keys"]),
                    ),
                    "performance": _policy_summary(
                        list(dict(payload.get("engines", {})).get(engine_name, {}).get("accepted_returns", []))
                    ),
                }
                for engine_name in engines
            },
        }
        for regime_label, payload in regime_breakdown_aggregates.items()
    }

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
