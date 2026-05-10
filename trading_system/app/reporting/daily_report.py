from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


_LIFECYCLE_STATES = ("INIT", "CONFIRM", "PAYLOAD", "PROTECT", "EXIT")
_REVIEW_ACTION_CAP = 5
_TARGET_REVIEW_KEYS = ("target_price", "target_stage", "fraction_basis", "runner_stop_price")
_EXECUTION_TIME_IN_FORCE_VALUES = frozenset({"GTC", "IOC", "FOK", "GTX"})


def _strict_mapping_row(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} rows must be mapping")
    return value


def _strict_mapping_rows(values: Sequence[Any], field: str) -> list[Mapping[str, Any]]:
    return [_strict_mapping_row(value, field) for value in values]


def _strict_number_value(value: Any, key: str, *, default: float | None = None) -> float:
    if value is None:
        if default is None:
            raise ValueError(f"{key} must be finite int or float")
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{key} must be finite int or float")
    return float(value)


def _strict_number_field(payload: Mapping[str, Any], key: str, *, required: bool = False, default: float = 0.0) -> float:
    if key not in payload:
        if required:
            raise ValueError(f"{key} must be finite int or float")
        return default
    return _strict_number_value(payload.get(key), key)


def _strict_bool_field(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be bool")
    return value


def _strict_mapping_field(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be mapping")
    return value


def _strict_string_value(value: Any, key: str, *, default: str = "", required: bool = False) -> str:
    if value is None:
        if required and not default:
            raise ValueError(f"{key} must be non-empty string")
        return default
    if not isinstance(value, str):
        raise ValueError(f"{key} must be string")
    if value != value.strip():
        raise ValueError(f"{key} must be canonical string")
    if required and not value:
        raise ValueError(f"{key} must be non-empty string")
    return value


def _strict_string_field(payload: Mapping[str, Any], key: str, *, default: str = "", required: bool = False) -> str:
    return _strict_string_value(payload.get(key), key, default=default, required=required)


def _strict_optional_string_field(payload: Mapping[str, Any], key: str, *, default: str = "") -> str:
    if key not in payload:
        return default
    return _strict_string_value(payload.get(key), key, required=True)


def _strict_optional_string_allowlist(payload: Mapping[str, Any], key: str, allowed: frozenset[str]) -> str:
    value = _strict_optional_string_field(payload, key)
    if value and value not in allowed:
        raise ValueError(f"{key} must be one of {sorted(allowed)}")
    return value


def _strict_reason_codes(payload: Mapping[str, Any]) -> list[str]:
    raw_codes = payload.get("reason_codes", [])
    if raw_codes is None:
        return []
    if isinstance(raw_codes, (str, bytes)) or not isinstance(raw_codes, Sequence):
        raise ValueError("reason_codes must be a sequence of strings")
    return [_strict_string_value(code, "reason_codes", required=True) for code in raw_codes]


def _rotation_leader_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    timeframe_meta = _strict_mapping_field(candidate, "timeframe_meta")
    relative_strength = _strict_mapping_field(timeframe_meta, "relative_strength")
    liquidity_meta = _strict_mapping_field(candidate, "liquidity_meta")
    return {
        "symbol": _strict_string_field(candidate, "symbol", required=True),
        "score": round(_strict_number_field(candidate, "score", required=True), 6),
        "daily_spread": round(_strict_number_field(relative_strength, "daily_spread"), 6),
        "h4_spread": round(_strict_number_field(relative_strength, "h4_spread"), 6),
        "h1_spread": round(_strict_number_field(relative_strength, "h1_spread"), 6),
        "volume_usdt_24h": _strict_number_field(liquidity_meta, "volume_usdt_24h"),
        "slippage_bps": _strict_number_field(liquidity_meta, "slippage_bps"),
    }


def _short_leader_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    timeframe_meta = _strict_mapping_field(candidate, "timeframe_meta")
    liquidity_meta = _strict_mapping_field(candidate, "liquidity_meta")
    row = {
        "symbol": _strict_string_field(candidate, "symbol", required=True),
        "setup_type": _strict_string_field(candidate, "setup_type", required=True),
        "score": round(_strict_number_field(candidate, "score", required=True), 6),
        "daily_bias": _strict_string_field(timeframe_meta, "daily_bias"),
        "h4_structure": _strict_string_field(timeframe_meta, "h4_structure"),
        "h1_trigger": _strict_string_field(timeframe_meta, "h1_trigger"),
        "derivatives": dict(_strict_mapping_field(timeframe_meta, "derivatives")),
        "volume_usdt_24h": _strict_number_field(liquidity_meta, "volume_usdt_24h"),
        "liquidity_tier": _strict_string_field(liquidity_meta, "liquidity_tier"),
    }
    for key in ("stop_family", "stop_reference", "invalidation_source", "invalidation_reason", "stop_policy_source"):
        value = candidate.get(key)
        if value:
            row[key] = _strict_string_value(value, key, required=True)
    return row


def _trend_leader_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    timeframe_meta = _strict_mapping_field(candidate, "timeframe_meta")
    liquidity_meta = _strict_mapping_field(candidate, "liquidity_meta")
    row = {
        "symbol": _strict_string_field(candidate, "symbol", required=True),
        "setup_type": _strict_string_field(candidate, "setup_type", required=True),
        "score": round(_strict_number_field(candidate, "score", required=True), 6),
        "daily_bias": _strict_string_field(timeframe_meta, "daily_bias"),
        "h4_structure": _strict_string_field(timeframe_meta, "h4_structure"),
        "h1_trigger": _strict_string_field(timeframe_meta, "h1_trigger"),
        "derivatives": dict(_strict_mapping_field(timeframe_meta, "derivatives")),
        "volume_usdt_24h": _strict_number_field(liquidity_meta, "volume_usdt_24h"),
        "liquidity_tier": _strict_string_field(liquidity_meta, "liquidity_tier"),
    }
    for key in ("stop_family", "stop_reference", "invalidation_source", "invalidation_reason", "stop_policy_source"):
        value = candidate.get(key)
        if value:
            row[key] = _strict_string_value(value, key, required=True)
    return row


def _lifecycle_leader_row(symbol: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    row = {
        "symbol": symbol,
        "state": _strict_string_field(payload, "state", default="INIT", required=True).upper(),
        "r_multiple": round(_strict_number_field(payload, "r_multiple"), 6),
        "reason_codes": _strict_reason_codes(payload),
    }
    for key in ("stop_family", "stop_reference", "invalidation_source", "invalidation_reason", "stop_policy_source"):
        value = payload.get(key)
        if value:
            row[key] = _strict_string_value(value, key, required=True)
    for key in ("first_target_hit", "second_target_hit", "runner_protected"):
        if key in payload:
            row[key] = _strict_bool_field(payload, key)
    runner_stop_price = payload.get("runner_stop_price")
    if runner_stop_price is not None:
        row["runner_stop_price"] = round(_strict_number_value(runner_stop_price, "runner_stop_price"), 8)
    if "scale_out_plan" in payload:
        row["scale_out_plan"] = dict(_strict_mapping_field(payload, "scale_out_plan"))
    if "second_target_source" in payload:
        row["second_target_source"] = _strict_string_value(payload.get("second_target_source"), "second_target_source", required=True)
    return row


def _review_action_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    meta = _strict_mapping_field(row, "meta")
    stop_semantics_keys = ("stop_family", "stop_reference", "invalidation_source", "invalidation_reason", "stop_policy_source")
    has_stop_semantics = any(meta.get(key) for key in stop_semantics_keys)

    target_price = meta.get("target_price", row.get("target_price"))
    target_stage = meta.get("target_stage")
    fraction_basis = meta.get("fraction_basis")
    runner_stop_price = meta.get("runner_stop_price")
    qty_fraction = row.get("qty_fraction")
    has_target_management_semantics = any(
        value is not None for value in (target_price, target_stage, fraction_basis, runner_stop_price, qty_fraction)
    )

    if not (has_stop_semantics or has_target_management_semantics):
        return None

    payload = {
        "symbol": _strict_string_field(row, "symbol", required=True),
        "action": _strict_string_field(row, "action", required=True),
        "priority": _strict_string_field(row, "priority", default="MEDIUM", required=True),
    }
    if has_stop_semantics:
        payload.update(
            {
                "stop_family": _strict_string_field(meta, "stop_family"),
                "stop_reference": _strict_string_field(meta, "stop_reference"),
                "invalidation_source": _strict_string_field(meta, "invalidation_source"),
                "invalidation_reason": _strict_string_field(meta, "invalidation_reason"),
                "stop_policy_source": _strict_string_field(meta, "stop_policy_source"),
            }
        )
    suggested_stop_loss = row.get("suggested_stop_loss")
    if suggested_stop_loss is not None:
        payload["suggested_stop_loss"] = round(_strict_number_value(suggested_stop_loss, "suggested_stop_loss"), 8)
    if qty_fraction is not None:
        payload["qty_fraction"] = _strict_number_value(qty_fraction, "qty_fraction")
    if target_price is not None:
        payload["target_price"] = round(_strict_number_value(target_price, "target_price"), 8)
    if target_stage is not None:
        payload["target_stage"] = _strict_string_value(target_stage, "target_stage", required=True)
    if fraction_basis is not None:
        payload["fraction_basis"] = _strict_string_value(fraction_basis, "fraction_basis", required=True)
    if runner_stop_price is not None:
        payload["runner_stop_price"] = round(_strict_number_value(runner_stop_price, "runner_stop_price"), 8)
    return payload


def _is_target_management_review_row(row: Mapping[str, Any]) -> bool:
    return any(key in row for key in _TARGET_REVIEW_KEYS)


def _cap_review_actions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) <= _REVIEW_ACTION_CAP:
        return rows

    selected_indices = list(range(_REVIEW_ACTION_CAP))
    target_indices = [idx for idx, row in enumerate(rows) if _is_target_management_review_row(row)]
    if not target_indices:
        return rows[:_REVIEW_ACTION_CAP]

    target_index_set = set(target_indices)
    for target_idx in target_indices:
        if target_idx in selected_indices:
            continue
        drop_candidates = [idx for idx in selected_indices if idx not in target_index_set]
        if not drop_candidates:
            break
        selected_indices.remove(drop_candidates[-1])
        selected_indices.append(target_idx)

    selected_indices.sort()
    return [rows[idx] for idx in selected_indices]


def build_lifecycle_report(
    *,
    lifecycle_updates: Mapping[str, Mapping[str, Any]],
    management_suggestions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    management_rows = _strict_mapping_rows(management_suggestions, "management_suggestions")
    state_counts = {state: 0 for state in _LIFECYCLE_STATES}
    leaders: list[dict[str, Any]] = []
    pending_confirmation_symbols: list[str] = []
    protected_symbols: list[str] = []
    exit_symbols: list[str] = []

    lifecycle_rows = sorted(
        (
            (_strict_string_value(symbol, "symbol", required=True), _strict_mapping_row(payload, "lifecycle_updates"))
            for symbol, payload in lifecycle_updates.items()
        ),
        key=lambda row: row[0],
    )

    for symbol, payload in lifecycle_rows:
        state = _strict_string_field(payload, "state", default="INIT", required=True).upper()
        if state in state_counts:
            state_counts[state] += 1
        leader = _lifecycle_leader_row(symbol, payload)
        leaders.append(leader)
        if state == "INIT":
            pending_confirmation_symbols.append(symbol)
        elif state == "PROTECT":
            protected_symbols.append(symbol)
        elif state == "EXIT":
            exit_symbols.append(symbol)

    leaders.sort(
        key=lambda row: (-_strict_number_field(row, "r_multiple", required=True), _strict_string_field(row, "symbol", required=True))
    )
    attention_symbols = sorted(
        {
            _strict_string_field(row, "symbol", required=True)
            for row in management_rows
            if row.get("symbol") is not None
        }
        | set(exit_symbols)
    )
    management_action_counts: dict[str, int] = {}
    review_actions: list[dict[str, Any]] = []
    audit_target_states: list[dict[str, Any]] = []
    for row in management_rows:
        action = _strict_string_field(row, "action").upper()
        if action:
            management_action_counts[action] = management_action_counts.get(action, 0) + 1
        review_row = _review_action_row(row)
        if review_row is not None:
            review_actions.append(review_row)
    for symbol, payload in lifecycle_rows:
        if "first_target_status" not in payload and "second_target_status" not in payload:
            continue
        audit_target_states.append(
            {
                "symbol": symbol,
                "first_target_status": _strict_optional_string_field(payload, "first_target_status"),
                "second_target_status": _strict_optional_string_field(payload, "second_target_status"),
            }
        )
    return {
        "tracked_count": len(lifecycle_updates),
        "state_counts": state_counts,
        "pending_confirmation_symbols": pending_confirmation_symbols,
        "protected_symbols": protected_symbols,
        "exit_symbols": exit_symbols,
        "attention_symbols": attention_symbols,
        "management_action_counts": management_action_counts,
        "review_actions": _cap_review_actions(review_actions),
        "audit_target_states": audit_target_states,
        "leaders": leaders[:3],
    }


def build_trend_report(
    *,
    trend_candidates: Sequence[Mapping[str, Any]],
    allocations: Sequence[Mapping[str, Any]],
    major_universe: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    candidate_rows = _strict_mapping_rows(trend_candidates, "trend_candidates")
    for row in candidate_rows:
        _strict_string_field(row, "symbol", required=True)
        _strict_number_field(row, "score", required=True)
    ranked = sorted(
        candidate_rows,
        key=lambda row: (-_strict_number_field(row, "score", required=True), _strict_string_field(row, "symbol")),
    )
    allocation_rows = _strict_mapping_rows(allocations, "allocations")
    accepted_symbols = sorted(
        {
            _strict_string_field(row, "symbol")
            for row in allocation_rows
            if _strict_string_field(row, "engine").lower() == "trend"
            and _strict_string_field(row, "status") in {"ACCEPTED", "DOWNSIZED"}
        }
    )
    leaders = [_trend_leader_row(row) for row in ranked[:3]]
    return {
        "universe_count": len(major_universe),
        "candidate_count": len(ranked),
        "accepted_symbols": accepted_symbols,
        "leaders": leaders,
    }


def build_rotation_report(
    *,
    rotation_candidates: Sequence[Mapping[str, Any]],
    allocations: Sequence[Mapping[str, Any]],
    executions: Sequence[Mapping[str, Any]],
    rotation_universe: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    candidate_rows = _strict_mapping_rows(rotation_candidates, "rotation_candidates")
    for row in candidate_rows:
        _strict_string_field(row, "symbol", required=True)
        _strict_number_field(row, "score", required=True)
    ranked = sorted(
        candidate_rows,
        key=lambda row: (-_strict_number_field(row, "score", required=True), _strict_string_field(row, "symbol")),
    )
    rotation_symbols = {_strict_string_field(row, "symbol") for row in ranked if row.get("symbol")}
    allocation_rows = _strict_mapping_rows(allocations, "allocations")
    accepted_symbols = sorted(
        {
            _strict_string_field(row, "symbol")
            for row in allocation_rows
            if _strict_string_field(row, "engine").lower() == "rotation"
            and _strict_string_field(row, "status") in {"ACCEPTED", "DOWNSIZED"}
        }
    )
    execution_rows = _strict_mapping_rows(executions, "executions")
    for row in execution_rows:
        _strict_optional_string_allowlist(row, "time_in_force", _EXECUTION_TIME_IN_FORCE_VALUES)
    executed_symbols = sorted(
        {
            _strict_string_field(row, "symbol")
            for row in execution_rows
            if _strict_string_field(row, "symbol") in rotation_symbols and _strict_string_field(row, "status") == "FILLED"
        }
    )
    leaders = [_rotation_leader_row(row) for row in ranked[:3]]
    return {
        "universe_count": len(rotation_universe),
        "candidate_count": len(ranked),
        "accepted_symbols": accepted_symbols,
        "executed_symbols": executed_symbols,
        "leaders": leaders,
    }


def build_short_report(
    *,
    short_candidates: Sequence[Mapping[str, Any]],
    allocations: Sequence[Mapping[str, Any]],
    short_universe: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    candidate_rows = _strict_mapping_rows(short_candidates, "short_candidates")
    for row in candidate_rows:
        _strict_string_field(row, "symbol", required=True)
        _strict_number_field(row, "score", required=True)
    ranked = sorted(
        candidate_rows,
        key=lambda row: (-_strict_number_field(row, "score", required=True), _strict_string_field(row, "symbol")),
    )
    allocation_rows = _strict_mapping_rows(allocations, "allocations")
    accepted_symbols = sorted(
        {
            _strict_string_field(row, "symbol")
            for row in allocation_rows
            if _strict_string_field(row, "engine").lower() == "short"
            and _strict_string_field(row, "status") in {"ACCEPTED", "DOWNSIZED"}
        }
    )
    deferred_execution_symbols = sorted(
        {
            _strict_string_field(row, "symbol")
            for row in allocation_rows
            if _strict_string_field(row, "engine").lower() == "short"
            and _strict_mapping_field(row, "execution").get("reason") == "short_execution_not_enabled"
        }
    )
    leaders = [_short_leader_row(row) for row in ranked[:3]]
    return {
        "universe_count": len(short_universe),
        "candidate_count": len(ranked),
        "accepted_symbols": accepted_symbols,
        "deferred_execution_symbols": deferred_execution_symbols,
        "leaders": leaders,
    }
