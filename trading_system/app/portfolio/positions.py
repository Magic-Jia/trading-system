from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import math
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
_EXPLICIT_TARGET_MANAGEMENT_STATE_KEYS = (
    "first_target_price",
    "second_target_price",
    "first_target_status",
    "second_target_status",
    "first_target_filled_qty",
    "second_target_filled_qty",
    "runner_protected",
    "runner_stop_price",
    "scale_out_plan",
    "original_position_qty",
    "remaining_position_qty",
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


def _strict_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping when present")
    return value


def _strict_finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite number when present")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite when present")
    return number


def _strict_optional_number(payload: Mapping[str, Any], key: str, field: str | None = None, default: float | None = None) -> float:
    label = field or key
    if key not in payload or payload.get(key) is None:
        if default is None:
            raise ValueError(f"{label} must be present")
        return default
    return _strict_finite_number(payload.get(key), label)


def _strict_optional_string(payload: Mapping[str, Any], key: str, field: str | None = None) -> str:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return ""
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string when present")
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError(f"{label} must not be blank when present")
    return normalized


def _strict_optional_bool(payload: Mapping[str, Any], field: str, default: bool = False) -> bool:
    if field not in payload or payload.get(field) is None:
        return default
    value = payload.get(field)
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be a bool when present")


def _strict_non_negative_quantity(payload: Mapping[str, Any], field: str, default: float | None = None) -> float:
    if field not in payload or payload.get(field) is None:
        if default is None:
            raise ValueError(f"{field} must be present")
        return default
    try:
        qty = _strict_finite_number(payload.get(field), field)
    except TypeError as exc:
        raise ValueError(str(exc)) from exc
    if not math.isfinite(qty) or qty < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return qty


def _partial_take_profit_stage(intent: ManagementActionIntent) -> str:
    if intent.action != "PARTIAL_TAKE_PROFIT":
        return ""
    meta = intent.meta or {}
    if "target_stage" not in meta or meta.get("target_stage") is None:
        return ""
    stage = meta.get("target_stage")
    if not isinstance(stage, str) or stage not in {"first", "second"}:
        raise ValueError("target_stage must be absent or one of: first, second")
    return stage


