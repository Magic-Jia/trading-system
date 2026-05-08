from __future__ import annotations

import math
from typing import Any, Iterable

from ...binance_client import FUTURES_BASE, public_get


REQUIRED_FILTERS = {
    "PRICE_FILTER",
    "LOT_SIZE",
}
OPTIONAL_MIN_NOTIONAL_FILTERS = ("MIN_NOTIONAL", "NOTIONAL")


def fetch_futures_testnet_exchange_info() -> dict[str, Any]:
    payload = public_get(FUTURES_BASE, "/fapi/v1/exchangeInfo")
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected futures testnet exchange info payload")
    return payload


def _float_value(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise RuntimeError(f"unsupported exchange metadata field: {label}")
    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"unsupported exchange metadata field: {label}") from exc
    if not math.isfinite(numeric_value) or numeric_value <= 0:
        raise RuntimeError(f"unsupported exchange metadata field: {label}")
    return numeric_value


def _canonical_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"unsupported exchange metadata field: {label}")
    return value.strip().upper()


def _required_exchange_string(row: dict[str, Any], key: str, *, label: str, transform: str = "upper") -> str:
    if key not in row:
        raise RuntimeError(f"unsupported exchange metadata field: {label}")
    value = row[key]
    if not isinstance(value, str):
        raise RuntimeError(f"unsupported exchange metadata field: {label}")
    if not value or value != value.strip():
        raise RuntimeError(f"unsupported exchange metadata field: {label}")
    if transform == "upper":
        return value.upper()
    if transform == "strict_upper" and value == value.upper():
        return value
    raise RuntimeError(f"unsupported exchange metadata field: {label}")


def _optional_exchange_string(row: dict[str, Any], key: str, *, label: str, transform: str = "upper") -> str:
    if key not in row:
        return ""
    return _required_exchange_string(row, key, label=label, transform=transform)


def _required_exchange_order_type(value: Any, *, symbol: str) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"unsupported exchange metadata field: {symbol}.orderTypes")
    if not value or value != value.strip() or value != value.upper():
        raise RuntimeError(f"unsupported exchange metadata field: {symbol}.orderTypes")
    return value


def _filter_index(symbol_row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    filters = symbol_row.get("filters", [])
    if not isinstance(filters, list):
        raise RuntimeError("unsupported futures symbol filters payload")

    indexed: dict[str, dict[str, Any]] = {}
    for row in filters:
        if not isinstance(row, dict):
            raise RuntimeError("unsupported futures symbol filter row")
        filter_type = _required_exchange_string(row, "filterType", label="filterType", transform="strict_upper")
        indexed[filter_type] = row
    return indexed


def _min_notional(filter_rows: dict[str, dict[str, Any]]) -> float:
    for filter_type in OPTIONAL_MIN_NOTIONAL_FILTERS:
        row = filter_rows.get(filter_type)
        if not row:
            continue
        field_name = "notional" if "notional" in row else "minNotional"
        if field_name not in row:
            continue
        return _float_value(row[field_name], label=f"{filter_type}.{field_name}")
    return 0.0


def _normalize_symbol_metadata(symbol_row: dict[str, Any]) -> dict[str, Any]:
    symbol = _required_exchange_string(symbol_row, "symbol", label="symbol")

    filter_rows = _filter_index(symbol_row)
    missing_filters = sorted(REQUIRED_FILTERS - set(filter_rows))
    if missing_filters:
        raise RuntimeError(f"missing futures exchange filters for {symbol}: {', '.join(missing_filters)}")

    order_types = symbol_row.get("orderTypes", [])
    if not isinstance(order_types, list):
        raise RuntimeError(f"unsupported order types for {symbol}")

    return {
        "quantity_step_size": _float_value(filter_rows["LOT_SIZE"].get("stepSize"), label=f"{symbol}.stepSize"),
        "price_tick_size": _float_value(filter_rows["PRICE_FILTER"].get("tickSize"), label=f"{symbol}.tickSize"),
        "min_notional": _min_notional(filter_rows),
        "allowed_order_types": [
            _required_exchange_order_type(order_type, symbol=symbol)
            for order_type in order_types
        ],
    }


def load_testnet_exchange_metadata(symbols: Iterable[str] | None = None) -> dict[str, dict[str, Any]]:
    payload = fetch_futures_testnet_exchange_info()
    symbol_rows = payload.get("symbols", [])
    if not isinstance(symbol_rows, list):
        raise RuntimeError("Unexpected futures testnet exchange metadata symbols payload")

    requested_symbols = None
    if symbols is not None:
        requested_symbols = {
            canonical_symbol
            for symbol in symbols
            if (canonical_symbol := _canonical_string(symbol, label="requested symbol"))
        }

    metadata: dict[str, dict[str, Any]] = {}
    for row in symbol_rows:
        if not isinstance(row, dict):
            raise RuntimeError("unsupported futures exchange symbol row")
        symbol = _optional_exchange_string(row, "symbol", label="symbol")
        if not symbol:
            continue
        if requested_symbols is not None and symbol not in requested_symbols:
            continue
        metadata[symbol] = _normalize_symbol_metadata(row)

    if requested_symbols is not None:
        missing_symbols = sorted(requested_symbols - set(metadata))
        if missing_symbols:
            raise RuntimeError(f"missing futures exchange metadata for: {', '.join(missing_symbols)}")

    return metadata
