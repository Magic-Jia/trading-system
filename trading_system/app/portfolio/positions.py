from __future__ import annotations

from datetime import datetime
from typing import Any

from .target_management import ensure_target_management_state, stage_completed, terminalize_all_unreachable_stages
from ..types import AccountSnapshot, BJ, ManagementActionIntent, OrderIntent, PositionSnapshot, RuntimeState


_POSITION_TAXONOMY_KEYS = (
    "taxonomy_stop_loss",
    "invalidation_source",
    "invalidation_reason",
    "stop_family",
    "stop_reference",
    "stop_policy_source",
)
_TARGET_MANAGEMENT_KEYS = (
    "structure_target_price",
    "first_target_price",
    "first_target_source",
    "first_target_status",
    "first_target_hit",
    "first_target_filled_qty",
    "second_target_price",
    "second_target_source",
    "second_target_status",
    "second_target_hit",
    "second_target_filled_qty",
    "runner_protected",
    "runner_stop_price",
    "original_position_qty",
    "remaining_position_qty",
    "scale_out_plan",
    "symbol_step_size",
    "min_order_qty",
    "legacy_partial_filled_qty",
)


def _now_bj() -> str:
    return datetime.now(BJ).isoformat()


def _round_price(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 8)


def _position_notional(snapshot: PositionSnapshot) -> float:
    if snapshot.notional:
        return round(abs(float(snapshot.notional)), 4)
    reference_price = snapshot.mark_price or snapshot.entry_price
    return round(abs(float(snapshot.qty)) * reference_price, 4)


def _unrealized_pnl(side: str, qty: float, entry_price: float, mark_price: float | None, fallback: float) -> float:
    if qty <= 0 or entry_price <= 0 or mark_price is None or mark_price <= 0:
        return round(float(fallback), 4)
    if side == "LONG":
        return round((mark_price - entry_price) * qty, 4)
    return round((entry_price - mark_price) * qty, 4)


def _source(existing: dict[str, Any], from_snapshot: bool, from_intent: bool) -> str:
    if from_snapshot and from_intent:
        return "hybrid"
    if from_intent:
        return "paper_execution"
    return existing.get("source", "account_snapshot")


