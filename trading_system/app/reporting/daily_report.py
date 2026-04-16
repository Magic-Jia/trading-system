from __future__ import annotations

from typing import Any, Mapping, Sequence


_LIFECYCLE_STATES = ("INIT", "CONFIRM", "PAYLOAD", "PROTECT", "EXIT")
_REVIEW_ACTION_CAP = 5
_TARGET_REVIEW_KEYS = ("target_price", "target_stage", "fraction_basis", "runner_stop_price")


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _rotation_leader_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    timeframe_meta = dict(candidate.get("timeframe_meta") or {})
    relative_strength = dict(timeframe_meta.get("relative_strength") or {})
    liquidity_meta = dict(candidate.get("liquidity_meta") or {})
    return {
        "symbol": str(candidate.get("symbol", "")),
        "score": round(_float(candidate.get("score")), 6),
        "daily_spread": round(_float(relative_strength.get("daily_spread")), 6),
        "h4_spread": round(_float(relative_strength.get("h4_spread")), 6),
        "h1_spread": round(_float(relative_strength.get("h1_spread")), 6),
        "volume_usdt_24h": _float(liquidity_meta.get("volume_usdt_24h")),
        "slippage_bps": _float(liquidity_meta.get("slippage_bps")),
    }


def _short_leader_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    timeframe_meta = dict(candidate.get("timeframe_meta") or {})
    liquidity_meta = dict(candidate.get("liquidity_meta") or {})
    row = {
        "symbol": str(candidate.get("symbol", "")),
        "setup_type": str(candidate.get("setup_type", "")),
        "score": round(_float(candidate.get("score")), 6),
        "daily_bias": str(timeframe_meta.get("daily_bias", "")),
        "h4_structure": str(timeframe_meta.get("h4_structure", "")),
        "h1_trigger": str(timeframe_meta.get("h1_trigger", "")),
        "derivatives": dict(timeframe_meta.get("derivatives") or {}),
        "volume_usdt_24h": _float(liquidity_meta.get("volume_usdt_24h")),
        "liquidity_tier": str(liquidity_meta.get("liquidity_tier", "")),
    }
    for key in ("stop_family", "stop_reference", "invalidation_source", "invalidation_reason", "stop_policy_source"):
        value = candidate.get(key)
        if value:
            row[key] = str(value)
    return row


def _trend_leader_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    timeframe_meta = dict(candidate.get("timeframe_meta") or {})
    liquidity_meta = dict(candidate.get("liquidity_meta") or {})
    row = {
        "symbol": str(candidate.get("symbol", "")),
        "setup_type": str(candidate.get("setup_type", "")),
        "score": round(_float(candidate.get("score")), 6),
        "daily_bias": str(timeframe_meta.get("daily_bias", "")),
        "h4_structure": str(timeframe_meta.get("h4_structure", "")),
        "h1_trigger": str(timeframe_meta.get("h1_trigger", "")),
        "derivatives": dict(timeframe_meta.get("derivatives") or {}),
        "volume_usdt_24h": _float(liquidity_meta.get("volume_usdt_24h")),
        "liquidity_tier": str(liquidity_meta.get("liquidity_tier", "")),
    }
    for key in ("stop_family", "stop_reference", "invalidation_source", "invalidation_reason", "stop_policy_source"):
        value = candidate.get(key)
        if value:
            row[key] = str(value)
    return row


