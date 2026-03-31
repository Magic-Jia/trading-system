from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from statistics import mean
from typing import Any, Iterable, Mapping

from trading_system.app.market_regime.classifier import classify_regime
from trading_system.app.signals import rotation_engine as rotation_signals
from trading_system.app.signals.rotation_engine import generate_rotation_candidates
from trading_system.app.signals.short_engine import generate_short_candidates
from trading_system.app.signals.trend_engine import generate_trend_candidates
from trading_system.app.universe.builder import build_universes

from .engine import _allocation_rows, _validated_candidates
from .metrics import expectancy, payoff_ratio, win_rate
from .types import DatasetSnapshotRow

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
        "allocation_rows": allocations,
        "returns": _accepted_allocation_returns(row, allocations, evaluation_window=evaluation_window),
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
    for symbol, universe_row in eligible.items():
        payload_value = symbols.get(symbol)
        if not isinstance(payload_value, Mapping):
            filter_counts["missing_payload"] += 1
            continue

        payload = payload_value
        if str(payload.get("sector", "")).lower() == "majors":
            filter_counts["major_filtered"] += 1
            continue
        if not rotation_signals._trend_intact(payload):
            filter_counts["trend_filtered"] += 1
            continue
        if not rotation_signals._passes_absolute_strength_gate(payload):
            filter_counts["absolute_strength_filtered"] += 1
            continue

        overheat_rejected = rotation_signals._reject_price_extension_overheat(payload)
        if overheat_rejected and "overheat" not in disabled_filters:
            filter_counts["overheat_filtered"] += 1
            continue
        if overheat_rejected and "overheat" in disabled_filters:
            filter_counts["overheat_bypassed"] += 1

        derivatives_features = rotation_signals.symbol_derivatives_features(row.derivatives, str(symbol))
        if rotation_signals._reject_overheated_crowded_leader(derivatives_features, payload):
            filter_counts["crowding_filtered"] += 1
            continue

        rs_features = rotation_signals._relative_strength_features(payload, proxy)
        if rs_features["relative_strength_rank"] < 0.38 or rs_features["persistence"] < (2.0 / 3.0):
            filter_counts["relative_strength_filtered"] += 1
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
            continue
        if total_score < rotation_signals._ROTATION_SCORE_FLOOR and "score_floor" in disabled_filters:
            filter_counts["score_floor_bypassed"] += 1

        stop_loss = rotation_signals._rotation_stop_loss(payload)
        if stop_loss <= 0.0:
            filter_counts["stop_loss_filtered"] += 1
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
                    "h4_structure": "leader_persistence",
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

    return {
        "input_universe": len(eligible),
        "candidates": sorted(
            candidates,
            key=lambda row: (-float(row.get("score", 0.0) or 0.0), str(row.get("symbol", ""))),
        ),
        "filter_counts": dict(filter_counts),
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


def run_engine_filter_ablation_experiment(
    rows: Iterable[DatasetSnapshotRow],
    *,
    evaluation_window: str = "3d",
) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=lambda row: (row.timestamp, row.run_id))
    variants: dict[str, dict[str, Any]] = {
        "trend_only": {"engine": "trend"},
        "rotation_only": {"engine": "rotation"},
        "short_only": {"engine": "short"},
        "rotation_without_overheat_filter": {"engine": "rotation", "disabled_filters": frozenset({"overheat"})},
    }
    results: dict[str, dict[str, Any]] = {}

    for variant_name, variant in variants.items():
        funnel_counts: dict[str, int] = {}
        filter_counts: dict[str, int] = {}
        accepted_returns: list[float] = []

        for row in ordered_rows:
            regime = _regime_for_row(row)
            if variant_name == "rotation_without_overheat_filter":
                traced = _rotation_candidates_with_trace(
                    row,
                    disabled_filters=frozenset(variant.get("disabled_filters", frozenset())),
                )
                candidate_rows = traced["candidates"]
                input_universe = int(traced["input_universe"])
                _merge_counts(filter_counts, traced["filter_counts"])
            else:
                engine_only = _engine_only_candidates(row, engine=str(variant["engine"]))
                regime = engine_only["regime"]
                candidate_rows = list(engine_only["candidates"])
                input_universe = int(engine_only["input_universe"])
                _merge_counts(filter_counts, engine_only["filter_counts"])

            pipeline = _run_candidate_pipeline(
                row,
                regime=regime,
                input_universe=input_universe,
                candidates=candidate_rows,
                evaluation_window=evaluation_window,
            )
            _merge_counts(funnel_counts, pipeline["funnel"])
            accepted_returns.extend(pipeline["returns"])

        results[variant_name] = {
            "funnel": _with_zero_defaults(funnel_counts, _FUNNEL_KEYS),
            "filter_counts": _with_zero_defaults(filter_counts, _ROTATION_FILTER_KEYS),
            "performance": _policy_summary(accepted_returns),
        }

    return {
        "metadata": {
            "snapshot_count": len(ordered_rows),
            "variant_count": len(results),
            "evaluation_window": evaluation_window,
        },
        "variants": results,
    }
