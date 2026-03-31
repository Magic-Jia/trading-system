from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from statistics import mean
from typing import Any, Iterable, Mapping

from trading_system.app.market_regime.classifier import classify_regime
from trading_system.app.signals.rotation_engine import generate_rotation_candidates
from trading_system.app.universe.builder import build_universes

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
