from __future__ import annotations

from collections import Counter
import math
from typing import Any, Callable, Mapping

from .metrics import cost_drag
from .types import BaselineReplayResult, PromotionMetadata, TradeLedgerRow


def _report_finite_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite number")
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite number")
    return parsed


def render_regime_scorecard(
    *,
    experiment_name: str,
    experiment: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    raw_by_regime = experiment.get("by_regime", {})
    if not isinstance(raw_by_regime, Mapping):
        raise ValueError("by_regime must be an object")
    by_regime = dict(raw_by_regime)
    best_regime = None
    best_return = None
    worst_regime = None
    worst_return = None
    for label, payload in by_regime.items():
        if not isinstance(payload, Mapping):
            raise ValueError(f"by_regime.{label} must be an object")
        forward_return_by_window = payload.get("forward_return_by_window", {})
        if not isinstance(forward_return_by_window, Mapping):
            raise ValueError(f"by_regime.{label}.forward_return_by_window must be an object")
        current = _report_finite_float(
            dict(forward_return_by_window).get("3d", 0.0),
            field_name=f"by_regime.{label}.forward_return_by_window.3d",
        )
        if best_return is None or current > best_return:
            best_regime, best_return = label, current
        if worst_return is None or current < worst_return:
            worst_regime, worst_return = label, current

    regimes_with_samples = len(by_regime)
    promotion_pass = regimes_with_samples >= 2 and (best_return or 0.0) > 0 and (worst_return or 0.0) < 0
    summary = (
        f"{best_regime} leads forward return dispersion while {worst_regime} stays weakest"
        if promotion_pass
        else "regime separation is not yet strong enough for promotion"
    )

    raw_metadata = experiment.get("metadata", {})
    if not isinstance(raw_metadata, Mapping):
        raise ValueError("experiment.metadata must be an object")
    experiment_metadata = dict(raw_metadata)
    return {
        "metadata": {
            "experiment_name": experiment_name,
            "dataset_root": metadata.get("dataset_root"),
            "baseline_name": metadata.get("baseline_name"),
            "variant_name": metadata.get("variant_name"),
            "sample_period": metadata.get("sample_period"),
        },
        "key_metrics": {
            "snapshot_count": _non_negative_int_field(experiment_metadata, "snapshot_count", label="experiment.metadata"),
            "regimes_covered": regimes_with_samples,
            "best_regime_3d": best_regime,
            "best_regime_3d_return": best_return or 0.0,
            "worst_regime_3d": worst_regime,
            "worst_regime_3d_return": worst_return or 0.0,
        },
        "decision_summary": {
            "decision": "保留" if promotion_pass else "暂缓，等更多样本",
            "summary": summary,
        },
        "promotion_gate": {
            "status": "pass" if promotion_pass else "hold",
            "checks": {
                "has_multiple_regimes": regimes_with_samples >= 2,
                "positive_best_regime": (best_return or 0.0) > 0,
                "negative_worst_regime": (worst_return or 0.0) < 0,
            },
        },
    }


def render_backtest_evaluation_report(
    *,
    experiment_name: str,
    evaluation: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be an object")
    walk_forward = dict(evaluation.get("walk_forward", {}))
    regimes = dict(evaluation.get("regimes", {}))
    cost_stress = dict(evaluation.get("cost_stress", {}))
    stress_scenarios = []
    for index, scenario_payload in enumerate(_list_field(cost_stress, "scenarios", label="cost_stress.scenarios")):
        if not isinstance(scenario_payload, Mapping):
            raise ValueError(f"cost_stress.scenarios[{index}] must be an object")
        scenario = scenario_payload.get("scenario", {})
        if not isinstance(scenario, Mapping):
            raise ValueError(f"cost_stress.scenarios[{index}].scenario must be an object")
        name = scenario.get("name", "")
        if name:
            stress_scenarios.append(
                _canonical_report_string(name, field_name=f"cost_stress.scenarios[{index}].scenario.name")
            )

    return {
        "summary": {
            "metadata": {
                **dict(metadata),
                "experiment_name": experiment_name,
                "evaluation_layer": "walk_forward_oos_regime_cost_stress",
            },
            "walk_forward_status": walk_forward.get("status"),
            "walk_forward_window_count": _non_negative_int_field(
                dict(walk_forward.get("metadata", {})), "window_count", label="walk_forward.metadata"
            ),
            "regime_bucket_count": len(_list_field(regimes, "buckets", label="regimes.buckets")),
            "cost_stress_scenarios": stress_scenarios,
        },
        "walk_forward": walk_forward,
        "regimes": regimes,
        "cost_stress": cost_stress,
    }


def _trade_breakdown_rows(
    trade_ledger: tuple[TradeLedgerRow, ...], *, key_name: str, key_fn: Callable[[TradeLedgerRow], Any]
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, float | int | str]] = {}
    for row in trade_ledger:
        bucket_key = str(key_fn(row))
        bucket = buckets.setdefault(
            bucket_key,
            {
                key_name: bucket_key,
                "trade_count": 0,
                "gross_pnl": 0.0,
                "net_pnl": 0.0,
                "fees": 0.0,
                "slippage": 0.0,
                "funding": 0.0,
            },
        )
        bucket["trade_count"] = int(bucket["trade_count"]) + 1
        bucket["gross_pnl"] = float(bucket["gross_pnl"]) + row.gross_pnl
        bucket["net_pnl"] = float(bucket["net_pnl"]) + row.net_pnl
        bucket["fees"] = float(bucket["fees"]) + row.fee_paid
        bucket["slippage"] = float(bucket["slippage"]) + row.slippage_paid
        bucket["funding"] = float(bucket["funding"]) + row.funding_paid
    return [buckets[key] for key in sorted(buckets)]


def _trade_ledger_payload(trade_ledger: tuple[TradeLedgerRow, ...]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": row.symbol,
            "market_type": row.market_type,
            "base_asset": row.base_asset,
            "side": row.side,
            "status": row.status,
            "entry_timestamp": row.entry_timestamp.isoformat(),
            "exit_timestamp": row.exit_timestamp.isoformat(),
            "entry_price": row.entry_price,
            "exit_price": row.exit_price,
            "qty": row.qty,
            "position_notional": row.position_notional,
            "gross_pnl": row.gross_pnl,
            "net_pnl": row.net_pnl,
            "fee_paid": row.fee_paid,
            "slippage_paid": row.slippage_paid,
            "funding_paid": row.funding_paid,
            "engine": row.engine,
            "setup_type": row.setup_type,
            "score": row.score,
            "stop_loss": row.stop_loss,
            "take_profit": row.take_profit,
            "exit_reason": row.exit_reason,
            "mfe_pct": row.mfe_pct,
            "mae_pct": row.mae_pct,
            "exit_move_pct": row.exit_move_pct,
            "simulated_exit_reason": row.simulated_exit_reason,
            "simulated_exit_price": row.simulated_exit_price,
            "simulated_exit_move_pct": row.simulated_exit_move_pct,
            "simulated_exit_ordering": row.simulated_exit_ordering,
            "simulated_gross_pnl": row.simulated_gross_pnl,
            "simulated_net_pnl": row.simulated_net_pnl,
            "cost_coverage_ratio": row.cost_coverage_ratio,
            "entry_reference_timeframe": row.entry_reference_timeframe,
            "entry_reference_price": row.entry_reference_price,
            "gate_timeframes": list(row.gate_timeframes),
            "trigger_timeframes": list(row.trigger_timeframes),
            "execution_price_source": row.execution_price_source,
            "fill_model": row.fill_model,
            "fill_quality": row.fill_quality,
            "exit_fill_model": row.exit_fill_model,
            "exit_price_source": row.exit_price_source,
            "exit_fill_quality": row.exit_fill_quality,
            "exit_fill_timestamp": row.exit_fill_timestamp.isoformat() if row.exit_fill_timestamp is not None else None,
            "exit_slippage_vs_reference_bps": row.exit_slippage_vs_reference_bps,
            "execution_timeframe": row.execution_timeframe,
            "execution_lag_bars": row.execution_lag_bars,
            "maker_status": row.maker_status,
            "first_fill_timestamp": row.first_fill_timestamp.isoformat() if row.first_fill_timestamp is not None else None,
            "last_fill_timestamp": row.last_fill_timestamp.isoformat() if row.last_fill_timestamp is not None else None,
            "queue_ahead_initial": row.queue_ahead_initial,
            "queue_ahead_remaining": row.queue_ahead_remaining,
            "maker_wait_seconds": row.maker_wait_seconds,
            "maker_reasons": list(row.maker_reasons),
            "mark_price": row.mark_price,
            "mark_price_timestamp": row.mark_price_timestamp.isoformat() if row.mark_price_timestamp is not None else None,
            "mark_price_age_seconds": row.mark_price_age_seconds,
            "funding_rate": row.funding_rate,
            "funding_timestamp": row.funding_timestamp.isoformat() if row.funding_timestamp is not None else None,
            "funding_age_seconds": row.funding_age_seconds,
            "open_interest_usdt": row.open_interest_usdt,
            "open_interest_timestamp": row.open_interest_timestamp.isoformat() if row.open_interest_timestamp is not None else None,
            "open_interest_age_seconds": row.open_interest_age_seconds,
            "requested_quantity": row.requested_quantity,
            "requested_notional": row.requested_notional,
            "filled_quantity": row.filled_quantity,
            "filled_notional": row.filled_notional,
            "unfilled_quantity": row.unfilled_quantity,
            "depth_levels_consumed": row.depth_levels_consumed,
            "execution_impact_bps": row.execution_impact_bps,
            "slippage_bps": row.slippage_bps,
        }
        for row in trade_ledger
    ]


