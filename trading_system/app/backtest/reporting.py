from __future__ import annotations

from collections import Counter
import math
from typing import Any, Callable, Mapping

from .metrics import cost_drag
from .types import BaselineReplayResult, PromotionMetadata, TradeLedgerRow


_MAKER_STATUS_VALUES = frozenset({"filled", "partial", "no_fill", "expired", "cancelled_replaced"})


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
    experiment_name = _canonical_report_string(experiment_name, field_name="experiment_name")
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be an object")
    report_metadata = _report_metadata_copy(metadata)
    raw_by_regime = experiment.get("by_regime", {})
    if not isinstance(raw_by_regime, Mapping):
        raise ValueError("by_regime must be an object")
    regime_labels: set[str] = set()
    best_regime = None
    best_return = None
    worst_regime = None
    worst_return = None
    for raw_label, payload in raw_by_regime.items():
        label = _canonical_report_string(raw_label, field_name="by_regime keys")
        if label in regime_labels:
            raise ValueError("by_regime keys must be unique")
        regime_labels.add(label)
        if not isinstance(payload, Mapping):
            raise ValueError(f"by_regime.{label} must be an object")
        forward_return_by_window = payload.get("forward_return_by_window", {})
        if not isinstance(forward_return_by_window, Mapping):
            raise ValueError(f"by_regime.{label}.forward_return_by_window must be an object")
        validated_forward_return_by_window: dict[str, float] = {}
        for raw_window, raw_return in forward_return_by_window.items():
            window = _canonical_report_string(
                raw_window,
                field_name=f"by_regime.{label}.forward_return_by_window key",
            )
            validated_forward_return_by_window[window] = _strict_present_finite_float(
                raw_return,
                field_name=f"by_regime.{label}.forward_return_by_window.{window}",
            )
        current = (
            validated_forward_return_by_window["3d"]
            if "3d" in validated_forward_return_by_window
            else 0.0
        )
        if best_return is None or current > best_return:
            best_regime, best_return = label, current
        if worst_return is None or current < worst_return:
            worst_regime, worst_return = label, current

    raw_duration_stats = experiment.get("duration_stats", {})
    if not isinstance(raw_duration_stats, Mapping):
        raise ValueError("duration_stats must be an object")
    for raw_label, payload in raw_duration_stats.items():
        label = _canonical_report_string(raw_label, field_name="duration_stats key")
        if not isinstance(payload, Mapping):
            raise ValueError(f"duration_stats.{label} must be an object")
        _non_negative_int_field(payload, "max_duration_bars", label=f"duration_stats.{label}")

    regimes_with_samples = len(regime_labels)
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
            "dataset_root": report_metadata.get("dataset_root"),
            "baseline_name": report_metadata.get("baseline_name"),
            "variant_name": report_metadata.get("variant_name"),
            "sample_period": report_metadata.get("sample_period"),
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
    experiment_name = _canonical_report_string(experiment_name, field_name="experiment_name")
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be an object")
    report_metadata = _report_metadata_copy(metadata)
    walk_forward = _mapping_field(evaluation, "walk_forward")
    regimes = _mapping_field(evaluation, "regimes")
    cost_stress = _mapping_field(evaluation, "cost_stress")
    raw_walk_forward_metadata = walk_forward.get("metadata", {})
    if not isinstance(raw_walk_forward_metadata, Mapping):
        raise ValueError("walk_forward.metadata must be an object")
    walk_forward_metadata = dict(raw_walk_forward_metadata)
    regime_buckets = _list_field(regimes, "buckets", label="regimes.buckets")
    validated_regime_buckets = []
    regime_bucket_labels: set[str] = set()
    for index, bucket in enumerate(regime_buckets):
        if not isinstance(bucket, Mapping):
            raise ValueError(f"regimes.buckets[{index}] must be an object")
        validated_bucket = dict(bucket)
        if "label" in validated_bucket:
            label = _canonical_report_string(
                validated_bucket["label"],
                field_name=f"regimes.buckets[{index}].label",
            )
            if label in regime_bucket_labels:
                raise ValueError("regimes.buckets labels must be unique")
            regime_bucket_labels.add(label)
            validated_bucket["label"] = label
        validated_regime_buckets.append(validated_bucket)
    validated_regimes = dict(regimes)
    validated_regimes["buckets"] = validated_regime_buckets
    stress_scenarios = []
    stress_scenario_names: set[str] = set()
    validated_cost_scenarios = []
    for index, scenario_payload in enumerate(_list_field(cost_stress, "scenarios", label="cost_stress.scenarios")):
        if not isinstance(scenario_payload, Mapping):
            raise ValueError(f"cost_stress.scenarios[{index}] must be an object")
        validated_scenario_payload = dict(scenario_payload)
        scenario = scenario_payload.get("scenario", {})
        if not isinstance(scenario, Mapping):
            raise ValueError(f"cost_stress.scenarios[{index}].scenario must be an object")
        if "name" in scenario:
            name = scenario["name"]
            scenario_name = _canonical_report_string(
                name, field_name=f"cost_stress.scenarios[{index}].scenario.name"
            )
            if scenario_name in stress_scenario_names:
                raise ValueError("cost_stress.scenarios scenario.name values must be unique")
            stress_scenario_names.add(scenario_name)
            stress_scenarios.append(scenario_name)
        for metrics_field in ("base_metrics", "stressed_metrics"):
            if metrics_field not in scenario_payload:
                continue
            metrics = scenario_payload[metrics_field]
            if not isinstance(metrics, Mapping):
                raise ValueError(f"cost_stress.scenarios[{index}].{metrics_field} must be an object")
            validated_metrics = dict(metrics)
            for metric_name, metric_value in validated_metrics.items():
                metric_key = _canonical_report_string(
                    metric_name,
                    field_name=f"cost_stress.scenarios[{index}].{metrics_field} key",
                )
                validated_metrics[metric_key] = _strict_present_finite_float(
                    metric_value,
                    field_name=f"cost_stress.scenarios[{index}].{metrics_field}.{metric_key}",
                )
            validated_scenario_payload[metrics_field] = validated_metrics
        validated_cost_scenarios.append(validated_scenario_payload)
    validated_cost_stress = dict(cost_stress)
    validated_cost_stress["scenarios"] = validated_cost_scenarios

    return {
        "summary": {
            "metadata": {
                **report_metadata,
                "experiment_name": experiment_name,
                "evaluation_layer": "walk_forward_oos_regime_cost_stress",
            },
            "walk_forward_status": walk_forward.get("status"),
            "walk_forward_window_count": _non_negative_int_field(
                walk_forward_metadata, "window_count", label="walk_forward.metadata"
            ),
            "regime_bucket_count": len(regime_buckets),
            "cost_stress_scenarios": stress_scenarios,
        },
        "walk_forward": walk_forward,
        "regimes": validated_regimes,
        "cost_stress": validated_cost_stress,
    }