def _taxonomy_fields(existing: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in _POSITION_TAXONOMY_KEYS:
        value = existing.get(key)
        if value is not None:
            payload[key] = value
    return payload


def _order_taxonomy_fields(order: OrderIntent, existing: dict[str, Any]) -> dict[str, Any]:
    meta = dict(order.meta or {})
    payload = _taxonomy_fields(existing)
    taxonomy_stop_loss = meta.get("taxonomy_stop_loss")
    if taxonomy_stop_loss is None:
        taxonomy_stop_loss = order.stop_loss
    try:
        payload["taxonomy_stop_loss"] = round(float(taxonomy_stop_loss), 8)
    except (TypeError, ValueError):
        pass
    for key in _POSITION_TAXONOMY_KEYS[1:]:
        value = meta.get(key)
        if value is not None:
            payload[key] = value
    return payload


def _target_management_fields(existing: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in _TARGET_MANAGEMENT_KEYS:
        if key in existing:
            payload[key] = existing.get(key)
    return payload


def _order_target_management_fields(order: OrderIntent) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    meta = dict(order.meta or {})
    for key in _TARGET_MANAGEMENT_KEYS:
        if key in meta:
            payload[key] = meta.get(key)
    return payload


def sync_positions_from_account(state: RuntimeState, account: AccountSnapshot) -> list[dict[str, Any]]:
    now_bj = _now_bj()
    seen_symbols: set[str] = set()

    for snapshot in account.open_positions:
        if snapshot.qty <= 0:
            continue

        existing = state.positions.get(snapshot.symbol, {})
        carry_existing = existing if existing.get("side") == snapshot.side else {}
        tracked_from_intent = bool(carry_existing.get("tracked_from_intent"))
        seen_symbols.add(snapshot.symbol)

        preserve_paper_position = (
            tracked_from_intent
            and float(carry_existing.get("qty", 0.0) or 0.0) > 0.0
            and float(carry_existing.get("entry_price", 0.0) or 0.0) > 0.0
        )
        qty = round(abs(float(carry_existing.get("qty", snapshot.qty) or snapshot.qty)), 6) if preserve_paper_position else round(
            abs(float(snapshot.qty)), 6
        )
        entry_price = (
            _round_price(float(carry_existing.get("entry_price", snapshot.entry_price) or snapshot.entry_price)) or 0.0
            if preserve_paper_position
            else (_round_price(snapshot.entry_price) or 0.0)
        )
        mark_price = _round_price(snapshot.mark_price)
        reference_price = mark_price or entry_price
        notional = round(qty * reference_price, 4) if preserve_paper_position else _position_notional(snapshot)
        unrealized_pnl = (
            _unrealized_pnl(snapshot.side, qty, entry_price, mark_price, snapshot.unrealized_pnl)
            if preserve_paper_position
            else round(float(snapshot.unrealized_pnl), 4)
        )

        synced_position = {
            "symbol": snapshot.symbol,
            "side": snapshot.side,
            "qty": qty,
            "entry_price": entry_price,
            "mark_price": mark_price,
            "unrealized_pnl": unrealized_pnl,
            "notional": notional,
            "leverage": snapshot.leverage,
            "stop_loss": carry_existing.get("stop_loss"),
            "take_profit": carry_existing.get("take_profit"),
            "status": "OPEN",
            "intent_id": carry_existing.get("intent_id"),
            "signal_id": carry_existing.get("signal_id"),
            **_taxonomy_fields(carry_existing),
            "source": _source(carry_existing, from_snapshot=True, from_intent=tracked_from_intent),
            "tracked_from_snapshot": True,
            "tracked_from_intent": tracked_from_intent,
            "opened_at_bj": carry_existing.get("opened_at_bj", now_bj),
            "updated_at_bj": now_bj,
            "last_synced_from": "account_snapshot",
            **_target_management_fields(carry_existing),
        }
        synced_position["remaining_position_qty"] = round(qty, 8)
        state.positions[snapshot.symbol] = ensure_target_management_state(synced_position)

    stale_symbols: list[str] = []
    for symbol, position in state.positions.items():
        if symbol in seen_symbols:
            continue
        if position.get("tracked_from_snapshot") and not position.get("tracked_from_intent"):
            stale_symbols.append(symbol)
            continue
        if position.get("tracked_from_snapshot"):
            position["tracked_from_snapshot"] = False
            position["source"] = _source(position, from_snapshot=False, from_intent=True)
            position["updated_at_bj"] = now_bj
            position["last_synced_from"] = "state_only"

    for symbol in stale_symbols:
        state.positions.pop(symbol, None)

    return list(state.positions.values())


def apply_executed_intent(state: RuntimeState, order: OrderIntent) -> dict[str, Any]:
    now_bj = _now_bj()
    existing = state.positions.get(order.symbol, {})
    tracked_from_snapshot = bool(existing.get("tracked_from_snapshot"))
    same_side = existing.get("side") == order.side
    carry_existing = existing if same_side else {}

    existing_qty = float(existing.get("qty", 0.0)) if same_side else 0.0
    aggregate_qty = existing_qty + float(order.qty)
    if aggregate_qty > 0 and existing_qty > 0:
        weighted_entry = (
            existing_qty * float(existing.get("entry_price", order.entry_price)) + float(order.qty) * float(order.entry_price)
        ) / aggregate_qty
    else:
        weighted_entry = float(order.entry_price)

    target_management_fields = _target_management_fields(carry_existing)
    order_target_management_fields = _order_target_management_fields(order)
    if order_target_management_fields:
        if not target_management_fields.get("first_target_price"):
            for key in ("first_target_price", "first_target_source"):
                if key in order_target_management_fields:
                    target_management_fields[key] = order_target_management_fields.get(key)
            for key in ("first_target_status", "first_target_hit", "first_target_filled_qty"):
                if key in order_target_management_fields and target_management_fields.get(key) is None:
                    target_management_fields[key] = order_target_management_fields.get(key)

        if not target_management_fields.get("second_target_price"):
            for key in ("second_target_price", "second_target_source"):
                if key in order_target_management_fields:
                    target_management_fields[key] = order_target_management_fields.get(key)
            for key in (
                "second_target_status",
                "second_target_hit",
                "second_target_filled_qty",
                "runner_protected",
                "runner_stop_price",
            ):
                if key in order_target_management_fields and target_management_fields.get(key) is None:
                    target_management_fields[key] = order_target_management_fields.get(key)

        for key in (
            "structure_target_price",
            "original_position_qty",
            "remaining_position_qty",
            "scale_out_plan",
            "symbol_step_size",
            "min_order_qty",
            "legacy_partial_filled_qty",
        ):
            if key in order_target_management_fields and target_management_fields.get(key) is None:
                target_management_fields[key] = order_target_management_fields.get(key)

    position = {
        "symbol": order.symbol,
        "side": order.side,
        "qty": round(aggregate_qty if aggregate_qty > 0 else float(order.qty), 6),
        "entry_price": round(weighted_entry, 8),
        "mark_price": carry_existing.get("mark_price", round(float(order.entry_price), 8)),
        "unrealized_pnl": round(float(carry_existing.get("unrealized_pnl", 0.0)), 4),
        "notional": round((aggregate_qty if aggregate_qty > 0 else float(order.qty)) * weighted_entry, 4),
        "leverage": carry_existing.get("leverage"),
        "stop_loss": round(float(order.stop_loss), 8),
        "take_profit": _round_price(order.take_profit),
        "status": "OPEN" if order.status in {"FILLED", "SENT"} else order.status,
        "intent_id": order.intent_id,
        "signal_id": order.signal_id,
        **_order_taxonomy_fields(order, carry_existing),
        **target_management_fields,
        "remaining_position_qty": round(aggregate_qty if aggregate_qty > 0 else float(order.qty), 8),
        "source": _source(carry_existing, from_snapshot=tracked_from_snapshot and same_side, from_intent=True),
        "tracked_from_snapshot": tracked_from_snapshot and same_side,
        "tracked_from_intent": True,
        "opened_at_bj": carry_existing.get("opened_at_bj", now_bj),
        "updated_at_bj": now_bj,
        "last_synced_from": "executed_intent",
    }
    updated_position = ensure_target_management_state(position)
    state.positions[order.symbol] = updated_position
    return updated_position


def apply_management_action_fill(state: RuntimeState, intent: ManagementActionIntent) -> dict[str, Any]:
    existing = state.positions.get(intent.symbol)
    if not existing:
        return {}

    position = dict(existing)
    current_qty = float(position.get("qty", 0.0) or 0.0)
    filled_qty = round(float(intent.qty or 0.0), 8)
    remaining_qty = max(round(current_qty - filled_qty, 8), 0.0)
    position["qty"] = remaining_qty
    position["remaining_position_qty"] = remaining_qty

    stage = str((intent.meta or {}).get("target_stage") or "")
    if intent.action == "PARTIAL_TAKE_PROFIT" and stage in {"first", "second"}:
        key = f"{stage}_target_filled_qty"
        position[key] = round(float(position.get(key, 0.0) or 0.0) + filled_qty, 8)
        if stage_completed(position, stage=stage):
            position[f"{stage}_target_status"] = "filled"
            position[f"{stage}_target_hit"] = True
            if stage == "second" and remaining_qty > 0:
                position["runner_protected"] = bool((intent.meta or {}).get("runner_protected"))
                position["runner_stop_price"] = (intent.meta or {}).get("runner_stop_price")
            elif stage == "second":
                position["runner_protected"] = False
                position["runner_stop_price"] = None
        else:
            position[f"{stage}_target_status"] = "pending"
            position[f"{stage}_target_hit"] = False
            if stage == "second":
                position["runner_protected"] = False
                position["runner_stop_price"] = None
    elif intent.action == "EXIT":
        position["qty"] = 0.0
        position["remaining_position_qty"] = 0.0
        position["runner_protected"] = False
        position["runner_stop_price"] = None

    updated = terminalize_all_unreachable_stages(position)
    state.positions[intent.symbol] = updated
    return updated
