from __future__ import annotations

from collections import Counter
from datetime import datetime
import math
import re
from typing import Any, Callable, Mapping

from .metrics import cost_drag
from .types import BaselineReplayResult, PromotionMetadata, TradeLedgerRow


_MAKER_STATUS_VALUES = frozenset({"filled", "partial", "no_fill", "expired", "cancelled_replaced"})
_MARGIN_MODE_VALUES = frozenset({"isolated", "cross"})
_EXECUTION_PRICE_SOURCES_BY_FILL_MODEL = {
    "reference_close": frozenset(("ohlcv_close",)),
    "next_bar_ohlcv": frozenset(("ohlcv_next_open",)),
    "taker_ohlcv_approx": frozenset(("ohlcv_reference",)),
    "taker_orderbook": frozenset(("best_bid", "best_ask", "no_crossing_evidence")),
    "taker_orderbook_depth": frozenset(("bid_depth", "ask_depth", "no_crossing_evidence")),
    "taker_trade_print": frozenset(("trade_print",)),
    "maker_orderbook_trade_evidence": frozenset(("trade_print", "book_cross", "no_crossing_evidence")),
    "maker_post_only_queue": frozenset(("trade_print", "no_crossing_evidence")),
}
_COST_STRESS_TRADE_NUMERIC_FIELDS = (
    "base_net_pnl",
    "stressed_net_pnl",
    "fee_paid",
    "slippage_paid",
    "funding_paid",
)
_RAW_MARKET_PROVENANCE_IDENTITY_FIELDS = (
    "source",
    "archive_root",
    "coverage_start",
    "coverage_end",
    "fetched_at",
)
_RAW_MARKET_PROVENANCE_TIMESTAMP_FIELDS = frozenset(
    {
        "coverage_start",
        "coverage_end",
        "fetched_at",
    }
)
_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_REQUIRED_REGIME_STRATIFIED_OOS_BUCKETS = ("volatility", "liquidity", "funding", "crash", "squeeze")
_REGIME_STRATIFIED_OOS_NUMERIC_METRICS = ("total_return", "max_drawdown", "sharpe")
_REQUIRED_PNL_ATTRIBUTION_BUCKETS = (
    "entry_alpha",
    "exit_alpha",
    "sizing",
    "fees",
    "funding",
    "slippage_execution_impact",
    "regime",
    "symbol_selection",
)
_PNL_ATTRIBUTION_TOLERANCE = 1e-9
_PORTFOLIO_CORRELATION_EXPOSURE_SCHEMA_VERSION = "portfolio_correlation_exposure.v1"
_DRAWDOWN_ANATOMY_SCHEMA_VERSION = "drawdown_anatomy.v1"
_DRAWDOWN_FAILURE_TYPES = frozenset({"edge_failure", "execution_failure", "risk_control_failure"})


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
    validated_walk_forward = _evaluation_walk_forward_payload(walk_forward)
    if "windows" in validated_walk_forward and "window_count" in walk_forward_metadata:
        window_count = _non_negative_int_field(
            walk_forward_metadata,
            "window_count",
            label="walk_forward.metadata",
        )
        if window_count != len(validated_walk_forward["windows"]):
            raise ValueError("walk_forward.metadata.window_count must match walk_forward.windows length")
        walk_forward_metadata["window_count"] = window_count
    if "raw_market" in walk_forward_metadata and walk_forward_metadata["raw_market"] is not None:
        walk_forward_metadata["raw_market"] = _report_raw_market_provenance(
            walk_forward_metadata["raw_market"],
            field_name="walk_forward.metadata.raw_market",
        )
        if report_metadata.get("raw_market") is not None and _raw_market_provenance_identity(
            walk_forward_metadata["raw_market"]
        ) != _raw_market_provenance_identity(report_metadata["raw_market"]):
            raise ValueError("walk_forward.metadata.raw_market must match metadata.raw_market source identity")
    if "metadata" in validated_walk_forward:
        validated_walk_forward["metadata"] = walk_forward_metadata
    regime_buckets = _list_field(regimes, "buckets", label="regimes.buckets")
    validated_regime_buckets = []
    regime_bucket_labels: set[str] = set()
    for index, bucket in enumerate(regime_buckets):
        if not isinstance(bucket, Mapping):
            raise ValueError(f"regimes.buckets[{index}] must be an object")
        validated_bucket = dict(bucket)
        label = _canonical_report_string(
            validated_bucket.get("label"),
            field_name=f"regimes.buckets[{index}].label",
        )
        if label in regime_bucket_labels:
            raise ValueError("regimes.buckets labels must be unique")
        regime_bucket_labels.add(label)
        validated_bucket["label"] = label
        if "trade_ids" in validated_bucket:
            trade_ids = _list_field(validated_bucket, "trade_ids", label=f"regimes.buckets[{index}].trade_ids")
            validated_bucket["trade_ids"] = [
                _canonical_report_string(
                    trade_id,
                    field_name=f"regimes.buckets[{index}].trade_ids[{trade_id_index}]",
                )
                for trade_id_index, trade_id in enumerate(trade_ids)
            ]
            seen_trade_ids: set[str] = set()
            for trade_id in validated_bucket["trade_ids"]:
                if trade_id in seen_trade_ids:
                    raise ValueError(f"duplicate regimes.buckets[{index}].trade_id: {trade_id}")
                seen_trade_ids.add(trade_id)
        metrics = validated_bucket.get("metrics")
        if not isinstance(metrics, Mapping):
            raise ValueError(f"regimes.buckets[{index}].metrics must be an object")
        validated_metrics = _evaluation_metric_payload(
            metrics,
            field_name=f"regimes.buckets[{index}].metrics",
        )
        if "trade_ids" in validated_bucket and "trade_count" in validated_metrics:
            trade_count = validated_metrics["trade_count"]
            if trade_count != len(validated_bucket["trade_ids"]):
                raise ValueError(
                    f"regimes.buckets[{index}].metrics.trade_count must match "
                    f"regimes.buckets[{index}].trade_ids length"
                )
        validated_bucket["metrics"] = validated_metrics
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
        scenario_name = _canonical_report_string(
            scenario.get("name"),
            field_name=f"cost_stress.scenarios[{index}].scenario.name",
        )
        if "label" in scenario_payload:
            label = _canonical_report_string(
                scenario_payload["label"],
                field_name=f"cost_stress.scenarios[{index}].label",
            )
            if label != f"cost_stress:{scenario_name}":
                raise ValueError(
                    f"cost_stress.scenarios[{index}].label must match "
                    f"cost_stress.scenarios[{index}].scenario.name"
                )
            validated_scenario_payload["label"] = label
        if scenario_name in stress_scenario_names:
            raise ValueError("cost_stress.scenarios scenario.name values must be unique")
        stress_scenario_names.add(scenario_name)
        stress_scenarios.append(scenario_name)
        for metrics_field in ("base_metrics", "stressed_metrics"):
            metrics = scenario_payload.get(metrics_field)
            if not isinstance(metrics, Mapping):
                raise ValueError(f"cost_stress.scenarios[{index}].{metrics_field} must be an object")
            validated_metrics = _cost_stress_metric_payload(
                metrics,
                field_name=f"cost_stress.scenarios[{index}].{metrics_field}",
            )
            validated_scenario_payload[metrics_field] = validated_metrics
        if "stressed_trades" in scenario_payload:
            stressed_trades = _list_field(
                scenario_payload,
                "stressed_trades",
                label=f"cost_stress.scenarios[{index}].stressed_trades",
            )
            validated_stressed_trades = []
            for trade_index, trade_payload in enumerate(stressed_trades):
                if not isinstance(trade_payload, Mapping):
                    raise ValueError(
                        f"cost_stress.scenarios[{index}].stressed_trades[{trade_index}] must be an object"
                    )
                validated_trade_payload = _strict_mapping_copy(
                    trade_payload,
                    field_name=f"cost_stress.scenarios[{index}].stressed_trades[{trade_index}]",
                )
                for numeric_field in _COST_STRESS_TRADE_NUMERIC_FIELDS:
                    if numeric_field not in validated_trade_payload:
                        continue
                    validated_trade_payload[numeric_field] = _strict_present_finite_float(
                        validated_trade_payload[numeric_field],
                        field_name=(
                            f"cost_stress.scenarios[{index}].stressed_trades"
                            f"[{trade_index}].{numeric_field}"
                        ),
                    )
                validated_stressed_trades.append(validated_trade_payload)
            validated_scenario_payload["stressed_trades"] = validated_stressed_trades
        validated_cost_scenarios.append(validated_scenario_payload)
    validated_cost_scenarios.sort(key=_cost_stress_scenario_sort_key)
    stress_scenarios = [
        scenario_name
        for scenario_payload in validated_cost_scenarios
        if (scenario_name := _cost_stress_scenario_name(scenario_payload)) is not None
    ]
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
        "walk_forward": validated_walk_forward,
        "regimes": validated_regimes,
        "cost_stress": validated_cost_stress,
    }


def _cost_stress_scenario_name(payload: Mapping[str, Any]) -> str | None:
    scenario = payload.get("scenario", {})
    if not isinstance(scenario, Mapping) or "name" not in scenario:
        return None
    return scenario["name"]


def _cost_stress_scenario_sort_key(payload: Mapping[str, Any]) -> tuple[int, str]:
    scenario_name = _cost_stress_scenario_name(payload)
    if scenario_name is None:
        return (1, "")
    return (0, scenario_name)


