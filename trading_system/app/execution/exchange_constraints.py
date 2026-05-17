from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

EXCHANGE_REJECT_REASON_CODES = (
    "tick_size_violation",
    "lot_size_violation",
    "min_notional_violation",
    "post_only_would_cross",
    "reduce_only_invalid",
    "insufficient_margin",
    "insufficient_balance",
    "rate_limit",
    "exchange_outage",
    "duplicate_client_order_id",
)

_EXCHANGE_CODE_TO_REASON = {
    "POST_ONLY_WOULD_CROSS": "post_only_would_cross",
    "REDUCE_ONLY_INVALID": "reduce_only_invalid",
    "INSUFFICIENT_MARGIN": "insufficient_margin",
    "INSUFFICIENT_BALANCE": "insufficient_balance",
    "RATE_LIMIT": "rate_limit",
    "EXCHANGE_OUTAGE": "exchange_outage",
    "DUPLICATE_CLIENT_ORDER_ID": "duplicate_client_order_id",
}


def reject_reason_from_exchange_code(raw_code: str) -> str:
    if not isinstance(raw_code, str) or not raw_code.strip():
        raise ValueError("exchange reject code must be a non-empty string")
    normalized = raw_code.strip().upper()
    reason_code = _EXCHANGE_CODE_TO_REASON.get(normalized)
    if reason_code is None:
        raise ValueError(f"unknown exchange reject code: {raw_code}")
    return reason_code


def build_exchange_reject_event(
    *,
    venue: str,
    symbol: str,
    client_order_id: str,
    raw_code: str,
    generated_at: str,
    max_age_seconds: int,
    now: str,
) -> dict[str, Any]:
    venue_value = _required_non_empty_string(venue, "venue")
    symbol_value = _required_non_empty_string(symbol, "symbol")
    client_order_id_value = _required_non_empty_string(client_order_id, "client_order_id")
    generated_at_dt = _parse_utc_timestamp(generated_at, "generated_at")
    now_dt = _parse_utc_timestamp(now, "now")
    max_age = _strict_positive_int(max_age_seconds, "max_age_seconds")
    if (now_dt - generated_at_dt).total_seconds() > max_age:
        raise ValueError("stale generated_at")
    return {
        "schema_version": "exchange_reject_event.v1",
        "venue": venue_value,
        "symbol": symbol_value,
        "client_order_id": client_order_id_value,
        "generated_at": generated_at,
        "raw_code": raw_code.strip().upper(),
        "reason_code": reject_reason_from_exchange_code(raw_code),
    }


def build_exchange_constraint_report(
    *,
    venue: str,
    symbol: str,
    generated_at: str,
    order: Mapping[str, Any],
    constraints: Mapping[str, Any],
) -> dict[str, Any]:
    venue_value = _required_non_empty_string(venue, "venue")
    symbol_value = _required_non_empty_string(symbol, "symbol")
    tick_size = _strict_positive_number(constraints.get("price_tick_size"), "price_tick_size")
    lot_size = _strict_positive_number(constraints.get("quantity_step_size"), "quantity_step_size")
    min_notional = _strict_non_negative_number(constraints.get("min_notional", 0.0), "min_notional")
    reason_codes: list[str] = []

    quantity = _strict_non_negative_number(order.get("quantity"), "quantity")
    if quantity == 0.0 or not _aligned_to_increment(quantity, lot_size):
        reason_codes.append("lot_size_violation")

    for label in ("price", "stop_price", "take_profit_stop_price"):
        value = order.get(label)
        if value is not None and not _aligned_to_increment(_strict_non_negative_number(value, label), tick_size):
            reason_codes.append("tick_size_violation")

    price = order.get("price")
    if price is not None:
        notional = quantity * _strict_non_negative_number(price, "price")
        if min_notional and notional < min_notional:
            reason_codes.append("min_notional_violation")

    if order.get("post_only") is True and _post_only_would_cross(order):
        reason_codes.append("post_only_would_cross")
    if order.get("reduce_only") is True and not order.get("has_position", True):
        reason_codes.append("reduce_only_invalid")

    return {
        "schema_version": "exchange_reject_report.v1",
        "venue": venue_value,
        "symbol": symbol_value,
        "generated_at": generated_at,
        "validation_passed": not reason_codes,
        "reason_codes": reason_codes,
    }


def _required_non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _strict_positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be a positive integer")
    if value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _strict_positive_number(value: Any, label: str) -> float:
    number = _strict_number(value, label)
    if number <= 0.0:
        raise ValueError(f"{label} must be a positive finite number")
    return number


def _strict_non_negative_number(value: Any, label: str) -> float:
    number = _strict_number(value, label)
    if number < 0.0:
        raise ValueError(f"{label} must be a non-negative finite number")
    return number


def _strict_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite non-bool number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite non-bool number")
    return number


def _aligned_to_increment(value: float, increment: float) -> bool:
    try:
        return Decimal(str(value)) % Decimal(str(increment)) == 0
    except InvalidOperation as exc:
        raise ValueError("unsupported exchange constraint increment validation") from exc


def _post_only_would_cross(order: Mapping[str, Any]) -> bool:
    side = order.get("side")
    price = order.get("price")
    if side not in {"BUY", "SELL"} or price is None:
        return False
    order_price = _strict_non_negative_number(price, "price")
    if side == "BUY" and order.get("best_ask") is not None:
        return order_price >= _strict_non_negative_number(order.get("best_ask"), "best_ask")
    if side == "SELL" and order.get("best_bid") is not None:
        return order_price <= _strict_non_negative_number(order.get("best_bid"), "best_bid")
    return False


def _parse_utc_timestamp(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty timestamp")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include timezone")
    return parsed.astimezone(timezone.utc)
