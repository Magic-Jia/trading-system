from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from .evidence_chain import FILENAME as EVIDENCE_CHAIN_FILENAME
from .evidence_chain import _generated_at, write_backtest_evidence_chain


WALK_FORWARD_SCHEMA_VERSION = "walk_forward_oos_report.v1"
COST_SENSITIVITY_SCHEMA_VERSION = "cost_sensitivity_report.v1"
WALK_FORWARD_REPORT_FILENAME = "walk_forward_oos_report.json"
COST_SENSITIVITY_REPORT_FILENAME = "cost_sensitivity_report.json"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_json_object(path: Path, source_name: str, reasons: list[str]) -> Mapping[str, Any] | None:
    if not path.is_file():
        reasons.append(f"{source_name}_missing")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        reasons.append(f"{source_name}_malformed:{type(exc).__name__}")
        return None
    if not isinstance(payload, Mapping):
        reasons.append(f"{source_name}_malformed:not_object")
        return None
    return payload


def _runtime_execution_sample_health_path(runtime_summary_path: str | Path | None, reasons: list[str]) -> Path | None:
    if runtime_summary_path is None:
        return None
    path = Path(runtime_summary_path)
    payload = _load_json_object(path, "runtime_summary", reasons)
    if payload is None:
        return None
    value = payload.get("execution_sample_collection_health_file")
    if not isinstance(value, str) or not value.strip():
        reasons.append("runtime_summary_execution_sample_collection_health_file_missing")
        return None
    return Path(value)


def _write_execution_sample_health_unavailable_marker(
    output_dir: Path,
    *,
    generated_at: str,
    reason_codes: list[str],
) -> Path:
    marker_path = output_dir / "execution_sample_collection_health_unavailable.json"
    payload = {
        "schema_version": "execution_sample_collection_health.v1",
        "status": "unavailable",
        "generated_at": generated_at,
        "decision_policy": "fail_closed",
        "sample_count": 0,
        "reason_codes": list(reason_codes) or ["execution_sample_collection_health_unavailable"],
    }
    _write_json(marker_path, payload)
    return marker_path


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _reasoned_number(
    value: Any,
    *,
    reason: str,
    reasons: list[str],
    default: float = 0.0,
) -> float:
    number = _finite_number(value)
    if number is None:
        reasons.append(reason)
        return default
    return number


