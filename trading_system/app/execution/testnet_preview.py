from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from ..types import OrderIntent
from .orders import EntryOrderPolicy, build_entry_order_payload, build_stop_order_payload, build_take_profit_payload


REQUIRED_ORDER_TYPES = {
    "entry": "LIMIT",
    "stop": "STOP_MARKET",
    "take_profit": "TAKE_PROFIT_MARKET",
}

_UNSUPPORTED_REASON_CODES = (
    ("symbol not allowed for testnet preview", "symbol_not_allowed"),
    ("missing exchange metadata", "missing_exchange_metadata"),
    ("order type incompatible with exchange metadata", "order_type_incompatible"),
    ("entry notional below exchange minimum", "entry_notional_below_minimum"),
    ("entry notional exceeds testnet cap", "entry_notional_exceeds_cap"),
    ("quantity step size or precision incompatible", "quantity_precision_incompatible"),
    ("price tick size or precision incompatible", "price_precision_incompatible"),
    ("fixed futures payload mapping incompatible: entry.type", "entry_order_type_incompatible"),
    ("fixed futures payload mapping incompatible: entry.timeInForce", "entry_time_in_force_incompatible"),
    ("fixed futures payload mapping incompatible: entry.price", "entry_price_missing"),
    ("fixed futures payload mapping incompatible: stop.type", "stop_order_type_incompatible"),
    ("fixed futures payload mapping incompatible: stop.closePosition", "stop_close_position_incompatible"),
    ("fixed futures payload mapping incompatible: stop.workingType", "stop_working_type_incompatible"),
    ("fixed futures payload mapping incompatible: take_profit.type", "take_profit_order_type_incompatible"),
    ("fixed futures payload mapping incompatible: take_profit.closePosition", "take_profit_close_position_incompatible"),
    ("fixed futures payload mapping incompatible: take_profit.workingType", "take_profit_working_type_incompatible"),
)


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError(f"unsupported preview numeric value: {value}") from exc


def _aligned_to_increment(value: float | None, increment: float | None) -> bool:
    if value is None or increment in {None, 0}:
        return True
    decimal_value = _decimal(value)
    decimal_increment = _decimal(increment)
    try:
        return decimal_value % decimal_increment == 0
    except InvalidOperation as exc:
        raise RuntimeError("unsupported preview increment validation") from exc


def _append_step_and_precision_reasons(
    *,
    reasons: list[str],
    intent: OrderIntent,
    quantity_step_size: float,
    price_tick_size: float,
) -> None:
    if not _aligned_to_increment(intent.qty, quantity_step_size):
        reasons.append(
            f"quantity step size or precision incompatible: qty={intent.qty} step_size={quantity_step_size}"
        )
    for label, price in (
        ("entry_price", intent.entry_price),
        ("stop_loss", intent.stop_loss),
        ("take_profit", intent.take_profit),
    ):
        if price is None:
            continue
        if not _aligned_to_increment(price, price_tick_size):
            reasons.append(
                f"price tick size or precision incompatible: {label}={price} tick_size={price_tick_size}"
            )


def _validate_payload_mapping(
    *,
    reasons: list[str],
    payloads: dict[str, dict[str, Any] | None],
    entry_order_policy: EntryOrderPolicy,
) -> None:
    entry_payload = payloads["entry"]
    stop_payload = payloads["stop"]
    take_profit_payload = payloads["take_profit"]

    expected_entry_type = "MARKET" if entry_order_policy == "taker_market" else REQUIRED_ORDER_TYPES["entry"]
    if entry_payload is None or entry_payload.get("type") != expected_entry_type:
        reasons.append("fixed futures payload mapping incompatible: entry.type")
    if entry_order_policy == "maker_only":
        if entry_payload is None or entry_payload.get("timeInForce") != "GTX":
            reasons.append("fixed futures payload mapping incompatible: entry.timeInForce")
        if entry_payload is None or entry_payload.get("price") is None:
            reasons.append("fixed futures payload mapping incompatible: entry.price")
    if stop_payload is None or stop_payload.get("type") != REQUIRED_ORDER_TYPES["stop"]:
        reasons.append("fixed futures payload mapping incompatible: stop.type")
    if stop_payload is None or str(stop_payload.get("closePosition", "")).lower() != "true":
        reasons.append("fixed futures payload mapping incompatible: stop.closePosition")
    if stop_payload is None or stop_payload.get("workingType") != "MARK_PRICE":
        reasons.append("fixed futures payload mapping incompatible: stop.workingType")

    if take_profit_payload is not None:
        if take_profit_payload.get("type") != REQUIRED_ORDER_TYPES["take_profit"]:
            reasons.append("fixed futures payload mapping incompatible: take_profit.type")
        if str(take_profit_payload.get("closePosition", "")).lower() != "true":
            reasons.append("fixed futures payload mapping incompatible: take_profit.closePosition")
        if take_profit_payload.get("workingType") != "MARK_PRICE":
            reasons.append("fixed futures payload mapping incompatible: take_profit.workingType")


def _unsupported_reason_code(reason: str) -> str:
    for prefix, code in _UNSUPPORTED_REASON_CODES:
        if reason.startswith(prefix):
            return code
    return "unsupported_preview_payload"


def _bool_from_exchange_flag(value: Any) -> bool:
    return value is True or value == "true"