def render_full_market_baseline_report(result: BaselineReplayResult) -> dict[str, Any]:
    reason_counts = Counter(
        reason
        for row in result.rejection_ledger
        for reason in row.reasons
    )

    if not isinstance(result.cost_breakdown, Mapping):
        raise ValueError("cost_breakdown must be an object")
    cost_breakdown_payload: dict[str, float] = {}
    for key, value in result.cost_breakdown.items():
        cost_key = _canonical_report_string(key, field_name="cost_breakdown key")
        cost_breakdown_payload[cost_key] = _report_finite_float(value, field_name=f"cost_breakdown.{cost_key}")

    return {
        "summary": {
            "experiment_name": result.portfolio_summary.experiment_name,
            "total_return": result.portfolio_summary.total_return,
            "max_drawdown": result.portfolio_summary.max_drawdown,
            "sharpe": result.portfolio_summary.sharpe,
            "sortino": result.portfolio_summary.sortino,
            "calmar": result.portfolio_summary.calmar,
            "turnover": result.portfolio_summary.turnover,
            "trade_count": result.portfolio_summary.trade_count,
            "cost_drag": cost_drag(result.gross_period_returns, result.net_period_returns),
            "cost_breakdown": cost_breakdown_payload,
        },
        "breakdowns": {
            "by_market": _trade_breakdown_rows(result.trade_ledger, key_name="market_type", key_fn=lambda row: row.market_type),
            "by_year": _trade_breakdown_rows(result.trade_ledger, key_name="year", key_fn=lambda row: row.exit_timestamp.year),
        },
        "audit": {
            "trade_count": len(result.trade_ledger),
            "accepted_count": sum(1 for row in result.trade_ledger if row.status == "accepted"),
            "resized_count": sum(1 for row in result.trade_ledger if row.status == "resized"),
            "rejection_count": len(result.rejection_ledger),
            "rejection_reasons": dict(sorted(reason_counts.items())),
        },
        "trades": _trade_ledger_payload(result.trade_ledger),
    }


