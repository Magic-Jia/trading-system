from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .exit_policy import ExitDecision, evaluate_exit_policy
from .positions import _has_explicit_target_management_state
from .target_management import reconciled_stage_qty, stage_requested_qty
from .lifecycle_v2 import advance_lifecycle_transition
from ..types import ManagementActionIntent, ManagementSuggestion, RuntimeState


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
            payload[key] = value
    return payload


def _exit_decision_to_suggestion(position: dict[str, Any], decision: ExitDecision) -> ManagementSuggestion:
    return ManagementSuggestion(
        symbol=str(position.get("symbol", "")),
        action=decision.action,
        side=str(position.get("side", "LONG")),
        priority=decision.priority,
        qty_fraction=decision.qty_fraction,
        reason=decision.reason,
        reference_price=decision.reference_price,
        meta={**_position_taxonomy_meta(position), **dict(decision.meta or {})},
    )


def _taxonomy_stop(side: str, entry_price: float, position: dict[str, Any]) -> float | None:
    stop = position.get("taxonomy_stop_loss")
    try:
        candidate = float(stop)
    except (TypeError, ValueError):
        return None
    if not _valid_stop(side, entry_price, candidate):
        return None
    return round(candidate, 8)


def evaluate_position(position: dict[str, Any], *, regime: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not _is_open(position):
        return []

    symbol = str(position.get("symbol", ""))
    side = str(position.get("side", "LONG"))
    entry_price = float(position.get("entry_price", 0.0) or 0.0)
    mark_price = position.get("mark_price")
    stop_loss = position.get("stop_loss")
    take_profit = position.get("take_profit")

    if entry_price <= 0:
        return []

    suggestions: list[ManagementSuggestion] = []
    taxonomy_meta = _position_taxonomy_meta(position)
    invalidation_source = str(position.get("invalidation_source") or "").strip()
    invalidation_reason = str(position.get("invalidation_reason") or "").strip()
    if not _valid_stop(side, entry_price, stop_loss):
        reference_price = float(mark_price) if mark_price is not None else entry_price
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

    stop_loss = float(stop_loss)
    risk_unit = abs(entry_price - stop_loss)
    if risk_unit <= 0:
        return []

    if mark_price is None:
        return []
    mark_price = float(mark_price)

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

    allow_target_stage_exits = bool(position.get("tracked_from_intent")) or _has_explicit_target_management_state(position)
    for decision in evaluate_exit_policy(position, regime=regime):
        trigger = str((decision.meta or {}).get("exit_trigger") or "")
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
    side = str(position.get("side", "LONG"))
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


def _target_management_lifecycle_projection(position: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in ("first_target_hit", "second_target_hit", "runner_protected"):
        if key in position:
            payload[key] = bool(position.get(key))

    runner_stop = position.get("runner_stop_price")
    if runner_stop is not None:
        runner_stop_price = _float(runner_stop)
        payload["runner_stop_price"] = round(runner_stop_price, 8)

    scale_out_plan = position.get("scale_out_plan")
    if scale_out_plan is not None:
        payload["scale_out_plan"] = dict(scale_out_plan or {})

    second_target_source = position.get("second_target_source")
    if second_target_source:
        payload["second_target_source"] = str(second_target_source)

    for key in ("first_target_status", "second_target_status"):
        if key in position:
            payload[key] = str(position.get(key) or "")

    return payload


def advance_lifecycle_positions(state: RuntimeState, lifecycle_config: Any) -> dict[str, dict[str, Any]]:
    latest = dict(getattr(state, "latest_lifecycle", {}) or {})
    updates: dict[str, dict[str, Any]] = {}
    protect_trigger = _float(getattr(lifecycle_config, "protect_r_multiple", 1.2)) or 1.2

    for symbol, position in state.positions.items():
        side = str(position.get("side", "LONG"))
        mark = _float(position.get("mark_price"))
        stop = position.get("stop_loss")
        stop_loss = _float(stop) if stop is not None else 0.0
        take_profit = position.get("take_profit")
        r_multiple = _r_multiple(position)
        carries_persistent_lifecycle = bool(position.get("tracked_from_intent")) or _has_explicit_target_management_state(position)
        current_state = str((latest.get(symbol) or {}).get("state", "INIT")) if carries_persistent_lifecycle else "INIT"

        stop_hit = False
        if mark > 0 and stop is not None and _valid_stop(side, _float(position.get("entry_price")), stop_loss):
            stop_hit = _stop_breached(side, mark, stop_loss)

        target_hit = False
        if mark > 0 and take_profit is not None:
            target_hit = _in_favor(side, mark, _float(take_profit))

        next_state, reason_codes = advance_lifecycle_transition(
            current_state,
            {
                "r_multiple": r_multiple,
                "confirmed": position.get("status") in {"OPEN", "FILLED", "SENT"},
                "payload_ready": _float(position.get("qty")) > 0,
                "trend_mature": r_multiple >= protect_trigger,
                "stop_hit": stop_hit,
                "target_hit": target_hit,
            },
            config=lifecycle_config,
        )
        projection = _target_management_lifecycle_projection(position) if carries_persistent_lifecycle else {}
        updates[symbol] = {
            "state": next_state.value,
            "reason_codes": reason_codes,
            "r_multiple": round(r_multiple, 6),
            **_position_taxonomy_meta(position),
            **projection,
        }

    return updates


def _intent_id(symbol: str, action: str) -> str:
    return f"mgmt-{symbol.lower()}-{action.lower()}".replace("_", "-")


def _position_qty(position: dict[str, Any]) -> float:
    return round(float(position.get("qty", 0.0) or 0.0), 8)


def build_management_action_intents(
    state: RuntimeState,
    suggestions: list[dict[str, Any]] | None = None,
) -> list[ManagementActionIntent]:
    rows = suggestions if suggestions is not None else evaluate_portfolio(state)
    intents: list[ManagementActionIntent] = []
    for row in rows:
        symbol = str(row.get("symbol", ""))
        action = str(row.get("action", ""))
        position = state.positions.get(symbol)
        if not symbol or position is None:
            continue

        position_qty = _position_qty(position)
        qty_fraction = float(row.get("qty_fraction") or 0.0)
        qty = None
        stop_loss = None
        row_meta = dict(row.get("meta") or {})
        target_stage = str(row_meta.get("target_stage") or "")

        if action in {"BREAK_EVEN", "ADD_PROTECTIVE_STOP"}:
            stop_loss = row.get("suggested_stop_loss")
        elif action in {"PARTIAL_TAKE_PROFIT", "DE_RISK"}:
            if action == "PARTIAL_TAKE_PROFIT" and target_stage in {"first", "second"}:
                qty = reconciled_stage_qty(position, stage=target_stage)
                if qty is None:
                    continue
            else:
                qty = round(position_qty * qty_fraction, 8)
        elif action == "EXIT":
            qty = position_qty

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