def _entry_notional(intent: OrderIntent, payload: dict[str, Any]) -> float | None:
    quantity = payload.get("quantity")
    if not isinstance(quantity, (int, float)) or isinstance(quantity, bool):
        return None
    price = payload.get("price", intent.entry_price)
    if not isinstance(price, (int, float)) or isinstance(price, bool):
        return None
    return float(quantity) * float(price)


def _replay_order_from_payload(
    *,
    intent: OrderIntent,
    payload: dict[str, Any],
    protective_order: bool,
) -> dict[str, Any]:
    order_type = payload.get("type")
    price = payload.get("price")
    stop_price = payload.get("stopPrice")
    time_in_force = payload.get("timeInForce")
    close_position = protective_order and _bool_from_exchange_flag(payload.get("closePosition"))
    return {
        "symbol": payload.get("symbol"),
        "side": payload.get("side"),
        "order_type": order_type,
        "quantity": None if close_position else payload.get("quantity"),
        "notional": None if close_position else _entry_notional(intent, payload),
        "price": price if order_type == "LIMIT" else None,
        "stop_price": stop_price,
        "limit_price": price if order_type == "LIMIT" else None,
        "reduce_only": protective_order,
        "close_position": close_position,
        "time_in_force": time_in_force,
        "post_only": time_in_force == "GTX",
    }


def _build_execution_preview(
    *,
    intent: OrderIntent,
    payloads: dict[str, dict[str, Any] | None],
    reasons: list[str],
) -> dict[str, Any]:
    orders: list[dict[str, Any]] = []
    entry_payload = payloads["entry"]
    if entry_payload is not None:
        orders.append(_replay_order_from_payload(intent=intent, payload=entry_payload, protective_order=False))
    for key in ("stop", "take_profit"):
        payload = payloads[key]
        if payload is not None:
            orders.append(_replay_order_from_payload(intent=intent, payload=payload, protective_order=True))
    return {
        "schema_version": "execution_preview.v1",
        "orders": orders,
        "unsupported": [{"reason_code": _unsupported_reason_code(reason), "detail": reason} for reason in reasons],
    }


def build_validated_order_preview(
    intent: OrderIntent,
    *,
    exchange_metadata: dict[str, dict[str, Any]],
    allowlist: list[str],
    max_order_notional_usdt: float,
    submission_enabled: bool,
    preview_source: str,
    entry_order_policy: EntryOrderPolicy = "maker_only",
    maker_entry_timeout_seconds: int = 15,
) -> dict[str, Any]:
    payloads = {
        "entry": build_entry_order_payload(intent, entry_order_policy=entry_order_policy),
        "stop": build_stop_order_payload(intent),
        "take_profit": build_take_profit_payload(intent),
    }
    order_types = [
        payload["type"]
        for payload in (payloads["entry"], payloads["stop"], payloads["take_profit"])
        if payload is not None
    ]

    reasons: list[str] = []
    normalized_allowlist = {str(symbol).strip().upper() for symbol in allowlist if str(symbol).strip()}
    if intent.symbol not in normalized_allowlist:
        reasons.append(f"symbol not allowed for testnet preview: {intent.symbol}")

    symbol_metadata = exchange_metadata.get(intent.symbol)
    if symbol_metadata is None:
        reasons.append(f"missing exchange metadata for {intent.symbol}")
    else:
        allowed_order_types = {
            str(order_type).strip().upper()
            for order_type in symbol_metadata.get("allowed_order_types", [])
            if str(order_type).strip()
        }
        for order_type in order_types:
            if order_type not in allowed_order_types:
                reasons.append(f"order type incompatible with exchange metadata: {order_type}")

        entry_notional = float(intent.qty) * float(intent.entry_price)
        min_notional = float(symbol_metadata.get("min_notional", 0.0) or 0.0)
        if min_notional and entry_notional < min_notional:
            reasons.append(
                f"entry notional below exchange minimum: notional={entry_notional} min_notional={min_notional}"
            )
        if entry_notional > float(max_order_notional_usdt):
            reasons.append(
                f"entry notional exceeds testnet cap: notional={entry_notional} max_order_notional_usdt={max_order_notional_usdt}"
            )

        _append_step_and_precision_reasons(
            reasons=reasons,
            intent=intent,
            quantity_step_size=float(symbol_metadata.get("quantity_step_size", 0.0) or 0.0),
            price_tick_size=float(symbol_metadata.get("price_tick_size", 0.0) or 0.0),
        )

    _validate_payload_mapping(reasons=reasons, payloads=payloads, entry_order_policy=entry_order_policy)

    submission_prerequisites_passed = not reasons

    return {
        "symbol": intent.symbol,
        "side": intent.side,
        "qty": intent.qty,
        "order_types": order_types,
        "payloads": payloads,
        "execution_preview": _build_execution_preview(intent=intent, payloads=payloads, reasons=reasons),
        "local_validation_passed": submission_prerequisites_passed,
        "submission_enabled": submission_enabled,
        "would_submit": submission_enabled and submission_prerequisites_passed,
        "submission_prerequisites_passed": submission_prerequisites_passed,
        "preview_source": preview_source,
        "entry_order_policy": entry_order_policy,
        "maker_entry_timeout_seconds": maker_entry_timeout_seconds,
        "reasons": reasons,
    }