def _scorecard_metadata(*, experiment_name: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "experiment_name": experiment_name,
        "dataset_root": metadata.get("dataset_root"),
        "baseline_name": metadata.get("baseline_name"),
        "variant_name": metadata.get("variant_name"),
        "sample_period": metadata.get("sample_period"),
        "evaluation_window": metadata.get("evaluation_window"),
    }


def _decision_summary(*, decision: str, summary: str) -> dict[str, str]:
    return {"decision": decision, "summary": summary}



def _promotion_metadata_sections(metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw = metadata.get("promotion_metadata")
    if raw is None:
        return {}
    if isinstance(raw, PromotionMetadata):
        runtime_fields = _canonical_report_string_list(
            list(raw.runtime_fields), field_name="promotion_metadata.runtime_fields"
        )
        rollback_target = _optional_canonical_report_string(
            raw.rollback_target, field_name="promotion_metadata.rollback_target"
        )
        rollback_trigger = _optional_canonical_report_string(
            raw.rollback_trigger, field_name="promotion_metadata.rollback_trigger"
        )
        observation_window = _optional_canonical_report_string(
            raw.observation_window, field_name="promotion_metadata.observation_window"
        )
    elif isinstance(raw, Mapping):
        runtime_fields = _canonical_report_string_list(
            raw.get("runtime_fields", []), field_name="promotion_metadata.runtime_fields"
        )
        rollback_target = _optional_canonical_report_string(
            raw.get("rollback_target"), field_name="promotion_metadata.rollback_target"
        )
        rollback_trigger = _optional_canonical_report_string(
            raw.get("rollback_trigger"), field_name="promotion_metadata.rollback_trigger"
        )
        observation_window = _optional_canonical_report_string(
            raw.get("observation_window"), field_name="promotion_metadata.observation_window"
        )
    else:
        raise ValueError("promotion_metadata must be an object")

    sections: dict[str, Any] = {}
    if runtime_fields:
        sections["runtime_observability"] = {"runtime_fields": runtime_fields}
    if rollback_target or rollback_trigger or observation_window:
        sections["rollback_plan"] = {
            "rollback_target": rollback_target,
            "rollback_trigger": rollback_trigger,
            "observation_window": observation_window,
        }
    return sections



def _canonical_report_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a canonical string")
    if not value or value.strip() != value:
        raise ValueError(f"{field_name} must be a canonical string")
    return value


def _optional_canonical_report_string(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _canonical_report_string(value, field_name=field_name)


def _canonical_report_string_list(value: object, *, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [_canonical_report_string(item, field_name=f"{field_name}[]") for item in value]


def _list_field(
    payload: Mapping[str, Any], field: str, *, default: list[Any] | None = None, label: str | None = None
) -> list[Any]:
    field_label = label or field
    if field not in payload:
        return list(default or [])
    raw_value = payload[field]
    if not isinstance(raw_value, list):
        raise ValueError(f"{field_label} must be a list")
    return list(raw_value)


_ALLOWED_DECISIONS = {"keep_researching", "candidate_for_promotion", "reject"}


def _non_negative_int_field(
    payload: Mapping[str, Any], field: str, *, label: str = "summary", default: int = 0
) -> int:
    if field not in payload:
        return default
    raw_value = payload[field]
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise ValueError(f"{label}.{field} must be a non-negative integer")
    if raw_value < 0:
        raise ValueError(f"{label}.{field} must be a non-negative integer")
    return raw_value


def _summary_int(summary_payload: Mapping[str, Any], field: str, default: int = 0) -> int:
    return _non_negative_int_field(summary_payload, field, label="summary", default=default)


def _metadata_int(metadata: Mapping[str, Any], field: str, default: int = 0) -> int:
    return _non_negative_int_field(metadata, field, label="metadata", default=default)


def _summary_float(summary_payload: Mapping[str, Any], field: str, default: float = 0.0) -> float:
    if field not in summary_payload:
        return default
    raw_value = summary_payload[field]
    if isinstance(raw_value, bool):
        raise ValueError(f"summary.{field} must be a finite number")
    try:
        value = float(raw_value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"summary.{field} must be a finite number") from exc
    if not math.isfinite(value):
        raise ValueError(f"summary.{field} must be a finite number")
    return value


def _public_strategy_factor_minimum_sample_count(
    *,
    summary_payload: Mapping[str, Any],
    factors: list[Mapping[str, Any]],
    metadata: Mapping[str, Any],
) -> int:
    raw_value = metadata.get("minimum_effectiveness_sample_count")
    if raw_value is None:
        raw_value = summary_payload.get("minimum_sample_count")
    if raw_value is None:
        for factor in factors:
            effectiveness = factor.get("effectiveness")
            if not isinstance(effectiveness, Mapping):
                continue
            raw_value = effectiveness.get("minimum_sample_count")
            if raw_value is not None:
                break
    if raw_value is None:
        return 0
    return _non_negative_int_field({"minimum_sample_count": raw_value}, "minimum_sample_count", label="effectiveness")


def _public_strategy_factor_sample_count(
    *,
    factors: list[Mapping[str, Any]],
    metadata: Mapping[str, Any],
) -> int:
    evaluated_sample_counts: list[int] = []
    for factor in factors:
        effectiveness = factor.get("effectiveness")
        if not isinstance(effectiveness, Mapping):
            continue
        sample_count = effectiveness.get("sample_count")
        if sample_count is not None:
            evaluated_sample_counts.append(
                _non_negative_int_field(
                    {"sample_count": sample_count}, "sample_count", label="effectiveness"
                )
            )
    if evaluated_sample_counts:
        return min(evaluated_sample_counts)
    return _metadata_int(metadata, "snapshot_count")


def _effectiveness_float(effectiveness: Mapping[str, Any], field: str, *, default: float = 0.0) -> float:
    if field not in effectiveness:
        return default
    raw_value = effectiveness[field]
    if isinstance(raw_value, bool):
        raise ValueError(f"effectiveness.{field} must be a finite number")
    try:
        value = float(raw_value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"effectiveness.{field} must be a finite number") from exc
    if not math.isfinite(value):
        raise ValueError(f"effectiveness.{field} must be a finite number")
    return value


def _public_strategy_factor_directionally_supported(factor: Mapping[str, Any]) -> bool:
    effectiveness = factor.get("effectiveness")
    if not isinstance(effectiveness, Mapping):
        return False
    if effectiveness.get("effectiveness_status") != "promising_research":
        return False

    sample_count = _non_negative_int_field(effectiveness, "sample_count")
    minimum_sample_count = _non_negative_int_field(effectiveness, "minimum_sample_count")
    if minimum_sample_count > 0 and sample_count < minimum_sample_count:
        return False

    correlation = effectiveness.get("information_coefficient")
    correlation_field = "information_coefficient"
    if correlation is None:
        correlation = effectiveness.get("rank_correlation")
        correlation_field = "rank_correlation"
    if correlation is None or _effectiveness_float(effectiveness, correlation_field) < 0.2:
        return False
    if _effectiveness_float(effectiveness, "top_minus_bottom_forward_return") <= 0.0:
        return False
    if _effectiveness_float(effectiveness, "top_bucket_hit_rate") < 0.5:
        return False
    return True


def _flatten_public_strategy_factor(factor: Mapping[str, Any]) -> dict[str, Any]:
    flattened = dict(factor)
    effectiveness = factor.get("effectiveness")
    if not isinstance(effectiveness, Mapping):
        return flattened

    effectiveness_payload = dict(effectiveness)
    if "sample_count" in effectiveness_payload:
        effectiveness_payload["sample_count"] = _non_negative_int_field(
            effectiveness_payload, "sample_count", label="effectiveness"
        )
    for key in (
        "minimum_sample_count",
        "information_coefficient",
        "rank_correlation",
        "top_bucket_avg_forward_return",
        "bottom_bucket_avg_forward_return",
        "top_minus_bottom_forward_return",
        "top_bucket_hit_rate",
        "effectiveness_status",
    ):
        if key not in effectiveness_payload:
            continue
        if key == "minimum_sample_count":
            flattened[key] = _non_negative_int_field(
                effectiveness_payload, "minimum_sample_count", label="effectiveness"
            )
        elif key == "effectiveness_status":
            flattened[key] = effectiveness_payload[key]
        else:
            flattened[key] = _effectiveness_float(effectiveness_payload, key)
    return flattened


def _variant_with_best_metric(
    variants: Mapping[str, Any],
    *,
    metric_fn: Callable[[str, Mapping[str, Any]], float],
) -> tuple[str | None, float]:
    best_name = None
    best_value = float("-inf")
    for variant_name, payload in variants.items():
        if not isinstance(variant_name, str) or not variant_name or variant_name.strip() != variant_name:
            raise ValueError("variant names must be canonical strings")
        if not isinstance(payload, Mapping):
            raise ValueError(f"variants.{variant_name} must be an object")
        value = _report_finite_float(metric_fn(variant_name, dict(payload)), field_name=f"variants.{variant_name}.metric")
        if best_name is None or value > best_value:
            best_name = variant_name
            best_value = value
    if best_name is None:
        return None, 0.0
    return best_name, best_value


def render_rotation_suppression_report(
    *,
    experiment_name: str,
    experiment: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    raw_policies = experiment.get("policies", {})
    if not isinstance(raw_policies, Mapping):
        raise ValueError("policies must be an object")
    policies = dict(raw_policies)

    def policy_payload(name: str) -> dict[str, Any]:
        raw_policy = policies.get(name, {})
        if not isinstance(raw_policy, Mapping):
            raise ValueError(f"policies.{name} must be an object")
        return dict(raw_policy)

    current_policy = policy_payload("current")
    soft_policy = policy_payload("soft_suppression")
    no_suppression_policy = policy_payload("no_suppression")
    current_pnl = _report_finite_float(current_policy.get("bucket_level_pnl", 0.0), field_name="policies.current.bucket_level_pnl")
    soft_pnl = _report_finite_float(soft_policy.get("bucket_level_pnl", 0.0), field_name="policies.soft_suppression.bucket_level_pnl")
    no_suppression_pnl = _report_finite_float(no_suppression_policy.get("bucket_level_pnl", 0.0), field_name="policies.no_suppression.bucket_level_pnl")
    opportunity_kill_rate = _report_finite_float(experiment.get("opportunity_kill_rate", 0.0), field_name="opportunity_kill_rate")
    avoid_loss_rate = _report_finite_float(experiment.get("avoid_loss_rate", 0.0), field_name="avoid_loss_rate")

    if soft_pnl > current_pnl and avoid_loss_rate >= opportunity_kill_rate:
        decision = "candidate_for_promotion"
        summary = "soft suppression outperformed the current policy while preserving more avoided-loss coverage than opportunity loss"
    elif max(current_pnl, soft_pnl, no_suppression_pnl) > 0.0:
        decision = "keep_researching"
        summary = "rotation suppression shows some edge, but the policy trade-off still needs more evidence"
    else:
        decision = "reject"
        summary = "rotation suppression variants are not producing positive bucket-level pnl in this sample"

    assert decision in _ALLOWED_DECISIONS
    return {
        "summary": {
            "metadata": dict(metadata),
            "policies": policies,
            "opportunity_kill_rate": opportunity_kill_rate,
            "avoid_loss_rate": avoid_loss_rate,
        },
        "comparison_rows": {
            "metadata": dict(metadata),
            "rows": _list_field(experiment, "rotation_comparison_rows", label="rotation_comparison_rows"),
        },
        "scorecard": {
            "metadata": _scorecard_metadata(experiment_name=experiment_name, metadata=metadata),
            "key_metrics": {
                "snapshot_count": int(metadata.get("snapshot_count", 0)),
                "current_bucket_level_pnl": current_pnl,
                "soft_suppression_bucket_level_pnl": soft_pnl,
                "no_suppression_bucket_level_pnl": no_suppression_pnl,
                "opportunity_kill_rate": opportunity_kill_rate,
                "avoid_loss_rate": avoid_loss_rate,
            },
            "decision_summary": _decision_summary(decision=decision, summary=summary),
            **_promotion_metadata_sections(metadata),
        },
    }


def render_allocator_friction_report(
    *,
    experiment_name: str,
    experiment: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    variants = dict(experiment.get("variants", {}))
    best_variant, best_base_net_bucket_pnl = _variant_with_best_metric(
        variants,
        metric_fn=lambda _name, payload: dict(dict(payload.get("frictions", {})).get("base", {})).get("net_bucket_pnl", 0.0),
    )
    current_allocator = variants.get("current_allocator", {})
    if not isinstance(current_allocator, Mapping):
        raise ValueError("variants.current_allocator must be an object")
    current_frictions = current_allocator.get("frictions", {})
    if not isinstance(current_frictions, Mapping):
        raise ValueError("variants.current_allocator.frictions must be an object")
    current_base = current_frictions.get("base", {})
    if not isinstance(current_base, Mapping):
        raise ValueError("variants.current_allocator.frictions.base must be an object")
    best_stressed_net_bucket_pnl = 0.0
    if best_variant is not None:
        best_payload = variants.get(best_variant, {})
        if not isinstance(best_payload, Mapping):
            raise ValueError(f"variants.{best_variant} must be an object")
        best_frictions = best_payload.get("frictions", {})
        if not isinstance(best_frictions, Mapping):
            raise ValueError(f"variants.{best_variant}.frictions must be an object")
        best_stressed = best_frictions.get("stressed", {})
        if not isinstance(best_stressed, Mapping):
            raise ValueError(f"variants.{best_variant}.frictions.stressed must be an object")
        best_stressed_net_bucket_pnl = _report_finite_float(
            best_stressed.get("net_bucket_pnl", 0.0),
            field_name=f"variants.{best_variant}.frictions.stressed.net_bucket_pnl",
        )
    current_base_net_bucket_pnl = _report_finite_float(
        current_base.get("net_bucket_pnl", 0.0),
        field_name="variants.current_allocator.frictions.base.net_bucket_pnl",
    )
    current_base_cost_drag = _report_finite_float(
        current_base.get("cost_drag", 0.0),
        field_name="variants.current_allocator.frictions.base.cost_drag",
    )

    if best_base_net_bucket_pnl > 0.0 and best_stressed_net_bucket_pnl > 0.0:
        decision = "candidate_for_promotion"
        summary = f"{best_variant} stays profitable under both base and stressed friction assumptions"
    elif best_base_net_bucket_pnl > 0.0:
        decision = "keep_researching"
        summary = "allocator friction variants stay positive in the base case, but they are not robust enough under stress yet"
    else:
        decision = "reject"
        summary = "allocator friction variants do not hold positive base-case net pnl"

    assert decision in _ALLOWED_DECISIONS
    return {
        "summary": {
            "metadata": dict(metadata),
            "variants": variants,
        },
        "comparison_rows": {
            "metadata": dict(metadata),
            "rows": list(experiment.get("comparison_rows", [])),
        },
        "scorecard": {
            "metadata": _scorecard_metadata(experiment_name=experiment_name, metadata=metadata),
            "key_metrics": {
                "snapshot_count": int(metadata.get("snapshot_count", 0)),
                "best_variant": best_variant,
                "best_base_net_bucket_pnl": best_base_net_bucket_pnl,
                "best_stressed_net_bucket_pnl": best_stressed_net_bucket_pnl,
                "current_allocator_base_net_bucket_pnl": current_base_net_bucket_pnl,
                "current_allocator_base_cost_drag": current_base_cost_drag,
            },
            "decision_summary": _decision_summary(decision=decision, summary=summary),
            **_promotion_metadata_sections(metadata),
        },
    }


def render_engine_filter_ablation_report(
    *,
    experiment_name: str,
    experiment: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    raw_variants = experiment.get("variants", {})
    if not isinstance(raw_variants, Mapping):
        raise ValueError("variants must be an object")
    variants = dict(raw_variants)
    best_variant, best_bucket_pnl = _variant_with_best_metric(
        variants,
        metric_fn=lambda _name, payload: dict(payload.get("performance", {})).get("bucket_level_pnl", 0.0),
    )
    best_payload = dict(variants.get(best_variant, {})) if best_variant is not None else {}
    raw_best_funnel = best_payload.get("funnel", {})
    if not isinstance(raw_best_funnel, Mapping):
        raise ValueError(f"variants.{best_variant}.funnel must be an object")
    accepted_allocations = _non_negative_int_field(
        raw_best_funnel,
        "accepted_allocations",
        label=f"variants.{best_variant}.funnel",
    )

    if best_bucket_pnl > 0.0 and accepted_allocations > 0:
        decision = "candidate_for_promotion"
        summary = f"{best_variant} produced the strongest positive bucket-level pnl with live candidate flow"
    elif accepted_allocations > 0:
        decision = "keep_researching"
        summary = "engine ablation still finds candidate flow, but the strongest variant is not convincingly positive yet"
    else:
        decision = "reject"
        summary = "engine ablation variants are not producing promotable candidate flow in this sample"

    assert decision in _ALLOWED_DECISIONS
    return {
        "summary": {
            "metadata": dict(metadata),
            "variants": variants,
        },
        "scorecard": {
            "metadata": _scorecard_metadata(experiment_name=experiment_name, metadata=metadata),
            "key_metrics": {
                "snapshot_count": _metadata_int(metadata, "snapshot_count"),
                "best_variant": best_variant,
                "best_bucket_level_pnl": best_bucket_pnl,
                "best_variant_accepted_allocations": accepted_allocations,
            },
            "decision_summary": _decision_summary(decision=decision, summary=summary),
            **_promotion_metadata_sections(metadata),
        },
    }


def _top_blocker(filter_counts: Mapping[str, Any]) -> tuple[str | None, int]:
    blocker_keys = [
        key
        for key in filter_counts
        if key != "selected" and not key.endswith("_bypassed") and float(filter_counts.get(key, 0) or 0) > 0
    ]
    if not blocker_keys:
        return None, 0
    blocker_key = max(blocker_keys, key=lambda key: (float(filter_counts.get(key, 0) or 0), key))
    return blocker_key, int(float(filter_counts.get(blocker_key, 0) or 0))



def _top_specific_eligibility_blocker(filter_counts: Mapping[str, Any]) -> tuple[str | None, int]:
    eligibility_keys = [
        key
        for key in filter_counts
        if key.startswith("eligibility_")
        and key != "eligibility_filtered"
        and float(filter_counts.get(key, 0) or 0) > 0
    ]
    if not eligibility_keys:
        return None, 0
    blocker_key = max(eligibility_keys, key=lambda key: (float(filter_counts.get(key, 0) or 0), key))
    return blocker_key, int(float(filter_counts.get(blocker_key, 0) or 0))



def _dominant_long_gate_blocker(filter_counts: Mapping[str, Any]) -> tuple[str | None, int]:
    blocker_gate, blocker_count = _top_blocker(filter_counts)
    if blocker_gate != "eligibility_filtered":
        return blocker_gate, blocker_count
    specific_gate, specific_count = _top_specific_eligibility_blocker(filter_counts)
    if specific_gate is None:
        return blocker_gate, blocker_count
    return specific_gate, specific_count


def render_long_gate_telemetry_report(
    *,
    experiment_name: str,
    experiment: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    raw_engines = experiment.get("engines", {})
    if not isinstance(raw_engines, Mapping):
        raise ValueError("engines must be an object")
    engines = dict(raw_engines)
    engine_funnels: dict[str, Mapping[str, Any]] = {}
    best_engine = None
    best_accept_count = -1
    dominant_blocker_engine = None
    dominant_blocker_gate = None
    dominant_blocker_count = 0

    for engine_name, payload in engines.items():
        if not isinstance(payload, Mapping):
            raise ValueError(f"engines.{engine_name} must be an object")
        raw_funnel = payload.get("funnel", {})
        if not isinstance(raw_funnel, Mapping):
            raise ValueError(f"engines.{engine_name}.funnel must be an object")
        funnel = dict(raw_funnel)
        engine_funnels[str(engine_name)] = funnel
        accept_count = int(funnel.get("accepted_allocations", 0))
        if accept_count > best_accept_count:
            best_engine = str(engine_name)
            best_accept_count = accept_count

        raw_filter_counts = payload.get("filter_counts", {})
        if not isinstance(raw_filter_counts, Mapping):
            raise ValueError(f"engines.{engine_name}.filter_counts must be an object")
        blocker_gate, blocker_count = _dominant_long_gate_blocker(dict(raw_filter_counts))
        if blocker_count > dominant_blocker_count:
            dominant_blocker_engine = str(engine_name)
            dominant_blocker_gate = blocker_gate
            dominant_blocker_count = blocker_count

    total_long_accepted_allocations = sum(
        int(funnel.get("accepted_allocations", 0)) for funnel in engine_funnels.values()
    )
    engines_with_candidate_flow = sum(
        1 for funnel in engine_funnels.values() if int(funnel.get("raw_candidates", 0)) > 0
    )

    if total_long_accepted_allocations > 0:
        decision = "keep_researching"
        summary = f"{best_engine} still produced some accepted long allocations, but long gate failures remain concentrated at {dominant_blocker_engine}:{dominant_blocker_gate}"
    else:
        decision = "reject"
        summary = f"no accepted long allocations were observed; the dominant blocker is {dominant_blocker_engine}:{dominant_blocker_gate}"

    assert decision in _ALLOWED_DECISIONS
    return {
        "summary": {
            "metadata": dict(metadata),
            "engines": engines,
        },
        "snapshot_rows": {
            "metadata": dict(metadata),
            "rows": list(experiment.get("snapshot_rows", [])),
        },
        "symbol_breakdown": {
            "metadata": dict(metadata),
            "engines": dict(experiment.get("symbol_breakdown", {})),
        },
        "regime_breakdown": {
            "metadata": dict(metadata),
            "regimes": dict(experiment.get("regime_breakdown", {})),
        },
        "scorecard": {
            "metadata": _scorecard_metadata(experiment_name=experiment_name, metadata=metadata),
            "key_metrics": {
                "snapshot_count": int(metadata.get("snapshot_count", 0)),
                "best_engine": best_engine,
                "best_engine_accepted_allocations": max(best_accept_count, 0),
                "total_long_accepted_allocations": total_long_accepted_allocations,
                "engines_with_candidate_flow": engines_with_candidate_flow,
                "dominant_blocker_engine": dominant_blocker_engine,
                "dominant_blocker_gate": dominant_blocker_gate,
                "dominant_blocker_count": dominant_blocker_count,
            },
            "decision_summary": _decision_summary(decision=decision, summary=summary),
            **_promotion_metadata_sections(metadata),
        },
    }


def render_public_strategy_factor_report(
    *,
    experiment_name: str,
    experiment: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    summary_payload = dict(experiment.get("summary", {}))
    raw_factors = [dict(factor) for factor in list(experiment.get("factors", []))]
    factors = [_flatten_public_strategy_factor(factor) for factor in raw_factors]
    supported_factor_count = _summary_int(summary_payload, "supported_factor_count")
    unsupported_factor_count = _summary_int(summary_payload, "unsupported_factor_count")
    effective_factor_count = _summary_int(summary_payload, "effective_factor_count")
    minimum_sample_count = _public_strategy_factor_minimum_sample_count(
        summary_payload=summary_payload,
        factors=raw_factors,
        metadata=metadata,
    )
    sample_count = _public_strategy_factor_sample_count(factors=raw_factors, metadata=metadata)
    directionally_supported_factor_count = sum(
        1 for factor in raw_factors if _public_strategy_factor_directionally_supported(factor)
    )

    if supported_factor_count <= 0:
        decision = "reject"
        summary = "public strategy ideas cannot be evaluated with the current dataset fields"
    elif minimum_sample_count > 0 and sample_count < minimum_sample_count:
        decision = "keep_researching"
        summary = "public strategy factor evidence is directionally interesting, but the sample is still below the minimum research threshold"
    elif minimum_sample_count > 0 and directionally_supported_factor_count > 0 and effective_factor_count > 0:
        decision = "candidate_for_promotion"
        summary = "public strategy factor research meets the minimum sample threshold and directional checks for at least one candidate"
    else:
        decision = "keep_researching"
        summary = "public strategy ideas were converted into evidence-backed factor candidates; data gaps remain non-promotable"

    assert decision in _ALLOWED_DECISIONS
    return {
        "summary": {
            "metadata": dict(metadata),
            "summary": summary_payload,
            "sample_count": sample_count,
            "minimum_sample_count": minimum_sample_count,
            "effective_factor_count": effective_factor_count,
            "decision": decision,
        },
        "factor_catalog": {
            "metadata": dict(metadata),
            "factors": factors,
        },
        "scorecard": {
            "metadata": _scorecard_metadata(experiment_name=experiment_name, metadata=metadata),
            "key_metrics": {
                "snapshot_count": _metadata_int(metadata, "snapshot_count"),
                "supported_factor_count": supported_factor_count,
                "unsupported_factor_count": unsupported_factor_count,
                "data_gap_count": _summary_int(summary_payload, "data_gap_count"),
                "evaluated_factor_count": _summary_int(summary_payload, "evaluated_factor_count"),
                "effective_factor_count": effective_factor_count,
            },
            "decision_summary": _decision_summary(decision=decision, summary=summary),
        },
    }


def render_llm_trend_breakout_report(
    *,
    experiment_name: str,
    experiment: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    summary_payload = dict(experiment.get("summary", {}))
    candidate_rows = _list_field(experiment, "candidate_rows")
    technical_candidate_count = _summary_int(summary_payload, "technical_candidate_count")
    accepted_candidate_count = _summary_int(summary_payload, "accepted_candidate_count")
    rejected_candidate_count = _summary_int(summary_payload, "rejected_candidate_count")
    acceptance_rate = _summary_float(summary_payload, "acceptance_rate")
    raw_rejection_reasons = summary_payload.get("rejection_reasons", {})
    if not isinstance(raw_rejection_reasons, Mapping):
        raise ValueError("summary.rejection_reasons must be an object")
    rejection_reasons = dict(raw_rejection_reasons)
    if accepted_candidate_count > 0 and acceptance_rate >= 0.25:
        decision = "keep_researching"
        summary = "LLM trend-breakout filter preserved some technical candidate flow; keep researching before any promotion"
    elif technical_candidate_count > 0:
        decision = "keep_researching"
        summary = "LLM trend-breakout filter is producing diagnostics, but accepted candidate flow is still thin"
    else:
        decision = "reject"
        summary = "LLM trend-breakout experiment produced no technical candidate flow in this sample"

    assert decision in _ALLOWED_DECISIONS
    return {
        "summary": {
            "metadata": dict(metadata),
            "summary": summary_payload,
        },
        "candidate_rows": {
            "metadata": dict(metadata),
            "rows": candidate_rows,
        },
        "scorecard": {
            "metadata": _scorecard_metadata(experiment_name=experiment_name, metadata=metadata),
            "key_metrics": {
                "snapshot_count": _non_negative_int_field(metadata, "snapshot_count", label="metadata"),
                "technical_candidate_count": technical_candidate_count,
                "accepted_candidate_count": accepted_candidate_count,
                "rejected_candidate_count": rejected_candidate_count,
                "acceptance_rate": acceptance_rate,
            },
            "decision_summary": _decision_summary(decision=decision, summary=summary),
            "rejection_reasons": rejection_reasons,
            **_promotion_metadata_sections(metadata),
        },
    }


def render_walk_forward_validation_report(
    *,
    experiment_name: str,
    experiment: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    raw_robustness_summary = experiment.get("robustness_summary", {})
    if not isinstance(raw_robustness_summary, Mapping):
        raise ValueError("robustness_summary must be an object")
    robustness_summary = dict(raw_robustness_summary)
    raw_parameter_stability = experiment.get("parameter_stability", {})
    if not isinstance(raw_parameter_stability, Mapping):
        raise ValueError("parameter_stability must be an object")
    parameter_stability = dict(raw_parameter_stability)
    raw_performance_dispersion = robustness_summary.get("performance_dispersion", {})
    if not isinstance(raw_performance_dispersion, Mapping):
        raise ValueError("performance_dispersion must be an object")
    performance_dispersion = dict(raw_performance_dispersion)
    raw_out_of_sample_scorecard = robustness_summary.get("out_of_sample_scorecard", {})
    if not isinstance(raw_out_of_sample_scorecard, Mapping):
        raise ValueError("out_of_sample_scorecard must be an object")
    out_of_sample_scorecard = dict(raw_out_of_sample_scorecard)
    windows = _list_field(experiment, "windows")

    out_of_sample_total_return = _report_finite_float(
        out_of_sample_scorecard.get("total_return", 0.0),
        field_name="out_of_sample_scorecard.total_return",
    )
    positive_window_ratio = _report_finite_float(
        performance_dispersion.get("positive_window_ratio", 0.0),
        field_name="performance_dispersion.positive_window_ratio",
    )
    parameter_stability_score = _report_finite_float(
        parameter_stability.get("parameter_stability_score", 0.0),
        field_name="parameter_stability.parameter_stability_score",
    )

    if out_of_sample_total_return > 0.0 and positive_window_ratio >= 0.6 and parameter_stability_score >= 0.5:
        decision = "candidate_for_promotion"
        summary = "walk-forward validation is positive out-of-sample with acceptable window hit-rate and stability"
    elif out_of_sample_total_return > 0.0 or positive_window_ratio >= 0.5:
        decision = "keep_researching"
        summary = "walk-forward validation shows partial robustness, but the out-of-sample evidence is not stable enough yet"
    else:
        decision = "reject"
        summary = "walk-forward validation does not show stable enough out-of-sample robustness"

    assert decision in _ALLOWED_DECISIONS
    return {
        "summary": {
            "metadata": dict(metadata),
            "robustness_summary": robustness_summary,
            "parameter_stability": parameter_stability,
        },
        "windows": {
            "metadata": dict(metadata),
            "rows": windows,
        },
        "scorecard": {
            "metadata": _scorecard_metadata(experiment_name=experiment_name, metadata=metadata),
            "key_metrics": {
                "snapshot_count": _metadata_int(metadata, "snapshot_count"),
                "window_count": _metadata_int(metadata, "window_count"),
                "out_of_sample_total_return": out_of_sample_total_return,
                "positive_window_ratio": positive_window_ratio,
                "parameter_stability_score": parameter_stability_score,
            },
            "decision_summary": _decision_summary(decision=decision, summary=summary),
            **_promotion_metadata_sections(metadata),
        },
    }
