from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


_REQUIRED_MANIFEST_FIELDS = (
    "experiment_kind",
    "dataset_root",
    "baseline_name",
    "variant_name",
    "sample_period",
    "window_counts",
    "bundle_name",
    "snapshot_count",
    "artifacts",
    "universe_asof_contract",
    "margin_liquidation_path_contract",
    "dynamic_sizing_evidence_contract",
)

_REQUIRED_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "full_market_baseline": ("summary.json", "breakdowns.json", "audit.json"),
    "rotation_suppression": ("summary.json", "comparison_rows.json", "scorecard.json"),
    "allocator_friction": ("summary.json", "comparison_rows.json", "scorecard.json"),
    "engine_filter_ablation": ("summary.json", "scorecard.json"),
    "walk_forward_validation": ("summary.json", "windows.json", "scorecard.json"),
}

_SAFE_EVIDENCE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SUPPORTED_EXECUTION_PREVIEW_SIDES = frozenset({"BUY", "SELL"})
_SUPPORTED_EXECUTION_PREVIEW_ORDER_TYPES = frozenset({"MARKET", "LIMIT", "STOP_MARKET", "TAKE_PROFIT_MARKET"})
_SUPPORTED_EXECUTION_PREVIEW_TIME_IN_FORCE = frozenset({"GTC", "IOC", "FOK", "GTX"})
_REQUIRED_UNIVERSE_ASOF_LIFECYCLE_FIELDS = frozenset(
    {"lifecycle_status", "delisted_at", "previous_symbol", "renamed_at", "contract_migration"}
)
_EXPECTED_MARGIN_LIQUIDATION_CONTRACT_FIELDS = {
    "margin_mode_field": "trades[].margin_mode",
    "maintenance_tier_field": "trades[].maintenance_tier",
    "leverage_field": "trades[].leverage",
    "notional_field": "trades[].notional",
    "unrealized_pnl_field": "trades[].unrealized_pnl",
    "liquidation_price_field": "trades[].liquidation_price",
    "funding_accrual_field": "trades[].funding_accrual",
    "as_of_field": "trades[].margin_evidence_as_of",
}
_EXPECTED_DYNAMIC_SIZING_CONTRACT_FIELDS = {
    "scope": "futures_trade_ledger",
    "decision_timestamp_field": "trades[].sizing_decision_at",
    "evidence_as_of_field": "trades[].dynamic_sizing_evidence.evidence_as_of",
    "baseline_risk_field": "trades[].dynamic_sizing_evidence.baseline_risk_fraction",
    "final_risk_field": "trades[].dynamic_sizing_evidence.final_risk_fraction",
    "override_evidence_field": "trades[].dynamic_sizing_evidence.override_evidence",
}
_REQUIRED_DYNAMIC_SIZING_AXES = ("liquidity", "volatility", "drawdown", "execution")
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
_CAPACITY_ANALYSIS_EVIDENCE_SCHEMA_VERSION = "capacity_analysis_evidence.v1"
_REQUIRED_CAPACITY_CHECKS = (
    "capital_limits_met",
    "liquidity_regime_capacity_met",
    "impact_deterioration_met",
    "symbol_level_capacity_met",
    "turnover_slippage_sensitivity_met",
    "assumptions_provenance_met",
)
_DRAWDOWN_ANATOMY_SCHEMA_VERSION = "drawdown_anatomy.v1"
_DRAWDOWN_FAILURE_TYPES = frozenset({"edge_failure", "execution_failure", "risk_control_failure"})


@dataclass(frozen=True, slots=True)
class BacktestBundle:
    root: Path
    manifest: dict[str, Any]
    artifacts: dict[str, dict[str, Any]]

    @property
    def experiment_kind(self) -> str:
        return str(self.manifest["experiment_kind"])



def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload



def _require_keys(payload: Mapping[str, Any], *, keys: tuple[str, ...], context: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"{context} is missing required keys: {joined}")



