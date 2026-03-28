from __future__ import annotations

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
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"unsupported exchange metadata field: {label}") from exc


def _filter_index(symbol_row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    filters = symbol_row.get("filters", [])
    if not isinstance(filters, list):
        raise RuntimeError("unsupported futures symbol filters payload")

    indexed: dict[str, dict[str, Any]] = {}
    for row in filters:
        if not isinstance(row, dict):
            raise RuntimeError("unsupported futures symbol filter row")
        filter_type = str(row.get("filterType", "")).strip().upper()
        if filter_type:
            indexed[filter_type] = row
    return indexed


def _min_notional(filter_rows: dict[str, dict[str, Any]]) -> float:
    for filter_type in OPTIONAL_MIN_NOTIONAL_FILTERS:
        row = filter_rows.get(filter_type)
        if not row:
            continue
        raw_value = row.get("notional", row.get("minNotional"))
        if raw_value is None:
            continue
        return _float_value(raw_value, label=f"{filter_type}.min_notional")
    return 0.0


def _normalize_symbol_metadata(symbol_row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(symbol_row.get("symbol", "")).strip().upper()
    if not symbol:
        raise RuntimeError("unsupported futures symbol metadata")

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
        "allowed_order_types": [str(order_type).strip().upper() for order_type in order_types if str(order_type).strip()],
    }


def load_testnet_exchange_metadata(symbols: Iterable[str] | None = None) -> dict[str, dict[str, Any]]:
    payload = fetch_futures_testnet_exchange_info()
    symbol_rows = payload.get("symbols", [])
    if not isinstance(symbol_rows, list):
        raise RuntimeError("Unexpected futures testnet exchange metadata symbols payload")

    requested_symbols = None
    if symbols is not None:
        requested_symbols = {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}

    metadata: dict[str, dict[str, Any]] = {}
    for row in symbol_rows:
        if not isinstance(row, dict):
            raise RuntimeError("unsupported futures exchange symbol row")
        symbol = str(row.get("symbol", "")).strip().upper()
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