def _lifecycle_leader_row(symbol: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    row = {
        "symbol": symbol,
        "state": str(payload.get("state", "INIT")).upper(),
        "r_multiple": round(_float(payload.get("r_multiple")), 6),
        "reason_codes": [str(code) for code in payload.get("reason_codes", [])],
    }
    for key in ("stop_family", "stop_reference", "invalidation_source", "invalidation_reason", "stop_policy_source"):
        value = payload.get(key)
        if value:
            row[key] = str(value)
    for key in ("first_target_hit", "second_target_hit", "runner_protected"):
        if key in payload:
            row[key] = bool(payload.get(key))
    runner_stop_price = payload.get("runner_stop_price")
    if runner_stop_price is not None:
        row["runner_stop_price"] = round(_float(runner_stop_price), 8)
    if "scale_out_plan" in payload:
        row["scale_out_plan"] = dict(payload.get("scale_out_plan") or {})
    second_target_source = payload.get("second_target_source")
    if second_target_source:
        row["second_target_source"] = str(second_target_source)
    return row


def _review_action_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    meta = dict(row.get("meta") or {})
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
        "symbol": str(row.get("symbol", "")),
        "action": str(row.get("action", "")),
        "priority": str(row.get("priority", "MEDIUM")),
    }
    if has_stop_semantics:
        payload.update(
            {
                "stop_family": str(meta.get("stop_family", "")),
                "stop_reference": str(meta.get("stop_reference", "")),
                "invalidation_source": str(meta.get("invalidation_source", "")),
                "invalidation_reason": str(meta.get("invalidation_reason", "")),
                "stop_policy_source": str(meta.get("stop_policy_source", "")),
            }
        )
    suggested_stop_loss = row.get("suggested_stop_loss")
    if suggested_stop_loss is not None:
        payload["suggested_stop_loss"] = round(_float(suggested_stop_loss), 8)
    if qty_fraction is not None:
        payload["qty_fraction"] = _float(qty_fraction)
    if target_price is not None:
        payload["target_price"] = round(_float(target_price), 8)
    if target_stage is not None:
        payload["target_stage"] = str(target_stage)
    if fraction_basis is not None:
        payload["fraction_basis"] = str(fraction_basis)
    if runner_stop_price is not None:
        payload["runner_stop_price"] = round(_float(runner_stop_price), 8)
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
    state_counts = {state: 0 for state in _LIFECYCLE_STATES}
    leaders: list[dict[str, Any]] = []
    pending_confirmation_symbols: list[str] = []
    protected_symbols: list[str] = []
    exit_symbols: list[str] = []

    for symbol, payload in sorted(lifecycle_updates.items()):
        state = str(payload.get("state", "INIT")).upper()
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

    leaders.sort(key=lambda row: (-_float(row.get("r_multiple")), str(row.get("symbol", ""))))
    attention_symbols = sorted(
        {
            str(row.get("symbol", ""))
            for row in management_suggestions
            if str(row.get("symbol", ""))
        }
        | set(exit_symbols)
    )
    management_action_counts: dict[str, int] = {}
    review_actions: list[dict[str, Any]] = []
    audit_target_states: list[dict[str, Any]] = []
    for row in management_suggestions:
        action = str(row.get("action", "")).upper()
        if action:
            management_action_counts[action] = management_action_counts.get(action, 0) + 1
        review_row = _review_action_row(row)
        if review_row is not None:
            review_actions.append(review_row)
    for symbol, payload in sorted(lifecycle_updates.items()):
        if "first_target_status" not in payload and "second_target_status" not in payload:
            continue
        audit_target_states.append(
            {
                "symbol": symbol,
                "first_target_status": str(payload.get("first_target_status", "")),
                "second_target_status": str(payload.get("second_target_status", "")),
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
    ranked = sorted(
        [dict(row) for row in trend_candidates if str(row.get("symbol", ""))],
        key=lambda row: (-_float(row.get("score")), str(row.get("symbol", ""))),
    )
    accepted_symbols = sorted(
        {
            str(row.get("symbol", ""))
            for row in allocations
            if str(row.get("engine", "")).lower() == "trend" and row.get("status") in {"ACCEPTED", "DOWNSIZED"}
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
    ranked = sorted(
        [dict(row) for row in rotation_candidates if str(row.get("symbol", ""))],
        key=lambda row: (-_float(row.get("score")), str(row.get("symbol", ""))),
    )
    rotation_symbols = {str(row.get("symbol", "")) for row in ranked if row.get("symbol")}
    accepted_symbols = sorted(
        {
            str(row.get("symbol", ""))
            for row in allocations
            if str(row.get("engine", "")).lower() == "rotation" and row.get("status") in {"ACCEPTED", "DOWNSIZED"}
        }
    )
    executed_symbols = sorted(
        {
            str(row.get("symbol", ""))
            for row in executions
            if str(row.get("symbol", "")) in rotation_symbols and row.get("status") == "FILLED"
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
    ranked = sorted(
        [dict(row) for row in short_candidates if str(row.get("symbol", ""))],
        key=lambda row: (-_float(row.get("score")), str(row.get("symbol", ""))),
    )
    accepted_symbols = sorted(
        {
            str(row.get("symbol", ""))
            for row in allocations
            if str(row.get("engine", "")).lower() == "short" and row.get("status") in {"ACCEPTED", "DOWNSIZED"}
        }
    )
    deferred_execution_symbols = sorted(
        {
            str(row.get("symbol", ""))
            for row in allocations
            if str(row.get("engine", "")).lower() == "short"
            and dict(row.get("execution") or {}).get("reason") == "short_execution_not_enabled"
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
