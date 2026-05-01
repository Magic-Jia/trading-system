from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Mapping

from .metrics import cost_drag
from .types import BaselineReplayResult, PromotionMetadata, TradeLedgerRow


def render_regime_scorecard(
    *,
    experiment_name: str,
    experiment: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    by_regime = dict(experiment.get("by_regime", {}))
    best_regime = None
    best_return = None
    worst_regime = None
    worst_return = None
    for label, payload in by_regime.items():
        current = float(dict(payload.get("forward_return_by_window", {})).get("3d", 0.0))
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

    return {
        "metadata": {
            "experiment_name": experiment_name,
            "dataset_root": metadata.get("dataset_root"),
            "baseline_name": metadata.get("baseline_name"),
            "variant_name": metadata.get("variant_name"),
            "sample_period": metadata.get("sample_period"),
        },
        "key_metrics": {
            "snapshot_count": dict(experiment.get("metadata", {})).get("snapshot_count", 0),
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
    walk_forward = dict(evaluation.get("walk_forward", {}))
    regimes = dict(evaluation.get("regimes", {}))
    cost_stress = dict(evaluation.get("cost_stress", {}))
    stress_scenarios = [
        str(dict(scenario_payload.get("scenario", {})).get("name", ""))
        for scenario_payload in cost_stress.get("scenarios", [])
    ]
    stress_scenarios = [name for name in stress_scenarios if name]

    return {
        "summary": {
            "metadata": {
                **dict(metadata),
                "experiment_name": experiment_name,
                "evaluation_layer": "walk_forward_oos_regime_cost_stress",
            },
            "walk_forward_status": walk_forward.get("status"),
            "walk_forward_window_count": int(dict(walk_forward.get("metadata", {})).get("window_count", 0)),
            "regime_bucket_count": len(list(regimes.get("buckets", []))),
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
            "cost_breakdown": dict(result.cost_breakdown),
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
        runtime_fields = list(raw.runtime_fields)
        rollback_target = raw.rollback_target
        rollback_trigger = raw.rollback_trigger
        observation_window = raw.observation_window
    elif isinstance(raw, Mapping):
        runtime_fields = [str(item) for item in raw.get("runtime_fields", [])]
        rollback_target = raw.get("rollback_target")
        rollback_trigger = raw.get("rollback_trigger")
        observation_window = raw.get("observation_window")
    else:
        return {}

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



_ALLOWED_DECISIONS = {"keep_researching", "candidate_for_promotion", "reject"}


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
    return int(raw_value or 0)


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
            evaluated_sample_counts.append(int(sample_count))
    if evaluated_sample_counts:
        return min(evaluated_sample_counts)
    return int(metadata.get("snapshot_count", 0))


def _public_strategy_factor_directionally_supported(factor: Mapping[str, Any]) -> bool:
    effectiveness = factor.get("effectiveness")
    if not isinstance(effectiveness, Mapping):
        return False
    if effectiveness.get("effectiveness_status") != "promising_research":
        return False

    sample_count = int(effectiveness.get("sample_count", 0) or 0)
    minimum_sample_count = int(effectiveness.get("minimum_sample_count", 0) or 0)
    if minimum_sample_count > 0 and sample_count < minimum_sample_count:
        return False

    correlation = effectiveness.get("information_coefficient")
    if correlation is None:
        correlation = effectiveness.get("rank_correlation")
    if correlation is None or float(correlation) < 0.2:
        return False
    if float(effectiveness.get("top_minus_bottom_forward_return", 0.0) or 0.0) <= 0.0:
        return False
    if float(effectiveness.get("top_bucket_hit_rate", 0.0) or 0.0) < 0.5:
        return False
    return True


def _flatten_public_strategy_factor(factor: Mapping[str, Any]) -> dict[str, Any]:
    flattened = dict(factor)
    effectiveness = factor.get("effectiveness")
    if not isinstance(effectiveness, Mapping):
        return flattened

    effectiveness_payload = dict(effectiveness)
    if "sample_count" in effectiveness_payload:
        flattened["sample_count"] = int(effectiveness_payload["sample_count"])
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
        if key in effectiveness_payload:
            flattened[key] = effectiveness_payload[key]
    return flattened


def _variant_with_best_metric(
    variants: Mapping[str, Any],
    *,
    metric_fn: Callable[[str, Mapping[str, Any]], float],
) -> tuple[str | None, float]:
    best_name = None
    best_value = float("-inf")
    for variant_name, payload in variants.items():
        value = float(metric_fn(variant_name, dict(payload)))
        if best_name is None or value > best_value:
            best_name = str(variant_name)
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
    policies = dict(experiment.get("policies", {}))
    current_policy = dict(policies.get("current", {}))
    soft_policy = dict(policies.get("soft_suppression", {}))
    no_suppression_policy = dict(policies.get("no_suppression", {}))
    current_pnl = float(current_policy.get("bucket_level_pnl", 0.0))
    soft_pnl = float(soft_policy.get("bucket_level_pnl", 0.0))
    no_suppression_pnl = float(no_suppression_policy.get("bucket_level_pnl", 0.0))
    opportunity_kill_rate = float(experiment.get("opportunity_kill_rate", 0.0))
    avoid_loss_rate = float(experiment.get("avoid_loss_rate", 0.0))

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
            "rows": list(experiment.get("rotation_comparison_rows", [])),
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
    current_base = dict(dict(variants.get("current_allocator", {})).get("frictions", {})).get("base", {})
    best_stressed_net_bucket_pnl = 0.0
    if best_variant is not None:
        best_stressed_net_bucket_pnl = float(
            dict(dict(variants.get(best_variant, {})).get("frictions", {})).get("stressed", {}).get("net_bucket_pnl", 0.0)
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
                "current_allocator_base_net_bucket_pnl": float(dict(current_base).get("net_bucket_pnl", 0.0)),
                "current_allocator_base_cost_drag": float(dict(current_base).get("cost_drag", 0.0)),
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
    variants = dict(experiment.get("variants", {}))
    best_variant, best_bucket_pnl = _variant_with_best_metric(
        variants,
        metric_fn=lambda _name, payload: dict(payload.get("performance", {})).get("bucket_level_pnl", 0.0),
    )
    best_payload = dict(variants.get(best_variant, {})) if best_variant is not None else {}
    accepted_allocations = int(dict(best_payload.get("funnel", {})).get("accepted_allocations", 0))

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
                "snapshot_count": int(metadata.get("snapshot_count", 0)),
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
    engines = dict(experiment.get("engines", {}))
    best_engine = None
    best_accept_count = -1
    dominant_blocker_engine = None
    dominant_blocker_gate = None
    dominant_blocker_count = 0

    for engine_name, payload in engines.items():
        funnel = dict(payload.get("funnel", {}))
        accept_count = int(funnel.get("accepted_allocations", 0))
        if accept_count > best_accept_count:
            best_engine = str(engine_name)
            best_accept_count = accept_count

        blocker_gate, blocker_count = _dominant_long_gate_blocker(dict(payload.get("filter_counts", {})))
        if blocker_count > dominant_blocker_count:
            dominant_blocker_engine = str(engine_name)
            dominant_blocker_gate = blocker_gate
            dominant_blocker_count = blocker_count

    total_long_accepted_allocations = sum(
        int(dict(payload.get("funnel", {})).get("accepted_allocations", 0)) for payload in engines.values()
    )
    engines_with_candidate_flow = sum(
        1 for payload in engines.values() if int(dict(payload.get("funnel", {})).get("raw_candidates", 0)) > 0
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
    supported_factor_count = int(summary_payload.get("supported_factor_count", 0))
    unsupported_factor_count = int(summary_payload.get("unsupported_factor_count", 0))
    effective_factor_count = int(summary_payload.get("effective_factor_count", 0))
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
                "snapshot_count": int(metadata.get("snapshot_count", 0)),
                "supported_factor_count": supported_factor_count,
                "unsupported_factor_count": unsupported_factor_count,
                "data_gap_count": int(summary_payload.get("data_gap_count", 0)),
                "evaluated_factor_count": int(summary_payload.get("evaluated_factor_count", 0)),
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
    candidate_rows = list(experiment.get("candidate_rows", []))
    technical_candidate_count = int(summary_payload.get("technical_candidate_count", 0))
    accepted_candidate_count = int(summary_payload.get("accepted_candidate_count", 0))
    rejected_candidate_count = int(summary_payload.get("rejected_candidate_count", 0))
    acceptance_rate = float(summary_payload.get("acceptance_rate", 0.0))
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
                "snapshot_count": int(metadata.get("snapshot_count", 0)),
                "technical_candidate_count": technical_candidate_count,
                "accepted_candidate_count": accepted_candidate_count,
                "rejected_candidate_count": rejected_candidate_count,
                "acceptance_rate": acceptance_rate,
            },
            "decision_summary": _decision_summary(decision=decision, summary=summary),
            "rejection_reasons": dict(summary_payload.get("rejection_reasons", {})),
            **_promotion_metadata_sections(metadata),
        },
    }


def render_walk_forward_validation_report(
    *,
    experiment_name: str,
    experiment: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    robustness_summary = dict(experiment.get("robustness_summary", {}))
    parameter_stability = dict(experiment.get("parameter_stability", {}))
    performance_dispersion = dict(robustness_summary.get("performance_dispersion", {}))
    out_of_sample_scorecard = dict(robustness_summary.get("out_of_sample_scorecard", {}))

    out_of_sample_total_return = float(out_of_sample_scorecard.get("total_return", 0.0))
    positive_window_ratio = float(performance_dispersion.get("positive_window_ratio", 0.0))
    parameter_stability_score = float(parameter_stability.get("parameter_stability_score", 0.0))

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
            "rows": list(experiment.get("windows", [])),
        },
        "scorecard": {
            "metadata": _scorecard_metadata(experiment_name=experiment_name, metadata=metadata),
            "key_metrics": {
                "snapshot_count": int(metadata.get("snapshot_count", 0)),
                "window_count": int(metadata.get("window_count", 0)),
                "out_of_sample_total_return": out_of_sample_total_return,
                "positive_window_ratio": positive_window_ratio,
                "parameter_stability_score": parameter_stability_score,
            },
            "decision_summary": _decision_summary(decision=decision, summary=summary),
            **_promotion_metadata_sections(metadata),
        },
    }