def _evaluation_walk_forward_payload(walk_forward: Mapping[str, Any]) -> dict[str, Any]:
    validated = dict(walk_forward)
    if "windows" not in validated:
        return validated
    windows = _list_field(validated, "windows", label="walk_forward.windows")
    validated_windows = []
    previous_payload_window_index: int | None = None
    payload_window_indices: list[int] = []
    previous_period_boundaries: tuple[datetime, datetime, datetime, datetime] | None = None
    for window_index, window in enumerate(windows):
        if not isinstance(window, Mapping):
            raise ValueError(f"walk_forward.windows[{window_index}] must be an object")
        validated_window = _strict_mapping_copy(
            window,
            field_name=f"walk_forward.windows[{window_index}]",
        )
        if "window_index" in validated_window and validated_window["window_index"] is not None:
            payload_window_index = _positive_int_field(
                validated_window,
                "window_index",
                label=f"walk_forward.windows[{window_index}]",
            )
            if previous_payload_window_index is not None and payload_window_index <= previous_payload_window_index:
                raise ValueError("walk_forward.windows window_index values must be strictly increasing")
            previous_payload_window_index = payload_window_index
            payload_window_indices.append(payload_window_index)
            validated_window["window_index"] = payload_window_index
        period_boundaries = _validate_walk_forward_window_periods(validated_window, window_index=window_index)
        if period_boundaries is not None:
            if previous_period_boundaries is not None and period_boundaries < previous_period_boundaries:
                raise ValueError("walk_forward.windows temporal ranges must be strictly increasing")
            previous_period_boundaries = period_boundaries
        splits = validated_window.get("splits")
        if splits is None:
            validated_windows.append(validated_window)
            continue
        if not isinstance(splits, Mapping):
            raise ValueError(f"walk_forward.windows[{window_index}].splits must be an object")
        validated_splits = _strict_mapping_copy(
            splits,
            field_name=f"walk_forward.windows[{window_index}].splits",
        )
        if set(validated_splits) != {"in_sample", "out_of_sample"}:
            raise ValueError(f"walk_forward.windows[{window_index}].splits keys must be in_sample/out_of_sample")
        split_trade_ids: set[str] = set()
        for split_name, split_payload in list(validated_splits.items()):
            split_label = _canonical_report_string(
                split_name,
                field_name=f"walk_forward.windows[{window_index}].splits key",
            )
            if not isinstance(split_payload, Mapping):
                raise ValueError(f"walk_forward.windows[{window_index}].splits.{split_label} must be an object")
            validated_split = _strict_mapping_copy(
                split_payload,
                field_name=f"walk_forward.windows[{window_index}].splits.{split_label}",
            )
            if "label" in validated_split:
                label = _canonical_report_string(
                    validated_split["label"],
                    field_name=f"walk_forward.windows[{window_index}].splits.{split_label}.label",
                )
                expected_label = "IS" if split_label == "in_sample" else "OOS"
                if label != expected_label:
                    raise ValueError(
                        f"walk_forward.windows[{window_index}].splits.{split_label}.label must match split key"
                    )
                validated_split["label"] = label
            metrics = validated_split.get("metrics")
            if not isinstance(metrics, Mapping):
                raise ValueError(
                    f"walk_forward.windows[{window_index}].splits.{split_label}.metrics must be an object"
                )
            validated_metrics = _evaluation_metric_payload(
                metrics,
                field_name=f"walk_forward.windows[{window_index}].splits.{split_label}.metrics",
            )
            validated_split["metrics"] = validated_metrics
            if "trade_ids" in validated_split:
                trade_ids = _list_field(
                    validated_split,
                    "trade_ids",
                    label=f"walk_forward.windows[{window_index}].splits.{split_label}.trade_ids",
                )
                validated_trade_ids = [
                    _canonical_report_string(
                        trade_id,
                        field_name=(
                            f"walk_forward.windows[{window_index}].splits.{split_label}"
                            f".trade_ids[{trade_id_index}]"
                        ),
                    )
                    for trade_id_index, trade_id in enumerate(trade_ids)
                ]
                for trade_id in validated_trade_ids:
                    if trade_id in split_trade_ids:
                        raise ValueError(f"duplicate walk_forward.windows[{window_index}].split trade_id: {trade_id}")
                    split_trade_ids.add(trade_id)
                validated_split["trade_ids"] = validated_trade_ids
                metrics = validated_split.get("metrics")
                if isinstance(metrics, Mapping) and "trade_count" in metrics:
                    trade_count = _non_negative_int_field(
                        metrics,
                        "trade_count",
                        label=f"walk_forward.windows[{window_index}].splits.{split_label}.metrics",
                    )
                    if trade_count != len(validated_trade_ids):
                        raise ValueError(
                            f"walk_forward.windows[{window_index}].splits.{split_label}.metrics.trade_count "
                            f"must match walk_forward.windows[{window_index}].splits.{split_label}.trade_ids length"
                        )
                    validated_split["metrics"]["trade_count"] = trade_count
            validated_splits[split_label] = validated_split
        validated_window["splits"] = validated_splits
        validated_windows.append(validated_window)
    if payload_window_indices and payload_window_indices != list(range(1, len(windows) + 1)):
        raise ValueError("walk_forward.windows window_index values must be contiguous from 1")
    validated["windows"] = validated_windows
    return validated


def _validate_walk_forward_window_periods(
    window: dict[str, Any],
    *,
    window_index: int,
) -> tuple[datetime, datetime, datetime, datetime] | None:
    present_period_names = {period_name for period_name in ("train_period", "test_period") if period_name in window}
    if present_period_names and present_period_names != {"train_period", "test_period"}:
        missing_period_name = ({"train_period", "test_period"} - present_period_names).pop()
        raise ValueError(f"walk_forward.windows[{window_index}].{missing_period_name} must be present")
    parsed_periods: dict[str, dict[str, datetime]] = {}
    for period_name in ("train_period", "test_period"):
        if period_name not in window:
            continue
        raw_period = window[period_name]
        if not isinstance(raw_period, Mapping):
            raise ValueError(f"walk_forward.windows[{window_index}].{period_name} must be an object")
        period = _strict_mapping_copy(
            raw_period,
            field_name=f"walk_forward.windows[{window_index}].{period_name}",
        )
        for boundary in ("start", "end"):
            if boundary not in period:
                raise ValueError(f"walk_forward.windows[{window_index}].{period_name}.{boundary} must be present")
            period[boundary] = _canonical_report_string(
                period[boundary],
                field_name=f"walk_forward.windows[{window_index}].{period_name}.{boundary}",
            )
        parsed = {
            "start": _walk_forward_period_datetime(
                period["start"],
                field_name=f"walk_forward.windows[{window_index}].{period_name}.start",
            ),
            "end": _walk_forward_period_datetime(
                period["end"],
                field_name=f"walk_forward.windows[{window_index}].{period_name}.end",
            ),
        }
        if parsed["start"] > parsed["end"]:
            raise ValueError(
                f"walk_forward.windows[{window_index}].{period_name}.start must be on or before "
                f"walk_forward.windows[{window_index}].{period_name}.end"
            )
        window[period_name] = period
        parsed_periods[period_name] = parsed
    if {"train_period", "test_period"}.issubset(parsed_periods) and (
        parsed_periods["train_period"]["end"] >= parsed_periods["test_period"]["start"]
    ):
        raise ValueError(
            f"walk_forward.windows[{window_index}].train_period.end must be before "
            f"walk_forward.windows[{window_index}].test_period.start"
        )
    if {"train_period", "test_period"}.issubset(parsed_periods):
        return (
            parsed_periods["train_period"]["start"],
            parsed_periods["train_period"]["end"],
            parsed_periods["test_period"]["start"],
            parsed_periods["test_period"]["end"],
        )
    return None


def _walk_forward_period_datetime(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp string") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} must match datetime.isoformat()")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return parsed


def _evaluation_metric_payload(metrics: Mapping[str, Any], *, field_name: str) -> dict[str, float | int]:
    validated_metrics: dict[str, float | int] = {}
    for metric_name, metric_value in metrics.items():
        metric_key = _canonical_report_string(metric_name, field_name=f"{field_name} key")
        if metric_key == "trade_count":
            validated_metrics[metric_key] = _non_negative_int_field(
                {metric_key: metric_value},
                metric_key,
                label=field_name,
            )
        else:
            validated_metrics[metric_key] = _strict_present_finite_float(
                metric_value,
                field_name=f"{field_name}.{metric_key}",
            )
    return validated_metrics


def _strict_surface_finite_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a finite strict number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite strict number")
    return parsed


def _parameter_stability_surface_value_list(value: Any, *, field_name: str) -> list[float]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name} must be a non-empty list")
    values = [
        _strict_surface_finite_float(item, field_name=f"{field_name}[{index}]")
        for index, item in enumerate(value)
    ]
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must be unique")
    return values