def build_walk_forward_oos_report(
    walk_forward_bundle_dir: str | Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    bundle_dir = Path(walk_forward_bundle_dir)
    evaluated_at = _generated_at(generated_at)
    reasons: list[str] = []
    if not bundle_dir.is_dir():
        reasons.append("walk_forward_bundle_missing")

    summary_payload = _load_json_object(bundle_dir / "summary.json", "walk_forward_summary", reasons)
    windows_payload = _load_json_object(bundle_dir / "windows.json", "walk_forward_windows", reasons)
    scorecard_payload = _load_json_object(bundle_dir / "scorecard.json", "walk_forward_scorecard", reasons)

    summary = _mapping(summary_payload)
    scorecard = _mapping(scorecard_payload)
    robustness = _mapping(summary.get("robustness_summary"))
    out_of_sample_scorecard = dict(_mapping(robustness.get("out_of_sample_scorecard")))
    performance_dispersion = _mapping(robustness.get("performance_dispersion"))
    parameter_stability = _mapping(summary.get("parameter_stability"))
    key_metrics = _mapping(scorecard.get("key_metrics"))

    windows = _mapping(windows_payload).get("rows")
    windows_list = windows if isinstance(windows, list) else []
    window_count = _positive_int(key_metrics.get("window_count"))
    if window_count is None:
        window_count = _positive_int(performance_dispersion.get("window_count"))
    if window_count is None:
        window_count = _positive_int(_mapping(summary.get("metadata")).get("window_count"))
    if window_count is None:
        window_count = len(windows_list)
    if window_count <= 0:
        reasons.append("walk_forward_zero_windows")

    trade_count = _positive_int(out_of_sample_scorecard.get("trade_count"))
    if trade_count is None:
        trade_count = 0
        reasons.append("walk_forward_oos_trade_count_zero")

    positive_window_ratio = _reasoned_number(
        key_metrics.get("positive_window_ratio", performance_dispersion.get("positive_window_ratio")),
        reason="walk_forward_positive_window_ratio_missing",
        reasons=reasons,
    )
    if positive_window_ratio < 0.6:
        reasons.append("walk_forward_positive_window_ratio_low")

    parameter_stability_score = _reasoned_number(
        key_metrics.get("parameter_stability_score", parameter_stability.get("parameter_stability_score")),
        reason="walk_forward_parameter_stability_score_missing",
        reasons=reasons,
    )
    if parameter_stability_score < 0.5:
        reasons.append("walk_forward_parameter_stability_score_low")

    out_of_sample_total_return = _reasoned_number(
        key_metrics.get("out_of_sample_total_return", out_of_sample_scorecard.get("total_return")),
        reason="walk_forward_oos_total_return_missing",
        reasons=reasons,
    )
    if out_of_sample_total_return <= 0.0:
        reasons.append("walk_forward_oos_total_return_non_positive")

    multiple_testing_correction = _mapping(scorecard.get("multiple_testing_correction"))
    adjusted_pass = multiple_testing_correction.get("adjusted_pass")
    if adjusted_pass is not True:
        reasons.append("walk_forward_multiple_testing_adjusted_pass_false")

    out_of_sample_scorecard["trade_count"] = trade_count
    return {
        "schema_version": WALK_FORWARD_SCHEMA_VERSION,
        "generated_at": evaluated_at,
        "summary": {
            "decision": "pass" if not reasons else "hold",
            "out_of_sample_scorecard": out_of_sample_scorecard,
            "window_count": window_count,
            "positive_window_ratio": positive_window_ratio,
            "parameter_stability_score": parameter_stability_score,
        },
        "reason_codes": reasons,
    }


def write_walk_forward_oos_report(
    walk_forward_bundle_dir: str | Path,
    output_path: str | Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    payload = build_walk_forward_oos_report(walk_forward_bundle_dir, generated_at=generated_at)
    _write_json(Path(output_path), payload)
    return payload


def build_cost_sensitivity_report(
    allocator_friction_bundle_dir: str | Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    bundle_dir = Path(allocator_friction_bundle_dir)
    evaluated_at = _generated_at(generated_at)
    reasons: list[str] = []
    if not bundle_dir.is_dir():
        reasons.append("cost_sensitivity_bundle_missing")

    summary_payload = _load_json_object(bundle_dir / "summary.json", "cost_sensitivity_summary", reasons)
    comparison_payload = _load_json_object(bundle_dir / "comparison_rows.json", "cost_sensitivity_comparison_rows", reasons)
    scorecard_payload = _load_json_object(bundle_dir / "scorecard.json", "cost_sensitivity_scorecard", reasons)

    variants = _mapping(_mapping(summary_payload).get("variants"))
    current_allocator = _mapping(variants.get("current_allocator"))
    frictions = _mapping(current_allocator.get("frictions"))
    comparison_rows = _mapping(comparison_payload).get("rows")
    scenario_names = {name for name in frictions if isinstance(name, str)}
    if isinstance(comparison_rows, list):
        scenario_names.update(
            row.get("friction_scenario")
            for row in comparison_rows
            if isinstance(row, Mapping) and isinstance(row.get("friction_scenario"), str)
        )
    scenario_count = len(scenario_names)
    if scenario_count <= 0:
        reasons.append("cost_sensitivity_zero_scenarios")

    base = _mapping(frictions.get("base"))
    stressed = _mapping(frictions.get("stressed"))
    if not base:
        reasons.append("cost_sensitivity_base_scenario_missing")
    if not stressed:
        reasons.append("cost_sensitivity_stressed_scenario_missing")

    key_metrics = _mapping(_mapping(scorecard_payload).get("key_metrics"))
    base_net_pnl = _reasoned_number(
        key_metrics.get("current_allocator_base_net_bucket_pnl", base.get("net_bucket_pnl")),
        reason="cost_sensitivity_base_net_pnl_missing",
        reasons=reasons,
    )
    stressed_net_pnl = _reasoned_number(
        key_metrics.get("best_stressed_net_bucket_pnl", stressed.get("net_bucket_pnl")),
        reason="cost_sensitivity_stressed_net_pnl_missing",
        reasons=reasons,
    )
    if base_net_pnl <= 0.0:
        reasons.append("cost_sensitivity_base_net_pnl_non_positive")
    if stressed_net_pnl <= 0.0:
        reasons.append("cost_sensitivity_stressed_net_pnl_non_positive")

    cost_drags = [
        _finite_number(payload.get("cost_drag"))
        for payload in frictions.values()
        if isinstance(payload, Mapping) and "cost_drag" in payload
    ]
    if not cost_drags or any(value is None or value < 0.0 for value in cost_drags):
        reasons.append("cost_sensitivity_cost_drag_invalid")

    multiple_testing_correction = _mapping(_mapping(scorecard_payload).get("multiple_testing_correction"))
    if multiple_testing_correction and multiple_testing_correction.get("adjusted_pass") is not True:
        reasons.append("cost_sensitivity_multiple_testing_adjusted_pass_false")

    net_pnls = [
        value
        for value in (_finite_number(_mapping(payload).get("net_bucket_pnl")) for payload in frictions.values())
        if value is not None
    ]
    worst_case_total_return = min(net_pnls) if net_pnls else 0.0

    return {
        "schema_version": COST_SENSITIVITY_SCHEMA_VERSION,
        "generated_at": evaluated_at,
        "summary": {
            "decision": "pass" if not reasons else "hold",
            "scenario_count": scenario_count,
            "base_net_pnl": base_net_pnl,
            "stressed_net_pnl": stressed_net_pnl,
            "worst_case_total_return": worst_case_total_return,
        },
        "reason_codes": reasons,
    }


def write_cost_sensitivity_report(
    allocator_friction_bundle_dir: str | Path,
    output_path: str | Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    payload = build_cost_sensitivity_report(allocator_friction_bundle_dir, generated_at=generated_at)
    _write_json(Path(output_path), payload)
    return payload


def write_professional_backtest_evidence(
    *,
    backtest_bundle_dir: str | Path,
    walk_forward_bundle_dir: str | Path,
    allocator_friction_bundle_dir: str | Path,
    output_dir: str | Path,
    execution_calibration_summary_path: str | Path | None = None,
    execution_calibration_unavailable_path: str | Path | None = None,
    execution_sample_collection_health_path: str | Path | None = None,
    runtime_summary_path: str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    evaluated_at = _generated_at(generated_at)
    walk_forward_report_path = output / WALK_FORWARD_REPORT_FILENAME
    cost_sensitivity_report_path = output / COST_SENSITIVITY_REPORT_FILENAME
    evidence_chain_path = output / EVIDENCE_CHAIN_FILENAME
    runtime_health_resolution_reasons: list[str] = []
    resolved_execution_sample_health_path = (
        Path(execution_sample_collection_health_path)
        if execution_sample_collection_health_path is not None
        else _runtime_execution_sample_health_path(runtime_summary_path, runtime_health_resolution_reasons)
    )
    if runtime_summary_path is not None and resolved_execution_sample_health_path is None:
        resolved_execution_sample_health_path = _write_execution_sample_health_unavailable_marker(
            output,
            generated_at=evaluated_at,
            reason_codes=runtime_health_resolution_reasons,
        )
    walk_forward_report = write_walk_forward_oos_report(
        walk_forward_bundle_dir,
        walk_forward_report_path,
        generated_at=evaluated_at,
    )
    cost_sensitivity_report = write_cost_sensitivity_report(
        allocator_friction_bundle_dir,
        cost_sensitivity_report_path,
        generated_at=evaluated_at,
    )
    evidence_chain = write_backtest_evidence_chain(
        backtest_bundle_dir,
        output_path=evidence_chain_path,
        walk_forward_report_path=walk_forward_report_path,
        cost_sensitivity_report_path=cost_sensitivity_report_path,
        execution_calibration_summary_path=execution_calibration_summary_path,
        execution_calibration_unavailable_path=execution_calibration_unavailable_path,
        execution_sample_collection_health_path=resolved_execution_sample_health_path,
        generated_at=evaluated_at,
    )
    result = {
        "walk_forward_report": walk_forward_report,
        "walk_forward_report_path": str(walk_forward_report_path),
        "cost_sensitivity_report": cost_sensitivity_report,
        "cost_sensitivity_report_path": str(cost_sensitivity_report_path),
        "evidence_chain": evidence_chain,
        "evidence_chain_path": str(evidence_chain_path),
    }
    if execution_calibration_summary_path is not None:
        result["execution_calibration_summary_path"] = str(execution_calibration_summary_path)
    if execution_calibration_unavailable_path is not None:
        result["execution_calibration_unavailable_path"] = str(execution_calibration_unavailable_path)
    if resolved_execution_sample_health_path is not None:
        result["execution_sample_collection_health_path"] = str(resolved_execution_sample_health_path)
    if runtime_summary_path is not None:
        result["runtime_summary_path"] = str(runtime_summary_path)
    return result