def _trade_breakdown_rows(
    trade_ledger: tuple[TradeLedgerRow, ...],
    *,
    key_name: str,
    key_fn: Callable[[TradeLedgerRow], Any],
    canonical_string_key: bool = False,
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, float | int | str]] = {}
    for index, row in enumerate(trade_ledger):
        raw_bucket_key = key_fn(row)
        bucket_key = (
            _canonical_report_string(raw_bucket_key, field_name=f"trades[{index}].{key_name}")
            if canonical_string_key
            else str(raw_bucket_key)
        )
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
            "symbol": _canonical_report_string(row.symbol, field_name=f"trades[{index}].symbol"),
            "market_type": _canonical_report_string(row.market_type, field_name=f"trades[{index}].market_type"),
            "base_asset": _canonical_report_string(row.base_asset, field_name=f"trades[{index}].base_asset"),
            "side": _canonical_report_string(row.side, field_name=f"trades[{index}].side"),
            "status": _canonical_report_string(row.status, field_name=f"trades[{index}].status"),
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
            "engine": _canonical_optional_empty_report_string(row.engine, field_name=f"trades[{index}].engine"),
            "setup_type": _canonical_optional_empty_report_string(
                row.setup_type, field_name=f"trades[{index}].setup_type"
            ),
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
            "cost_coverage_ratio": _optional_non_negative_finite_float(
                row.cost_coverage_ratio,
                field_name=f"trades[{index}].cost_coverage_ratio",
            ),
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
            "maker_status": _optional_supported_report_string(
                row.maker_status,
                field_name=f"trades[{index}].maker_status",
                allowed=_MAKER_STATUS_VALUES,
            ),
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
            "execution_impact_bps": _optional_non_negative_finite_float(
                row.execution_impact_bps,
                field_name=f"trades[{index}].execution_impact_bps",
            ),
            "slippage_bps": _optional_non_negative_finite_float(
                row.slippage_bps,
                field_name=f"trades[{index}].slippage_bps",
            ),
        }
        for index, row in enumerate(trade_ledger)
    ]


