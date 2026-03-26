from __future__ import annotations

from datetime import datetime
from typing import Any

from ..types import AccountSnapshot, BJ, OrderIntent, PositionSnapshot, RuntimeState


_POSITION_TAXONOMY_KEYS = (
    "taxonomy_stop_loss",
    "invalidation_source",
    "invalidation_reason",
    "stop_family",
    "stop_reference",
    "stop_policy_source",
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


def sync_positions_from_account(state: RuntimeState, account: AccountSnapshot) -> list[dict[str, Any]]:
    now_bj = _now_bj()
    seen_symbols: set[str] = set()

    for snapshot in account.open_positions:
        if snapshot.qty <= 0:
            continue

        existing = state.positions.get(snapshot.symbol, {})
        tracked_from_intent = bool(existing.get("tracked_from_intent"))
        seen_symbols.add(snapshot.symbol)

        preserve_paper_position = (
            tracked_from_intent
            and existing.get("side") == snapshot.side
            and float(existing.get("qty", 0.0) or 0.0) > 0.0
            and float(existing.get("entry_price", 0.0) or 0.0) > 0.0
        )
        qty = round(abs(float(existing.get("qty", snapshot.qty) or snapshot.qty)), 6) if preserve_paper_position else round(
            abs(float(snapshot.qty)), 6
        )
        entry_price = (
            _round_price(float(existing.get("entry_price", snapshot.entry_price) or snapshot.entry_price)) or 0.0
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

        state.positions[snapshot.symbol] = {
            "symbol": snapshot.symbol,
            "side": snapshot.side,
            "qty": qty,
            "entry_price": entry_price,
            "mark_price": mark_price,
            "unrealized_pnl": unrealized_pnl,
            "notional": notional,
            "leverage": snapshot.leverage,
            "stop_loss": existing.get("stop_loss"),
            "take_profit": existing.get("take_profit"),
            "status": "OPEN",
            "intent_id": existing.get("intent_id"),
            "signal_id": existing.get("signal_id"),
            **_taxonomy_fields(existing),
            "source": _source(existing, from_snapshot=True, from_intent=tracked_from_intent),
            "tracked_from_snapshot": True,
            "tracked_from_intent": tracked_from_intent,
            "opened_at_bj": existing.get("opened_at_bj", now_bj),
            "updated_at_bj": now_bj,
            "last_synced_from": "account_snapshot",
        }

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

    existing_qty = float(existing.get("qty", 0.0)) if same_side else 0.0
    aggregate_qty = existing_qty + float(order.qty)
    if aggregate_qty > 0 and existing_qty > 0:
        weighted_entry = (
            existing_qty * float(existing.get("entry_price", order.entry_price)) + float(order.qty) * float(order.entry_price)
        ) / aggregate_qty
    else:
        weighted_entry = float(order.entry_price)

    position = {
        "symbol": order.symbol,
        "side": order.side,
        "qty": round(aggregate_qty if aggregate_qty > 0 else float(order.qty), 6),
        "entry_price": round(weighted_entry, 8),
        "mark_price": existing.get("mark_price", round(float(order.entry_price), 8)),
        "unrealized_pnl": round(float(existing.get("unrealized_pnl", 0.0)), 4),
        "notional": round((aggregate_qty if aggregate_qty > 0 else float(order.qty)) * weighted_entry, 4),
        "leverage": existing.get("leverage"),
        "stop_loss": round(float(order.stop_loss), 8),
        "take_profit": _round_price(order.take_profit),
        "status": "OPEN" if order.status in {"FILLED", "SENT"} else order.status,
        "intent_id": order.intent_id,
        "signal_id": order.signal_id,
        **_order_taxonomy_fields(order, existing),
        "source": _source(existing, from_snapshot=tracked_from_snapshot, from_intent=True),
        "tracked_from_snapshot": tracked_from_snapshot,
        "tracked_from_intent": True,
        "opened_at_bj": existing.get("opened_at_bj", now_bj),
        "updated_at_bj": now_bj,
        "last_synced_from": "executed_intent",
    }
    state.positions[order.symbol] = position
    return position