def _require_mapping(payload: Mapping[str, Any], key: str, *, context: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{context}.{key} must be an object")
    return dict(value)


def _require_real_number(payload: Mapping[str, Any], key: str, *, context: str) -> float:
    value = payload.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{context}.{key} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{context}.{key} must be finite")
    return parsed


def _require_strict_real_number(value: Any, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a finite strict number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{context} must be a finite strict number")
    return parsed


def _require_bounded_ratio(value: Any, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a bounded ratio strict number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0 or parsed > 1.0:
        raise ValueError(f"{context} must be a bounded ratio strict number")
    return parsed


def _require_canonical_string_value(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{context} must be a canonical string")
    return value


def _require_canonical_bucket_identity(value: Any, *, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or not _is_safe_evidence_identifier(value)
    ):
        raise ValueError(f"{context} must be canonical")
    return value


def _require_non_negative_int(payload: Mapping[str, Any], key: str, *, context: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{context}.{key} must be a non-negative integer")
    return value


def _require_positive_int(payload: Mapping[str, Any], key: str, *, context: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{context}.{key} must be a positive integer")
    return value


def _require_multiple_testing_correction(
    payload: Mapping[str, Any],
    *,
    context: str,
    expected_trials: int | None = None,
) -> dict[str, Any]:
    raw = payload.get("multiple_testing_correction")
    if not isinstance(raw, Mapping):
        raise ValueError(f"{context}.multiple_testing_correction must be present")
    correction = dict(raw)
    if correction.get("schema_version") != "multiple_testing_correction.v1":
        raise ValueError(
            f"{context}.multiple_testing_correction.schema_version must be multiple_testing_correction.v1"
        )
    if "number_of_trials" not in correction:
        raise ValueError(f"{context}.multiple_testing_correction.number_of_trials must be present")
    number_of_trials = correction["number_of_trials"]
    if isinstance(number_of_trials, bool) or not isinstance(number_of_trials, int) or number_of_trials <= 1:
        raise ValueError(
            f"{context}.multiple_testing_correction.number_of_trials must be an integer greater than one"
        )
    if expected_trials is not None and number_of_trials != expected_trials:
        raise ValueError(f"{context}.multiple_testing_correction.number_of_trials must match candidate count")
    method = correction.get("correction_method")
    if not isinstance(method, str) or not method.strip() or method != method.strip():
        raise ValueError(f"{context}.multiple_testing_correction.correction_method must be canonical")
    evidence_fields = ("corrected_p_value", "corrected_q_value", "adjusted_threshold", "conservative_threshold")
    present_evidence_fields = [field for field in evidence_fields if field in correction and correction[field] is not None]
    if not present_evidence_fields:
        raise ValueError(f"{context}.multiple_testing_correction must include corrected evidence")
    for field in present_evidence_fields:
        value = correction[field]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError(f"{context}.multiple_testing_correction.{field} must be a finite number")
        correction[field] = float(value)
    if not isinstance(correction.get("adjusted_pass"), bool):
        raise ValueError(f"{context}.multiple_testing_correction.adjusted_pass must be a bool")
    correction["number_of_trials"] = number_of_trials
    return correction


def _is_safe_evidence_identifier(value: str) -> bool:
    return _SAFE_EVIDENCE_IDENTIFIER_RE.fullmatch(value) is not None


def _append_unique_reason(reason_codes: list[str], reason_code: str) -> None:
    if reason_code not in reason_codes:
        reason_codes.append(reason_code)


def _validate_preview_string(
    value: Any,
    *,
    field_name: str,
    supported: frozenset[str] | None,
    reason_codes: list[str],
) -> str | None:
    if not isinstance(value, str) or not value:
        _append_unique_reason(reason_codes, f"{field_name}_not_string")
        return None
    if value != value.strip():
        _append_unique_reason(reason_codes, f"{field_name}_not_canonical")
        return None
    if supported is not None and value not in supported:
        _append_unique_reason(reason_codes, f"{field_name}_unsupported")
    return value


def _validate_optional_preview_number(
    value: Any,
    *,
    field_name: str,
    reason_codes: list[str],
) -> None:
    if value is None:
        return
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        _append_unique_reason(reason_codes, f"{field_name}_not_strict_number")
        return
    if not math.isfinite(float(value)):
        _append_unique_reason(reason_codes, f"{field_name}_not_finite")


def _validate_preview_bool(value: Any, *, field_name: str, reason_codes: list[str]) -> None:
    if not isinstance(value, bool):
        _append_unique_reason(reason_codes, f"{field_name}_not_bool")


def _is_preview_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _validate_execution_preview_order_shape(order: Mapping[str, Any], *, reason_codes: list[str]) -> None:
    order_type = order.get("order_type")
    time_in_force = order.get("time_in_force")
    post_only = order.get("post_only")
    close_position = order.get("close_position")
    if close_position is True:
        if order.get("quantity") is not None:
            _append_unique_reason(reason_codes, "quantity_must_be_absent_for_close_position")
        if order.get("notional") is not None:
            _append_unique_reason(reason_codes, "notional_must_be_absent_for_close_position")
    elif order_type in {"MARKET", "LIMIT"} and not _is_preview_number(order.get("quantity")):
        _append_unique_reason(reason_codes, "quantity_required_for_entry_order")
    if order_type == "LIMIT":
        if not _is_preview_number(order.get("limit_price")):
            _append_unique_reason(reason_codes, "limit_price_required_for_limit")
        if not _is_preview_number(order.get("price")):
            _append_unique_reason(reason_codes, "price_required_for_limit")
    if order_type == "MARKET":
        for field_name in ("price", "limit_price", "stop_price"):
            if order.get(field_name) is not None:
                _append_unique_reason(reason_codes, f"{field_name}_must_be_absent_for_market")
    if order_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
        if not _is_preview_number(order.get("stop_price")):
            _append_unique_reason(reason_codes, "stop_price_required_for_stop_market")
        if close_position is not True:
            _append_unique_reason(reason_codes, "close_position_required_for_stop_market")
    if time_in_force == "GTX" and post_only is not True:
        _append_unique_reason(reason_codes, "post_only_required_for_gtx")
    if post_only is True and time_in_force != "GTX":
        _append_unique_reason(reason_codes, "time_in_force_gtx_required_for_post_only")


def validate_execution_preview_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    reason_codes: list[str] = []
    if not isinstance(payload, Mapping):
        return {"valid": False, "reason_codes": ["payload_not_object"]}
    if payload.get("schema_version") != "execution_preview.v1":
        _append_unique_reason(reason_codes, "schema_version_unsupported")
    orders = payload.get("orders")
    if not isinstance(orders, list) or not orders:
        _append_unique_reason(reason_codes, "orders_not_non_empty_list")
        orders = []
    for order in orders:
        if not isinstance(order, Mapping):
            _append_unique_reason(reason_codes, "order_not_object")
            continue
        _validate_preview_string(order.get("symbol"), field_name="symbol", supported=None, reason_codes=reason_codes)
        _validate_preview_string(
            order.get("side"),
            field_name="side",
            supported=_SUPPORTED_EXECUTION_PREVIEW_SIDES,
            reason_codes=reason_codes,
        )
        _validate_preview_string(
            order.get("order_type"),
            field_name="order_type",
            supported=_SUPPORTED_EXECUTION_PREVIEW_ORDER_TYPES,
            reason_codes=reason_codes,
        )
        for field_name in ("quantity", "notional", "price", "stop_price", "limit_price"):
            _validate_optional_preview_number(order.get(field_name), field_name=field_name, reason_codes=reason_codes)
        _validate_preview_bool(order.get("reduce_only"), field_name="reduce_only", reason_codes=reason_codes)
        _validate_preview_bool(order.get("close_position"), field_name="close_position", reason_codes=reason_codes)
        _validate_preview_bool(order.get("post_only"), field_name="post_only", reason_codes=reason_codes)
        time_in_force = order.get("time_in_force")
        if time_in_force is not None:
            _validate_preview_string(
                time_in_force,
                field_name="time_in_force",
                supported=_SUPPORTED_EXECUTION_PREVIEW_TIME_IN_FORCE,
                reason_codes=reason_codes,
            )
        _validate_execution_preview_order_shape(order, reason_codes=reason_codes)
    unsupported = payload.get("unsupported")
    if not isinstance(unsupported, list):
        _append_unique_reason(reason_codes, "unsupported_not_list")
    else:
        for item in unsupported:
            if not isinstance(item, Mapping):
                _append_unique_reason(reason_codes, "unsupported_item_not_object")
                continue
            reason_code = item.get("reason_code")
            if not isinstance(reason_code, str) or not reason_code or reason_code != reason_code.strip():
                _append_unique_reason(reason_codes, "unsupported_reason_code_invalid")
            elif not _is_safe_evidence_identifier(reason_code):
                _append_unique_reason(reason_codes, "unsupported_reason_code_invalid")
            else:
                _append_unique_reason(reason_codes, "unsupported_orders_present")
    return {"valid": not reason_codes, "reason_codes": reason_codes}


def _parse_iso_datetime(value: str, *, context: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{context} must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must be timezone-aware")
    return parsed


def _validate_runtime_observability_plan(payload: Mapping[str, Any], *, context: str) -> None:
    for field_name in ("runtime_observability", "runtime_observability_plan"):
        if field_name not in payload:
            continue
        plan = payload.get(field_name)
        if not isinstance(plan, Mapping):
            raise ValueError(f"{context}.{field_name} must be an object")
        runtime_fields = plan.get("runtime_fields")
        if not (isinstance(runtime_fields, list) and runtime_fields):
            raise ValueError(f"{context}.{field_name}.runtime_fields must be a list of strings")
        for item in runtime_fields:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{context}.{field_name}.runtime_fields must be a list of strings")
            if item != item.strip():
                raise ValueError(f"{context}.{field_name}.runtime_fields must be canonical strings")


def _validate_rollback_plan(payload: Mapping[str, Any], *, context: str) -> None:
    if "rollback_plan" not in payload:
        return
    rollback_plan = payload.get("rollback_plan")
    if not isinstance(rollback_plan, Mapping):
        raise ValueError(f"{context}.rollback_plan must be an object")
    for field_name in ("rollback_target", "rollback_trigger", "observation_window"):
        field_value = rollback_plan.get(field_name)
        if not isinstance(field_value, str) or not field_value.strip():
            raise ValueError(f"{context}.rollback_plan.{field_name} must be a string")
        if field_value != field_value.strip():
            raise ValueError(f"{context}.rollback_plan.{field_name} must be canonical")


def _validate_universe_asof_contract(payload: Mapping[str, Any], *, context: str) -> None:
    contract = payload.get("universe_asof_contract")
    if not isinstance(contract, Mapping):
        raise ValueError(f"{context}.universe_asof_contract must be an object")
    schema_version = contract.get("schema_version")
    if schema_version != "universe_asof_contract.v1":
        raise ValueError(f"{context}.universe_asof_contract.schema_version must be universe_asof_contract.v1")
    membership_source = contract.get("membership_source")
    if not isinstance(membership_source, str) or not membership_source.strip():
        raise ValueError(f"{context}.universe_asof_contract.membership_source must be a string")
    if membership_source != membership_source.strip():
        raise ValueError(f"{context}.universe_asof_contract.membership_source must be canonical")
    if membership_source == "current_universe_snapshot":
        raise ValueError(f"{context}.universe_asof_contract.membership_source must not be current_universe_snapshot")
    expected_strings = {
        "as_of_field": "instrument_snapshot.as_of",
        "decision_timestamp_field": "metadata.timestamp",
    }
    for field_name, expected in expected_strings.items():
        if contract.get(field_name) != expected:
            raise ValueError(f"{context}.universe_asof_contract.{field_name} must be {expected}")
    required_lifecycle_fields = contract.get("required_lifecycle_fields")
    if not isinstance(required_lifecycle_fields, list):
        raise ValueError(f"{context}.universe_asof_contract.required_lifecycle_fields must be a list")
    normalized_fields: set[str] = set()
    for index, field in enumerate(required_lifecycle_fields):
        if not isinstance(field, str) or not field.strip() or field != field.strip():
            raise ValueError(
                f"{context}.universe_asof_contract.required_lifecycle_fields[{index}] must be a canonical string"
            )
        normalized_fields.add(field)
    if not _REQUIRED_UNIVERSE_ASOF_LIFECYCLE_FIELDS.issubset(normalized_fields):
        raise ValueError(f"{context}.universe_asof_contract.required_lifecycle_fields must include lifecycle evidence")
    for field_name in ("supports_delisted", "supports_renames", "supports_contract_migrations"):
        if contract.get(field_name) is not True:
            raise ValueError(f"{context}.universe_asof_contract.{field_name} must be true")


def _validate_margin_liquidation_path_contract(payload: Mapping[str, Any], *, context: str) -> None:
    contract = payload.get("margin_liquidation_path_contract")
    if not isinstance(contract, Mapping):
        raise ValueError(f"{context}.margin_liquidation_path_contract must be an object")
    if contract.get("schema_version") != "margin_liquidation_path_contract.v1":
        raise ValueError(
            f"{context}.margin_liquidation_path_contract.schema_version must be "
            "margin_liquidation_path_contract.v1"
        )
    if contract.get("scope") != "futures_trade_ledger":
        raise ValueError(f"{context}.margin_liquidation_path_contract.scope must be futures_trade_ledger")
    for field_name, expected in _EXPECTED_MARGIN_LIQUIDATION_CONTRACT_FIELDS.items():
        if contract.get(field_name) != expected:
            raise ValueError(
                f"{context}.margin_liquidation_path_contract.{field_name} must be {expected}"
            )
    accepted_margin_modes = contract.get("accepted_margin_modes")
    if not isinstance(accepted_margin_modes, list):
        raise ValueError(f"{context}.margin_liquidation_path_contract.accepted_margin_modes must be a list")
    if set(accepted_margin_modes) != {"isolated", "cross"}:
        raise ValueError(
            f"{context}.margin_liquidation_path_contract.accepted_margin_modes must be isolated and cross"
        )
    for index, mode in enumerate(accepted_margin_modes):
        if not isinstance(mode, str) or not mode.strip() or mode != mode.strip():
            raise ValueError(
                f"{context}.margin_liquidation_path_contract.accepted_margin_modes[{index}] must be canonical"
            )
    if contract.get("fail_closed") is not True:
        raise ValueError(f"{context}.margin_liquidation_path_contract.fail_closed must be true")


def _validate_dynamic_sizing_evidence_contract(payload: Mapping[str, Any], *, context: str) -> None:
    contract = payload.get("dynamic_sizing_evidence_contract")
    if not isinstance(contract, Mapping):
        raise ValueError(f"{context}.dynamic_sizing_evidence_contract must be an object")
    if contract.get("schema_version") != "dynamic_sizing_evidence_contract.v1":
        raise ValueError(
            f"{context}.dynamic_sizing_evidence_contract.schema_version must be "
            "dynamic_sizing_evidence_contract.v1"
        )
    for field_name, expected in _EXPECTED_DYNAMIC_SIZING_CONTRACT_FIELDS.items():
        if contract.get(field_name) != expected:
            raise ValueError(
                f"{context}.dynamic_sizing_evidence_contract.{field_name} must be {expected}"
            )
    required_axes = contract.get("required_degradation_axes")
    if not isinstance(required_axes, list):
        raise ValueError(f"{context}.dynamic_sizing_evidence_contract.required_degradation_axes must be a list")
    if tuple(required_axes) != _REQUIRED_DYNAMIC_SIZING_AXES:
        joined = ", ".join(_REQUIRED_DYNAMIC_SIZING_AXES)
        raise ValueError(
            f"{context}.dynamic_sizing_evidence_contract.required_degradation_axes must be {joined}"
        )
    for index, axis in enumerate(required_axes):
        if not isinstance(axis, str) or not axis.strip() or axis != axis.strip():
            raise ValueError(
                f"{context}.dynamic_sizing_evidence_contract.required_degradation_axes[{index}] must be canonical"
            )
    if contract.get("fail_closed") is not True:
        raise ValueError(f"{context}.dynamic_sizing_evidence_contract.fail_closed must be true")


def _require_dynamic_sizing_evidence(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context}.dynamic_sizing_evidence must be an object")
    evidence = dict(value)
    if evidence.get("schema_version") != "dynamic_sizing_evidence.v1":
        raise ValueError("dynamic_sizing_evidence.schema_version must be dynamic_sizing_evidence.v1")
    required_axes = evidence.get("required_axes")
    if not isinstance(required_axes, list) or tuple(required_axes) != _REQUIRED_DYNAMIC_SIZING_AXES:
        joined = ", ".join(_REQUIRED_DYNAMIC_SIZING_AXES)
        raise ValueError(f"dynamic_sizing_evidence.required_axes must be {joined}")
    for index, axis in enumerate(required_axes):
        _require_canonical_bucket_identity(axis, context=f"dynamic_sizing_evidence.required_axes[{index}]")
    decisions = evidence.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise ValueError("dynamic_sizing_evidence.decisions must be a non-empty list")
    normalized_decisions: list[dict[str, Any]] = []
    for index, raw_decision in enumerate(decisions):
        decision_context = f"dynamic_sizing_evidence.decisions[{index}]"
        if not isinstance(raw_decision, Mapping):
            raise ValueError(f"{decision_context} must be an object")
        decision = dict(raw_decision)
        decision["decision_id"] = _require_canonical_bucket_identity(
            decision.get("decision_id"),
            context=f"{decision_context}.decision_id",
        )
        decision_at = _parse_iso_datetime(
            _require_canonical_string_value(
                decision.get("sizing_decision_at"),
                context=f"{decision_context}.sizing_decision_at",
            ),
            context=f"{decision_context}.sizing_decision_at",
        )
        evidence_as_of = _parse_iso_datetime(
            _require_canonical_string_value(
                decision.get("evidence_as_of"),
                context=f"{decision_context}.evidence_as_of",
            ),
            context=f"{decision_context}.evidence_as_of",
        )
        if evidence_as_of > decision_at:
            raise ValueError(f"{decision_context}.evidence_as_of must not be after sizing_decision_at")
        baseline_risk = _require_strict_real_number(
            decision.get("baseline_risk_fraction"),
            context=f"{decision_context}.baseline_risk_fraction",
        )
        final_risk = _require_strict_real_number(
            decision.get("final_risk_fraction"),
            context=f"{decision_context}.final_risk_fraction",
        )
        axes = decision.get("axes")
        if not isinstance(axes, Mapping):
            raise ValueError(f"{decision_context}.axes must be an object")
        normalized_axes: dict[str, dict[str, Any]] = {}
        degraded = False
        for axis_name in _REQUIRED_DYNAMIC_SIZING_AXES:
            axis = axes.get(axis_name)
            axis_context = f"{decision_context}.axes.{axis_name}"
            if not isinstance(axis, Mapping):
                raise ValueError(f"{axis_context} must be an object")
            normalized_axis = dict(axis)
            if not isinstance(normalized_axis.get("degraded"), bool):
                raise ValueError(f"{axis_context}.degraded must be a bool")
            degraded = degraded or bool(normalized_axis["degraded"])
            normalized_axis["risk_multiplier"] = _require_strict_real_number(
                normalized_axis.get("risk_multiplier"),
                context=f"{axis_context}.risk_multiplier",
            )
            normalized_axes[axis_name] = normalized_axis
        override_evidence = decision.get("override_evidence")
        if degraded and final_risk > baseline_risk and override_evidence is None:
            raise ValueError(
                f"{decision_context} must not increase risk during degraded conditions without override evidence"
            )
        if override_evidence is not None:
            if not isinstance(override_evidence, Mapping):
                raise ValueError(f"{decision_context}.override_evidence must be an object")
            override = dict(override_evidence)
            for field_name in ("override_id", "approved_by", "reason"):
                override[field_name] = _require_canonical_string_value(
                    override.get(field_name),
                    context=f"{decision_context}.override_evidence.{field_name}",
                )
            decision["override_evidence"] = override
        decision["baseline_risk_fraction"] = baseline_risk
        decision["final_risk_fraction"] = final_risk
        decision["axes"] = normalized_axes
        normalized_decisions.append(decision)
    evidence["decisions"] = normalized_decisions
    return evidence


def _validate_optional_readiness_plans(bundle: BacktestBundle) -> None:
    payloads: list[tuple[Mapping[str, Any], str]] = [(bundle.manifest, f"{bundle.root}/manifest.json")]
    for artifact_name, payload in bundle.artifacts.items():
        payloads.append((payload, f"{bundle.root}/{artifact_name}"))
    for payload, context in payloads:
        _validate_runtime_observability_plan(payload, context=context)
        _validate_rollback_plan(payload, context=context)


def _validate_split_metadata(payload: Mapping[str, Any], *, context: str) -> dict[str, Any]:
    split_metadata = payload.get("split_metadata")
    if not isinstance(split_metadata, Mapping):
        raise ValueError(f"{context}.split_metadata must be an object")
    metadata = dict(split_metadata)
    if metadata.get("schema_version") != "walk_forward_split_metadata.v1":
        raise ValueError(f"{context}.split_metadata.schema_version must be walk_forward_split_metadata.v1")
    for field_name in ("purge_bars", "embargo_bars"):
        value = metadata.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{context}.split_metadata.{field_name} must be a non-negative integer")
    for field_name in ("timestamp_format", "trade_timestamp_basis", "boundary_policy"):
        if field_name not in metadata or metadata[field_name] is None:
            continue
        value = metadata[field_name]
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"{context}.split_metadata.{field_name} must be canonical")
    return metadata


def _validate_window_split_metadata(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context}.split_metadata must be an object")
    metadata = dict(value)
    train_run_ids = metadata.get("train_run_ids")
    test_run_ids = metadata.get("test_run_ids")
    if not isinstance(train_run_ids, list) or not isinstance(test_run_ids, list):
        raise ValueError(f"{context}.split_metadata train/test run_ids must be lists")
    normalized_train = []
    normalized_test = []
    for field_name, raw_values, normalized in (
        ("train_run_ids", train_run_ids, normalized_train),
        ("test_run_ids", test_run_ids, normalized_test),
    ):
        for index, raw_value in enumerate(raw_values):
            if not isinstance(raw_value, str) or not raw_value.strip() or raw_value != raw_value.strip():
                raise ValueError(f"{context}.split_metadata.{field_name}[{index}] must be canonical")
            normalized.append(raw_value)
    if set(normalized_train) & set(normalized_test):
        raise ValueError(f"{context}.split_metadata train/test run_ids must be disjoint")
    return metadata


def _parse_canonical_window_datetime(value: Any, *, context: str) -> datetime:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{context} must be a canonical ISO datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{context} must be a canonical ISO datetime") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{context} must match datetime.isoformat()")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must be timezone-aware")
    return parsed


def _validate_window_periods(row: Mapping[str, Any], *, context: str) -> None:
    present = {period_name for period_name in ("train_period", "test_period") if period_name in row}
    if not present:
        return
    if present != {"train_period", "test_period"}:
        missing = ({"train_period", "test_period"} - present).pop()
        raise ValueError(f"{context}.{missing} must be present")
    parsed: dict[str, dict[str, datetime]] = {}
    for period_name in ("train_period", "test_period"):
        raw_period = row.get(period_name)
        if not isinstance(raw_period, Mapping):
            raise ValueError(f"{context}.{period_name} must be an object")
        parsed[period_name] = {}
        for boundary in ("start", "end"):
            if boundary not in raw_period:
                raise ValueError(f"{context}.{period_name}.{boundary} must be present")
            parsed[period_name][boundary] = _parse_canonical_window_datetime(
                raw_period[boundary],
                context=f"{context}.{period_name}.{boundary}",
            )
        if parsed[period_name]["start"] > parsed[period_name]["end"]:
            raise ValueError(f"{context}.{period_name}.start must be on or before {period_name}.end")
    if parsed["train_period"]["end"] >= parsed["test_period"]["start"]:
        raise ValueError(f"{context}.train_period.end must be before test_period.start")


def _validate_parameter_stability_selected_optimum(payload: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{context}.selected_optimum must be an object")
    parameters = payload.get("parameters")
    if not isinstance(parameters, Mapping) or not parameters:
        raise ValueError(f"{context}.selected_optimum.parameters must be a non-empty object")
    validated_parameters: dict[str, float] = {}
    for raw_name, raw_value in parameters.items():
        name = _require_canonical_string_value(raw_name, context=f"{context}.selected_optimum.parameters key")
        validated_parameters[name] = _require_strict_real_number(
            raw_value,
            context=f"{context}.selected_optimum.parameters.{name}",
        )
    return {
        "parameters": validated_parameters,
        "metric": _require_canonical_string_value(payload.get("metric"), context=f"{context}.selected_optimum.metric"),
        "value": _require_strict_real_number(payload.get("value"), context=f"{context}.selected_optimum.value"),
    }


def _validate_parameter_stability_surface(payload: Any, *, context: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"{context}.stability_surface must be a non-empty list")
    validated_surface: list[dict[str, Any]] = []
    parameter_names: set[str] = set()
    for index, raw_row in enumerate(payload):
        row_context = f"{context}.stability_surface[{index}]"
        if not isinstance(raw_row, Mapping):
            raise ValueError(f"{row_context} must be an object")
        parameter_name = _require_canonical_string_value(raw_row.get("parameter_name"), context=f"{row_context}.parameter_name")
        if parameter_name in parameter_names:
            raise ValueError(f"{context}.stability_surface.parameter_name values must be unique")
        parameter_names.add(parameter_name)
        tested_values = raw_row.get("tested_values")
        if not isinstance(tested_values, list) or not tested_values:
            raise ValueError(f"{row_context}.tested_values must be a non-empty list")
        validated_tested_values = [
            _require_strict_real_number(value, context=f"{row_context}.tested_values[{value_index}]")
            for value_index, value in enumerate(tested_values)
        ]
        if len(set(validated_tested_values)) != len(validated_tested_values):
            raise ValueError(f"{row_context}.tested_values must be unique")
        tested_range = _require_mapping(raw_row, "tested_range", context=row_context)
        range_min = _require_strict_real_number(tested_range.get("min"), context=f"{row_context}.tested_range.min")
        range_max = _require_strict_real_number(tested_range.get("max"), context=f"{row_context}.tested_range.max")
        if range_max < range_min:
            raise ValueError(f"{row_context}.tested_range.max must be >= min")
        neighborhood_metrics = _require_mapping(raw_row, "neighborhood_metrics", context=row_context)
        validated_neighborhood_metrics: dict[str, float | int] = {}
        for raw_metric_name, raw_metric_value in neighborhood_metrics.items():
            metric_name = _require_canonical_string_value(
                raw_metric_name,
                context=f"{row_context}.neighborhood_metrics key",
            )
            if metric_name == "neighbor_count":
                validated_neighborhood_metrics[metric_name] = _require_positive_int(
                    neighborhood_metrics,
                    metric_name,
                    context=f"{row_context}.neighborhood_metrics",
                )
            else:
                validated_neighborhood_metrics[metric_name] = _require_strict_real_number(
                    raw_metric_value,
                    context=f"{row_context}.neighborhood_metrics.{metric_name}",
                )
        if "neighbor_count" not in validated_neighborhood_metrics:
            raise ValueError(f"{row_context}.neighborhood_metrics.neighbor_count must be a positive integer")
        validated_surface.append(
            {
                "parameter_name": parameter_name,
                "tested_values": validated_tested_values,
                "tested_range": {"min": range_min, "max": range_max},
                "neighborhood_metrics": validated_neighborhood_metrics,
            }
        )
    return validated_surface


def _validate_parameter_stability_isolated_spike(payload: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{context}.isolated_spike must be an object")
    is_isolated = payload.get("is_isolated")
    if not isinstance(is_isolated, bool):
        raise ValueError(f"{context}.isolated_spike.is_isolated must be a bool")
    rejection_reason = payload.get("rejection_reason")
    if is_isolated:
        rejection_reason = _require_canonical_string_value(
            rejection_reason,
            context=f"{context}.isolated_spike.rejection_reason",
        )
    elif rejection_reason is not None:
        rejection_reason = _require_canonical_string_value(
            rejection_reason,
            context=f"{context}.isolated_spike.rejection_reason",
        )
    return {"is_isolated": is_isolated, "rejection_reason": rejection_reason}


def _validate_parameter_stability_payload(payload: Mapping[str, Any], *, context: str) -> dict[str, Any]:
    validated = dict(payload)
    validated["parameter_stability_score"] = _require_bounded_ratio(
        payload.get("parameter_stability_score"),
        context=f"{context}.parameter_stability_score",
    )
    validated["stability_score_threshold"] = _require_bounded_ratio(
        payload.get("stability_score_threshold"),
        context=f"{context}.stability_score_threshold",
    )
    validated["selected_optimum"] = _validate_parameter_stability_selected_optimum(
        payload.get("selected_optimum"),
        context=context,
    )
    validated["stability_surface"] = _validate_parameter_stability_surface(
        payload.get("stability_surface"),
        context=context,
    )
    validated["isolated_spike"] = _validate_parameter_stability_isolated_spike(
        payload.get("isolated_spike"),
        context=context,
    )
    return validated


def _require_regime_stratified_oos_evidence(payload: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{context}.regime_stratified_oos must be present")
    evidence = dict(payload)
    if evidence.get("schema_version") != "regime_stratified_oos.v1":
        raise ValueError(f"{context}.regime_stratified_oos.schema_version must be regime_stratified_oos.v1")
    required_buckets = evidence.get("required_buckets")
    if not isinstance(required_buckets, list):
        raise ValueError(f"{context}.regime_stratified_oos.required_buckets must be a list")
    normalized_required = [
        _require_canonical_string_value(bucket, context=f"{context}.regime_stratified_oos.required_buckets[{index}]")
        for index, bucket in enumerate(required_buckets)
    ]
    expected_required = set(_REQUIRED_REGIME_STRATIFIED_OOS_BUCKETS)
    if set(normalized_required) != expected_required:
        raise ValueError(
            f"{context}.regime_stratified_oos.required_buckets must include "
            f"{', '.join(_REQUIRED_REGIME_STRATIFIED_OOS_BUCKETS)}"
        )
    buckets = evidence.get("buckets")
    if not isinstance(buckets, list) or not buckets:
        raise ValueError(f"{context}.regime_stratified_oos.buckets must be a non-empty list")
    seen_buckets: set[str] = set()
    validated_buckets: list[dict[str, Any]] = []
    collapsed_buckets: list[str] = []
    for index, raw_bucket in enumerate(buckets):
        bucket_context = f"{context}.regime_stratified_oos.buckets[{index}]"
        if not isinstance(raw_bucket, Mapping):
            raise ValueError(f"{bucket_context} must be an object")
        bucket_name = _require_canonical_string_value(raw_bucket.get("bucket"), context=f"{bucket_context}.bucket")
        if bucket_name in seen_buckets:
            raise ValueError(f"{context}.regime_stratified_oos.buckets bucket values must be unique")
        seen_buckets.add(bucket_name)
        metrics = _require_mapping(raw_bucket, "metrics", context=bucket_context)
        validated_metrics: dict[str, Any] = {}
        for metric_name in _REGIME_STRATIFIED_OOS_NUMERIC_METRICS:
            validated_metrics[metric_name] = _require_strict_real_number(
                metrics.get(metric_name),
                context=f"{bucket_context}.metrics.{metric_name}",
            )
        validated_metrics["trade_count"] = _require_non_negative_int(
            metrics,
            "trade_count",
            context=f"{bucket_context}.metrics",
        )
        if validated_metrics["total_return"] < 0.0:
            collapsed_buckets.append(bucket_name)
        validated_buckets.append({"bucket": bucket_name, "metrics": validated_metrics})
    for required_bucket in _REQUIRED_REGIME_STRATIFIED_OOS_BUCKETS:
        if required_bucket not in seen_buckets:
            raise ValueError(f"{context}.regime_stratified_oos.buckets must include required bucket {required_bucket}")
    return {
        "schema_version": "regime_stratified_oos.v1",
        "required_buckets": list(_REQUIRED_REGIME_STRATIFIED_OOS_BUCKETS),
        "buckets": validated_buckets,
        "collapsed_buckets": collapsed_buckets,
    }


def _require_pnl_attribution_evidence(
    raw_evidence: Any,
    *,
    context: str,
    reported_pnl: float,
) -> dict[str, Any]:
    if not isinstance(raw_evidence, Mapping):
        raise ValueError(f"{context}.pnl_attribution must be present for positive PnL claims")
    evidence = dict(raw_evidence)
    if evidence.get("schema_version") != "pnl_attribution.v1":
        raise ValueError(f"{context}.pnl_attribution.schema_version must be pnl_attribution.v1")
    evidence_reported_pnl = _require_strict_real_number(
        evidence.get("reported_pnl"),
        context=f"{context}.pnl_attribution.reported_pnl",
    )
    if abs(evidence_reported_pnl - reported_pnl) > _PNL_ATTRIBUTION_TOLERANCE:
        raise ValueError(f"{context}.pnl_attribution.reported_pnl must match reported PnL")
    buckets = evidence.get("buckets")
    if not isinstance(buckets, list) or not buckets:
        raise ValueError(f"{context}.pnl_attribution.buckets must be a non-empty list")
    seen_buckets: set[str] = set()
    validated_buckets: list[dict[str, Any]] = []
    total_contribution = 0.0
    for index, raw_bucket in enumerate(buckets):
        bucket_context = f"{context}.pnl_attribution.buckets[{index}]"
        if not isinstance(raw_bucket, Mapping):
            raise ValueError(f"{bucket_context} must be an object")
        bucket = _require_canonical_bucket_identity(raw_bucket.get("bucket"), context=f"{bucket_context}.bucket")
        if bucket in seen_buckets:
            raise ValueError(f"{context}.pnl_attribution.buckets bucket values must be unique")
        seen_buckets.add(bucket)
        contribution = _require_strict_real_number(
            raw_bucket.get("contribution"),
            context=f"{bucket_context}.contribution",
        )
        total_contribution += contribution
        validated_buckets.append({"bucket": bucket, "contribution": contribution})
    for required_bucket in _REQUIRED_PNL_ATTRIBUTION_BUCKETS:
        if required_bucket not in seen_buckets:
            raise ValueError(f"{context}.pnl_attribution.buckets must include required bucket {required_bucket}")
    if abs(total_contribution - reported_pnl) > _PNL_ATTRIBUTION_TOLERANCE:
        raise ValueError(f"{context}.pnl_attribution.total_contribution must materially match reported_pnl")
    return {
        "schema_version": "pnl_attribution.v1",
        "reported_pnl": evidence_reported_pnl,
        "buckets": validated_buckets,
        "total_contribution": total_contribution,
    }


def _require_canonical_utc_timestamp(value: Any, *, context: str) -> datetime:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{context} must be a canonical UTC Z timestamp")
    if not value.endswith("Z"):
        raise ValueError(f"{context} must be a canonical UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{context} must be a canonical UTC Z timestamp") from exc
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise ValueError(f"{context} must be a canonical UTC Z timestamp")
    return parsed


def _require_non_negative_strict_number(value: Any, *, context: str) -> float:
    parsed = _require_strict_real_number(value, context=context)
    if parsed < 0.0:
        raise ValueError(f"{context} must be non-negative")
    return parsed


def _portfolio_exposure_limits(limits: Any, *, context: str) -> dict[str, float]:
    if not isinstance(limits, Mapping):
        raise ValueError(f"{context}.limits must be an object")
    raw_limits = dict(limits)
    required = (
        "max_net_exposure_pct",
        "max_gross_exposure_pct",
        "max_symbol_gross_exposure_pct",
        "max_cluster_gross_exposure_pct",
        "max_pairwise_correlation",
        "max_crowded_risk_score",
    )
    return {
        field: _require_non_negative_strict_number(raw_limits.get(field), context=f"{context}.limits.{field}")
        for field in required
    }


def _portfolio_symbol_rows(
    rows: Any,
    *,
    context: str,
    limits: Mapping[str, float],
    breaches: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{context}.symbols must be a non-empty list")
    seen_symbols: set[str] = set()
    validated: list[dict[str, Any]] = []
    for index, raw_row in enumerate(rows):
        row_context = f"{context}.symbols[{index}]"
        if not isinstance(raw_row, Mapping):
            raise ValueError(f"{row_context} must be an object")
        symbol = _require_canonical_bucket_identity(raw_row.get("symbol"), context=f"{row_context}.symbol")
        if symbol in seen_symbols:
            raise ValueError(f"{context}.symbols symbol values must be unique")
        seen_symbols.add(symbol)
        cluster = _require_canonical_bucket_identity(raw_row.get("cluster"), context=f"{row_context}.cluster")
        gross = _require_non_negative_strict_number(
            raw_row.get("gross_exposure_pct"),
            context=f"{row_context}.gross_exposure_pct",
        )
        net = _require_strict_real_number(raw_row.get("net_exposure_pct"), context=f"{row_context}.net_exposure_pct")
        if gross > limits["max_symbol_gross_exposure_pct"]:
            breaches.append(f"{symbol} gross exposure exceeds configured limit")
        validated.append({"symbol": symbol, "cluster": cluster, "gross_exposure_pct": gross, "net_exposure_pct": net})
    return validated


def _portfolio_cluster_rows(
    rows: Any,
    *,
    context: str,
    limits: Mapping[str, float],
    breaches: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{context}.clusters must be a non-empty list")
    seen_clusters: set[str] = set()
    validated: list[dict[str, Any]] = []
    for index, raw_row in enumerate(rows):
        row_context = f"{context}.clusters[{index}]"
        if not isinstance(raw_row, Mapping):
            raise ValueError(f"{row_context} must be an object")
        cluster = _require_canonical_bucket_identity(raw_row.get("cluster"), context=f"{row_context}.cluster")
        if cluster in seen_clusters:
            raise ValueError(f"{context}.clusters cluster values must be unique")
        seen_clusters.add(cluster)
        gross = _require_non_negative_strict_number(
            raw_row.get("gross_exposure_pct"),
            context=f"{row_context}.gross_exposure_pct",
        )
        net = _require_strict_real_number(raw_row.get("net_exposure_pct"), context=f"{row_context}.net_exposure_pct")
        if gross > limits["max_cluster_gross_exposure_pct"]:
            breaches.append(f"{cluster} cluster gross exposure exceeds configured limit")
        validated.append({"cluster": cluster, "gross_exposure_pct": gross, "net_exposure_pct": net})
    return validated


def _portfolio_correlation_rows(
    rows: Any,
    *,
    context: str,
    limits: Mapping[str, float],
    breaches: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError(f"{context}.correlations must be a list")
    validated: list[dict[str, Any]] = []
    for index, raw_row in enumerate(rows):
        row_context = f"{context}.correlations[{index}]"
        if not isinstance(raw_row, Mapping):
            raise ValueError(f"{row_context} must be an object")
        left = _require_canonical_bucket_identity(raw_row.get("left_symbol"), context=f"{row_context}.left_symbol")
        right = _require_canonical_bucket_identity(raw_row.get("right_symbol"), context=f"{row_context}.right_symbol")
        if left == right:
            raise ValueError(f"{row_context}.left_symbol and right_symbol must differ")
        correlation = _require_strict_real_number(raw_row.get("correlation"), context=f"{row_context}.correlation")
        if abs(correlation) > limits["max_pairwise_correlation"]:
            breaches.append(f"{left}/{right} pairwise correlation exceeds configured limit")
        validated.append({"left_symbol": left, "right_symbol": right, "correlation": correlation})
    return validated


def _portfolio_crowded_risk(
    raw_value: Any,
    *,
    context: str,
    limits: Mapping[str, float],
    breaches: list[str],
) -> dict[str, Any]:
    if not isinstance(raw_value, Mapping):
        raise ValueError(f"{context}.crowded_risk must be an object")
    score = _require_non_negative_strict_number(raw_value.get("score"), context=f"{context}.crowded_risk.score")
    if score > limits["max_crowded_risk_score"]:
        breaches.append("crowded risk score exceeds configured limit")
    evidence = raw_value.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError(f"{context}.crowded_risk.evidence must be a non-empty list")
    return {
        "score": score,
        "evidence": [
            _require_canonical_bucket_identity(item, context=f"{context}.crowded_risk.evidence[{index}]")
            for index, item in enumerate(evidence)
        ],
    }


def _explicit_portfolio_risk_hold(raw_value: Any, *, context: str) -> dict[str, Any] | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, Mapping):
        raise ValueError(f"{context}.risk_hold must be an object")
    active = raw_value.get("active")
    if active is not True:
        raise ValueError(f"{context}.risk_hold.active must be true")
    reason = _require_canonical_bucket_identity(raw_value.get("reason"), context=f"{context}.risk_hold.reason")
    return {"active": True, "reason": reason}


def _require_portfolio_correlation_exposure_evidence(payload: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{context}.portfolio_correlation_exposure must be present for positive OOS evidence")
    evidence = dict(payload)
    if evidence.get("schema_version") != _PORTFOLIO_CORRELATION_EXPOSURE_SCHEMA_VERSION:
        raise ValueError(
            f"{context}.portfolio_correlation_exposure.schema_version must be "
            f"{_PORTFOLIO_CORRELATION_EXPOSURE_SCHEMA_VERSION}"
        )
    field_context = f"{context}.portfolio_correlation_exposure"
    as_of = _require_canonical_utc_timestamp(evidence.get("as_of"), context=f"{field_context}.as_of")
    decision_timestamp = _require_canonical_utc_timestamp(
        evidence.get("decision_timestamp"),
        context=f"{field_context}.decision_timestamp",
    )
    if as_of > decision_timestamp:
        raise ValueError(f"{field_context}.as_of must be at or before decision_timestamp")
    max_age_seconds = _require_non_negative_int(evidence, "max_age_seconds", context=field_context)
    if (decision_timestamp - as_of).total_seconds() > max_age_seconds:
        raise ValueError(f"{field_context}.as_of must not be stale")
    limits = _portfolio_exposure_limits(evidence.get("limits"), context=field_context)
    portfolio = _require_mapping(evidence, "portfolio", context=field_context)
    net_exposure = _require_strict_real_number(
        portfolio.get("net_exposure_pct"),
        context=f"{field_context}.portfolio.net_exposure_pct",
    )
    gross_exposure = _require_non_negative_strict_number(
        portfolio.get("gross_exposure_pct"),
        context=f"{field_context}.portfolio.gross_exposure_pct",
    )
    breaches: list[str] = []
    if abs(net_exposure) > limits["max_net_exposure_pct"]:
        breaches.append("portfolio net exposure exceeds configured limit")
    if gross_exposure > limits["max_gross_exposure_pct"]:
        breaches.append("portfolio gross exposure exceeds configured limit")
    symbols = _portfolio_symbol_rows(evidence.get("symbols"), context=field_context, limits=limits, breaches=breaches)
    clusters = _portfolio_cluster_rows(evidence.get("clusters"), context=field_context, limits=limits, breaches=breaches)
    correlations = _portfolio_correlation_rows(
        evidence.get("correlations", []),
        context=field_context,
        limits=limits,
        breaches=breaches,
    )
    crowded_risk = _portfolio_crowded_risk(
        evidence.get("crowded_risk"),
        context=field_context,
        limits=limits,
        breaches=breaches,
    )
    risk_hold = _explicit_portfolio_risk_hold(evidence.get("risk_hold"), context=field_context)
    return {
        "schema_version": _PORTFOLIO_CORRELATION_EXPOSURE_SCHEMA_VERSION,
        "as_of": evidence["as_of"],
        "decision_timestamp": evidence["decision_timestamp"],
        "max_age_seconds": max_age_seconds,
        "limits": limits,
        "portfolio": {
            "net_exposure_pct": net_exposure,
            "gross_exposure_pct": gross_exposure,
        },
        "symbols": symbols,
        "clusters": clusters,
        "correlations": correlations,
        "crowded_risk": crowded_risk,
        "breaches": breaches,
        **({"risk_hold": risk_hold} if risk_hold is not None else {}),
    }


def _capacity_limits(raw_value: Any, *, context: str) -> dict[str, float]:
    if not isinstance(raw_value, Mapping):
        raise ValueError(f"{context}.limits must be an object")
    required = (
        "max_capital_usdt",
        "max_position_notional_usdt",
        "max_turnover_ratio",
        "max_slippage_bps",
        "max_impact_deterioration_bps",
    )
    limits: dict[str, float] = {}
    for field in required:
        parsed = _require_non_negative_strict_number(raw_value.get(field), context=f"{context}.limits.{field}")
        if parsed <= 0.0:
            raise ValueError(f"{context}.limits.{field} must be positive")
        limits[field] = parsed
    return limits


def _capacity_provenance(raw_value: Any, *, context: str) -> dict[str, dict[str, str]]:
    if not isinstance(raw_value, Mapping):
        raise ValueError(f"{context}.provenance must be an object")
    normalized: dict[str, dict[str, str]] = {}
    for field in ("liquidity", "impact", "assumptions"):
        item = raw_value.get(field)
        if not isinstance(item, Mapping):
            raise ValueError(f"{context}.provenance.{field} must be an object")
        source = _require_canonical_bucket_identity(item.get("source"), context=f"{context}.provenance.{field}.source")
        artifact_ref = _require_canonical_string_value(
            item.get("artifact_ref"),
            context=f"{context}.provenance.{field}.artifact_ref",
        )
        if Path(artifact_ref).is_absolute() or ".." in Path(artifact_ref).parts or artifact_ref != str(Path(artifact_ref)):
            raise ValueError(f"{context}.provenance.{field}.artifact_ref must be a safe canonical relative path")
        normalized[field] = {"source": source, "artifact_ref": artifact_ref}
    return normalized


def _capacity_symbol_rows(
    rows: Any,
    *,
    context: str,
    breaches: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{context}.symbols must be a non-empty list")
    seen_symbols: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw_row in enumerate(rows):
        row_context = f"{context}.symbols[{index}]"
        if not isinstance(raw_row, Mapping):
            raise ValueError(f"{row_context} must be an object")
        symbol = _require_canonical_bucket_identity(raw_row.get("symbol"), context=f"{row_context}.symbol")
        if symbol in seen_symbols:
            raise ValueError(f"{context}.symbols symbol values must be unique")
        seen_symbols.add(symbol)
        claimed = _require_non_negative_strict_number(
            raw_row.get("claimed_capacity_usdt"),
            context=f"{row_context}.claimed_capacity_usdt",
        )
        max_capacity = _require_non_negative_strict_number(
            raw_row.get("max_capacity_usdt"),
            context=f"{row_context}.max_capacity_usdt",
        )
        if claimed > max_capacity:
            breaches.append(f"{symbol} claimed capacity exceeds symbol limit")
        liquidity_regime = _require_canonical_bucket_identity(
            raw_row.get("liquidity_regime"),
            context=f"{row_context}.liquidity_regime",
        )
        impact_bps = _require_non_negative_strict_number(raw_row.get("impact_bps"), context=f"{row_context}.impact_bps")
        slippage_bps = _require_non_negative_strict_number(
            raw_row.get("slippage_bps"),
            context=f"{row_context}.slippage_bps",
        )
        normalized.append(
            {
                "symbol": symbol,
                "claimed_capacity_usdt": claimed,
                "max_capacity_usdt": max_capacity,
                "liquidity_regime": liquidity_regime,
                "impact_bps": impact_bps,
                "slippage_bps": slippage_bps,
            }
        )
    return normalized


def _require_drawdown_anatomy_evidence(payload: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{context}.drawdown_anatomy must be present for positive OOS evidence")
    evidence = dict(payload)
    if evidence.get("schema_version") != _DRAWDOWN_ANATOMY_SCHEMA_VERSION:
        raise ValueError(f"{context}.drawdown_anatomy.schema_version must be {_DRAWDOWN_ANATOMY_SCHEMA_VERSION}")
    field_context = f"{context}.drawdown_anatomy"
    as_of = _require_canonical_utc_timestamp(evidence.get("as_of"), context=f"{field_context}.as_of")
    decision_timestamp = _require_canonical_utc_timestamp(
        evidence.get("decision_timestamp"),
        context=f"{field_context}.decision_timestamp",
    )
    if as_of > decision_timestamp:
        raise ValueError(f"{field_context}.as_of must be at or before decision_timestamp")
    max_age_seconds = _require_non_negative_int(evidence, "max_age_seconds", context=field_context)
    if (decision_timestamp - as_of).total_seconds() > max_age_seconds:
        raise ValueError(f"{field_context}.as_of must not be stale")
    severe_threshold = _require_non_negative_strict_number(
        evidence.get("severe_drawdown_threshold_pct"),
        context=f"{field_context}.severe_drawdown_threshold_pct",
    )
    raw_drawdowns = evidence.get("drawdowns")
    if not isinstance(raw_drawdowns, list) or not raw_drawdowns:
        raise ValueError(f"{field_context}.drawdowns must be a non-empty list")
    seen_cluster_ids: set[tuple[str, str, str]] = set()
    drawdowns: list[dict[str, Any]] = []
    for index, raw_drawdown in enumerate(raw_drawdowns):
        row_context = f"{field_context}.drawdowns[{index}]"
        if not isinstance(raw_drawdown, Mapping):
            raise ValueError(f"{row_context} must be an object")
        row = dict(raw_drawdown)
        drawdown_id = _require_canonical_bucket_identity(row.get("drawdown_id"), context=f"{row_context}.drawdown_id")
        severity_pct = _require_non_negative_strict_number(
            row.get("severity_pct"),
            context=f"{row_context}.severity_pct",
        )
        peak_timestamp = _require_canonical_utc_timestamp(
            row.get("peak_timestamp"),
            context=f"{row_context}.peak_timestamp",
        )
        trough_timestamp = _require_canonical_utc_timestamp(
            row.get("trough_timestamp"),
            context=f"{row_context}.trough_timestamp",
        )
        recovery_timestamp = _require_canonical_utc_timestamp(
            row.get("recovery_timestamp"),
            context=f"{row_context}.recovery_timestamp",
        )
        if peak_timestamp > trough_timestamp:
            raise ValueError(f"{row_context}.peak_timestamp must be at or before trough_timestamp")
        if trough_timestamp > recovery_timestamp:
            raise ValueError(f"{row_context}.trough_timestamp must be at or before recovery_timestamp")
        regime_cluster_id = _require_canonical_bucket_identity(
            row.get("regime_cluster_id"),
            context=f"{row_context}.regime_cluster_id",
        )
        symbol_cluster_id = _require_canonical_bucket_identity(
            row.get("symbol_cluster_id"),
            context=f"{row_context}.symbol_cluster_id",
        )
        trade_cluster_id = _require_canonical_bucket_identity(
            row.get("trade_cluster_id"),
            context=f"{row_context}.trade_cluster_id",
        )
        cluster_key = (regime_cluster_id, symbol_cluster_id, trade_cluster_id)
        if cluster_key in seen_cluster_ids:
            raise ValueError(f"{field_context}.drawdowns cluster ids must be unique")
        seen_cluster_ids.add(cluster_key)
        attribution = _require_mapping(row, "attribution", context=row_context)
        normalized_attribution = {
            "edge_failure_pct": _require_non_negative_strict_number(
                attribution.get("edge_failure_pct"),
                context=f"{row_context}.attribution.edge_failure_pct",
            ),
            "execution_failure_pct": _require_non_negative_strict_number(
                attribution.get("execution_failure_pct"),
                context=f"{row_context}.attribution.execution_failure_pct",
            ),
            "risk_control_failure_pct": _require_non_negative_strict_number(
                attribution.get("risk_control_failure_pct"),
                context=f"{row_context}.attribution.risk_control_failure_pct",
            ),
            "primary_failure": _require_canonical_bucket_identity(
                attribution.get("primary_failure"),
                context=f"{row_context}.attribution.primary_failure",
            ),
        }
        if severity_pct >= severe_threshold and normalized_attribution["primary_failure"] not in _DRAWDOWN_FAILURE_TYPES:
            raise ValueError(f"{row_context}.attribution.primary_failure must explain severe drawdown")
        exposure = _require_mapping(row, "exposure_concentration", context=row_context)
        normalized_exposure = {
            "max_symbol_exposure_pct": _require_non_negative_strict_number(
                exposure.get("max_symbol_exposure_pct"),
                context=f"{row_context}.exposure_concentration.max_symbol_exposure_pct",
            ),
            "max_cluster_exposure_pct": _require_non_negative_strict_number(
                exposure.get("max_cluster_exposure_pct"),
                context=f"{row_context}.exposure_concentration.max_cluster_exposure_pct",
            ),
            "crowded_risk_score": _require_non_negative_strict_number(
                exposure.get("crowded_risk_score"),
                context=f"{row_context}.exposure_concentration.crowded_risk_score",
            ),
        }
        raw_mitigation_evidence = row.get("mitigation_evidence")
        if not isinstance(raw_mitigation_evidence, list):
            raise ValueError(f"{row_context}.mitigation_evidence must be a list")
        mitigation_evidence = [
            _require_canonical_bucket_identity(item, context=f"{row_context}.mitigation_evidence[{item_index}]")
            for item_index, item in enumerate(raw_mitigation_evidence)
        ]
        if severity_pct >= severe_threshold and not mitigation_evidence:
            raise ValueError(f"{row_context} severe drawdown must include mitigation evidence")
        drawdowns.append(
            {
                "drawdown_id": drawdown_id,
                "severity_pct": severity_pct,
                "peak_timestamp": row["peak_timestamp"],
                "trough_timestamp": row["trough_timestamp"],
                "recovery_timestamp": row["recovery_timestamp"],
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
        "as_of": evidence["as_of"],
        "decision_timestamp": evidence["decision_timestamp"],
        "max_age_seconds": max_age_seconds,
        "severe_drawdown_threshold_pct": severe_threshold,
        "drawdowns": drawdowns,
    }


def _require_capacity_analysis_evidence(payload: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{context}.capacity_analysis_evidence must be present for positive capacity claims")
    evidence = dict(payload)
    field_context = f"{context}.capacity_analysis_evidence"
    if evidence.get("schema_version") != _CAPACITY_ANALYSIS_EVIDENCE_SCHEMA_VERSION:
        raise ValueError(
            f"{field_context}.schema_version must be {_CAPACITY_ANALYSIS_EVIDENCE_SCHEMA_VERSION}"
        )
    source = _require_mapping(evidence, "evidence_source", context=field_context)
    source_type = _require_canonical_bucket_identity(source.get("type"), context=f"{field_context}.evidence_source.type")
    if source_type != "capacity_analysis_report":
        raise ValueError(f"{field_context}.evidence_source.type must be capacity_analysis_report")
    _require_canonical_bucket_identity(source.get("run_id"), context=f"{field_context}.evidence_source.run_id")
    if "exported_at" in source:
        _require_canonical_utc_timestamp(source["exported_at"], context=f"{field_context}.evidence_source.exported_at")
    as_of = _require_canonical_utc_timestamp(evidence.get("as_of"), context=f"{field_context}.as_of")
    decision_timestamp = _require_canonical_utc_timestamp(
        evidence.get("decision_timestamp"),
        context=f"{field_context}.decision_timestamp",
    )
    if as_of > decision_timestamp:
        raise ValueError(f"{field_context}.as_of must be at or before decision_timestamp")
    current_date = datetime(2026, 5, 16, tzinfo=as_of.tzinfo).date()
    if as_of.date() > current_date:
        raise ValueError(f"{field_context}.as_of must not be future dated")
    if as_of.date() != current_date:
        raise ValueError(f"{field_context}.as_of must be current")
    checks = _require_mapping(evidence, "checks", context=field_context)
    normalized_checks: dict[str, bool] = {}
    for check_name in _REQUIRED_CAPACITY_CHECKS:
        if not isinstance(checks.get(check_name), bool):
            raise ValueError(f"{field_context}.checks.{check_name} must be a bool")
        normalized_checks[check_name] = bool(checks[check_name])
    limits = _capacity_limits(evidence.get("limits"), context=field_context)
    summary = _require_mapping(evidence, "summary", context=field_context)
    claimed_capacity = _require_non_negative_strict_number(
        summary.get("claimed_capacity_usdt"),
        context=f"{field_context}.summary.claimed_capacity_usdt",
    )
    capital_required = _require_non_negative_strict_number(
        summary.get("capital_required_usdt"),
        context=f"{field_context}.summary.capital_required_usdt",
    )
    turnover = _require_non_negative_strict_number(
        summary.get("estimated_turnover_ratio"),
        context=f"{field_context}.summary.estimated_turnover_ratio",
    )
    slippage = _require_non_negative_strict_number(
        summary.get("estimated_slippage_bps"),
        context=f"{field_context}.summary.estimated_slippage_bps",
    )
    impact = _require_non_negative_strict_number(
        summary.get("impact_deterioration_bps"),
        context=f"{field_context}.summary.impact_deterioration_bps",
    )
    liquidity_regime = _require_canonical_bucket_identity(
        summary.get("liquidity_regime"),
        context=f"{field_context}.summary.liquidity_regime",
    )
    breaches: list[str] = []
    if claimed_capacity > limits["max_capital_usdt"]:
        breaches.append("claimed capacity exceeds max capital")
    if capital_required > limits["max_position_notional_usdt"]:
        breaches.append("capital required exceeds max position notional")
    if turnover > limits["max_turnover_ratio"]:
        breaches.append("turnover exceeds configured limit")
    if slippage > limits["max_slippage_bps"]:
        breaches.append("slippage exceeds configured limit")
    if impact > limits["max_impact_deterioration_bps"]:
        breaches.append("impact deterioration exceeds configured limit")
    symbols = _capacity_symbol_rows(evidence.get("symbols"), context=field_context, breaches=breaches)
    provenance = _capacity_provenance(evidence.get("provenance"), context=field_context)
    return {
        "schema_version": _CAPACITY_ANALYSIS_EVIDENCE_SCHEMA_VERSION,
        "evidence_source": dict(source),
        "as_of": evidence["as_of"],
        "decision_timestamp": evidence["decision_timestamp"],
        "checks": normalized_checks,
        "limits": limits,
        "summary": {
            "claimed_capacity_usdt": claimed_capacity,
            "capital_required_usdt": capital_required,
            "estimated_turnover_ratio": turnover,
            "estimated_slippage_bps": slippage,
            "impact_deterioration_bps": impact,
            "liquidity_regime": liquidity_regime,
        },
        "symbols": symbols,
        "provenance": provenance,
        "breaches": breaches,
    }


def _require_rows(payload: Mapping[str, Any], *, context: str) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"{context}.rows must be a list")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            raise ValueError(f"{context}.rows[{index}] must be an object")
        for key in item:
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{context}.rows[{index}] key must be a string")
            if key != key.strip():
                raise ValueError(f"{context}.rows[{index}] key must be canonical")
        normalized.append(dict(item))
    return normalized



def _first_mapping(variants: Mapping[str, Any], *, context: str) -> dict[str, Any]:
    for key, value in variants.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{context} key must be a string")
        if key != key.strip():
            raise ValueError(f"{context} key must be canonical")
        if isinstance(value, dict):
            return dict(value)
        raise ValueError(f"{context}.{key} must be an object")
    raise ValueError(f"{context} must not be empty")



def _validate_manifest(bundle_dir: Path, manifest: Mapping[str, Any]) -> None:
    _require_keys(manifest, keys=_REQUIRED_MANIFEST_FIELDS, context=f"{bundle_dir}/manifest.json")
    for field_name in ("experiment_kind", "dataset_root", "baseline_name", "variant_name", "bundle_name"):
        field_value = manifest.get(field_name)
        if not isinstance(field_value, str) or not field_value.strip():
            raise ValueError(f"{bundle_dir}/manifest.json.{field_name} must be a string")
        if field_value != field_value.strip():
            raise ValueError(f"{bundle_dir}/manifest.json.{field_name} must be canonical")
    expected_bundle_name = f"{manifest['experiment_kind']}__{manifest['baseline_name']}__{manifest['variant_name']}"
    if manifest["bundle_name"] != expected_bundle_name:
        raise ValueError(f"{bundle_dir}/manifest.json.bundle_name must match experiment identity")
    if not Path(manifest["dataset_root"]).is_absolute():
        raise ValueError(f"{bundle_dir}/manifest.json.dataset_root must be an absolute path")
    _require_non_negative_int(manifest, "snapshot_count", context=f"{bundle_dir}/manifest.json")
    sample_period = _require_mapping(manifest, "sample_period", context=f"{bundle_dir}/manifest.json")
    _require_keys(sample_period, keys=("start", "end"), context=f"{bundle_dir}/manifest.json.sample_period")
    for boundary in ("start", "end"):
        boundary_value = sample_period.get(boundary)
        if not isinstance(boundary_value, str) or not boundary_value.strip():
            raise ValueError(f"{bundle_dir}/manifest.json.sample_period.{boundary} must be a string")
        if boundary_value != boundary_value.strip():
            raise ValueError(f"{bundle_dir}/manifest.json.sample_period.{boundary} must be canonical")
    sample_start = _parse_iso_datetime(sample_period["start"], context=f"{bundle_dir}/manifest.json.sample_period.start")
    sample_end = _parse_iso_datetime(sample_period["end"], context=f"{bundle_dir}/manifest.json.sample_period.end")
    if sample_start >= sample_end:
        raise ValueError(f"{bundle_dir}/manifest.json.sample_period start must be before end")
    window_counts = _require_mapping(manifest, "window_counts", context=f"{bundle_dir}/manifest.json")
    for count_name, count_value in window_counts.items():
        if not isinstance(count_name, str) or not count_name.strip():
            raise ValueError(f"{bundle_dir}/manifest.json.window_counts key must be a string")
        if count_name != count_name.strip():
            raise ValueError(f"{bundle_dir}/manifest.json.window_counts.{count_name} key must be canonical")
        if not isinstance(count_value, int) or isinstance(count_value, bool) or count_value < 0:
            raise ValueError(f"{bundle_dir}/manifest.json.window_counts.{count_name} must be a non-negative integer")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError(f"{bundle_dir}/manifest.json.artifacts must be a list of strings")
    seen_artifacts: set[str] = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, str) or not artifact.strip():
            raise ValueError(f"{bundle_dir}/manifest.json.artifacts must be a list of strings")
        if artifact != artifact.strip():
            raise ValueError(f"{bundle_dir}/manifest.json.artifacts[{index}] must be canonical")
        artifact_path = Path(artifact)
        if artifact_path.is_absolute() or ".." in artifact_path.parts or any(part == "" for part in artifact_path.parts):
            raise ValueError(f"{bundle_dir}/manifest.json.artifacts[{index}] must be a safe relative path")
        if artifact in seen_artifacts:
            raise ValueError(f"{bundle_dir}/manifest.json.artifacts[{index}] duplicates {artifact}")
        seen_artifacts.add(artifact)

    experiment_kind = str(manifest["experiment_kind"])
    required_artifacts = _REQUIRED_ARTIFACTS.get(experiment_kind)
    if required_artifacts is None:
        supported = ", ".join(sorted(_REQUIRED_ARTIFACTS))
        raise ValueError(f"unsupported experiment_kind in manifest: {experiment_kind}; supported: {supported}")
    for filename in ("manifest.json", *required_artifacts):
        if filename not in artifacts:
            raise ValueError(f"{bundle_dir}/manifest.json.artifacts is missing {filename}")
        if not (bundle_dir / filename).is_file():
            raise FileNotFoundError(f"missing artifact file: {bundle_dir / filename}")
    _validate_universe_asof_contract(manifest, context=f"{bundle_dir}/manifest.json")
    _validate_margin_liquidation_path_contract(manifest, context=f"{bundle_dir}/manifest.json")
    _validate_dynamic_sizing_evidence_contract(manifest, context=f"{bundle_dir}/manifest.json")



def _validate_full_market_bundle(bundle: BacktestBundle) -> None:
    summary_json = bundle.artifacts["summary.json"]
    summary = _require_mapping(summary_json, "summary", context=f"{bundle.root}/summary.json")
    _require_keys(
        summary,
        keys=("total_return", "max_drawdown", "sharpe", "turnover", "trade_count", "cost_drag", "cost_breakdown"),
        context=f"{bundle.root}/summary.json.summary",
    )

    for numeric_key in ("total_return", "max_drawdown", "sharpe", "turnover", "cost_drag"):
        _require_real_number(summary, numeric_key, context=f"{bundle.root}/summary.json.summary")
    _require_non_negative_int(summary, "trade_count", context=f"{bundle.root}/summary.json.summary")
    if float(summary["total_return"]) > 0.0:
        summary["pnl_attribution"] = _require_pnl_attribution_evidence(
            summary.get("pnl_attribution"),
            context=f"{bundle.root}/summary.json.summary",
            reported_pnl=float(summary["total_return"]),
        )

    breakdowns_json = bundle.artifacts["breakdowns.json"]
    breakdowns = _require_mapping(breakdowns_json, "breakdowns", context=f"{bundle.root}/breakdowns.json")
    _require_keys(breakdowns, keys=("by_market", "by_year"), context=f"{bundle.root}/breakdowns.json.breakdowns")
    for group_name in ("by_market", "by_year"):
        rows = breakdowns.get(group_name)
        if not isinstance(rows, list):
            raise ValueError(f"{bundle.root}/breakdowns.json.breakdowns.{group_name} must be a list")
        identity_key = "market_type" if group_name == "by_market" else "year"
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ValueError(f"{bundle.root}/breakdowns.json.breakdowns.{group_name}[{index}] must be an object")
            for row_key in row:
                if not isinstance(row_key, str) or not row_key.strip():
                    raise ValueError(f"{bundle.root}/breakdowns.json.breakdowns.{group_name}[{index}] key must be a string")
                if row_key != row_key.strip():
                    raise ValueError(f"{bundle.root}/breakdowns.json.breakdowns.{group_name}[{index}] key must be canonical")
            identity_value = row.get(identity_key)
            if not isinstance(identity_value, str) or not identity_value.strip():
                raise ValueError(f"{bundle.root}/breakdowns.json.breakdowns.{group_name}[{index}].{identity_key} must be a string")
            if identity_value != identity_value.strip():
                raise ValueError(f"{bundle.root}/breakdowns.json.breakdowns.{group_name}[{index}].{identity_key} must be canonical")
            _require_non_negative_int(row, "trade_count", context=f"{bundle.root}/breakdowns.json.breakdowns.{group_name}[{index}]")
            _require_real_number(row, "net_pnl", context=f"{bundle.root}/breakdowns.json.breakdowns.{group_name}[{index}]")

    audit_json = bundle.artifacts["audit.json"]
    audit = _require_mapping(audit_json, "audit", context=f"{bundle.root}/audit.json")
    _require_keys(audit, keys=("trade_count", "rejection_reasons"), context=f"{bundle.root}/audit.json.audit")
    _require_non_negative_int(audit, "trade_count", context=f"{bundle.root}/audit.json.audit")
    rejection_reasons = _require_mapping(audit, "rejection_reasons", context=f"{bundle.root}/audit.json.audit")
    for reason, count in rejection_reasons.items():
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{bundle.root}/audit.json.audit.rejection_reasons key must be a string")
        if reason != reason.strip():
            raise ValueError(f"{bundle.root}/audit.json.audit.rejection_reasons key must be canonical")
        if not _is_safe_evidence_identifier(reason):
            raise ValueError(f"{bundle.root}/audit.json.audit.rejection_reasons key must be a safe identifier")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"{bundle.root}/audit.json.audit.rejection_reasons.{reason} must be a non-negative integer")



def _validate_rotation_bundle(bundle: BacktestBundle) -> None:
    summary = bundle.artifacts["summary.json"]
    _require_keys(summary, keys=("metadata", "policies", "opportunity_kill_rate", "avoid_loss_rate"), context=f"{bundle.root}/summary.json")
    policies = _require_mapping(summary, "policies", context=f"{bundle.root}/summary.json")
    for policy_name in ("current", "soft_suppression"):
        policy = _require_mapping(policies, policy_name, context=f"{bundle.root}/summary.json.policies")
        _require_keys(policy, keys=("bucket_level_pnl", "trade_count"), context=f"{bundle.root}/summary.json.policies.{policy_name}")
        _require_real_number(policy, "bucket_level_pnl", context=f"{bundle.root}/summary.json.policies.{policy_name}")
        _require_non_negative_int(policy, "trade_count", context=f"{bundle.root}/summary.json.policies.{policy_name}")
    for numeric_key in ("opportunity_kill_rate", "avoid_loss_rate"):
        _require_real_number(summary, numeric_key, context=f"{bundle.root}/summary.json")
    _require_rows(bundle.artifacts["comparison_rows.json"], context=f"{bundle.root}/comparison_rows.json")
    scorecard = bundle.artifacts["scorecard.json"]
    key_metrics = _require_mapping(scorecard, "key_metrics", context=f"{bundle.root}/scorecard.json")
    _require_keys(
        key_metrics,
        keys=("current_bucket_level_pnl", "soft_suppression_bucket_level_pnl", "opportunity_kill_rate", "avoid_loss_rate"),
        context=f"{bundle.root}/scorecard.json.key_metrics",
    )
    for numeric_key in ("current_bucket_level_pnl", "soft_suppression_bucket_level_pnl", "opportunity_kill_rate", "avoid_loss_rate"):
        _require_real_number(key_metrics, numeric_key, context=f"{bundle.root}/scorecard.json.key_metrics")



def _validate_allocator_bundle(bundle: BacktestBundle) -> None:
    summary = bundle.artifacts["summary.json"]
    variants = _require_mapping(summary, "variants", context=f"{bundle.root}/summary.json")
    if len(variants) > 1:
        _require_multiple_testing_correction(
            bundle.artifacts["scorecard.json"],
            context=f"{bundle.root}/scorecard.json",
            expected_trials=len(variants),
        )
    variant = _require_mapping(variants, "current_allocator", context=f"{bundle.root}/summary.json.variants")
    allocation_summary = _require_mapping(variant, "allocation_summary", context=f"{bundle.root}/summary.json.variants.current_allocator")
    _require_keys(allocation_summary, keys=("accepted_allocations",), context=f"{bundle.root}/summary.json.variants.allocation_summary")
    _require_non_negative_int(
        allocation_summary,
        "accepted_allocations",
        context=f"{bundle.root}/summary.json.variants.allocation_summary",
    )
    frictions = _require_mapping(variant, "frictions", context=f"{bundle.root}/summary.json.variants")
    base = _require_mapping(frictions, "base", context=f"{bundle.root}/summary.json.variants.frictions")
    _require_keys(base, keys=("net_bucket_pnl", "cost_drag", "trade_count"), context=f"{bundle.root}/summary.json.variants.frictions.base")
    for numeric_key in ("net_bucket_pnl", "cost_drag"):
        _require_real_number(base, numeric_key, context=f"{bundle.root}/summary.json.variants.frictions.base")
    _require_non_negative_int(base, "trade_count", context=f"{bundle.root}/summary.json.variants.frictions.base")
    _require_rows(bundle.artifacts["comparison_rows.json"], context=f"{bundle.root}/comparison_rows.json")
    scorecard = bundle.artifacts["scorecard.json"]
    key_metrics = _require_mapping(scorecard, "key_metrics", context=f"{bundle.root}/scorecard.json")
    _require_keys(
        key_metrics,
        keys=("best_base_net_bucket_pnl", "best_stressed_net_bucket_pnl", "current_allocator_base_cost_drag"),
        context=f"{bundle.root}/scorecard.json.key_metrics",
    )
    for numeric_key in ("best_base_net_bucket_pnl", "best_stressed_net_bucket_pnl", "current_allocator_base_cost_drag"):
        _require_real_number(key_metrics, numeric_key, context=f"{bundle.root}/scorecard.json.key_metrics")



def _validate_engine_bundle(bundle: BacktestBundle) -> None:
    summary = bundle.artifacts["summary.json"]
    variants = _require_mapping(summary, "variants", context=f"{bundle.root}/summary.json")
    if len(variants) > 1:
        _require_multiple_testing_correction(
            bundle.artifacts["scorecard.json"],
            context=f"{bundle.root}/scorecard.json",
            expected_trials=len(variants),
        )
    variant = _first_mapping(variants, context=f"{bundle.root}/summary.json.variants")
    _require_mapping(variant, "funnel", context=f"{bundle.root}/summary.json.variants")
    _require_mapping(variant, "filter_counts", context=f"{bundle.root}/summary.json.variants")
    _require_mapping(variant, "performance", context=f"{bundle.root}/summary.json.variants")
    scorecard = bundle.artifacts["scorecard.json"]
    key_metrics = _require_mapping(scorecard, "key_metrics", context=f"{bundle.root}/scorecard.json")
    _require_keys(
        key_metrics,
        keys=("best_bucket_level_pnl", "best_variant_accepted_allocations"),
        context=f"{bundle.root}/scorecard.json.key_metrics",
    )
    _require_real_number(key_metrics, "best_bucket_level_pnl", context=f"{bundle.root}/scorecard.json.key_metrics")
    _require_non_negative_int(key_metrics, "best_variant_accepted_allocations", context=f"{bundle.root}/scorecard.json.key_metrics")



def _validate_walk_forward_bundle(bundle: BacktestBundle) -> None:
    if "split_metadata" in bundle.manifest:
        _validate_split_metadata(bundle.manifest, context=f"{bundle.root}/manifest.json")
    if "split_metadata" in bundle.artifacts["summary.json"].get("metadata", {}):
        summary_metadata = _require_mapping(bundle.artifacts["summary.json"], "metadata", context=f"{bundle.root}/summary.json")
        _validate_split_metadata(summary_metadata, context=f"{bundle.root}/summary.json.metadata")
    summary = bundle.artifacts["summary.json"]
    robustness_summary = _require_mapping(summary, "robustness_summary", context=f"{bundle.root}/summary.json")
    out_of_sample_scorecard = _require_mapping(robustness_summary, "out_of_sample_scorecard", context=f"{bundle.root}/summary.json.robustness_summary")
    _require_keys(
        out_of_sample_scorecard,
        keys=("total_return", "max_drawdown", "sharpe"),
        context=f"{bundle.root}/summary.json.robustness_summary.out_of_sample_scorecard",
    )
    for numeric_key in ("total_return", "max_drawdown", "sharpe"):
        _require_real_number(
            out_of_sample_scorecard,
            numeric_key,
            context=f"{bundle.root}/summary.json.robustness_summary.out_of_sample_scorecard",
        )
    if float(out_of_sample_scorecard["total_return"]) > 0.0:
        attribution = _require_pnl_attribution_evidence(
            summary.get("pnl_attribution"),
            context=f"{bundle.root}/summary.json",
            reported_pnl=float(out_of_sample_scorecard["total_return"]),
        )
        summary["pnl_attribution"] = attribution
        if "dynamic_sizing_evidence" in summary:
            summary["dynamic_sizing_evidence"] = _require_dynamic_sizing_evidence(
                summary["dynamic_sizing_evidence"],
                context=f"{bundle.root}/summary.json",
            )
        if "pnl_attribution" in bundle.artifacts["scorecard.json"]:
            bundle.artifacts["scorecard.json"]["pnl_attribution"] = _require_pnl_attribution_evidence(
                bundle.artifacts["scorecard.json"]["pnl_attribution"],
                context=f"{bundle.root}/scorecard.json",
                reported_pnl=float(out_of_sample_scorecard["total_return"]),
            )
        if "dynamic_sizing_evidence" in bundle.artifacts["scorecard.json"]:
            bundle.artifacts["scorecard.json"]["dynamic_sizing_evidence"] = _require_dynamic_sizing_evidence(
                bundle.artifacts["scorecard.json"]["dynamic_sizing_evidence"],
                context=f"{bundle.root}/scorecard.json",
            )
    performance_dispersion = _require_mapping(robustness_summary, "performance_dispersion", context=f"{bundle.root}/summary.json.robustness_summary")
    _require_keys(
        performance_dispersion,
        keys=("positive_window_ratio",),
        context=f"{bundle.root}/summary.json.robustness_summary.performance_dispersion",
    )
    _require_real_number(
        performance_dispersion,
        "positive_window_ratio",
        context=f"{bundle.root}/summary.json.robustness_summary.performance_dispersion",
    )
    worst_window = _require_mapping(robustness_summary, "worst_window", context=f"{bundle.root}/summary.json.robustness_summary")
    worst_window_scorecard = _require_mapping(worst_window, "scorecard", context=f"{bundle.root}/summary.json.robustness_summary.worst_window")
    _require_keys(
        worst_window_scorecard,
        keys=("total_return",),
        context=f"{bundle.root}/summary.json.robustness_summary.worst_window.scorecard",
    )
    _require_real_number(
        worst_window_scorecard,
        "total_return",
        context=f"{bundle.root}/summary.json.robustness_summary.worst_window.scorecard",
    )
    parameter_stability = _require_mapping(summary, "parameter_stability", context=f"{bundle.root}/summary.json")
    summary["parameter_stability"] = _validate_parameter_stability_payload(
        parameter_stability,
        context=f"{bundle.root}/summary.json.parameter_stability",
    )
    if "regime_stratified_oos" in summary:
        summary["regime_stratified_oos"] = _require_regime_stratified_oos_evidence(
            summary.get("regime_stratified_oos"),
            context=f"{bundle.root}/summary.json",
        )
    if "portfolio_correlation_exposure" in summary:
        summary["portfolio_correlation_exposure"] = _require_portfolio_correlation_exposure_evidence(
            summary.get("portfolio_correlation_exposure"),
            context=f"{bundle.root}/summary.json",
        )
    if "capacity_analysis_evidence" in summary:
        summary["capacity_analysis_evidence"] = _require_capacity_analysis_evidence(
            summary.get("capacity_analysis_evidence"),
            context=f"{bundle.root}/summary.json",
        )
    if "drawdown_anatomy" in summary:
        summary["drawdown_anatomy"] = _require_drawdown_anatomy_evidence(
            summary.get("drawdown_anatomy"),
            context=f"{bundle.root}/summary.json",
        )
    windows = _require_rows(bundle.artifacts["windows.json"], context=f"{bundle.root}/windows.json")
    _require_multiple_testing_correction(
        bundle.artifacts["scorecard.json"],
        context=f"{bundle.root}/scorecard.json",
        expected_trials=max(len(windows), 2),
    )
    for index, row in enumerate(windows):
        _validate_window_periods(row, context=f"{bundle.root}/windows.json.rows[{index}]")
        if "split_metadata" in row:
            _validate_window_split_metadata(row["split_metadata"], context=f"{bundle.root}/windows.json.rows[{index}]")
        out_of_sample = _require_mapping(row, "out_of_sample", context=f"{bundle.root}/windows.json.rows[{index}]")
        scorecard_row = _require_mapping(out_of_sample, "scorecard", context=f"{bundle.root}/windows.json.rows[{index}].out_of_sample")
        _require_real_number(
            scorecard_row,
            "total_return",
            context=f"{bundle.root}/windows.json.rows[{index}].out_of_sample.scorecard",
        )
    scorecard = bundle.artifacts["scorecard.json"]
    key_metrics = _require_mapping(scorecard, "key_metrics", context=f"{bundle.root}/scorecard.json")
    _require_keys(
        key_metrics,
        keys=("out_of_sample_total_return", "positive_window_ratio", "parameter_stability_score"),
        context=f"{bundle.root}/scorecard.json.key_metrics",
    )
    for numeric_key in ("out_of_sample_total_return", "positive_window_ratio", "parameter_stability_score"):
        _require_real_number(key_metrics, numeric_key, context=f"{bundle.root}/scorecard.json.key_metrics")
    if "regime_stratified_oos" in scorecard:
        scorecard["regime_stratified_oos"] = _require_regime_stratified_oos_evidence(
            scorecard["regime_stratified_oos"],
            context=f"{bundle.root}/scorecard.json",
        )
    if "portfolio_correlation_exposure" in scorecard:
        scorecard["portfolio_correlation_exposure"] = _require_portfolio_correlation_exposure_evidence(
            scorecard["portfolio_correlation_exposure"],
            context=f"{bundle.root}/scorecard.json",
        )
    if "capacity_analysis_evidence" in scorecard:
        scorecard["capacity_analysis_evidence"] = _require_capacity_analysis_evidence(
            scorecard["capacity_analysis_evidence"],
            context=f"{bundle.root}/scorecard.json",
        )
    if "drawdown_anatomy" in scorecard:
        scorecard["drawdown_anatomy"] = _require_drawdown_anatomy_evidence(
            scorecard["drawdown_anatomy"],
            context=f"{bundle.root}/scorecard.json",
        )



def load_backtest_bundle(path: str | Path) -> BacktestBundle:
    bundle_dir = Path(path)
    if not bundle_dir.is_dir():
        raise FileNotFoundError(f"bundle directory does not exist: {bundle_dir}")

    manifest = _read_json(bundle_dir / "manifest.json")
    _validate_manifest(bundle_dir, manifest)
    experiment_kind = str(manifest["experiment_kind"])
    artifacts = {filename: _read_json(bundle_dir / filename) for filename in _REQUIRED_ARTIFACTS[experiment_kind]}
    bundle = BacktestBundle(root=bundle_dir, manifest=manifest, artifacts=artifacts)

    validators = {
        "full_market_baseline": _validate_full_market_bundle,
        "rotation_suppression": _validate_rotation_bundle,
        "allocator_friction": _validate_allocator_bundle,
        "engine_filter_ablation": _validate_engine_bundle,
        "walk_forward_validation": _validate_walk_forward_bundle,
    }
    validators[experiment_kind](bundle)
    _validate_optional_readiness_plans(bundle)
    return bundle



def _scorecard_metrics(bundle: BacktestBundle) -> dict[str, Any]:
    scorecard = bundle.artifacts.get("scorecard.json")
    if scorecard is None:
        return {}
    metrics = scorecard.get("key_metrics")
    if not isinstance(metrics, dict):
        return {}
    return dict(metrics)



def _metric_snapshot(bundle: BacktestBundle) -> dict[str, float]:
    if bundle.experiment_kind == "full_market_baseline":
        summary = _require_mapping(bundle.artifacts["summary.json"], "summary", context=f"{bundle.root}/summary.json")
        return {
            "total_return": float(summary.get("total_return", 0.0)),
            "max_drawdown": float(summary.get("max_drawdown", 0.0)),
            "sharpe": float(summary.get("sharpe", 0.0)),
            "cost_drag": float(summary.get("cost_drag", 0.0)),
        }

    if bundle.experiment_kind == "walk_forward_validation":
        summary = bundle.artifacts["summary.json"]
        robustness_summary = _require_mapping(summary, "robustness_summary", context=f"{bundle.root}/summary.json")
        out_of_sample = _require_mapping(robustness_summary, "out_of_sample_scorecard", context=f"{bundle.root}/summary.json.robustness_summary")
        metrics = _scorecard_metrics(bundle)
        return {
            "total_return": float(out_of_sample.get("total_return", 0.0)),
            "max_drawdown": float(out_of_sample.get("max_drawdown", 0.0)),
            "sharpe": float(out_of_sample.get("sharpe", 0.0)),
            "cost_drag": 0.0,
            "positive_window_ratio": float(metrics.get("positive_window_ratio", 0.0)),
            "parameter_stability_score": float(metrics.get("parameter_stability_score", 0.0)),
        }

    metrics = _scorecard_metrics(bundle)
    snapshot = {
        "total_return": 0.0,
        "max_drawdown": 0.0,
        "sharpe": 0.0,
        "cost_drag": 0.0,
    }
    if bundle.experiment_kind == "rotation_suppression":
        summary = bundle.artifacts["summary.json"]
        policies = _require_mapping(summary, "policies", context=f"{bundle.root}/summary.json")
        soft_policy = _require_mapping(policies, "soft_suppression", context=f"{bundle.root}/summary.json.policies")
        snapshot.update(
            {
                "total_return": float(soft_policy.get("bucket_level_pnl", 0.0)),
                "opportunity_kill_rate": float(summary.get("opportunity_kill_rate", 0.0)),
                "avoid_loss_rate": float(summary.get("avoid_loss_rate", 0.0)),
                "trade_count": float(soft_policy.get("trade_count", 0.0)),
            }
        )
        return snapshot
    if bundle.experiment_kind == "allocator_friction":
        variants = _require_mapping(bundle.artifacts["summary.json"], "variants", context=f"{bundle.root}/summary.json")
        current_variant = _require_mapping(variants, "current_allocator", context=f"{bundle.root}/summary.json.variants")
        allocation_summary = _require_mapping(
            current_variant,
            "allocation_summary",
            context=f"{bundle.root}/summary.json.variants.current_allocator",
        )
        snapshot.update(
            {
                "total_return": _require_real_number(
                    metrics,
                    "best_base_net_bucket_pnl",
                    context=f"{bundle.root}/scorecard.json.key_metrics",
                ),
                "cost_drag": _require_real_number(
                    metrics,
                    "current_allocator_base_cost_drag",
                    context=f"{bundle.root}/scorecard.json.key_metrics",
                ),
                "accepted_allocations": float(
                    _require_non_negative_int(
                        allocation_summary,
                        "accepted_allocations",
                        context=f"{bundle.root}/summary.json.variants.allocation_summary",
                    )
                ),
            }
        )
        return snapshot
    if bundle.experiment_kind == "engine_filter_ablation":
        snapshot.update(
            {
                "total_return": float(metrics.get("best_bucket_level_pnl", 0.0)),
                "accepted_allocations": float(metrics.get("best_variant_accepted_allocations", 0.0)),
            }
        )
        return snapshot
    return snapshot



def _metric_deltas(baseline: BacktestBundle, variant: BacktestBundle) -> dict[str, float]:
    baseline_metrics = _metric_snapshot(baseline)
    variant_metrics = _metric_snapshot(variant)
    keys = sorted(set(baseline_metrics) | set(variant_metrics))
    deltas = {
        key: round(float(variant_metrics.get(key, 0.0)) - float(baseline_metrics.get(key, 0.0)), 10)
        for key in keys
    }
    for required_key in ("total_return", "max_drawdown", "sharpe", "cost_drag"):
        deltas.setdefault(required_key, 0.0)
    return deltas



def _variant_summary(bundle: BacktestBundle) -> dict[str, Any]:
    summary = bundle.artifacts["summary.json"]
    if bundle.experiment_kind == "full_market_baseline":
        return _require_mapping(summary, "summary", context=f"{bundle.root}/summary.json")
    return summary



def _has_baseline_variant_pair(baseline: BacktestBundle, variant: BacktestBundle) -> bool:
    return (
        baseline.experiment_kind == variant.experiment_kind
        and str(baseline.manifest.get("baseline_name", "")) == str(variant.manifest.get("baseline_name", ""))
        and str(baseline.manifest.get("bundle_name", "")) != str(variant.manifest.get("bundle_name", ""))
    )



def _ensure_comparable_manifests(baseline: BacktestBundle, variant: BacktestBundle) -> None:
    comparable_fields = ("dataset_root", "sample_period", "window_counts", "snapshot_count")
    mismatches = [
        field_name
        for field_name in comparable_fields
        if baseline.manifest.get(field_name) != variant.manifest.get(field_name)
    ]
    if mismatches:
        joined = ", ".join(mismatches)
        raise ValueError(f"baseline and variant bundles must share the same dataset/sample contract: {joined}")

    baseline_correction = _scorecard_multiple_testing_correction(baseline)
    variant_correction = _scorecard_multiple_testing_correction(variant)
    if baseline_correction is not None and variant_correction is not None:
        if baseline_correction["number_of_trials"] != variant_correction["number_of_trials"]:
            raise ValueError("multiple_testing_correction.number_of_trials must match")


def _scorecard_multiple_testing_correction(bundle: BacktestBundle) -> dict[str, Any] | None:
    scorecard = bundle.artifacts.get("scorecard.json")
    if not isinstance(scorecard, Mapping) or "multiple_testing_correction" not in scorecard:
        return None
    return _require_multiple_testing_correction(scorecard, context=f"{bundle.root}/scorecard.json")



def _has_cost_adjusted_edge(baseline: BacktestBundle, variant: BacktestBundle) -> bool:
    if variant.experiment_kind == "full_market_baseline":
        deltas = _metric_deltas(baseline, variant)
        return deltas["total_return"] > 0.0 and deltas["sharpe"] >= 0.0

    if variant.experiment_kind == "rotation_suppression":
        baseline_summary = baseline.artifacts["summary.json"]
        variant_summary = variant.artifacts["summary.json"]
        baseline_policies = _require_mapping(baseline_summary, "policies", context=f"{baseline.root}/summary.json")
        variant_policies = _require_mapping(variant_summary, "policies", context=f"{variant.root}/summary.json")
        baseline_soft = _require_mapping(baseline_policies, "soft_suppression", context=f"{baseline.root}/summary.json.policies")
        baseline_current = _require_mapping(baseline_policies, "current", context=f"{baseline.root}/summary.json.policies")
        variant_soft = _require_mapping(variant_policies, "soft_suppression", context=f"{variant.root}/summary.json.policies")
        variant_current = _require_mapping(variant_policies, "current", context=f"{variant.root}/summary.json.policies")
        baseline_edge = float(baseline_soft.get("bucket_level_pnl", 0.0)) - float(baseline_current.get("bucket_level_pnl", 0.0))
        variant_edge = float(variant_soft.get("bucket_level_pnl", 0.0)) - float(variant_current.get("bucket_level_pnl", 0.0))
        return variant_edge > baseline_edge and float(variant_summary.get("avoid_loss_rate", 0.0)) >= float(variant_summary.get("opportunity_kill_rate", 0.0))

    if variant.experiment_kind == "allocator_friction":
        baseline_metrics = _scorecard_metrics(baseline)
        variant_metrics = _scorecard_metrics(variant)
        return float(variant_metrics.get("best_base_net_bucket_pnl", 0.0)) > float(baseline_metrics.get("best_base_net_bucket_pnl", 0.0))

    if variant.experiment_kind == "engine_filter_ablation":
        baseline_metrics = _scorecard_metrics(baseline)
        variant_metrics = _scorecard_metrics(variant)
        return float(variant_metrics.get("best_bucket_level_pnl", 0.0)) > float(baseline_metrics.get("best_bucket_level_pnl", 0.0))

    if variant.experiment_kind == "walk_forward_validation":
        deltas = _metric_deltas(baseline, variant)
        return deltas["total_return"] > 0.0

    return False



def _has_out_of_sample_evidence(bundle: BacktestBundle) -> bool:
    if bundle.experiment_kind != "walk_forward_validation":
        return False
    summary = bundle.artifacts["summary.json"]
    robustness_summary = _require_mapping(summary, "robustness_summary", context=f"{bundle.root}/summary.json")
    out_of_sample = _require_mapping(robustness_summary, "out_of_sample_scorecard", context=f"{bundle.root}/summary.json.robustness_summary")
    windows = _require_rows(bundle.artifacts["windows.json"], context=f"{bundle.root}/windows.json")
    return bool(windows) and "total_return" in out_of_sample


def _has_purged_embargoed_split_metadata(bundle: BacktestBundle) -> bool:
    if bundle.experiment_kind != "walk_forward_validation":
        return True
    summary_metadata = bundle.artifacts["summary.json"].get("metadata", {})
    if not (isinstance(bundle.manifest.get("split_metadata"), Mapping) or (
        isinstance(summary_metadata, Mapping) and isinstance(summary_metadata.get("split_metadata"), Mapping)
    )):
        return False
    windows = _require_rows(bundle.artifacts["windows.json"], context=f"{bundle.root}/windows.json")
    if not windows:
        return False
    for row in windows:
        split_metadata = row.get("split_metadata")
        if not isinstance(split_metadata, Mapping):
            return False
        train_run_ids = split_metadata.get("train_run_ids")
        test_run_ids = split_metadata.get("test_run_ids")
        if not isinstance(train_run_ids, list) or not isinstance(test_run_ids, list):
            return False
        if set(train_run_ids) & set(test_run_ids):
            return False
    return True



def _has_explanation(bundle: BacktestBundle) -> bool:
    if bundle.experiment_kind == "full_market_baseline":
        breakdowns = _require_mapping(bundle.artifacts["breakdowns.json"], "breakdowns", context=f"{bundle.root}/breakdowns.json")
        audit = _require_mapping(bundle.artifacts["audit.json"], "audit", context=f"{bundle.root}/audit.json")
        return bool(breakdowns.get("by_market")) and isinstance(audit.get("rejection_reasons"), dict)

    summary = bundle.artifacts["summary.json"]
    if bundle.experiment_kind == "rotation_suppression":
        return "opportunity_kill_rate" in summary and "avoid_loss_rate" in summary
    if bundle.experiment_kind == "allocator_friction":
        variants = _require_mapping(summary, "variants", context=f"{bundle.root}/summary.json")
        variant = _first_mapping(variants, context=f"{bundle.root}/summary.json.variants")
        return "allocation_summary" in variant and "frictions" in variant
    if bundle.experiment_kind == "engine_filter_ablation":
        variants = _require_mapping(summary, "variants", context=f"{bundle.root}/summary.json")
        variant = _first_mapping(variants, context=f"{bundle.root}/summary.json.variants")
        return "funnel" in variant and "filter_counts" in variant and "performance" in variant
    if bundle.experiment_kind == "walk_forward_validation":
        robustness_summary = _require_mapping(summary, "robustness_summary", context=f"{bundle.root}/summary.json")
        return "performance_dispersion" in robustness_summary and (
            "worst_window" in robustness_summary or "worst_window" in summary
        )
    return False



def _has_runtime_observability_plan(bundle: BacktestBundle) -> bool:
    payloads = [bundle.manifest, *bundle.artifacts.values()]
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        runtime_observability = payload.get("runtime_observability")
        if isinstance(runtime_observability, dict):
            runtime_fields = runtime_observability.get("runtime_fields")
            if isinstance(runtime_fields, list) and all(isinstance(item, str) for item in runtime_fields) and runtime_fields:
                return True
        runtime_observability_plan = payload.get("runtime_observability_plan")
        if isinstance(runtime_observability_plan, dict):
            runtime_fields = runtime_observability_plan.get("runtime_fields")
            if isinstance(runtime_fields, list) and all(isinstance(item, str) for item in runtime_fields) and runtime_fields:
                return True
    return False



def _has_rollback_plan(bundle: BacktestBundle) -> bool:
    payloads = [bundle.manifest, *bundle.artifacts.values()]
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        rollback_plan = payload.get("rollback_plan")
        if not isinstance(rollback_plan, dict):
            continue
        rollback_target = rollback_plan.get("rollback_target")
        if not isinstance(rollback_target, str) or not rollback_target.strip():
            continue
        if any(
            isinstance(rollback_plan.get(key), str) and str(rollback_plan.get(key)).strip()
            for key in ("rollback_trigger", "observation_window")
        ):
            return True
    return False


def _has_parameter_stability_surface(bundle: BacktestBundle) -> bool:
    if bundle.experiment_kind != "walk_forward_validation":
        return False
    parameter_stability = bundle.artifacts["summary.json"].get("parameter_stability")
    if not isinstance(parameter_stability, Mapping):
        return False
    return bool(
        parameter_stability.get("stability_surface")
        and parameter_stability.get("selected_optimum")
        and "stability_score_threshold" in parameter_stability
        and parameter_stability.get("isolated_spike") is not None
    )


def _isolated_spike_rejection_reason(bundle: BacktestBundle) -> str | None:
    if bundle.experiment_kind != "walk_forward_validation":
        return None
    parameter_stability = bundle.artifacts["summary.json"].get("parameter_stability")
    if not isinstance(parameter_stability, Mapping):
        return None
    isolated_spike = parameter_stability.get("isolated_spike")
    if not isinstance(isolated_spike, Mapping) or isolated_spike.get("is_isolated") is not True:
        return None
    rejection_reason = isolated_spike.get("rejection_reason")
    return rejection_reason if isinstance(rejection_reason, str) else "isolated_spike_optimum"


def _regime_stratified_oos_evidence(bundle: BacktestBundle) -> dict[str, Any] | None:
    if bundle.experiment_kind != "walk_forward_validation":
        return None
    summary = bundle.artifacts["summary.json"]
    if "regime_stratified_oos" in summary:
        return _require_regime_stratified_oos_evidence(summary["regime_stratified_oos"], context=f"{bundle.root}/summary.json")
    scorecard = bundle.artifacts["scorecard.json"]
    if "regime_stratified_oos" in scorecard:
        return _require_regime_stratified_oos_evidence(scorecard["regime_stratified_oos"], context=f"{bundle.root}/scorecard.json")
    return None


def _pnl_attribution_evidence(bundle: BacktestBundle) -> dict[str, Any] | None:
    if bundle.experiment_kind == "full_market_baseline":
        summary = _require_mapping(bundle.artifacts["summary.json"], "summary", context=f"{bundle.root}/summary.json")
        if "pnl_attribution" in summary:
            return _require_pnl_attribution_evidence(
                summary["pnl_attribution"],
                context=f"{bundle.root}/summary.json.summary",
                reported_pnl=float(summary.get("total_return", 0.0)),
            )
        return None
    if bundle.experiment_kind == "walk_forward_validation":
        summary = bundle.artifacts["summary.json"]
        robustness_summary = _require_mapping(summary, "robustness_summary", context=f"{bundle.root}/summary.json")
        out_of_sample = _require_mapping(
            robustness_summary,
            "out_of_sample_scorecard",
            context=f"{bundle.root}/summary.json.robustness_summary",
        )
        reported_pnl = float(out_of_sample.get("total_return", 0.0))
        if "pnl_attribution" in summary:
            return _require_pnl_attribution_evidence(
                summary["pnl_attribution"],
                context=f"{bundle.root}/summary.json",
                reported_pnl=reported_pnl,
            )
        scorecard = bundle.artifacts["scorecard.json"]
        if "pnl_attribution" in scorecard:
            return _require_pnl_attribution_evidence(
                scorecard["pnl_attribution"],
                context=f"{bundle.root}/scorecard.json",
                reported_pnl=reported_pnl,
            )
    return None


def _has_pnl_attribution_evidence(bundle: BacktestBundle) -> bool:
    snapshot = _metric_snapshot(bundle)
    if snapshot.get("total_return", 0.0) <= 0.0:
        return True
    return _pnl_attribution_evidence(bundle) is not None


def _dynamic_sizing_evidence(bundle: BacktestBundle) -> dict[str, Any] | None:
    if bundle.experiment_kind == "walk_forward_validation":
        summary = bundle.artifacts["summary.json"]
        if "dynamic_sizing_evidence" in summary:
            return _require_dynamic_sizing_evidence(
                summary["dynamic_sizing_evidence"],
                context=f"{bundle.root}/summary.json",
            )
        scorecard = bundle.artifacts["scorecard.json"]
        if "dynamic_sizing_evidence" in scorecard:
            return _require_dynamic_sizing_evidence(
                scorecard["dynamic_sizing_evidence"],
                context=f"{bundle.root}/scorecard.json",
            )
    return None


def _has_dynamic_sizing_evidence(bundle: BacktestBundle) -> bool:
    if bundle.experiment_kind != "walk_forward_validation":
        return True
    snapshot = _metric_snapshot(bundle)
    if snapshot.get("total_return", 0.0) <= 0.0:
        return True
    return _dynamic_sizing_evidence(bundle) is not None


def _has_regime_stratified_oos_evidence(bundle: BacktestBundle) -> bool:
    if bundle.experiment_kind != "walk_forward_validation":
        return True
    return _regime_stratified_oos_evidence(bundle) is not None


def _portfolio_correlation_exposure_evidence(bundle: BacktestBundle) -> dict[str, Any] | None:
    if bundle.experiment_kind != "walk_forward_validation":
        return None
    summary = bundle.artifacts["summary.json"]
    if "portfolio_correlation_exposure" in summary:
        return _require_portfolio_correlation_exposure_evidence(
            summary["portfolio_correlation_exposure"],
            context=f"{bundle.root}/summary.json",
        )
    scorecard = bundle.artifacts["scorecard.json"]
    if "portfolio_correlation_exposure" in scorecard:
        return _require_portfolio_correlation_exposure_evidence(
            scorecard["portfolio_correlation_exposure"],
            context=f"{bundle.root}/scorecard.json",
        )
    return None


def _has_portfolio_correlation_exposure_evidence(bundle: BacktestBundle) -> bool:
    if bundle.experiment_kind != "walk_forward_validation":
        return True
    return _portfolio_correlation_exposure_evidence(bundle) is not None


def _portfolio_correlation_exposure_breaches(bundle: BacktestBundle) -> list[str]:
    evidence = _portfolio_correlation_exposure_evidence(bundle)
    if evidence is None:
        return []
    if evidence.get("risk_hold", {}).get("active") is True:
        return []
    return list(evidence.get("breaches", []))


def _capacity_analysis_evidence(bundle: BacktestBundle) -> dict[str, Any] | None:
    if bundle.experiment_kind != "walk_forward_validation":
        return None
    summary = bundle.artifacts["summary.json"]
    if "capacity_analysis_evidence" in summary:
        return _require_capacity_analysis_evidence(
            summary["capacity_analysis_evidence"],
            context=f"{bundle.root}/summary.json",
        )
    scorecard = bundle.artifacts["scorecard.json"]
    if "capacity_analysis_evidence" in scorecard:
        return _require_capacity_analysis_evidence(
            scorecard["capacity_analysis_evidence"],
            context=f"{bundle.root}/scorecard.json",
        )
    return None


def _drawdown_anatomy_evidence(bundle: BacktestBundle) -> dict[str, Any] | None:
    if bundle.experiment_kind != "walk_forward_validation":
        return None
    summary = bundle.artifacts["summary.json"]
    if "drawdown_anatomy" in summary:
        return _require_drawdown_anatomy_evidence(
            summary["drawdown_anatomy"],
            context=f"{bundle.root}/summary.json",
        )
    scorecard = bundle.artifacts["scorecard.json"]
    if "drawdown_anatomy" in scorecard:
        return _require_drawdown_anatomy_evidence(
            scorecard["drawdown_anatomy"],
            context=f"{bundle.root}/scorecard.json",
        )
    return None


def _has_capacity_analysis_evidence(bundle: BacktestBundle) -> bool:
    if bundle.experiment_kind != "walk_forward_validation":
        return True
    snapshot = _metric_snapshot(bundle)
    if snapshot.get("total_return", 0.0) <= 0.0:
        return True
    return _capacity_analysis_evidence(bundle) is not None


def _has_drawdown_anatomy_evidence(bundle: BacktestBundle) -> bool:
    if bundle.experiment_kind != "walk_forward_validation":
        return True
    snapshot = _metric_snapshot(bundle)
    if snapshot.get("total_return", 0.0) <= 0.0:
        return True
    return _drawdown_anatomy_evidence(bundle) is not None


def _capacity_analysis_breaches(bundle: BacktestBundle) -> list[str]:
    evidence = _capacity_analysis_evidence(bundle)
    if evidence is None:
        return []
    breaches = list(evidence.get("breaches", []))
    checks = evidence.get("checks", {})
    if isinstance(checks, Mapping):
        breaches.extend(
            check_name
            for check_name in _REQUIRED_CAPACITY_CHECKS
            if checks.get(check_name) is not True
        )
    return breaches


def _collapsed_regime_oos_buckets(bundle: BacktestBundle) -> list[str]:
    evidence = _regime_stratified_oos_evidence(bundle)
    if evidence is None:
        return []
    return list(evidence.get("collapsed_buckets", []))



def _out_of_sample_collapses(bundle: BacktestBundle) -> bool:
    if bundle.experiment_kind != "walk_forward_validation":
        return False
    summary = bundle.artifacts["summary.json"]
    robustness_summary = _require_mapping(summary, "robustness_summary", context=f"{bundle.root}/summary.json")
    out_of_sample = _require_mapping(robustness_summary, "out_of_sample_scorecard", context=f"{bundle.root}/summary.json.robustness_summary")
    performance_dispersion = _require_mapping(robustness_summary, "performance_dispersion", context=f"{bundle.root}/summary.json.robustness_summary")
    worst_window = robustness_summary.get("worst_window", summary.get("worst_window", {}))
    worst_window_total_return = 0.0
    if isinstance(worst_window, dict):
        scorecard = worst_window.get("scorecard", {})
        if isinstance(scorecard, dict):
            worst_window_total_return = float(scorecard.get("total_return", 0.0))
    out_of_sample_total_return = float(out_of_sample.get("total_return", 0.0))
    positive_window_ratio = float(performance_dispersion.get("positive_window_ratio", 0.0))
    return out_of_sample_total_return < 0.0 or positive_window_ratio < 0.5 or worst_window_total_return < 0.0



def _why(
    checks: Mapping[str, bool],
    *,
    out_of_sample_collapses: bool,
    isolated_spike_rejection_reason: str | None,
) -> list[str]:
    reasons: list[str] = []
    if not checks["has_baseline_variant_pair"]:
        reasons.append("missing baseline vs variant pair")
    if not checks["has_cost_adjusted_edge"]:
        reasons.append("cost-adjusted edge disappears")
    if not checks["has_out_of_sample_evidence"]:
        reasons.append("missing out-of-sample evidence")
    if not checks["has_purged_embargoed_split_metadata"]:
        reasons.append("missing purged/embargoed walk-forward split metadata")
    if out_of_sample_collapses:
        reasons.append("out-of-sample direction reverses or clearly collapses")
    if not checks["has_attribution_or_funnel_explanation"]:
        reasons.append("missing attribution or funnel explanation")
    if "has_pnl_attribution_evidence" in checks and not checks["has_pnl_attribution_evidence"]:
        reasons.append("missing pnl attribution evidence")
    if "has_dynamic_sizing_evidence" in checks and not checks["has_dynamic_sizing_evidence"]:
        reasons.append("missing dynamic sizing evidence")
    if not checks["has_runtime_observability_plan"]:
        reasons.append("missing runtime observability plan")
    if not checks["has_rollback_plan"]:
        reasons.append("missing rollback plan")
    if "has_parameter_stability_surface" in checks and not checks["has_parameter_stability_surface"]:
        reasons.append("missing parameter stability surface")
    if isolated_spike_rejection_reason is not None:
        reasons.append(f"isolated spike optimum: {isolated_spike_rejection_reason}")
    if "has_regime_stratified_oos_evidence" in checks and not checks["has_regime_stratified_oos_evidence"]:
        reasons.append("missing regime-stratified OOS evidence")
    if "rejects_regime_bucket_collapse" in checks and not checks["rejects_regime_bucket_collapse"]:
        reasons.append("regime-stratified OOS bucket collapses")
    if (
        "has_portfolio_correlation_exposure_evidence" in checks
        and not checks["has_portfolio_correlation_exposure_evidence"]
    ):
        reasons.append("missing portfolio correlation/exposure evidence")
    if (
        "rejects_portfolio_correlation_exposure_breach" in checks
        and not checks["rejects_portfolio_correlation_exposure_breach"]
    ):
        reasons.append("portfolio correlation/exposure evidence breaches configured limits")
    if "has_capacity_analysis_evidence" in checks and not checks["has_capacity_analysis_evidence"]:
        reasons.append("missing capacity analysis evidence")
    if "rejects_capacity_limit_breach" in checks and not checks["rejects_capacity_limit_breach"]:
        reasons.append("capacity analysis evidence breaches configured limits")
    if "has_drawdown_anatomy_evidence" in checks and not checks["has_drawdown_anatomy_evidence"]:
        reasons.append("missing drawdown anatomy evidence")
    return reasons



def _walk_forward_regresses_against_baseline(metric_deltas: Mapping[str, float]) -> bool:
    return (
        metric_deltas.get("positive_window_ratio", 0.0) < 0.0
        or metric_deltas.get("parameter_stability_score", 0.0) < 0.0
        or metric_deltas.get("sharpe", 0.0) < 0.0
    )



def _decision(
    checks: Mapping[str, bool],
    *,
    experiment_kind: str,
    metric_deltas: Mapping[str, float],
    out_of_sample_collapses: bool,
    isolated_spike_rejection_reason: str | None,
) -> str:
    if not checks["has_cost_adjusted_edge"]:
        return "reject"
    if out_of_sample_collapses:
        return "reject"
    if isolated_spike_rejection_reason is not None:
        return "reject"
    if "has_regime_stratified_oos_evidence" in checks and not checks["has_regime_stratified_oos_evidence"]:
        return "reject"
    if "rejects_regime_bucket_collapse" in checks and not checks["rejects_regime_bucket_collapse"]:
        return "reject"
    if (
        "has_portfolio_correlation_exposure_evidence" in checks
        and not checks["has_portfolio_correlation_exposure_evidence"]
    ):
        return "reject"
    if (
        "rejects_portfolio_correlation_exposure_breach" in checks
        and not checks["rejects_portfolio_correlation_exposure_breach"]
    ):
        return "reject"
    if "has_capacity_analysis_evidence" in checks and not checks["has_capacity_analysis_evidence"]:
        return "reject"
    if "rejects_capacity_limit_breach" in checks and not checks["rejects_capacity_limit_breach"]:
        return "reject"
    if "has_drawdown_anatomy_evidence" in checks and not checks["has_drawdown_anatomy_evidence"]:
        return "reject"
    if not checks["has_purged_embargoed_split_metadata"]:
        return "reject"
        return "reject"
    if "has_pnl_attribution_evidence" in checks and not checks["has_pnl_attribution_evidence"]:
        return "reject"
    if "has_dynamic_sizing_evidence" in checks and not checks["has_dynamic_sizing_evidence"]:
        return "reject"
    if not checks["has_out_of_sample_evidence"]:
        return "hold"
    if experiment_kind == "walk_forward_validation" and _walk_forward_regresses_against_baseline(metric_deltas):
        return "hold"
    if not all(checks.values()):
        return "hold"
    return "candidate_for_promotion"



def compare_backtest_bundles(*, baseline_bundle: str | Path, variant_bundle: str | Path) -> dict[str, dict[str, Any]]:
    baseline = load_backtest_bundle(baseline_bundle)
    variant = load_backtest_bundle(variant_bundle)
    if baseline.experiment_kind != variant.experiment_kind:
        raise ValueError(
            "baseline and variant bundles must share the same experiment_kind: "
            f"{baseline.experiment_kind} != {variant.experiment_kind}"
        )
    _ensure_comparable_manifests(baseline, variant)

    checks = {
        "has_baseline_variant_pair": _has_baseline_variant_pair(baseline, variant),
        "has_cost_adjusted_edge": _has_cost_adjusted_edge(baseline, variant),
        "has_out_of_sample_evidence": _has_out_of_sample_evidence(variant),
        "has_purged_embargoed_split_metadata": _has_purged_embargoed_split_metadata(variant),
        "has_attribution_or_funnel_explanation": _has_explanation(variant),
        "has_pnl_attribution_evidence": _has_pnl_attribution_evidence(variant),
        "has_dynamic_sizing_evidence": _has_dynamic_sizing_evidence(variant),
        "has_runtime_observability_plan": _has_runtime_observability_plan(variant),
        "has_rollback_plan": _has_rollback_plan(variant),
    }
    if variant.experiment_kind == "walk_forward_validation":
        checks["has_parameter_stability_surface"] = _has_parameter_stability_surface(variant)
        checks["rejects_isolated_spike_optimum"] = _isolated_spike_rejection_reason(variant) is None
        checks["has_regime_stratified_oos_evidence"] = _has_regime_stratified_oos_evidence(variant)
        checks["rejects_regime_bucket_collapse"] = not _collapsed_regime_oos_buckets(variant)
        checks["has_portfolio_correlation_exposure_evidence"] = _has_portfolio_correlation_exposure_evidence(
            variant
        )
        checks["rejects_portfolio_correlation_exposure_breach"] = not _portfolio_correlation_exposure_breaches(
            variant
        )
        checks["has_capacity_analysis_evidence"] = _has_capacity_analysis_evidence(variant)
        checks["rejects_capacity_limit_breach"] = not _capacity_analysis_breaches(variant)
        checks["has_drawdown_anatomy_evidence"] = _has_drawdown_anatomy_evidence(variant)
    metric_deltas = _metric_deltas(baseline, variant)
    out_of_sample_collapses = _out_of_sample_collapses(variant)
    isolated_spike_rejection_reason = _isolated_spike_rejection_reason(variant)
    collapsed_regime_buckets = _collapsed_regime_oos_buckets(variant)
    portfolio_exposure_breaches = _portfolio_correlation_exposure_breaches(variant)
    capacity_analysis_breaches = _capacity_analysis_breaches(variant)
    why = _why(
        checks,
        out_of_sample_collapses=out_of_sample_collapses,
        isolated_spike_rejection_reason=isolated_spike_rejection_reason,
    )
    if collapsed_regime_buckets:
        collapse_reason = f"regime-stratified OOS bucket collapses: {', '.join(collapsed_regime_buckets)}"
        why = [reason for reason in why if reason != "regime-stratified OOS bucket collapses"]
        why.append(collapse_reason)
    if portfolio_exposure_breaches:
        why = [
            reason
            for reason in why
            if reason != "portfolio correlation/exposure evidence breaches configured limits"
        ]
        why.extend(portfolio_exposure_breaches)
    if capacity_analysis_breaches:
        why = [
            reason
            for reason in why
            if reason != "capacity analysis evidence breaches configured limits"
        ]
        why.extend(capacity_analysis_breaches)
    decision = _decision(
        checks,
        experiment_kind=variant.experiment_kind,
        metric_deltas=metric_deltas,
        out_of_sample_collapses=out_of_sample_collapses,
        isolated_spike_rejection_reason=isolated_spike_rejection_reason,
    )

    promotion_gate = {
        "experiment_kind": variant.experiment_kind,
        "baseline_bundle": str(baseline.root),
        "variant_bundle": str(variant.root),
        "decision": decision,
        "checks": checks,
        "metric_deltas": metric_deltas,
        "why": why,
    }
    regime_evidence = _regime_stratified_oos_evidence(variant)
    if regime_evidence is not None:
        promotion_gate["regime_stratified_oos"] = regime_evidence
    pnl_attribution = _pnl_attribution_evidence(variant)
    if pnl_attribution is not None:
        promotion_gate["pnl_attribution"] = pnl_attribution
    dynamic_sizing_evidence = _dynamic_sizing_evidence(variant)
    if dynamic_sizing_evidence is not None:
        promotion_gate["dynamic_sizing_evidence"] = dynamic_sizing_evidence
    portfolio_correlation_exposure = _portfolio_correlation_exposure_evidence(variant)
    if portfolio_correlation_exposure is not None:
        promotion_gate["portfolio_correlation_exposure"] = portfolio_correlation_exposure
    capacity_analysis_evidence = _capacity_analysis_evidence(variant)
    if capacity_analysis_evidence is not None:
        promotion_gate["capacity_analysis_evidence"] = capacity_analysis_evidence
    drawdown_anatomy = _drawdown_anatomy_evidence(variant)
    if drawdown_anatomy is not None:
        promotion_gate["drawdown_anatomy"] = drawdown_anatomy
    decision_summary = {
        "experiment_kind": variant.experiment_kind,
        "baseline_bundle": str(baseline.root),
        "variant_bundle": str(variant.root),
        "decision": decision,
        "summary": "; ".join(why) if why else "all promotion gate checks passed",
        "why": why,
        "artifacts": ["promotion_gate.json", "decision_summary.json"],
    }
    if dynamic_sizing_evidence is not None:
        decision_summary["dynamic_sizing_evidence"] = dynamic_sizing_evidence
    if capacity_analysis_evidence is not None:
        decision_summary["capacity_analysis_evidence"] = capacity_analysis_evidence
    if drawdown_anatomy is not None:
        decision_summary["drawdown_anatomy"] = drawdown_anatomy
    return {"promotion_gate": promotion_gate, "decision_summary": decision_summary}
