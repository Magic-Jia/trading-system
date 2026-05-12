from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import math
from collections.abc import Mapping
from typing import Any

from .exit_policy import ExitDecision, evaluate_exit_policy
from .positions import _has_explicit_target_management_state
from .target_management import reconciled_stage_qty, stage_requested_qty
from .lifecycle_v2 import advance_lifecycle_transition
from ..types import ManagementActionIntent, ManagementSuggestion, RuntimeState

_MANAGEMENT_ACTIONS = {"BREAK_EVEN", "PARTIAL_TAKE_PROFIT", "EXIT", "ADD_PROTECTIVE_STOP", "DE_RISK"}


def _is_open(position: dict[str, Any]) -> bool:
    return position.get("status") in {None, "OPEN", "FILLED", "SENT"}


def _in_favor(side: str, mark_price: float, reference_price: float) -> bool:
    if side == "LONG":
        return mark_price >= reference_price
    return mark_price <= reference_price


def _stop_breached(side: str, mark_price: float, stop_loss: float) -> bool:
    if side == "LONG":
        return mark_price <= stop_loss
    return mark_price >= stop_loss


def _valid_stop(side: str, entry_price: float, stop_loss: float | None) -> bool:
    if stop_loss is None:
        return False
    if side == "LONG":
        return stop_loss < entry_price
    return stop_loss > entry_price


def _position_side(position: dict[str, Any]) -> str:
    raw_side = position.get("side", "LONG")
    if not isinstance(raw_side, str):
        raise ValueError("side must be LONG or SHORT")
    side = raw_side.strip().upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    return side