def _taxonomy_fields(existing: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in _POSITION_TAXONOMY_KEYS:
        value = existing.get(key)
        if value is not None:
            payload[key] = value
    return payload


def _order_taxonomy_fields(order: OrderIntent, existing: dict[str, Any]) -> dict[str, Any]:
    meta = _strict_mapping(order.meta, "order.meta")
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
    meta = _strict_mapping(order.meta, "order.meta")
    for key in _TARGET_MANAGEMENT_KEYS:
        if key in meta:
            payload[key] = meta.get(key)
    return payload


def _has_explicit_target_management_state(existing: dict[str, Any]) -> bool:
    if not any(key in existing for key in _EXPLICIT_TARGET_MANAGEMENT_STATE_KEYS):
        return False

    def _positive(value: Any) -> bool:
        try:
            return float(value) > 0.0
        except (TypeError, ValueError):
            return False

    statuses: dict[str, str] = {}
    for key in ("first_target_status", "second_target_status"):
        status = _strict_optional_string(existing, key)
        statuses[key] = status
        if status and status != "pending":
            return True

    if _positive(existing.get("first_target_filled_qty")) or _positive(existing.get("second_target_filled_qty")):
        return True
    if _strict_optional_bool(existing, "runner_protected") or existing.get("runner_stop_price") is not None:
        return True
    if _positive(existing.get("legacy_partial_filled_qty")):
        return True

    first_source = _strict_optional_string(existing, "first_target_source")
    second_source = _strict_optional_string(existing, "second_target_source")
    has_legacy_or_structure_seed = existing.get("take_profit") is not None or existing.get("structure_target_price") is not None
    pending_only = all(not value or value == "pending" for value in statuses.values())
    fallback_seed_only = first_source == "fallback_1r" and second_source == "fixed_2r"

    if pending_only and fallback_seed_only and not has_legacy_or_structure_seed:
        return False

    return True


def _position_close_event_payload(symbol: str, position: dict[str, Any], now_bj: str) -> dict[str, Any]:
    return {
        "event": "POSITION_CLOSED",
        "symbol": symbol,
        "side": position.get("side"),
        "intent_id": position.get("intent_id"),
        "signal_id": position.get("signal_id"),
        "entry_price": position.get("entry_price"),
        "stop_loss": position.get("stop_loss"),
        "take_profit": position.get("take_profit"),
        "opened_at_bj": position.get("opened_at_bj"),
        "closed_at_bj": now_bj,
        "notified": False,
    }


def _mark_intent_position_closed(state: RuntimeState, symbol: str, position: dict[str, Any], now_bj: str) -> None:
    position["qty"] = 0.0
    position["remaining_position_qty"] = 0.0
    position["status"] = "CLOSED"
    position["closed_at_bj"] = now_bj
    position["tracked_from_snapshot"] = False
    position["source"] = _source(position, from_snapshot=False, from_intent=True)
    position["updated_at_bj"] = now_bj
    position["last_synced_from"] = "account_snapshot_closed"
    state.active_orders[f"position-closed-{symbol}"] = _position_close_event_payload(symbol, position, now_bj)


def sync_positions_from_account(state: RuntimeState, account: AccountSnapshot) -> list[dict[str, Any]]:
    now_bj = _now_bj()
    seen_symbols: set[str] = set()
    account_meta = _strict_mapping(account.meta, "account.meta")
    snapshot_source = _strict_optional_string(account_meta, "snapshot_source", "account.meta.snapshot_source")
    if not snapshot_source:
        snapshot_source = _strict_optional_string(account_meta, "source", "account.meta.source")

    for snapshot in account.open_positions:
        if snapshot.qty <= 0:
            continue

        existing = state.positions.get(snapshot.symbol, {})
        carry_existing = existing if existing.get("side") == snapshot.side else {}
        tracked_from_intent = _strict_optional_bool(carry_existing, "tracked_from_intent")
        seen_symbols.add(snapshot.symbol)

        carry_qty = _strict_optional_number(
            carry_existing,
            "qty",
            f"positions[{snapshot.symbol}].qty",
            default=0.0,
        )
        carry_entry_price = _strict_optional_number(
            carry_existing,
            "entry_price",
            f"positions[{snapshot.symbol}].entry_price",
            default=0.0,
        )
        preserve_paper_position = (
            tracked_from_intent
            and "testnet" not in snapshot_source
            and "binance" not in snapshot_source
            and carry_qty > 0.0
            and carry_entry_price > 0.0
        )
        qty = round(abs(carry_qty), 6) if preserve_paper_position else round(abs(float(snapshot.qty)), 6)
        entry_price = (
            _round_price(carry_entry_price) or 0.0
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
        state.active_orders.pop(f"position-closed-{snapshot.symbol}", None)

    stale_symbols: list[str] = []
    for symbol, position in state.positions.items():
        if symbol in seen_symbols:
            continue
        if position.get("tracked_from_snapshot") and not position.get("tracked_from_intent"):
            stale_symbols.append(symbol)
            continue
        status = str(position.get("status", "OPEN")).upper()
        if position.get("tracked_from_intent") and status not in {"CLOSED", "SKIPPED", "FAILED", "CANCELLED"}:
            _mark_intent_position_closed(state, symbol, position, now_bj)
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
    tracked_from_snapshot = _strict_optional_bool(existing, "tracked_from_snapshot")
    same_side = existing.get("side") == order.side
    carry_existing = existing if same_side else {}
    _strict_mapping(order.meta, "order.meta")
    order_qty = _strict_finite_number(order.qty, "order.qty")
    order_entry_price = _strict_finite_number(order.entry_price, "order.entry_price")
    order_stop_loss = _strict_finite_number(order.stop_loss, "order.stop_loss")
    order_take_profit = None if order.take_profit is None else _strict_finite_number(order.take_profit, "order.take_profit")
    existing_qty = (
        _strict_optional_number(carry_existing, "qty", f"positions[{order.symbol}].qty", default=0.0) if same_side else 0.0
    )
    aggregate_qty = existing_qty + order_qty
    if aggregate_qty > 0 and existing_qty > 0:
        existing_entry_price = _strict_optional_number(
            carry_existing,
            "entry_price",
            f"positions[{order.symbol}].entry_price",
            default=order_entry_price,
        )
        weighted_entry = (
            existing_qty * existing_entry_price + order_qty * order_entry_price
        ) / aggregate_qty
    else:
        weighted_entry = order_entry_price

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
        "qty": round(aggregate_qty if aggregate_qty > 0 else order_qty, 6),
        "entry_price": round(weighted_entry, 8),
        "mark_price": carry_existing.get("mark_price", round(order_entry_price, 8)),
        "unrealized_pnl": round(
            _strict_optional_number(
                carry_existing,
                "unrealized_pnl",
                f"positions[{order.symbol}].unrealized_pnl",
                default=0.0,
            ),
            4,
        ),
        "notional": round((aggregate_qty if aggregate_qty > 0 else order_qty) * weighted_entry, 4),
        "leverage": carry_existing.get("leverage"),
        "stop_loss": round(order_stop_loss, 8),
        "take_profit": _round_price(order_take_profit),
        "status": "OPEN" if order.status in {"FILLED", "SENT"} else order.status,
        "intent_id": order.intent_id,
        "signal_id": order.signal_id,
        **_order_taxonomy_fields(order, carry_existing),
        **target_management_fields,
        "remaining_position_qty": round(aggregate_qty if aggregate_qty > 0 else order_qty, 8),
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
    current_qty = _strict_non_negative_quantity(position, "qty", default=0.0)
    filled_qty = round(
        _strict_non_negative_quantity(
            {"qty": intent.qty} if intent.qty is not None else {},
            "qty",
            default=0.0,
        ),
        8,
    )
    remaining_qty = max(round(current_qty - filled_qty, 8), 0.0)
    position["qty"] = remaining_qty
    position["remaining_position_qty"] = remaining_qty

    stage = _partial_take_profit_stage(intent)
    if intent.action == "PARTIAL_TAKE_PROFIT" and stage in {"first", "second"}:
        key = f"{stage}_target_filled_qty"
        stage_filled_qty = _strict_non_negative_quantity(position, key, default=0.0)
        position[key] = round(stage_filled_qty + filled_qty, 8)
        if stage_completed(position, stage=stage):
            position[f"{stage}_target_status"] = "filled"
            position[f"{stage}_target_hit"] = True
            if stage == "second" and remaining_qty > 0:
                position["runner_protected"] = _strict_optional_bool(intent.meta or {}, "runner_protected")
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