def render_full_market_baseline_report(result: BaselineReplayResult) -> dict[str, Any]:
    reason_counts = Counter(
        reason
        for row_index, row in enumerate(result.rejection_ledger)
        for reason in _canonical_report_string_tuple(row.reasons, field_name=f"rejections[{row_index}].reasons")
    )

    if not isinstance(result.cost_breakdown, Mapping):
        raise ValueError("cost_breakdown must be an object")
    cost_breakdown_payload: dict[str, float] = {}
    for key, value in result.cost_breakdown.items():
        cost_key = _canonical_report_string(key, field_name="cost_breakdown key")
        cost_breakdown_payload[cost_key] = _strict_present_finite_float(
            value,
            field_name=f"cost_breakdown.{cost_key}",
        )

    gross_period_returns = _strict_finite_float_sequence(
        result.gross_period_returns,
        field_name="gross_period_returns",
    )
    net_period_returns = _strict_finite_float_sequence(
        result.net_period_returns,
        field_name="net_period_returns",
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
            "cost_drag": cost_drag(gross_period_returns, net_period_returns),
            "cost_breakdown": cost_breakdown_payload,
        },
        "breakdowns": {
            "by_market": _trade_breakdown_rows(
                result.trade_ledger,
                key_name="market_type",
                key_fn=lambda row: row.market_type,
                canonical_string_key=True,
            ),
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


_REPORT_METADATA_IDENTIFIER_FIELDS = frozenset(
    {
        "dataset_root",
        "baseline_name",
        "variant_name",
        "evaluation_window",
    }
)


def _report_metadata_copy(metadata: Mapping[str, Any]) -> dict[str, Any]:
    copied = _strict_mapping_copy(metadata, field_name="metadata")
    for field in _REPORT_METADATA_IDENTIFIER_FIELDS:
        if field in copied and copied[field] is not None:
            copied[field] = _canonical_report_string(copied[field], field_name=f"metadata.{field}")
    if "sample_period" in copied and copied["sample_period"] is not None:
        copied["sample_period"] = _report_sample_period(copied["sample_period"])
    return copied


def _report_sample_period(value: object) -> dict[str, str | None] | str:
    if type(value) is str:
        return _canonical_report_string(value, field_name="metadata.sample_period")
    if not isinstance(value, Mapping):
        raise ValueError("metadata.sample_period must be an object or canonical string")
    period = _strict_mapping_copy(value, field_name="metadata.sample_period")
    for boundary in ("start", "end"):
        if boundary not in period:
            raise ValueError(f"metadata.sample_period.{boundary} must be present")
        if period[boundary] is not None:
            period[boundary] = _canonical_report_string(
                period[boundary],
                field_name=f"metadata.sample_period.{boundary}",
            )
    return {
        "start": period["start"],
        "end": period["end"],
    }


def _decision_summary(*, decision: str, summary: str) -> dict[str, str]:
    return {"decision": decision, "summary": summary}



def _promotion_metadata_sections(metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw = metadata.get("promotion_metadata")
    if raw is None:
        return {}
    if isinstance(raw, PromotionMetadata):
        runtime_fields = list(
            _canonical_report_string_tuple(
                raw.runtime_fields, field_name="promotion_metadata.runtime_fields"
            )
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
    elif isinstance(raw, dict):
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


def _optional_supported_report_string(value: object, *, field_name: str, allowed: frozenset[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value or value not in allowed:
        raise ValueError(f"{field_name} must be a supported canonical string")
    return value


def _canonical_optional_empty_report_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a canonical string")
    if value.strip() != value:
        raise ValueError(f"{field_name} must be a canonical string")
    return value


def _canonical_report_string_list(value: object, *, field_name: str) -> list[str]:
    if type(value) is not list:
        raise ValueError(f"{field_name} must be a list")
    return [_canonical_report_string(item, field_name=f"{field_name}[]") for item in value]


def _canonical_report_string_tuple(value: object, *, field_name: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise ValueError(f"{field_name} must be a tuple")
    return tuple(
        _canonical_report_string(item, field_name=f"{field_name}[{index}]")
        for index, item in enumerate(value)
    )


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


def _mapping_field(payload: Mapping[str, Any], field: str, *, default: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if field not in payload:
        return _strict_mapping_copy(default or {}, field_name=field)
    raw_value = payload[field]
    if not isinstance(raw_value, Mapping):
        raise ValueError(f"{field} must be an object")
    return _strict_mapping_copy(raw_value, field_name=field)


def _strict_mapping_copy(payload: Mapping[Any, Any], *, field_name: str) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise ValueError(f"{field_name} key must be a string")
        copied[key] = value
    return copied


def _strict_optional_mapping(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    raw_value = payload.get(field, {})
    if not isinstance(raw_value, Mapping):
        raise ValueError(f"{field} must be an object")
    return _strict_mapping_copy(raw_value, field_name=field)


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


def _positive_int_field(payload: Mapping[str, Any], field: str, *, label: str = "summary") -> int:
    value = _non_negative_int_field(payload, field, label=label)
    if value <= 0:
        raise ValueError(f"{label}.{field} must be a positive integer")
    return value


def _strict_positive_int_field(payload: Mapping[str, Any], field: str, *, label: str) -> int:
    raw_value = payload[field]
    if isinstance(raw_value, bool) or not isinstance(raw_value, int) or raw_value <= 0:
        raise ValueError(f"{label}.{field} must be a positive integer")
    return raw_value


def _summary_int(summary_payload: Mapping[str, Any], field: str, default: int = 0) -> int:
    return _non_negative_int_field(summary_payload, field, label="summary", default=default)


def _metadata_int(metadata: Mapping[str, Any], field: str, default: int = 0) -> int:
    return _non_negative_int_field(metadata, field, label="metadata", default=default)


def _summary_float(summary_payload: Mapping[str, Any], field: str, default: float = 0.0) -> float:
    if field not in summary_payload:
        return default
    raw_value = summary_payload[field]
    if isinstance(raw_value, bool) or not isinstance(raw_value, int | float):
        raise ValueError(f"summary.{field} must be a finite number")
    value = float(raw_value)
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
    if isinstance(raw_value, bool) or not isinstance(raw_value, int | float):
        raise ValueError(f"effectiveness.{field} must be a finite number")
    value = float(raw_value)
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


def _flatten_public_strategy_factor(factor: Mapping[str, Any], *, field_name: str = "factor") -> dict[str, Any]:
    flattened = _strict_mapping_copy(factor, field_name=field_name)
    effectiveness = factor.get("effectiveness")
    if not isinstance(effectiveness, Mapping):
        return flattened

    effectiveness_payload = _strict_mapping_copy(effectiveness, field_name=f"{field_name}.effectiveness")
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


def _strict_present_finite_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite number")
    return parsed


def _strict_bounded_ratio_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a bounded ratio strict number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0 or parsed > 1.0:
        raise ValueError(f"{field_name} must be a bounded ratio strict number")
    return parsed


def _strict_bounded_percentage_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be a bounded percentage strict number")
    if value < 0 or value > 100:
        raise ValueError(f"{field_name} must be a bounded percentage strict number")
    return value


def _strict_non_negative_finite_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a non-negative finite strict number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ValueError(f"{field_name} must be a non-negative finite strict number")
    return parsed


def _optional_non_negative_finite_float(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a non-negative finite number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ValueError(f"{field_name} must be a non-negative finite number")
    return parsed


def _strict_finite_float_sequence(value: Any, *, field_name: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    return tuple(
        _strict_present_finite_float(item, field_name=f"{field_name}[{index}]")
        for index, item in enumerate(value)
    )


def _llm_trend_candidate_rows(rows: list[Any]) -> list[dict[str, Any]]:
    numeric_fields = ("technical_score", "sentiment_score", "final_score", "label_confidence")
    validated_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"candidate_rows[{index}] must be an object")
        validated = dict(row)
        for field in numeric_fields:
            if field not in validated or validated[field] is None:
                continue
            validated[field] = _strict_present_finite_float(
                validated[field],
                field_name=f"candidate_rows[{index}].{field}",
            )
        validated_rows.append(validated)
    return validated_rows


_WALK_FORWARD_COVERAGE_RATIO_FIELDS = ("coverage_ratio",)
_WALK_FORWARD_COVERAGE_PERCENT_FIELDS = (
    "coverage_pct",
    "data_coverage_pct",
    "sample_coverage_pct",
    "trade_coverage_pct",
    "signal_coverage_pct",
    "regime_coverage_pct",
    "fill_coverage_pct",
    "universe_coverage_pct",
    "benchmark_coverage_pct",
    "execution_coverage_pct",
)


def _walk_forward_scorecard_coverage_fields(scorecard: dict[str, Any], *, label: str) -> dict[str, Any]:
    for field in _WALK_FORWARD_COVERAGE_RATIO_FIELDS:
        if field in scorecard and scorecard[field] is not None:
            scorecard[field] = _strict_bounded_ratio_float(scorecard[field], field_name=f"{label}.{field}")
    for field in _WALK_FORWARD_COVERAGE_PERCENT_FIELDS:
        if field in scorecard and scorecard[field] is not None:
            scorecard[field] = _strict_bounded_percentage_int(scorecard[field], field_name=f"{label}.{field}")
    return scorecard


def _walk_forward_window_rows(rows: list[Any]) -> list[dict[str, Any]]:
    scorecard_numeric_fields = (
        "total_return",
        "max_drawdown",
        "sharpe",
        "sortino",
        "calmar",
        "win_rate",
        "payoff_ratio",
        "expectancy",
    )
    scorecard_count_fields = ("trade_count", "win_count", "loss_count")
    scorecard_duration_fields = (
        "holding_bars",
        "duration_bars",
        "max_duration_bars",
        "min_duration_bars",
        "median_duration_bars",
        "lookback_bars",
        "window_span_bars",
        "age_bars",
        "bars_since_entry",
    )
    validated_rows: list[dict[str, Any]] = []
    for index, window in enumerate(rows):
        if not isinstance(window, Mapping):
            raise ValueError(f"windows[{index}] must be an object")
        validated = dict(window)
        if "window_index" in validated and validated["window_index"] is not None:
            validated["window_index"] = _non_negative_int_field(
                validated,
                "window_index",
                label=f"windows[{index}]",
            )
        for segment_name in ("in_sample", "out_of_sample"):
            segment = validated.get(segment_name)
            if segment is None:
                continue
            if not isinstance(segment, Mapping):
                raise ValueError(f"windows[{index}].{segment_name} must be an object")
            validated_segment = dict(segment)
            if "run_ids" in validated_segment:
                validated_segment["run_ids"] = _canonical_report_string_list(
                    validated_segment["run_ids"],
                    field_name=f"windows[{index}].{segment_name}.run_ids",
                )
            if "snapshot_count" in validated_segment and validated_segment["snapshot_count"] is not None:
                validated_segment["snapshot_count"] = _non_negative_int_field(
                    validated_segment,
                    "snapshot_count",
                    label=f"windows[{index}].{segment_name}",
                )
            scorecard = validated_segment.get("scorecard")
            if scorecard is None:
                validated[segment_name] = validated_segment
                continue
            if not isinstance(scorecard, Mapping):
                raise ValueError(f"windows[{index}].{segment_name}.scorecard must be an object")
            validated_scorecard = dict(scorecard)
            for field in scorecard_numeric_fields:
                if field not in validated_scorecard or validated_scorecard[field] is None:
                    continue
                field_name = f"windows[{index}].{segment_name}.scorecard.{field}"
                if field == "win_rate":
                    validated_scorecard[field] = _strict_bounded_ratio_float(
                        validated_scorecard[field],
                        field_name=field_name,
                    )
                elif field == "payoff_ratio":
                    validated_scorecard[field] = _strict_non_negative_finite_float(
                        validated_scorecard[field],
                        field_name=field_name,
                    )
                else:
                    validated_scorecard[field] = _strict_present_finite_float(
                        validated_scorecard[field],
                        field_name=field_name,
                    )
            for field in scorecard_count_fields:
                if field in validated_scorecard and validated_scorecard[field] is not None:
                    validated_scorecard[field] = _non_negative_int_field(
                        validated_scorecard,
                        field,
                        label=f"windows[{index}].{segment_name}.scorecard",
                    )
            for field in scorecard_duration_fields:
                if field in validated_scorecard and validated_scorecard[field] is not None:
                    validated_scorecard[field] = _strict_positive_int_field(
                        validated_scorecard,
                        field,
                        label=f"windows[{index}].{segment_name}.scorecard",
                    )
            validated_scorecard = _walk_forward_scorecard_coverage_fields(
                validated_scorecard,
                label=f"windows[{index}].{segment_name}.scorecard",
            )
            validated_segment["scorecard"] = validated_scorecard
            validated[segment_name] = validated_segment
        validated_rows.append(validated)
    return validated_rows


_WALK_FORWARD_DURATION_FIELDS = (
    "holding_bars",
    "duration_bars",
    "max_duration_bars",
    "min_duration_bars",
    "median_duration_bars",
    "lookback_bars",
    "window_span_bars",
    "age_bars",
    "bars_since_entry",
)

_WALK_FORWARD_SCORECARD_NUMERIC_FIELDS = (
    "total_return",
    "max_drawdown",
    "sharpe",
    "sortino",
    "calmar",
    "win_rate",
    "payoff_ratio",
    "expectancy",
)


def _walk_forward_scorecard_counts(scorecard: dict[str, Any], *, label: str) -> dict[str, Any]:
    for field in _WALK_FORWARD_SCORECARD_NUMERIC_FIELDS:
        if field not in scorecard or scorecard[field] is None:
            continue
        field_name = f"{label}.{field}"
        if field == "win_rate":
            scorecard[field] = _strict_bounded_ratio_float(scorecard[field], field_name=field_name)
        elif field == "payoff_ratio":
            scorecard[field] = _strict_non_negative_finite_float(scorecard[field], field_name=field_name)
        else:
            scorecard[field] = _strict_present_finite_float(scorecard[field], field_name=field_name)
    for field in ("trade_count", "win_count", "loss_count"):
        if field in scorecard and scorecard[field] is not None:
            scorecard[field] = _non_negative_int_field(scorecard, field, label=label)
    for field in _WALK_FORWARD_DURATION_FIELDS:
        if field in scorecard and scorecard[field] is not None:
            scorecard[field] = _strict_positive_int_field(scorecard, field, label=label)
    scorecard = _walk_forward_scorecard_coverage_fields(scorecard, label=label)
    return scorecard


def _walk_forward_performance_dispersion(payload: Mapping[str, Any]) -> dict[str, Any]:
    performance_dispersion = dict(payload)
    for field in ("window_count", "positive_window_count"):
        if field in performance_dispersion and performance_dispersion[field] is not None:
            performance_dispersion[field] = _non_negative_int_field(
                performance_dispersion,
                field,
                label="performance_dispersion",
            )
    if (
        "positive_window_ratio" in performance_dispersion
        and performance_dispersion["positive_window_ratio"] is not None
    ):
        performance_dispersion["positive_window_ratio"] = _strict_bounded_ratio_float(
            performance_dispersion["positive_window_ratio"],
            field_name="performance_dispersion.positive_window_ratio",
        )
    for field in _WALK_FORWARD_DURATION_FIELDS:
        if field in performance_dispersion and performance_dispersion[field] is not None:
            performance_dispersion[field] = _strict_positive_int_field(
                performance_dispersion,
                field,
                label="performance_dispersion",
            )
    return performance_dispersion


def _walk_forward_worst_window(payload: Any) -> dict[str, Any] | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ValueError("worst_window must be an object")
    worst_window = dict(payload)
    if "window_index" in worst_window and worst_window["window_index"] is not None:
        worst_window["window_index"] = _positive_int_field(worst_window, "window_index", label="worst_window")
    scorecard = worst_window.get("scorecard")
    if scorecard is not None:
        if not isinstance(scorecard, Mapping):
            raise ValueError("worst_window.scorecard must be an object")
        worst_window["scorecard"] = _walk_forward_scorecard_counts(
            dict(scorecard),
            label="worst_window.scorecard",
        )
    return worst_window


def _rotation_comparison_rows(rows: list[Any]) -> list[dict[str, Any]]:
    validated_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"rotation_comparison_rows[{index}] must be an object")
        validated_rows.append(dict(row))
    return validated_rows


def _allocator_comparison_rows(rows: list[Any]) -> list[dict[str, Any]]:
    validated_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"comparison_rows[{index}] must be an object")
        validated_rows.append(dict(row))
    return validated_rows


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
    policies = {
        _canonical_report_string(policy_name, field_name="policies keys"): policy_payload
        for policy_name, policy_payload in raw_policies.items()
    }

    def policy_payload(name: str) -> dict[str, Any]:
        if name not in policies:
            raise ValueError(f"policies.{name} must be an object")
        raw_policy = policies[name]
        if not isinstance(raw_policy, Mapping):
            raise ValueError(f"policies.{name} must be an object")
        return dict(raw_policy)

    current_policy = policy_payload("current")
    soft_policy = policy_payload("soft_suppression")
    no_suppression_policy = policy_payload("no_suppression")
    current_pnl = _strict_present_finite_float(current_policy.get("bucket_level_pnl", 0.0), field_name="policies.current.bucket_level_pnl")
    soft_pnl = _strict_present_finite_float(soft_policy.get("bucket_level_pnl", 0.0), field_name="policies.soft_suppression.bucket_level_pnl")
    no_suppression_pnl = _strict_present_finite_float(no_suppression_policy.get("bucket_level_pnl", 0.0), field_name="policies.no_suppression.bucket_level_pnl")
    opportunity_kill_rate = _strict_present_finite_float(
        experiment.get("opportunity_kill_rate", 0.0), field_name="opportunity_kill_rate"
    )
    avoid_loss_rate = _strict_present_finite_float(experiment.get("avoid_loss_rate", 0.0), field_name="avoid_loss_rate")

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
            "rows": _rotation_comparison_rows(
                _list_field(experiment, "rotation_comparison_rows", label="rotation_comparison_rows")
            ),
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
    raw_variants = experiment.get("variants", {})
    if not isinstance(raw_variants, Mapping):
        raise ValueError("variants must be an object")
    variants = dict(raw_variants)

    def base_net_bucket_pnl(variant_name: str, payload: Mapping[str, Any]) -> float:
        frictions = payload.get("frictions", {})
        if not isinstance(frictions, Mapping):
            raise ValueError(f"variants.{variant_name}.frictions must be an object")
        if "base" not in frictions:
            raise ValueError(f"variants.{variant_name}.frictions.base must be an object")
        base = frictions["base"]
        if not isinstance(base, Mapping):
            raise ValueError(f"variants.{variant_name}.frictions.base must be an object")
        return _strict_present_finite_float(
            base.get("net_bucket_pnl", 0.0),
            field_name=f"variants.{variant_name}.frictions.base.net_bucket_pnl",
        )

    best_variant, best_base_net_bucket_pnl = _variant_with_best_metric(
        variants,
        metric_fn=base_net_bucket_pnl,
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
        best_stressed_net_bucket_pnl = _strict_present_finite_float(
            best_stressed.get("net_bucket_pnl", 0.0),
            field_name=f"variants.{best_variant}.frictions.stressed.net_bucket_pnl",
        )
    current_base_net_bucket_pnl = _report_finite_float(
        current_base.get("net_bucket_pnl", 0.0),
        field_name="variants.current_allocator.frictions.base.net_bucket_pnl",
    )
    current_base_cost_drag = _strict_present_finite_float(
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
            "rows": _allocator_comparison_rows(_list_field(experiment, "comparison_rows")),
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

    def bucket_level_pnl(variant_name: str, payload: Mapping[str, Any]) -> float:
        performance = payload.get("performance", {})
        if not isinstance(performance, Mapping):
            raise ValueError(f"variants.{variant_name}.performance must be an object")
        return dict(performance).get("bucket_level_pnl", 0.0)

    best_variant, best_bucket_pnl = _variant_with_best_metric(
        variants,
        metric_fn=bucket_level_pnl,
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


def _top_blocker(filter_counts: Mapping[str, Any], *, label: str = "filter_counts") -> tuple[str | None, int]:
    blocker_keys = [
        key
        for key in filter_counts
        if key != "selected"
        and not key.endswith("_bypassed")
        and _non_negative_int_field(filter_counts, key, label=label) > 0
    ]
    if not blocker_keys:
        return None, 0
    blocker_key = max(
        blocker_keys,
        key=lambda key: (_non_negative_int_field(filter_counts, key, label=label), key),
    )
    return blocker_key, _non_negative_int_field(filter_counts, blocker_key, label=label)



def _top_specific_eligibility_blocker(
    filter_counts: Mapping[str, Any], *, label: str = "filter_counts"
) -> tuple[str | None, int]:
    eligibility_keys = [
        key
        for key in filter_counts
        if key.startswith("eligibility_")
        and key != "eligibility_filtered"
        and _non_negative_int_field(filter_counts, key, label=label) > 0
    ]
    if not eligibility_keys:
        return None, 0
    blocker_key = max(
        eligibility_keys,
        key=lambda key: (_non_negative_int_field(filter_counts, key, label=label), key),
    )
    return blocker_key, _non_negative_int_field(filter_counts, blocker_key, label=label)



def _dominant_long_gate_blocker(
    filter_counts: Mapping[str, Any], *, label: str = "filter_counts"
) -> tuple[str | None, int]:
    blocker_gate, blocker_count = _top_blocker(filter_counts, label=label)
    if blocker_gate != "eligibility_filtered":
        return blocker_gate, blocker_count
    specific_gate, specific_count = _top_specific_eligibility_blocker(filter_counts, label=label)
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
        engine_label = _canonical_report_string(engine_name, field_name="engine names")
        if not isinstance(payload, Mapping):
            raise ValueError(f"engines.{engine_label} must be an object")
        raw_funnel = payload.get("funnel", {})
        if not isinstance(raw_funnel, Mapping):
            raise ValueError(f"engines.{engine_label}.funnel must be an object")
        funnel = dict(raw_funnel)
        engine_funnels[engine_label] = funnel
        funnel_label = f"engines.{engine_label}.funnel"
        accept_count = _non_negative_int_field(funnel, "accepted_allocations", label=funnel_label)
        if accept_count > best_accept_count:
            best_engine = engine_label
            best_accept_count = accept_count

        raw_filter_counts = payload.get("filter_counts", {})
        if not isinstance(raw_filter_counts, Mapping):
            raise ValueError(f"engines.{engine_label}.filter_counts must be an object")
        blocker_gate, blocker_count = _dominant_long_gate_blocker(
            dict(raw_filter_counts),
            label=f"engines.{engine_label}.filter_counts",
        )
        if blocker_count > dominant_blocker_count:
            dominant_blocker_engine = engine_label
            dominant_blocker_gate = blocker_gate
            dominant_blocker_count = blocker_count

    total_long_accepted_allocations = sum(
        _non_negative_int_field(funnel, "accepted_allocations", label=f"engines.{engine_name}.funnel")
        for engine_name, funnel in engine_funnels.items()
    )
    engines_with_candidate_flow = sum(
        1
        for engine_name, funnel in engine_funnels.items()
        if _non_negative_int_field(funnel, "raw_candidates", label=f"engines.{engine_name}.funnel") > 0
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
            "rows": _list_field(experiment, "snapshot_rows"),
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
    summary_payload = _strict_optional_mapping(experiment, "summary")
    raw_factors = []
    for index, factor in enumerate(list(experiment.get("factors", []))):
        if not isinstance(factor, Mapping):
            raise ValueError(f"factors[{index}] must be an object")
        raw_factors.append(_strict_mapping_copy(factor, field_name=f"factors[{index}]"))
    factors = [
        _flatten_public_strategy_factor(factor, field_name=f"factors[{index}]")
        for index, factor in enumerate(raw_factors)
    ]
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
    summary_payload = _strict_optional_mapping(experiment, "summary")
    candidate_rows = _llm_trend_candidate_rows(_list_field(experiment, "candidate_rows"))
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
    performance_dispersion = _walk_forward_performance_dispersion(raw_performance_dispersion)
    raw_out_of_sample_scorecard = robustness_summary.get("out_of_sample_scorecard", {})
    if not isinstance(raw_out_of_sample_scorecard, Mapping):
        raise ValueError("out_of_sample_scorecard must be an object")
    out_of_sample_scorecard = _walk_forward_scorecard_counts(
        dict(raw_out_of_sample_scorecard),
        label="out_of_sample_scorecard",
    )
    if "in_sample_scorecard" in robustness_summary:
        raw_in_sample_scorecard = robustness_summary["in_sample_scorecard"]
        if not isinstance(raw_in_sample_scorecard, Mapping):
            raise ValueError("in_sample_scorecard must be an object")
        robustness_summary["in_sample_scorecard"] = _walk_forward_scorecard_counts(
            dict(raw_in_sample_scorecard),
            label="in_sample_scorecard",
        )
    robustness_summary["out_of_sample_scorecard"] = out_of_sample_scorecard
    robustness_summary["performance_dispersion"] = performance_dispersion
    if "worst_window" in robustness_summary:
        robustness_summary["worst_window"] = _walk_forward_worst_window(robustness_summary["worst_window"])
    windows = _walk_forward_window_rows(_list_field(experiment, "windows"))

    out_of_sample_total_return = _report_finite_float(
        out_of_sample_scorecard.get("total_return", 0.0),
        field_name="out_of_sample_scorecard.total_return",
    )
    positive_window_ratio = _report_finite_float(
        performance_dispersion.get("positive_window_ratio", 0.0),
        field_name="performance_dispersion.positive_window_ratio",
    )
    parameter_stability_score = _strict_bounded_ratio_float(
        parameter_stability.get("parameter_stability_score", 0.0),
        field_name="parameter_stability.parameter_stability_score",
    )
    parameter_stability["parameter_stability_score"] = parameter_stability_score

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