def _parameter_stability_surface(
    value: Any,
    *,
    field_name: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name} must be a non-empty list")
    validated_surface: list[dict[str, Any]] = []
    parameter_names: set[str] = set()
    for index, raw_row in enumerate(value):
        row_name = f"{field_name}[{index}]"
        if not isinstance(raw_row, Mapping):
            raise ValueError(f"{row_name} must be an object")
        row = _strict_mapping_copy(raw_row, field_name=row_name)
        parameter_name = _canonical_report_string(row.get("parameter_name"), field_name=f"{row_name}.parameter_name")
        if parameter_name in parameter_names:
            raise ValueError(f"{field_name}.parameter_name values must be unique")
        parameter_names.add(parameter_name)
        tested_values = _parameter_stability_surface_value_list(
            row.get("tested_values"),
            field_name=f"{row_name}.tested_values",
        )
        raw_tested_range = row.get("tested_range")
        if not isinstance(raw_tested_range, Mapping):
            raise ValueError(f"{row_name}.tested_range must be an object")
        tested_range = _strict_mapping_copy(raw_tested_range, field_name=f"{row_name}.tested_range")
        range_min = _strict_surface_finite_float(tested_range.get("min"), field_name=f"{row_name}.tested_range.min")
        range_max = _strict_surface_finite_float(tested_range.get("max"), field_name=f"{row_name}.tested_range.max")
        if range_max < range_min:
            raise ValueError(f"{row_name}.tested_range.max must be >= min")
        raw_neighborhood_metrics = row.get("neighborhood_metrics")
        if not isinstance(raw_neighborhood_metrics, Mapping):
            raise ValueError(f"{row_name}.neighborhood_metrics must be an object")
        neighborhood_metrics = _strict_mapping_copy(
            raw_neighborhood_metrics,
            field_name=f"{row_name}.neighborhood_metrics",
        )
        validated_neighborhood_metrics: dict[str, float | int] = {}
        for metric_name, metric_value in neighborhood_metrics.items():
            metric_key = _canonical_report_string(metric_name, field_name=f"{row_name}.neighborhood_metrics key")
            if metric_key == "neighbor_count":
                validated_neighborhood_metrics[metric_key] = _positive_int_field(
                    neighborhood_metrics,
                    metric_key,
                    label=f"{row_name}.neighborhood_metrics",
                )
            else:
                validated_neighborhood_metrics[metric_key] = _strict_surface_finite_float(
                    metric_value,
                    field_name=f"{row_name}.neighborhood_metrics.{metric_key}",
                )
        if "neighbor_count" not in validated_neighborhood_metrics:
            raise ValueError(f"{row_name}.neighborhood_metrics.neighbor_count must be a positive integer")
        validated_surface.append(
            {
                "parameter_name": parameter_name,
                "tested_values": tested_values,
                "tested_range": {"min": range_min, "max": range_max},
                "neighborhood_metrics": validated_neighborhood_metrics,
            }
        )
    return validated_surface


