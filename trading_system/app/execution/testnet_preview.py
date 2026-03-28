from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from ..types import OrderIntent
from .orders import build_entry_order_payload, build_stop_order_payload, build_take_profit_payload


REQUIRED_ORDER_TYPES = {
    "entry": "MARKET",
    "stop": "STOP_MARKET",
    "take_profit": "TAKE_PROFIT_MARKET",
}


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
) -> None:
    entry_payload = payloads["entry"]
    stop_payload = payloads["stop"]
    take_profit_payload = payloads["take_profit"]

    if entry_payload is None or entry_payload.get("type") != REQUIRED_ORDER_TYPES["entry"]:
        reasons.append("fixed futures payload mapping incompatible: entry.type")
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


def build_validated_order_preview(
    intent: OrderIntent,
    *,
    exchange_metadata: dict[str, dict[str, Any]],
    allowlist: list[str],
    max_order_notional_usdt: float,
    submission_enabled: bool,
    preview_source: str,
) -> dict[str, Any]:
    del preview_source

    payloads = {
        "entry": build_entry_order_payload(intent),
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

    _validate_payload_mapping(reasons=reasons, payloads=payloads)

    return {
        "symbol": intent.symbol,
        "side": intent.side,
        "qty": intent.qty,
        "order_types": order_types,
        "payloads": payloads,
        "local_validation_passed": not reasons,
        "reasons": reasons,
    }