def _position_float(position: dict[str, Any], field: str, *, default: float | None = None) -> float | None:
    if field not in position or position.get(field) is None:
        return default
    raw_value = position[field]
    if isinstance(raw_value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return value


def _present_finite_number(payload: dict[str, Any], field: str) -> float | None:
    if field not in payload or payload.get(field) is None:
        return None
    raw_value = payload[field]
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise ValueError(f"{field} must be a finite non-bool number when present")
    value = float(raw_value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be a finite non-bool number when present")
    return value


def _present_string(payload: Mapping[str, Any], field: str) -> str | None:
    if field not in payload or payload.get(field) is None:
        return None
    value = payload[field]
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string when present")
    return value


def _mapping_with_string_keys(value: Mapping[Any, Any], label: str) -> dict[str, Any]:
    for key in value:
        if not isinstance(key, str):
            raise TypeError(f"{label} keys must be strings")
    return dict(value)


def _present_config_finite_number(config: Any, field: str, default: float) -> float:
    if isinstance(config, Mapping):
        config = _mapping_with_string_keys(config, "lifecycle_config")
        if field not in config or config.get(field) is None:
            return default
        return _present_finite_number(config, field) or default
    if not hasattr(config, field):
        return default
    value = getattr(config, field)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite non-bool number when present")
    candidate = float(value)
    if not math.isfinite(candidate):
        raise ValueError(f"{field} must be a finite non-bool number when present")
    return candidate


def _present_bool(payload: dict[str, Any], field: str) -> bool | None:
    if field not in payload or payload.get(field) is None:
        return None
    value = payload[field]
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a bool when present")
    return value


def _suggest_protective_stop(side: str, entry_price: float, mark_price: float | None) -> float:
    # MVP fallback: anchor the stop 2% beyond entry, but if price is already through
    # that level, place it 0.5% beyond the current mark instead to avoid previewing an
    # already-breached stop. This stays deterministic and keeps the stop on the loss side.
    if side == "LONG":
        fallback = entry_price * 0.98
        if mark_price is None or mark_price <= 0:
            return round(fallback, 8)
        return round(min(fallback, mark_price * 0.995), 8)

    fallback = entry_price * 1.02
    if mark_price is None or mark_price <= 0:
        return round(fallback, 8)
    return round(max(fallback, mark_price * 1.005), 8)


def _position_taxonomy_meta(position: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in (
        "taxonomy_stop_loss",
        "invalidation_source",
        "invalidation_reason",
        "stop_family",
        "stop_reference",
        "stop_policy_source",
    ):
        value = position.get(key)
        if value is not None:
            if key != "taxonomy_stop_loss":
                _present_string(position, key)
            payload[key] = value
    return payload


def _exit_decision_to_suggestion(position: dict[str, Any], decision: ExitDecision) -> ManagementSuggestion:
    decision_meta = decision.meta or {}
    if not isinstance(decision_meta, Mapping):
        raise TypeError("decision.meta must be a mapping when present")
    decision_meta = _mapping_with_string_keys(decision_meta, "decision.meta")
    return ManagementSuggestion(
        symbol=_present_string(position, "symbol") or "",
        action=decision.action,
        side=_position_side(position),
        priority=decision.priority,
        qty_fraction=decision.qty_fraction,
        reason=decision.reason,
        reference_price=decision.reference_price,
        meta={**_position_taxonomy_meta(position), **decision_meta},
    )


def _taxonomy_stop(side: str, entry_price: float, position: dict[str, Any]) -> float | None:
    candidate = _present_finite_number(position, "taxonomy_stop_loss")
    if candidate is None:
        return None
    if not _valid_stop(side, entry_price, candidate):
        return None
    return round(candidate, 8)


def evaluate_position(position: dict[str, Any], *, regime: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not _is_open(position):
        return []

    symbol = _present_string(position, "symbol") or ""
    side = _position_side(position)
    entry_price = _position_float(position, "entry_price", default=0.0)
    mark_price = _position_float(position, "mark_price", default=None)
    stop_loss = _position_float(position, "stop_loss", default=None)
    take_profit = position.get("take_profit")

    if entry_price <= 0:
        return []

    suggestions: list[ManagementSuggestion] = []
    taxonomy_meta = _position_taxonomy_meta(position)
    invalidation_source = (_present_string(position, "invalidation_source") or "").strip()
    invalidation_reason = (_present_string(position, "invalidation_reason") or "").strip()
    if not _valid_stop(side, entry_price, stop_loss):
        reference_price = mark_price if mark_price is not None else entry_price
        taxonomy_stop_loss = _taxonomy_stop(side, entry_price, position)
        if taxonomy_stop_loss is not None:
            suggested_stop_loss = taxonomy_stop_loss
            reason = "当前持仓缺少有效止损，建议先按共享 stop taxonomy 恢复保护止损。"
            meta = {
                "position_source": position.get("source"),
                "heuristic": "shared_stop_taxonomy",
                "entry_price": round(entry_price, 8),
                "mark_price": round(reference_price, 8),
                **taxonomy_meta,
            }
        else:
            suggested_stop_loss = _suggest_protective_stop(side, entry_price, mark_price)
            reason = "当前持仓缺少有效止损，建议先补保护性止损后再谈加仓或死扛。"
            meta = {
                "position_source": position.get("source"),
                "heuristic": "entry_2pct_or_mark_0.5pct_buffer",
                "entry_price": round(entry_price, 8),
                "mark_price": round(reference_price, 8),
            }
        suggestions.append(
            ManagementSuggestion(
                symbol=symbol,
                action="ADD_PROTECTIVE_STOP",
                side=side,
                priority="MEDIUM",
                reason=reason,
                suggested_stop_loss=suggested_stop_loss,
                reference_price=reference_price,
                meta=meta,
            )
        )
        return [asdict(item) for item in suggestions]

    risk_unit = abs(entry_price - stop_loss)
    if risk_unit <= 0:
        return []

    if mark_price is None:
        return []

    break_even_trigger = entry_price + risk_unit if side == "LONG" else entry_price - risk_unit
    if _in_favor(side, mark_price, break_even_trigger) and stop_loss != entry_price:
        reason = "价格已至少走出 1R，允许把止损上提到保本位。"
        if invalidation_reason and invalidation_source:
            reason = f"{invalidation_reason}（{invalidation_source}）仍是当前失效条件，价格已至少走出 1R，允许把止损上提到保本位。"
        elif invalidation_reason:
            reason = f"{invalidation_reason} 仍是当前失效条件，价格已至少走出 1R，允许把止损上提到保本位。"
        suggestions.append(
            ManagementSuggestion(
                symbol=symbol,
                action="BREAK_EVEN",
                side=side,
                priority="MEDIUM",
                reason=reason,
                suggested_stop_loss=round(entry_price, 8),
                reference_price=round(mark_price, 8),
                meta={"trigger_price": round(break_even_trigger, 8), "risk_unit": round(risk_unit, 8), **taxonomy_meta},
            )
        )

    tracked_from_intent = _present_bool(position, "tracked_from_intent")
    allow_target_stage_exits = bool(tracked_from_intent) or _has_explicit_target_management_state(position)
    for decision in evaluate_exit_policy(position, regime=regime):
        decision_meta = decision.meta or {}
        if not isinstance(decision_meta, Mapping):
            raise TypeError("decision.meta must be a mapping when present")
        decision_meta = _mapping_with_string_keys(decision_meta, "decision.meta")
        trigger_value = decision_meta.get("exit_trigger")
        if trigger_value is not None and not isinstance(trigger_value, str):
            raise ValueError("decision.meta.exit_trigger must be a string when present")
        trigger = (trigger_value or "").strip()
        if (
            not allow_target_stage_exits
            and (
                (decision.action == "PARTIAL_TAKE_PROFIT" and trigger in {"first_target_hit", "second_target_hit"})
                or (decision.action == "EXIT" and trigger == "runner_stop_hit")
            )
        ):
            continue
        suggestions.append(_exit_decision_to_suggestion(position, decision))

    if _stop_breached(side, mark_price, stop_loss) and not any(item.action == "EXIT" for item in suggestions):
        reason = "当前价格已跌破（或升破）止损位，建议按计划退出。"
        if invalidation_reason and invalidation_source:
            reason = f"{invalidation_reason}（{invalidation_source}），当前价格已触发止损，建议按计划退出。"
        elif invalidation_reason:
            reason = f"{invalidation_reason}，当前价格已触发止损，建议按计划退出。"
        suggestions.append(
            ManagementSuggestion(
                symbol=symbol,
                action="EXIT",
                side=side,
                priority="HIGH",
                qty_fraction=1.0,
                reason=reason,
                reference_price=round(mark_price, 8),
                meta={"stop_loss": round(stop_loss, 8), **taxonomy_meta},
            )
        )

    return [asdict(item) for item in suggestions]


def evaluate_portfolio(state: RuntimeState, *, regime: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    effective_regime = regime if regime is not None else dict(getattr(state, "latest_regime", {}) or {})
    for position in state.positions.values():
        suggestions.extend(evaluate_position(position, regime=effective_regime))
    return suggestions


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _r_multiple(position: dict[str, Any]) -> float:
    side = _position_side(position)
    entry = _float(position.get("entry_price"))
    mark = _float(position.get("mark_price"))
    stop = position.get("stop_loss")
    stop_loss = _float(stop) if stop is not None else 0.0
    if entry <= 0 or mark <= 0 or not _valid_stop(side, entry, stop_loss):
        return 0.0

    risk_unit = abs(entry - stop_loss)
    if risk_unit <= 0:
        return 0.0
    if side == "LONG":
        return (mark - entry) / risk_unit
    return (entry - mark) / risk_unit


def _position_entry_profile(position: dict[str, Any]) -> str:
    entry_profile = _present_string(position, "entry_profile")
    strategy_profile = _present_string(position, "strategy_profile")
    profile = (entry_profile or strategy_profile or "").strip().lower().replace("-", "_")
    if not profile:
        meta = position.get("meta")
        if isinstance(meta, dict):
            meta_profile = _present_string(meta, "entry_profile")
            profile = (meta_profile or "").strip().lower().replace("-", "_")
    return profile


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _holding_hours(position: dict[str, Any]) -> float | None:
    opened = _parse_datetime(position.get("opened_at") or position.get("entry_time") or position.get("created_at"))
    if opened is None:
        return None
    return max((datetime.now(timezone.utc) - opened).total_seconds() / 3600.0, 0.0)


def _target_management_lifecycle_projection(position: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in ("first_target_hit", "second_target_hit", "runner_protected"):
        if key in position:
            payload[key] = _present_bool(position, key)

    runner_stop = position.get("runner_stop_price")
    if runner_stop is not None:
        runner_stop_price = _present_finite_number(position, "runner_stop_price")
        payload["runner_stop_price"] = round(runner_stop_price, 8)

    scale_out_plan = position.get("scale_out_plan")
    if scale_out_plan is not None:
        if not isinstance(scale_out_plan, Mapping):
            raise TypeError("scale_out_plan must be a mapping when present")
        payload["scale_out_plan"] = _mapping_with_string_keys(scale_out_plan, "scale_out_plan")

    second_target_source = position.get("second_target_source")
    if second_target_source:
        payload["second_target_source"] = _present_string(position, "second_target_source")

    for key in ("first_target_status", "second_target_status"):
        if key in position:
            payload[key] = _present_string(position, key) or ""

    return payload


def advance_lifecycle_positions(state: RuntimeState, lifecycle_config: Any) -> dict[str, dict[str, Any]]:
    latest = dict(getattr(state, "latest_lifecycle", {}) or {})
    updates: dict[str, dict[str, Any]] = {}
    protect_trigger = _present_config_finite_number(lifecycle_config, "protect_r_multiple", 1.2)

    for symbol, position in state.positions.items():
        side = _position_side(position)
        mark_value = _present_finite_number(position, "mark_price")
        mark = mark_value if mark_value is not None else 0.0
        stop = position.get("stop_loss")
        stop_loss = _float(stop) if stop is not None else 0.0
        take_profit = position.get("take_profit")
        if take_profit is not None:
            take_profit = _present_finite_number(position, "take_profit")
        r_multiple = _r_multiple(position)
        tracked_from_intent = _present_bool(position, "tracked_from_intent")
        carries_persistent_lifecycle = bool(tracked_from_intent) or _has_explicit_target_management_state(position)
        current_state = "INIT"
        if carries_persistent_lifecycle:
            latest_entry = latest.get(symbol) or {}
            if not isinstance(latest_entry, Mapping):
                raise TypeError(f"latest_lifecycle.{symbol} must be a mapping when present")
            current_state = _present_string(latest_entry, "state") or "INIT"

        stop_hit = False
        if mark > 0 and stop is not None and _valid_stop(side, _float(position.get("entry_price")), stop_loss):
            stop_hit = _stop_breached(side, mark, stop_loss)

        target_hit = False
        if mark > 0 and take_profit is not None:
            target_hit = _in_favor(side, mark, take_profit)

        max_holding_hours = _present_config_finite_number(lifecycle_config, "max_holding_hours", 0.0)
        holding_hours = _holding_hours(position)
        max_holding_elapsed = (
            _position_entry_profile(position) == "short_term"
            and max_holding_hours > 0.0
            and holding_hours is not None
            and holding_hours >= max_holding_hours
        )

        next_state, reason_codes = advance_lifecycle_transition(
            current_state,
            {
                "r_multiple": r_multiple,
                "confirmed": position.get("status") in {"OPEN", "FILLED", "SENT"},
                "payload_ready": _float(position.get("qty")) > 0,
                "trend_mature": r_multiple >= protect_trigger,
                "stop_hit": stop_hit,
                "target_hit": target_hit,
                "exit_requested": max_holding_elapsed,
            },
            config=lifecycle_config,
        )
        reason_codes = list(reason_codes)
        extra_meta: dict[str, Any] = {}
        if max_holding_elapsed:
            reason_codes = ["max_holding_hours_elapsed"]
            extra_meta["max_holding_hours"] = int(max_holding_hours) if max_holding_hours.is_integer() else max_holding_hours
            if holding_hours is not None:
                extra_meta["holding_hours"] = round(holding_hours, 4)
        projection = _target_management_lifecycle_projection(position) if carries_persistent_lifecycle else {}
        updates[symbol] = {
            "state": next_state.value,
            "reason_codes": reason_codes,
            "r_multiple": round(r_multiple, 6),
            **extra_meta,
            **_position_taxonomy_meta(position),
            **projection,
        }

    return updates


def _intent_id(symbol: str, action: str) -> str:
    return f"mgmt-{symbol.lower()}-{action.lower()}".replace("_", "-")


def _position_qty(position: dict[str, Any]) -> float:
    qty = _present_finite_number(position, "qty")
    return round(qty or 0.0, 8)


def _remaining_or_position_qty(position: dict[str, Any], position_qty: float) -> float:
    remaining_qty = _present_finite_number(position, "remaining_position_qty")
    if remaining_qty is None:
        remaining_qty = position_qty
    return round(max(min(remaining_qty, position_qty), 0.0), 8)


def _strict_suggested_stop_loss(position: dict[str, Any], row: Mapping[str, Any], *, require_loss_side: bool) -> float:
    stop_loss = _present_finite_number(row, "suggested_stop_loss")
    if stop_loss is None:
        raise ValueError("suggested_stop_loss must be a finite non-bool number when present")
    side = _position_side(position)
    entry_price = _present_finite_number(position, "entry_price")
    if entry_price is None or entry_price <= 0:
        raise ValueError("entry_price must be a finite non-bool number when present")
    if require_loss_side and not _valid_stop(side, entry_price, stop_loss):
        raise ValueError("suggested_stop_loss must stay on the loss side")
    return round(stop_loss, 8)


def _management_row_symbol(row: Mapping[str, Any]) -> str:
    value = row.get("symbol", "")
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or value != value.strip().upper() or not value:
        raise ValueError("management suggestion symbol must be a canonical string")
    return value


def _management_row_action(row: Mapping[str, Any]) -> str:
    value = row.get("action", "")
    if not isinstance(value, str) or value not in _MANAGEMENT_ACTIONS:
        raise ValueError("management suggestion action must be a canonical action")
    return value


def build_management_action_intents(
    state: RuntimeState,
    suggestions: list[dict[str, Any]] | None = None,
) -> list[ManagementActionIntent]:
    rows = suggestions if suggestions is not None else evaluate_portfolio(state)
    intents: list[ManagementActionIntent] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("management suggestion row must be a mapping")
        row = _mapping_with_string_keys(row, "management suggestion row")
        symbol = _management_row_symbol(row)
        action = _management_row_action(row)
        position = state.positions.get(symbol)
        if not symbol or position is None:
            continue

        position_qty = _position_qty(position)
        available_qty = _remaining_or_position_qty(position, position_qty)
        qty_fraction = _present_finite_number(row, "qty_fraction") or 0.0
        qty = None
        stop_loss = None
        raw_meta = row.get("meta")
        if raw_meta is None:
            row_meta = {}
        elif not isinstance(raw_meta, Mapping):
            raise TypeError("management suggestion meta must be a mapping when present")
        else:
            row_meta = _mapping_with_string_keys(raw_meta, "management suggestion meta")
        target_stage_value = row_meta.get("target_stage")
        if target_stage_value is not None and not isinstance(target_stage_value, str):
            raise ValueError("management suggestion meta.target_stage must be a string when present")
        target_stage = target_stage_value or ""

        if action in {"BREAK_EVEN", "ADD_PROTECTIVE_STOP"}:
            stop_loss = _strict_suggested_stop_loss(position, row, require_loss_side=action == "ADD_PROTECTIVE_STOP")
        elif action in {"PARTIAL_TAKE_PROFIT", "DE_RISK"}:
            if action == "PARTIAL_TAKE_PROFIT" and target_stage in {"first", "second"}:
                qty = reconciled_stage_qty(position, stage=target_stage)
                if qty is None:
                    continue
            else:
                qty = round(min(position_qty * qty_fraction, available_qty), 8)
        elif action == "EXIT":
            qty = available_qty

        meta = {
            "reason": row.get("reason"),
            "priority": row.get("priority"),
            "qty_fraction": row.get("qty_fraction"),
            "position_stop_loss": position.get("stop_loss"),
            "position_take_profit": position.get("take_profit"),
            **row_meta,
        }
        if action == "PARTIAL_TAKE_PROFIT" and target_stage in {"first", "second"} and qty is not None:
            meta.update(
                {
                    "target_stage": target_stage,
                    "fraction_basis": row_meta.get("fraction_basis") or "original_position",
                    "requested_qty": stage_requested_qty(position, stage=target_stage),
                    "reconciled_qty": qty,
                }
            )

        intents.append(
            ManagementActionIntent(
                intent_id=_intent_id(symbol, action),
                symbol=symbol,
                action=action,
                side=str(row.get("side", position.get("side", "LONG"))),
                position_qty=position_qty,
                qty=qty,
                stop_loss=stop_loss,
                reference_price=row.get("reference_price"),
                meta=meta,
            )
        )
    return intents