def _parameter_stability_selected_optimum(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    optimum = _strict_mapping_copy(value, field_name=field_name)
    raw_parameters = optimum.get("parameters")
    if not isinstance(raw_parameters, Mapping) or not raw_parameters:
        raise ValueError(f"{field_name}.parameters must be a non-empty object")
    parameters: dict[str, float] = {}
    for parameter_name, parameter_value in raw_parameters.items():
        key = _canonical_report_string(parameter_name, field_name=f"{field_name}.parameters key")
        parameters[key] = _strict_surface_finite_float(
            parameter_value,
            field_name=f"{field_name}.parameters.{key}",
        )
    return {
        "parameters": parameters,
        "metric": _canonical_report_string(optimum.get("metric"), field_name=f"{field_name}.metric"),
        "value": _strict_surface_finite_float(optimum.get("value"), field_name=f"{field_name}.value"),
    }


def _parameter_stability_isolated_spike(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    spike = _strict_mapping_copy(value, field_name=field_name)
    is_isolated = spike.get("is_isolated")
    if not isinstance(is_isolated, bool):
        raise ValueError(f"{field_name}.is_isolated must be a bool")
    rejection_reason = spike.get("rejection_reason")
    if is_isolated:
        rejection_reason = _canonical_report_string(
            rejection_reason,
            field_name=f"{field_name}.rejection_reason",
        )
    elif rejection_reason is not None:
        rejection_reason = _canonical_report_string(
            rejection_reason,
            field_name=f"{field_name}.rejection_reason",
        )
    return {"is_isolated": is_isolated, "rejection_reason": rejection_reason}


def _parameter_stability_payload(parameter_stability: Mapping[str, Any], *, field_name: str) -> dict[str, Any]:
    validated = dict(parameter_stability)
    parameter_stability_score = _strict_bounded_ratio_float(
        validated.get("parameter_stability_score"),
        field_name=f"{field_name}.parameter_stability_score",
    )
    stability_score_threshold = _strict_bounded_ratio_float(
        validated.get("stability_score_threshold"),
        field_name=f"{field_name}.stability_score_threshold",
    )
    validated["parameter_stability_score"] = parameter_stability_score
    validated["stability_score_threshold"] = stability_score_threshold
    validated["selected_optimum"] = _parameter_stability_selected_optimum(
        validated.get("selected_optimum"),
        field_name=f"{field_name}.selected_optimum",
    )
    validated["stability_surface"] = _parameter_stability_surface(
        validated.get("stability_surface"),
        field_name=f"{field_name}.stability_surface",
    )
    validated["isolated_spike"] = _parameter_stability_isolated_spike(
        validated.get("isolated_spike"),
        field_name=f"{field_name}.isolated_spike",
    )
    return validated


def _regime_stratified_oos_metric(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a finite strict number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite strict number")
    return parsed


def _regime_stratified_oos_evidence(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be present for positive OOS evidence")
    evidence = _strict_mapping_copy(value, field_name=field_name)
    if evidence.get("schema_version") != "regime_stratified_oos.v1":
        raise ValueError(f"{field_name}.schema_version must be regime_stratified_oos.v1")
    required_buckets = _list_field(evidence, "required_buckets", label=f"{field_name}.required_buckets")
    normalized_required = [
        _canonical_report_string(bucket, field_name=f"{field_name}.required_buckets[{index}]")
        for index, bucket in enumerate(required_buckets)
    ]
    if set(normalized_required) != set(_REQUIRED_REGIME_STRATIFIED_OOS_BUCKETS):
        raise ValueError(
            f"{field_name}.required_buckets must include "
            f"{', '.join(_REQUIRED_REGIME_STRATIFIED_OOS_BUCKETS)}"
        )
    raw_buckets = _list_field(evidence, "buckets", label=f"{field_name}.buckets")
    if not raw_buckets:
        raise ValueError(f"{field_name}.buckets must be a non-empty list")
    seen_buckets: set[str] = set()
    collapsed_buckets: list[str] = []
    validated_buckets: list[dict[str, Any]] = []
    for index, raw_bucket in enumerate(raw_buckets):
        bucket_field = f"{field_name}.buckets[{index}]"
        if not isinstance(raw_bucket, Mapping):
            raise ValueError(f"{bucket_field} must be an object")
        bucket = _strict_mapping_copy(raw_bucket, field_name=bucket_field)
        bucket_name = _canonical_report_string(bucket.get("bucket"), field_name=f"{bucket_field}.bucket")
        if bucket_name in seen_buckets:
            raise ValueError(f"{field_name}.buckets bucket values must be unique")
        seen_buckets.add(bucket_name)
        raw_metrics = bucket.get("metrics")
        if not isinstance(raw_metrics, Mapping):
            raise ValueError(f"{bucket_field}.metrics must be an object")
        metrics = _strict_mapping_copy(raw_metrics, field_name=f"{bucket_field}.metrics")
        validated_metrics: dict[str, Any] = {}
        for metric_name in _REGIME_STRATIFIED_OOS_NUMERIC_METRICS:
            validated_metrics[metric_name] = _regime_stratified_oos_metric(
                metrics.get(metric_name),
                field_name=f"{bucket_field}.metrics.{metric_name}",
            )
        validated_metrics["trade_count"] = _non_negative_int_field(
            metrics,
            "trade_count",
            label=f"{bucket_field}.metrics",
        )
        if validated_metrics["total_return"] < 0.0:
            collapsed_buckets.append(bucket_name)
        validated_buckets.append({"bucket": bucket_name, "metrics": validated_metrics})
    for required_bucket in _REQUIRED_REGIME_STRATIFIED_OOS_BUCKETS:
        if required_bucket not in seen_buckets:
            raise ValueError(f"{field_name}.buckets must include required bucket {required_bucket}")
    return {
        "schema_version": "regime_stratified_oos.v1",
        "required_buckets": list(_REQUIRED_REGIME_STRATIFIED_OOS_BUCKETS),
        "buckets": validated_buckets,
        "collapsed_buckets": collapsed_buckets,
    }


def _portfolio_correlation_exposure_evidence(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be present for positive OOS evidence")
    evidence = _strict_mapping_copy(value, field_name=field_name)
    if evidence.get("schema_version") != _PORTFOLIO_CORRELATION_EXPOSURE_SCHEMA_VERSION:
        raise ValueError(
            f"{field_name}.schema_version must be {_PORTFOLIO_CORRELATION_EXPOSURE_SCHEMA_VERSION}"
        )
    as_of = _canonical_utc_report_timestamp(
        _canonical_report_string(evidence.get("as_of"), field_name=f"{field_name}.as_of"),
        field_name=f"{field_name}.as_of",
    )
    decision_timestamp = _canonical_utc_report_timestamp(
        _canonical_report_string(
            evidence.get("decision_timestamp"),
            field_name=f"{field_name}.decision_timestamp",
        ),
        field_name=f"{field_name}.decision_timestamp",
    )
    parsed_as_of = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    parsed_decision_timestamp = datetime.fromisoformat(decision_timestamp.replace("Z", "+00:00"))
    if parsed_as_of > parsed_decision_timestamp:
        raise ValueError(f"{field_name}.as_of must be at or before decision_timestamp")
    max_age_seconds = _non_negative_int_field(evidence, "max_age_seconds", label=field_name)
    if (parsed_decision_timestamp - parsed_as_of).total_seconds() > max_age_seconds:
        raise ValueError(f"{field_name}.as_of must not be stale")

    limits = _mapping_field(evidence, "limits")
    limit_fields = (
        "max_net_exposure_pct",
        "max_gross_exposure_pct",
        "max_symbol_gross_exposure_pct",
        "max_cluster_gross_exposure_pct",
        "max_pairwise_correlation",
        "max_crowded_risk_score",
    )
    validated_limits = {
        key: _strict_non_negative_finite_float(limits.get(key), field_name=f"{field_name}.limits.{key}")
        for key in limit_fields
    }
    portfolio = _mapping_field(evidence, "portfolio")
    validated_portfolio = {
        "net_exposure_pct": _strict_present_finite_float(
            portfolio.get("net_exposure_pct"),
            field_name=f"{field_name}.portfolio.net_exposure_pct",
        ),
        "gross_exposure_pct": _strict_non_negative_finite_float(
            portfolio.get("gross_exposure_pct"),
            field_name=f"{field_name}.portfolio.gross_exposure_pct",
        ),
    }

    breaches: list[str] = []
    if abs(validated_portfolio["net_exposure_pct"]) > validated_limits["max_net_exposure_pct"]:
        breaches.append("portfolio net exposure exceeds configured limit")
    if validated_portfolio["gross_exposure_pct"] > validated_limits["max_gross_exposure_pct"]:
        breaches.append("portfolio gross exposure exceeds configured limit")

    seen_symbols: set[str] = set()
    symbols: list[dict[str, Any]] = []
    for index, raw_symbol in enumerate(_list_field(evidence, "symbols", label=f"{field_name}.symbols")):
        row_field = f"{field_name}.symbols[{index}]"
        if not isinstance(raw_symbol, Mapping):
            raise ValueError(f"{row_field} must be an object")
        row = _strict_mapping_copy(raw_symbol, field_name=row_field)
        symbol = _canonical_bucket_identity(row.get("symbol"), field_name=f"{row_field}.symbol")
        if symbol in seen_symbols:
            raise ValueError(f"{field_name}.symbols symbol values must be unique")
        seen_symbols.add(symbol)
        cluster = _canonical_bucket_identity(row.get("cluster"), field_name=f"{row_field}.cluster")
        gross = _strict_non_negative_finite_float(
            row.get("gross_exposure_pct"),
            field_name=f"{row_field}.gross_exposure_pct",
        )
        net = _strict_present_finite_float(row.get("net_exposure_pct"), field_name=f"{row_field}.net_exposure_pct")
        if gross > validated_limits["max_symbol_gross_exposure_pct"]:
            breaches.append(f"{symbol} gross exposure exceeds configured limit")
        symbols.append({"symbol": symbol, "cluster": cluster, "gross_exposure_pct": gross, "net_exposure_pct": net})

    seen_clusters: set[str] = set()
    clusters: list[dict[str, Any]] = []
    for index, raw_cluster in enumerate(_list_field(evidence, "clusters", label=f"{field_name}.clusters")):
        row_field = f"{field_name}.clusters[{index}]"
        if not isinstance(raw_cluster, Mapping):
            raise ValueError(f"{row_field} must be an object")
        row = _strict_mapping_copy(raw_cluster, field_name=row_field)
        cluster = _canonical_bucket_identity(row.get("cluster"), field_name=f"{row_field}.cluster")
        if cluster in seen_clusters:
            raise ValueError(f"{field_name}.clusters cluster values must be unique")
        seen_clusters.add(cluster)
        gross = _strict_non_negative_finite_float(
            row.get("gross_exposure_pct"),
            field_name=f"{row_field}.gross_exposure_pct",
        )
        net = _strict_present_finite_float(row.get("net_exposure_pct"), field_name=f"{row_field}.net_exposure_pct")
        if gross > validated_limits["max_cluster_gross_exposure_pct"]:
            breaches.append(f"{cluster} cluster gross exposure exceeds configured limit")
        clusters.append({"cluster": cluster, "gross_exposure_pct": gross, "net_exposure_pct": net})

    correlations: list[dict[str, Any]] = []
    for index, raw_correlation in enumerate(
        _list_field(evidence, "correlations", default=[], label=f"{field_name}.correlations")
    ):
        row_field = f"{field_name}.correlations[{index}]"
        if not isinstance(raw_correlation, Mapping):
            raise ValueError(f"{row_field} must be an object")
        row = _strict_mapping_copy(raw_correlation, field_name=row_field)
        left = _canonical_bucket_identity(row.get("left_symbol"), field_name=f"{row_field}.left_symbol")
        right = _canonical_bucket_identity(row.get("right_symbol"), field_name=f"{row_field}.right_symbol")
        if left == right:
            raise ValueError(f"{row_field}.left_symbol and right_symbol must differ")
        correlation = _strict_present_finite_float(row.get("correlation"), field_name=f"{row_field}.correlation")
        if abs(correlation) > validated_limits["max_pairwise_correlation"]:
            breaches.append(f"{left}/{right} pairwise correlation exceeds configured limit")
        correlations.append({"left_symbol": left, "right_symbol": right, "correlation": correlation})

    crowded = _mapping_field(evidence, "crowded_risk")
    crowded_score = _strict_non_negative_finite_float(
        crowded.get("score"),
        field_name=f"{field_name}.crowded_risk.score",
    )
    if crowded_score > validated_limits["max_crowded_risk_score"]:
        breaches.append("crowded risk score exceeds configured limit")
    crowded_evidence = _list_field(crowded, "evidence", label=f"{field_name}.crowded_risk.evidence")
    if not crowded_evidence:
        raise ValueError(f"{field_name}.crowded_risk.evidence must be a non-empty list")
    result = {
        "schema_version": _PORTFOLIO_CORRELATION_EXPOSURE_SCHEMA_VERSION,
        "as_of": as_of,
        "decision_timestamp": decision_timestamp,
        "max_age_seconds": max_age_seconds,
        "limits": validated_limits,
        "portfolio": validated_portfolio,
        "symbols": symbols,
        "clusters": clusters,
        "correlations": correlations,
        "crowded_risk": {
            "score": crowded_score,
            "evidence": [
                _canonical_bucket_identity(item, field_name=f"{field_name}.crowded_risk.evidence[{index}]")
                for index, item in enumerate(crowded_evidence)
            ],
        },
        "breaches": breaches,
    }
    if "risk_hold" in evidence:
        risk_hold = _mapping_field(evidence, "risk_hold")
        if risk_hold.get("active") is not True:
            raise ValueError(f"{field_name}.risk_hold.active must be true")
        result["risk_hold"] = {
            "active": True,
            "reason": _canonical_bucket_identity(risk_hold.get("reason"), field_name=f"{field_name}.risk_hold.reason"),
        }
    return result


def _drawdown_anatomy_finite_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a finite strict number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite strict number")
    return parsed


def _drawdown_anatomy_non_negative_float(value: Any, *, field_name: str) -> float:
    parsed = _drawdown_anatomy_finite_float(value, field_name=field_name)
    if parsed < 0.0:
        raise ValueError(f"{field_name} must be a non-negative finite strict number")
    return parsed


def _drawdown_anatomy_timestamp(value: Any, *, field_name: str) -> tuple[str, datetime]:
    timestamp = _canonical_utc_report_timestamp(
        _canonical_report_string(value, field_name=field_name),
        field_name=field_name,
    )
    return timestamp, datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def _drawdown_anatomy_evidence(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be present for positive OOS evidence")
    evidence = _strict_mapping_copy(value, field_name=field_name)
    if evidence.get("schema_version") != _DRAWDOWN_ANATOMY_SCHEMA_VERSION:
        raise ValueError(f"{field_name}.schema_version must be {_DRAWDOWN_ANATOMY_SCHEMA_VERSION}")
    as_of, parsed_as_of = _drawdown_anatomy_timestamp(evidence.get("as_of"), field_name=f"{field_name}.as_of")
    decision_timestamp, parsed_decision_timestamp = _drawdown_anatomy_timestamp(
        evidence.get("decision_timestamp"),
        field_name=f"{field_name}.decision_timestamp",
    )
    if parsed_as_of > parsed_decision_timestamp:
        raise ValueError(f"{field_name}.as_of must be at or before decision_timestamp")
    max_age_seconds = _non_negative_int_field(evidence, "max_age_seconds", label=field_name)
    if (parsed_decision_timestamp - parsed_as_of).total_seconds() > max_age_seconds:
        raise ValueError(f"{field_name}.as_of must not be stale")
    severe_threshold = _drawdown_anatomy_non_negative_float(
        evidence.get("severe_drawdown_threshold_pct"),
        field_name=f"{field_name}.severe_drawdown_threshold_pct",
    )
    raw_drawdowns = _list_field(evidence, "drawdowns", label=f"{field_name}.drawdowns")
    if not raw_drawdowns:
        raise ValueError(f"{field_name}.drawdowns must be a non-empty list")
    seen_cluster_ids: set[tuple[str, str, str]] = set()
    drawdowns: list[dict[str, Any]] = []
    for index, raw_drawdown in enumerate(raw_drawdowns):
        row_field = f"{field_name}.drawdowns[{index}]"
        if not isinstance(raw_drawdown, Mapping):
            raise ValueError(f"{row_field} must be an object")
        row = _strict_mapping_copy(raw_drawdown, field_name=row_field)
        drawdown_id = _canonical_bucket_identity(row.get("drawdown_id"), field_name=f"{row_field}.drawdown_id")
        severity_pct = _drawdown_anatomy_non_negative_float(
            row.get("severity_pct"),
            field_name=f"{row_field}.severity_pct",
        )
        peak_timestamp, parsed_peak = _drawdown_anatomy_timestamp(
            row.get("peak_timestamp"),
            field_name=f"{row_field}.peak_timestamp",
        )
        trough_timestamp, parsed_trough = _drawdown_anatomy_timestamp(
            row.get("trough_timestamp"),
            field_name=f"{row_field}.trough_timestamp",
        )
        recovery_timestamp, parsed_recovery = _drawdown_anatomy_timestamp(
            row.get("recovery_timestamp"),
            field_name=f"{row_field}.recovery_timestamp",
        )
        if parsed_peak > parsed_trough:
            raise ValueError(f"{row_field}.peak_timestamp must be at or before trough_timestamp")
        if parsed_trough > parsed_recovery:
            raise ValueError(f"{row_field}.trough_timestamp must be at or before recovery_timestamp")
        regime_cluster_id = _canonical_bucket_identity(
            row.get("regime_cluster_id"),
            field_name=f"{row_field}.regime_cluster_id",
        )
        symbol_cluster_id = _canonical_bucket_identity(
            row.get("symbol_cluster_id"),
            field_name=f"{row_field}.symbol_cluster_id",
        )
        trade_cluster_id = _canonical_bucket_identity(
            row.get("trade_cluster_id"),
            field_name=f"{row_field}.trade_cluster_id",
        )
        cluster_key = (regime_cluster_id, symbol_cluster_id, trade_cluster_id)
        if cluster_key in seen_cluster_ids:
            raise ValueError(f"{field_name}.drawdowns cluster ids must be unique")
        seen_cluster_ids.add(cluster_key)
        raw_attribution = row.get("attribution")
        if not isinstance(raw_attribution, Mapping):
            raise ValueError(f"{row_field}.attribution must be an object")
        attribution = _strict_mapping_copy(raw_attribution, field_name=f"{row_field}.attribution")
        normalized_attribution = {
            "edge_failure_pct": _drawdown_anatomy_non_negative_float(
                attribution.get("edge_failure_pct"),
                field_name=f"{row_field}.attribution.edge_failure_pct",
            ),
            "execution_failure_pct": _drawdown_anatomy_non_negative_float(
                attribution.get("execution_failure_pct"),
                field_name=f"{row_field}.attribution.execution_failure_pct",
            ),
            "risk_control_failure_pct": _drawdown_anatomy_non_negative_float(
                attribution.get("risk_control_failure_pct"),
                field_name=f"{row_field}.attribution.risk_control_failure_pct",
            ),
            "primary_failure": _canonical_bucket_identity(
                attribution.get("primary_failure"),
                field_name=f"{row_field}.attribution.primary_failure",
            ),
        }
        if severity_pct >= severe_threshold and normalized_attribution["primary_failure"] not in _DRAWDOWN_FAILURE_TYPES:
            raise ValueError(f"{row_field}.attribution.primary_failure must explain severe drawdown")
        raw_exposure = row.get("exposure_concentration")
        if not isinstance(raw_exposure, Mapping):
            raise ValueError(f"{row_field}.exposure_concentration must be an object")
        exposure = _strict_mapping_copy(raw_exposure, field_name=f"{row_field}.exposure_concentration")
        normalized_exposure = {
            "max_symbol_exposure_pct": _drawdown_anatomy_non_negative_float(
                exposure.get("max_symbol_exposure_pct"),
                field_name=f"{row_field}.exposure_concentration.max_symbol_exposure_pct",
            ),
            "max_cluster_exposure_pct": _drawdown_anatomy_non_negative_float(
                exposure.get("max_cluster_exposure_pct"),
                field_name=f"{row_field}.exposure_concentration.max_cluster_exposure_pct",
            ),
            "crowded_risk_score": _drawdown_anatomy_non_negative_float(
                exposure.get("crowded_risk_score"),
                field_name=f"{row_field}.exposure_concentration.crowded_risk_score",
            ),
        }
        mitigation_evidence = [
            _canonical_bucket_identity(item, field_name=f"{row_field}.mitigation_evidence[{item_index}]")
            for item_index, item in enumerate(
                _list_field(row, "mitigation_evidence", label=f"{row_field}.mitigation_evidence")
            )
        ]
        if severity_pct >= severe_threshold and not mitigation_evidence:
            raise ValueError(f"{row_field} severe drawdown must include mitigation evidence")
        drawdowns.append(
            {
                "drawdown_id": drawdown_id,
                "severity_pct": severity_pct,
                "peak_timestamp": peak_timestamp,
                "trough_timestamp": trough_timestamp,
                "recovery_timestamp": recovery_timestamp,
                "regime_cluster_id": regime_cluster_id,
                "symbol_cluster_id": symbol_cluster_id,
                "trade_cluster_id": trade_cluster_id,
                "attribution": normalized_attribution,
                "exposure_concentration": normalized_exposure,
                "mitigation_evidence": mitigation_evidence,
            }
        )
    return {
        "schema_version": _DRAWDOWN_ANATOMY_SCHEMA_VERSION,
        "as_of": as_of,
        "decision_timestamp": decision_timestamp,
        "max_age_seconds": max_age_seconds,
        "severe_drawdown_threshold_pct": severe_threshold,
        "drawdowns": drawdowns,
    }


def _cost_stress_metric_payload(metrics: Mapping[str, Any], *, field_name: str) -> dict[str, Any]:
    validated_metrics: dict[str, Any] = {}
    for metric_name, metric_value in metrics.items():
        metric_key = _canonical_report_string(metric_name, field_name=f"{field_name} key")
        validated_metrics[metric_key] = _cost_stress_metric_value(
            metric_value,
            field_name=f"{field_name}.{metric_key}",
        )
    return validated_metrics


def _cost_stress_metric_value(value: Any, *, field_name: str) -> Any:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must not be a boolean")
    if isinstance(value, Mapping):
        return _cost_stress_metric_payload(value, field_name=field_name)
    if isinstance(value, list):
        return [
            _cost_stress_metric_value(item, field_name=f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    return _strict_present_finite_float(value, field_name=field_name)


def _trade_breakdown_rows(
    trade_ledger: tuple[TradeLedgerRow, ...],
    *,
    key_name: str,
    key_fn: Callable[[TradeLedgerRow], Any],
    canonical_string_key: bool = False,
    optional_empty_string_key: bool = False,
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, float | int | str]] = {}
    for index, row in enumerate(trade_ledger):
        raw_bucket_key = key_fn(row)
        if optional_empty_string_key:
            bucket_key = _canonical_optional_empty_report_string(
                raw_bucket_key,
                field_name=f"trades[{index}].{key_name}",
            )
        elif canonical_string_key:
            bucket_key = _canonical_report_string(raw_bucket_key, field_name=f"trades[{index}].{key_name}")
        else:
            bucket_key = str(raw_bucket_key)
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
        bucket["gross_pnl"] = float(bucket["gross_pnl"]) + _trade_finite_float(
            row.gross_pnl,
            field_name=f"trades[{index}].gross_pnl",
        )
        bucket["net_pnl"] = float(bucket["net_pnl"]) + _trade_finite_float(
            row.net_pnl,
            field_name=f"trades[{index}].net_pnl",
        )
        bucket["fees"] = float(bucket["fees"]) + _trade_finite_float(
            row.fee_paid,
            field_name=f"trades[{index}].fee_paid",
        )
        bucket["slippage"] = float(bucket["slippage"]) + _trade_finite_float(
            row.slippage_paid,
            field_name=f"trades[{index}].slippage_paid",
        )
        bucket["funding"] = float(bucket["funding"]) + _trade_finite_float(
            row.funding_paid,
            field_name=f"trades[{index}].funding_paid",
        )
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
            "entry_price": _trade_finite_float(row.entry_price, field_name=f"trades[{index}].entry_price"),
            "exit_price": _trade_finite_float(row.exit_price, field_name=f"trades[{index}].exit_price"),
            "qty": _trade_finite_float(row.qty, field_name=f"trades[{index}].qty"),
            "position_notional": _trade_finite_float(
                row.position_notional,
                field_name=f"trades[{index}].position_notional",
            ),
            "gross_pnl": _trade_finite_float(row.gross_pnl, field_name=f"trades[{index}].gross_pnl"),
            "net_pnl": _trade_finite_float(row.net_pnl, field_name=f"trades[{index}].net_pnl"),
            "fee_paid": _trade_finite_float(row.fee_paid, field_name=f"trades[{index}].fee_paid"),
            "slippage_paid": _trade_finite_float(
                row.slippage_paid,
                field_name=f"trades[{index}].slippage_paid",
            ),
            "funding_paid": _trade_finite_float(row.funding_paid, field_name=f"trades[{index}].funding_paid"),
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
            "execution_price_source": _validated_execution_price_source(
                row.execution_price_source,
                fill_model=row.fill_model,
                source_field_name=f"trades[{index}].execution_price_source",
                fill_model_field_name=f"trades[{index}].fill_model",
            ),
            "fill_model": _canonical_execution_fill_model(
                row.fill_model,
                field_name=f"trades[{index}].fill_model",
            ),
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
            "first_fill_timestamp": _validated_first_fill_timestamp(
                row.first_fill_timestamp,
                row.last_fill_timestamp,
                field_name=f"trades[{index}].first_fill_timestamp",
            ).isoformat()
            if row.first_fill_timestamp is not None
            else None,
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
            "fee_provenance": _report_cost_input_provenance(
                row.fee_provenance,
                field_name=f"trades[{index}].fee_provenance",
            ),
            "funding_provenance": _report_cost_input_provenance(
                row.funding_provenance,
                field_name=f"trades[{index}].funding_provenance",
            ),
            "open_interest_usdt": row.open_interest_usdt,
            "open_interest_timestamp": row.open_interest_timestamp.isoformat() if row.open_interest_timestamp is not None else None,
            "open_interest_age_seconds": row.open_interest_age_seconds,
            **_margin_liquidation_path_payload(row, index=index),
            "requested_quantity": row.requested_quantity,
            "requested_notional": row.requested_notional,
            "filled_quantity": row.filled_quantity,
            "filled_notional": row.filled_notional,
            "unfilled_quantity": row.unfilled_quantity,
            "unfilled_notional": row.unfilled_notional,
            "depth_levels_consumed": row.depth_levels_consumed,
            "execution_impact_bps": _optional_non_negative_finite_float(
                row.execution_impact_bps,
                field_name=f"trades[{index}].execution_impact_bps",
            ),
            "slippage_bps": _optional_finite_float(
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
            "by_symbol": _trade_breakdown_rows(
                result.trade_ledger,
                key_name="symbol",
                key_fn=lambda row: row.symbol,
                canonical_string_key=True,
            ),
            "by_setup_type": _trade_breakdown_rows(
                result.trade_ledger,
                key_name="setup_type",
                key_fn=lambda row: row.setup_type,
                optional_empty_string_key=True,
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
    report_metadata = _report_metadata_copy(metadata)
    return {
        "experiment_name": experiment_name,
        "dataset_root": report_metadata.get("dataset_root"),
        "baseline_name": report_metadata.get("baseline_name"),
        "variant_name": report_metadata.get("variant_name"),
        "sample_period": report_metadata.get("sample_period"),
        "evaluation_window": report_metadata.get("evaluation_window"),
    }


_REPORT_METADATA_IDENTIFIER_FIELDS = frozenset(
    {
        "dataset_root",
        "baseline_name",
        "variant_name",
        "evaluation_window",
        "schema_version",
    }
)

_REQUIRED_UNIVERSE_ASOF_LIFECYCLE_FIELDS = frozenset(
    {"lifecycle_status", "delisted_at", "previous_symbol", "renamed_at", "contract_migration"}
)


def _report_metadata_copy(metadata: Mapping[str, Any]) -> dict[str, Any]:
    copied = _strict_mapping_copy(metadata, field_name="metadata")
    for field in _REPORT_METADATA_IDENTIFIER_FIELDS:
        if field in copied and copied[field] is not None:
            copied[field] = _canonical_report_string(copied[field], field_name=f"metadata.{field}")
    if "sample_period" in copied and copied["sample_period"] is not None:
        copied["sample_period"] = _report_sample_period(copied["sample_period"])
    if "raw_market" in copied and copied["raw_market"] is not None:
        copied["raw_market"] = _report_raw_market_provenance(copied["raw_market"])
    if "universe_asof_contract" in copied and copied["universe_asof_contract"] is not None:
        copied["universe_asof_contract"] = _report_universe_asof_contract(copied["universe_asof_contract"])
    if "split_metadata" in copied and copied["split_metadata"] is not None:
        copied["split_metadata"] = _walk_forward_split_metadata(
            copied["split_metadata"],
            field_name="metadata.split_metadata",
        )
    return copied


def _report_cost_input_provenance(value: object, *, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    provenance = _strict_mapping_copy(value, field_name=field_name)
    for field in (
        "schema_version",
        "kind",
        "account_id",
        "venue",
        "symbol",
        "side",
        "timeframe",
        "tier",
        "effective_at",
        "as_of",
        "observed_at",
    ):
        if field not in provenance:
            raise ValueError(f"{field_name}.{field} must be present")
        provenance[field] = _canonical_report_string(provenance[field], field_name=f"{field_name}.{field}")
    provenance["rate"] = _report_finite_float(provenance.get("rate"), field_name=f"{field_name}.rate")
    return provenance


def _walk_forward_split_metadata(value: object, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    metadata = _strict_mapping_copy(value, field_name=field_name)
    schema_version = metadata.get("schema_version")
    if schema_version != "walk_forward_split_metadata.v1":
        raise ValueError(f"{field_name}.schema_version must be walk_forward_split_metadata.v1")
    for key in ("purge_bars", "embargo_bars"):
        metadata[key] = _non_negative_int_field(metadata, key, label=field_name)
    for key in ("timestamp_format", "trade_timestamp_basis", "boundary_policy"):
        if key in metadata and metadata[key] is not None:
            metadata[key] = _canonical_report_string(metadata[key], field_name=f"{field_name}.{key}")
    return metadata


def _walk_forward_window_split_metadata(value: object, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    metadata = _strict_mapping_copy(value, field_name=field_name)
    if "schema_version" in metadata and metadata["schema_version"] is not None:
        metadata["schema_version"] = _canonical_report_string(
            metadata["schema_version"],
            field_name=f"{field_name}.schema_version",
        )
    for key in ("purge_bars", "embargo_bars"):
        if key in metadata and metadata[key] is not None:
            metadata[key] = _non_negative_int_field(metadata, key, label=field_name)
    train_run_ids = _canonical_report_string_list(
        metadata.get("train_run_ids", []),
        field_name=f"{field_name}.train_run_ids",
    )
    test_run_ids = _canonical_report_string_list(
        metadata.get("test_run_ids", []),
        field_name=f"{field_name}.test_run_ids",
    )
    if set(train_run_ids) & set(test_run_ids):
        raise ValueError(f"{field_name} train/test run_ids must be disjoint")
    metadata["train_run_ids"] = train_run_ids
    metadata["test_run_ids"] = test_run_ids
    return metadata


def _report_universe_asof_contract(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("metadata.universe_asof_contract must be an object")
    contract = _strict_mapping_copy(value, field_name="metadata.universe_asof_contract")
    if contract.get("schema_version") != "universe_asof_contract.v1":
        raise ValueError("metadata.universe_asof_contract.schema_version must be universe_asof_contract.v1")
    membership_source = _canonical_report_string(
        contract.get("membership_source"),
        field_name="metadata.universe_asof_contract.membership_source",
    )
    if membership_source == "current_universe_snapshot":
        raise ValueError("metadata.universe_asof_contract.membership_source must not be current_universe_snapshot")
    contract["membership_source"] = membership_source
    expected_strings = {
        "as_of_field": "instrument_snapshot.as_of",
        "decision_timestamp_field": "metadata.timestamp",
    }
    for field_name, expected in expected_strings.items():
        if contract.get(field_name) != expected:
            raise ValueError(f"metadata.universe_asof_contract.{field_name} must be {expected}")
    fields = contract.get("required_lifecycle_fields")
    if not isinstance(fields, list):
        raise ValueError("metadata.universe_asof_contract.required_lifecycle_fields must be a list")
    normalized = [
        _canonical_report_string(
            field,
            field_name=f"metadata.universe_asof_contract.required_lifecycle_fields[{index}]",
        )
        for index, field in enumerate(fields)
    ]
    if not _REQUIRED_UNIVERSE_ASOF_LIFECYCLE_FIELDS.issubset(set(normalized)):
        raise ValueError("metadata.universe_asof_contract.required_lifecycle_fields must include lifecycle evidence")
    contract["required_lifecycle_fields"] = normalized
    for field_name in ("supports_delisted", "supports_renames", "supports_contract_migrations"):
        if contract.get(field_name) is not True:
            raise ValueError(f"metadata.universe_asof_contract.{field_name} must be true")
    return contract


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


def _report_raw_market_provenance(value: object, *, field_name: str = "metadata.raw_market") -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    provenance = _strict_mapping_copy(value, field_name=field_name)
    for field in _RAW_MARKET_PROVENANCE_IDENTITY_FIELDS:
        if field not in provenance:
            raise ValueError(f"{field_name}.{field} must be present")
        provenance[field] = _canonical_report_string(
            provenance[field],
            field_name=f"{field_name}.{field}",
        )
    for field in _RAW_MARKET_PROVENANCE_TIMESTAMP_FIELDS:
        _canonical_utc_report_timestamp(
            provenance[field],
            field_name=f"{field_name}.{field}",
        )
    return provenance


def _raw_market_provenance_identity(provenance: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    return tuple(provenance[field] for field in _RAW_MARKET_PROVENANCE_IDENTITY_FIELDS)


def _canonical_utc_report_timestamp(value: str, *, field_name: str) -> str:
    if not _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a canonical UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a canonical UTC Z timestamp") from exc
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise ValueError(f"{field_name} must be a canonical UTC Z timestamp")
    return value


def _decision_summary(*, decision: str, summary: str) -> dict[str, str]:
    return {"decision": decision, "summary": summary}


def _strict_multiple_testing_correction(payload: object, *, expected_trials: int | None = None) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("multiple_testing_correction must be present")
    correction = _strict_mapping_copy(payload, field_name="multiple_testing_correction")
    if correction.get("schema_version") != "multiple_testing_correction.v1":
        raise ValueError("multiple_testing_correction.schema_version must be multiple_testing_correction.v1")
    if "number_of_trials" not in correction:
        raise ValueError("multiple_testing_correction.number_of_trials must be present")
    number_of_trials = correction["number_of_trials"]
    if isinstance(number_of_trials, bool) or not isinstance(number_of_trials, int) or number_of_trials <= 1:
        raise ValueError("multiple_testing_correction.number_of_trials must be an integer greater than one")
    if expected_trials is not None and number_of_trials != expected_trials:
        raise ValueError("multiple_testing_correction.number_of_trials must match candidate count")
    correction_method = _canonical_report_string(
        correction.get("correction_method"),
        field_name="multiple_testing_correction.correction_method",
    )
    correction["number_of_trials"] = number_of_trials
    correction["correction_method"] = correction_method

    evidence_fields = ("corrected_p_value", "corrected_q_value", "adjusted_threshold", "conservative_threshold")
    present_evidence_fields = [field for field in evidence_fields if field in correction and correction[field] is not None]
    if not present_evidence_fields:
        raise ValueError("multiple_testing_correction must include corrected evidence")
    for field in present_evidence_fields:
        correction[field] = _strict_present_finite_float(
            correction[field],
            field_name=f"multiple_testing_correction.{field}",
        )
    if "adjusted_pass" not in correction or not isinstance(correction["adjusted_pass"], bool):
        raise ValueError("multiple_testing_correction.adjusted_pass must be a bool")
    return correction


def _multiple_testing_decision_allowed(correction: Mapping[str, Any]) -> bool:
    return correction.get("adjusted_pass") is True



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
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a canonical string")
    if not value or value.strip() != value:
        raise ValueError(f"{field_name} must be a canonical string")
    return value


def _canonical_bucket_identity(value: object, *, field_name: str) -> str:
    if type(value) is not str or not value or value.strip() != value or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError(f"{field_name} must be canonical")
    return value


def _optional_canonical_report_string(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _canonical_report_string(value, field_name=field_name)


def _optional_supported_report_string(value: object, *, field_name: str, allowed: frozenset[str]) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value or value.strip() != value or value not in allowed:
        raise ValueError(f"{field_name} must be a supported canonical string")
    return value


def _canonical_execution_fill_model(value: object, *, field_name: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError(f"{field_name} must be a supported canonical string")
    if value not in _EXECUTION_PRICE_SOURCES_BY_FILL_MODEL:
        raise ValueError(f"{field_name} must be a supported canonical string")
    return value


def _validated_execution_price_source(
    value: object,
    *,
    fill_model: object,
    source_field_name: str,
    fill_model_field_name: str,
) -> str:
    canonical_fill_model = _canonical_execution_fill_model(fill_model, field_name=fill_model_field_name)
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError(f"{source_field_name} must be a supported canonical string")
    if value not in _EXECUTION_PRICE_SOURCES_BY_FILL_MODEL[canonical_fill_model]:
        raise ValueError(f"{source_field_name} must agree with fill_model")
    return value


def _validated_first_fill_timestamp(
    first_fill_timestamp: datetime | None,
    last_fill_timestamp: datetime | None,
    *,
    field_name: str,
) -> datetime | None:
    if (
        first_fill_timestamp is not None
        and last_fill_timestamp is not None
        and first_fill_timestamp > last_fill_timestamp
    ):
        raise ValueError(f"{field_name} must be at or before last_fill_timestamp")
    return first_fill_timestamp


def _required_margin_mode(value: object, *, field_name: str) -> str:
    if type(value) is not str or value not in _MARGIN_MODE_VALUES:
        raise ValueError(f"{field_name} must be isolated or cross")
    return value


def _required_positive_finite_float(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a positive finite number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{field_name} must be a positive finite number")
    return parsed


def _required_margin_as_of(value: object, *, field_name: str) -> datetime:
    if value is None:
        raise ValueError(f"{field_name} must be present")
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _margin_liquidation_path_payload(row: TradeLedgerRow, *, index: int) -> dict[str, Any]:
    if row.market_type != "futures":
        return {}
    margin_mode = _required_margin_mode(row.margin_mode, field_name=f"trades[{index}].margin_mode")
    maintenance_tier = _canonical_report_string(
        row.maintenance_tier,
        field_name=f"trades[{index}].maintenance_tier",
    )
    leverage = _required_positive_finite_float(row.leverage, field_name=f"trades[{index}].leverage")
    notional = _required_positive_finite_float(row.notional, field_name=f"trades[{index}].notional")
    unrealized_pnl = _strict_present_finite_float(
        row.unrealized_pnl,
        field_name=f"trades[{index}].unrealized_pnl",
    )
    liquidation_price = _required_positive_finite_float(
        row.liquidation_price,
        field_name=f"trades[{index}].liquidation_price",
    )
    funding_accrual = _strict_present_finite_float(
        row.funding_accrual,
        field_name=f"trades[{index}].funding_accrual",
    )
    margin_evidence_as_of = _required_margin_as_of(
        row.margin_evidence_as_of,
        field_name=f"trades[{index}].margin_evidence_as_of",
    )
    entry_price = _trade_finite_float(row.entry_price, field_name=f"trades[{index}].entry_price")
    side = _canonical_report_string(row.side, field_name=f"trades[{index}].side")
    if side == "long" and liquidation_price >= entry_price:
        raise ValueError(f"trades[{index}].liquidation_price must be below entry_price for long futures")
    if side == "short" and liquidation_price <= entry_price:
        raise ValueError(f"trades[{index}].liquidation_price must be above entry_price for short futures")
    return {
        "margin_mode": margin_mode,
        "maintenance_tier": maintenance_tier,
        "leverage": leverage,
        "notional": notional,
        "unrealized_pnl": unrealized_pnl,
        "liquidation_price": liquidation_price,
        "funding_accrual": funding_accrual,
        "margin_evidence_as_of": margin_evidence_as_of.isoformat(),
    }


def _canonical_optional_empty_report_string(value: object, *, field_name: str) -> str:
    if type(value) is not str:
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
        if key in copied:
            raise ValueError(f"{field_name} keys must be unique")
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


def _strict_attribution_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a finite strict number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite strict number")
    return parsed


def _pnl_attribution_payload(raw_value: Any, *, field_name: str, reported_pnl: float) -> dict[str, Any]:
    if not isinstance(raw_value, Mapping):
        raise ValueError(f"{field_name} must be present for positive PnL claims")
    payload = _strict_mapping_copy(raw_value, field_name=field_name)
    if payload.get("schema_version") != "pnl_attribution.v1":
        raise ValueError(f"{field_name}.schema_version must be pnl_attribution.v1")
    evidence_reported_pnl = _strict_attribution_float(payload.get("reported_pnl"), field_name=f"{field_name}.reported_pnl")
    if abs(evidence_reported_pnl - reported_pnl) > _PNL_ATTRIBUTION_TOLERANCE:
        raise ValueError(f"{field_name}.reported_pnl must match reported PnL")
    buckets = _list_field(payload, "buckets", label=f"{field_name}.buckets")
    if not buckets:
        raise ValueError(f"{field_name}.buckets must be a non-empty list")
    seen_buckets: set[str] = set()
    validated_buckets: list[dict[str, Any]] = []
    total_contribution = 0.0
    for index, raw_bucket in enumerate(buckets):
        bucket_field = f"{field_name}.buckets[{index}]"
        if not isinstance(raw_bucket, Mapping):
            raise ValueError(f"{bucket_field} must be an object")
        bucket_payload = _strict_mapping_copy(raw_bucket, field_name=bucket_field)
        bucket = _canonical_bucket_identity(bucket_payload.get("bucket"), field_name=f"{bucket_field}.bucket")
        if bucket in seen_buckets:
            raise ValueError(f"{field_name}.buckets bucket values must be unique")
        seen_buckets.add(bucket)
        contribution = _strict_attribution_float(
            bucket_payload.get("contribution"),
            field_name=f"{bucket_field}.contribution",
        )
        total_contribution += contribution
        validated_buckets.append({"bucket": bucket, "contribution": contribution})
    for required_bucket in _REQUIRED_PNL_ATTRIBUTION_BUCKETS:
        if required_bucket not in seen_buckets:
            raise ValueError(f"{field_name}.buckets must include required bucket {required_bucket}")
    if abs(total_contribution - reported_pnl) > _PNL_ATTRIBUTION_TOLERANCE:
        raise ValueError(f"{field_name}.total_contribution must materially match reported_pnl")
    return {
        "schema_version": "pnl_attribution.v1",
        "reported_pnl": evidence_reported_pnl,
        "buckets": validated_buckets,
        "total_contribution": total_contribution,
    }


def _trade_finite_float(value: Any, *, field_name: str) -> float:
    return _strict_present_finite_float(value, field_name=field_name)


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


def _optional_finite_float(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite number")
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
    raw_market_identity: tuple[str, str, str, str, str] | None = None
    previous_period_boundaries: tuple[datetime, datetime, datetime, datetime] | None = None
    for index, window in enumerate(rows):
        if not isinstance(window, Mapping):
            raise ValueError(f"windows[{index}] must be an object")
        validated = dict(window)
        validated.pop("out_of_sample_rows", None)
        if "window_index" in validated and validated["window_index"] is not None:
            validated["window_index"] = _non_negative_int_field(
                validated,
                "window_index",
                label=f"windows[{index}]",
            )
        if "raw_market" in validated and validated["raw_market"] is not None:
            validated["raw_market"] = _report_raw_market_provenance(
                validated["raw_market"],
                field_name=f"windows[{index}].raw_market",
            )
            current_raw_market_identity = tuple(
                validated["raw_market"][field] for field in _RAW_MARKET_PROVENANCE_IDENTITY_FIELDS
            )
            if raw_market_identity is None:
                raw_market_identity = current_raw_market_identity
            elif current_raw_market_identity != raw_market_identity:
                raise ValueError("windows raw_market source identity must be consistent across walk-forward windows")
        period_boundaries = _walk_forward_window_periods(validated, window_index=index)
        if period_boundaries is not None:
            if previous_period_boundaries is not None and period_boundaries < previous_period_boundaries:
                raise ValueError("windows temporal ranges must be strictly increasing")
            previous_period_boundaries = period_boundaries
        if "split_metadata" in validated and validated["split_metadata"] is not None:
            validated["split_metadata"] = _walk_forward_window_split_metadata(
                validated["split_metadata"],
                field_name=f"windows[{index}].split_metadata",
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


def _walk_forward_window_periods(
    window: dict[str, Any],
    *,
    window_index: int,
) -> tuple[datetime, datetime, datetime, datetime] | None:
    present_periods = {period_name for period_name in ("train_period", "test_period") if period_name in window}
    if present_periods and present_periods != {"train_period", "test_period"}:
        missing = ({"train_period", "test_period"} - present_periods).pop()
        raise ValueError(f"windows[{window_index}].{missing} must be present")
    parsed: dict[str, dict[str, datetime]] = {}
    for period_name in ("train_period", "test_period"):
        if period_name not in window:
            continue
        raw_period = window[period_name]
        if not isinstance(raw_period, Mapping):
            raise ValueError(f"windows[{window_index}].{period_name} must be an object")
        period = _strict_mapping_copy(raw_period, field_name=f"windows[{window_index}].{period_name}")
        for boundary in ("start", "end"):
            if boundary not in period:
                raise ValueError(f"windows[{window_index}].{period_name}.{boundary} must be present")
            period[boundary] = _canonical_report_string(
                period[boundary],
                field_name=f"windows[{window_index}].{period_name}.{boundary}",
            )
        parsed[period_name] = {
            "start": _walk_forward_iso_datetime(
                period["start"],
                field_name=f"windows[{window_index}].{period_name}.start",
            ),
            "end": _walk_forward_iso_datetime(
                period["end"],
                field_name=f"windows[{window_index}].{period_name}.end",
            ),
        }
        if parsed[period_name]["start"] > parsed[period_name]["end"]:
            raise ValueError(f"windows[{window_index}].{period_name}.start must be on or before end")
        window[period_name] = period
    if {"train_period", "test_period"}.issubset(parsed):
        if parsed["train_period"]["end"] >= parsed["test_period"]["start"]:
            raise ValueError(f"windows[{window_index}].train_period.end must be before test_period.start")
        return (
            parsed["train_period"]["start"],
            parsed["train_period"]["end"],
            parsed["test_period"]["start"],
            parsed["test_period"]["end"],
        )
    return None


def _walk_forward_iso_datetime(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp string") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} must match datetime.isoformat()")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return parsed


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
    comparison_rows = _allocator_comparison_rows(_list_field(experiment, "comparison_rows"))
    multiple_testing_correction = (
        _strict_multiple_testing_correction(
            experiment.get("multiple_testing_correction"),
            expected_trials=len(variants),
        )
        if len(variants) > 1
        else None
    )

    if (
        best_base_net_bucket_pnl > 0.0
        and best_stressed_net_bucket_pnl > 0.0
        and (multiple_testing_correction is None or _multiple_testing_decision_allowed(multiple_testing_correction))
    ):
        decision = "candidate_for_promotion"
        summary = f"{best_variant} stays profitable under both base and stressed friction assumptions"
    elif best_base_net_bucket_pnl > 0.0:
        decision = "keep_researching"
        summary = "allocator friction variants are positive before final promotion correction or stress robustness"
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
            "rows": comparison_rows,
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
            **({"multiple_testing_correction": multiple_testing_correction} if multiple_testing_correction is not None else {}),
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
    multiple_testing_correction = (
        _strict_multiple_testing_correction(
            experiment.get("multiple_testing_correction"),
            expected_trials=len(variants),
        )
        if len(variants) > 1
        else None
    )

    if (
        best_bucket_pnl > 0.0
        and accepted_allocations > 0
        and (multiple_testing_correction is None or _multiple_testing_decision_allowed(multiple_testing_correction))
    ):
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
            **({"multiple_testing_correction": multiple_testing_correction} if multiple_testing_correction is not None else {}),
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
    report_metadata = _report_metadata_copy(metadata)
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

    snapshot_count = _metadata_int(report_metadata, "snapshot_count")
    window_count = _metadata_int(report_metadata, "window_count")
    out_of_sample_total_return = _report_finite_float(
        out_of_sample_scorecard.get("total_return", 0.0),
        field_name="out_of_sample_scorecard.total_return",
    )
    positive_window_ratio = _report_finite_float(
        performance_dispersion.get("positive_window_ratio", 0.0),
        field_name="performance_dispersion.positive_window_ratio",
    )
    parameter_stability = _parameter_stability_payload(
        parameter_stability,
        field_name="parameter_stability",
    )
    parameter_stability_score = parameter_stability["parameter_stability_score"]
    snapshot_count = _metadata_int(metadata, "snapshot_count")
    window_count = _metadata_int(metadata, "window_count")
    multiple_testing_correction = _strict_multiple_testing_correction(
        experiment.get("multiple_testing_correction"),
        expected_trials=max(len(windows), 2),
    )
    parameter_stability["parameter_stability_score"] = parameter_stability_score
    positive_oos_claim = (
        out_of_sample_total_return > 0.0
        and positive_window_ratio >= 0.6
        and parameter_stability_score >= 0.5
    )
    if out_of_sample_total_return > 0.0 and positive_window_ratio >= 0.6 and parameter_stability_score >= 0.5:
        if "split_metadata" not in report_metadata:
            raise ValueError("walk_forward.split_metadata must be present for positive OOS evidence")
        for index, window in enumerate(windows):
            if "split_metadata" not in window:
                raise ValueError(f"windows[{index}].split_metadata must be present for positive OOS evidence")
            if "train_period" not in window or "test_period" not in window:
                raise ValueError(f"windows[{index}] train/test periods must be present for positive OOS evidence")

    pnl_attribution = None
    if out_of_sample_total_return > 0.0 or "pnl_attribution" in experiment:
        pnl_attribution = _pnl_attribution_payload(
            experiment.get("pnl_attribution"),
            field_name="pnl_attribution",
            reported_pnl=out_of_sample_total_return,
        )

    regime_stratified_oos = None
    if positive_oos_claim or "regime_stratified_oos" in experiment:
        regime_stratified_oos = _regime_stratified_oos_evidence(
            experiment.get("regime_stratified_oos"),
            field_name="regime_stratified_oos",
        )
    portfolio_correlation_exposure = None
    if positive_oos_claim or "portfolio_correlation_exposure" in experiment:
        portfolio_correlation_exposure = _portfolio_correlation_exposure_evidence(
            experiment.get("portfolio_correlation_exposure"),
            field_name="portfolio_correlation_exposure",
        )
    drawdown_anatomy = None
    if positive_oos_claim or "drawdown_anatomy" in experiment:
        drawdown_anatomy = _drawdown_anatomy_evidence(
            experiment.get("drawdown_anatomy"),
            field_name="drawdown_anatomy",
        )

    if (
        out_of_sample_total_return > 0.0
        and positive_window_ratio >= 0.6
        and parameter_stability_score >= 0.5
        and regime_stratified_oos is not None
        and not regime_stratified_oos["collapsed_buckets"]
        and _multiple_testing_decision_allowed(multiple_testing_correction)
    ):
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
            "metadata": report_metadata,
            "robustness_summary": robustness_summary,
            "parameter_stability": parameter_stability,
            **({"pnl_attribution": pnl_attribution} if pnl_attribution is not None else {}),
            **({"drawdown_anatomy": drawdown_anatomy} if drawdown_anatomy is not None else {}),
        },
        "windows": {
            "metadata": report_metadata,
            "rows": windows,
        },
        "scorecard": {
            "metadata": _scorecard_metadata(experiment_name=experiment_name, metadata=report_metadata),
            "key_metrics": {
                "snapshot_count": snapshot_count,
                "window_count": window_count,
                "out_of_sample_total_return": out_of_sample_total_return,
                "positive_window_ratio": positive_window_ratio,
                "parameter_stability_score": parameter_stability_score,
            },
            "decision_summary": _decision_summary(decision=decision, summary=summary),
            "multiple_testing_correction": multiple_testing_correction,
            **({"regime_stratified_oos": regime_stratified_oos} if regime_stratified_oos is not None else {}),
            **({"pnl_attribution": pnl_attribution} if pnl_attribution is not None else {}),
            **(
                {"portfolio_correlation_exposure": portfolio_correlation_exposure}
                if portfolio_correlation_exposure is not None
                else {}
            ),
            **({"drawdown_anatomy": drawdown_anatomy} if drawdown_anatomy is not None else {}),
            **_promotion_metadata_sections(report_metadata),
        },
    }
