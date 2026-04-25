from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Mapping

from .metrics import cost_drag
from .types import BaselineReplayResult, TradeLedgerRow


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


_ALLOWED_DECISIONS = {"keep_researching", "candidate_for_promotion", "reject"}


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
        },
    }


def render_public_strategy_factor_report(
    *,
    experiment_name: str,
    experiment: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    summary_payload = dict(experiment.get("summary", {}))
    factors = list(experiment.get("factors", []))
    supported_factor_count = int(summary_payload.get("supported_factor_count", 0))
    unsupported_factor_count = int(summary_payload.get("unsupported_factor_count", 0))
    decision = "keep_researching" if supported_factor_count > 0 else "reject"
    summary = (
        "public strategy ideas were converted into evidence-backed factor candidates; data gaps remain non-promotable"
        if supported_factor_count > 0
        else "public strategy ideas cannot be evaluated with the current dataset fields"
    )

    assert decision in _ALLOWED_DECISIONS
    return {
        "summary": {
            "metadata": dict(metadata),
            "summary": summary_payload,
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
                "effective_factor_count": int(summary_payload.get("effective_factor_count", 0)),
            },
            "decision_summary": _decision_summary(decision=decision, summary=summary),
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
        },
    }
