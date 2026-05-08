from __future__ import annotations

from typing import Any

import pytest

from trading_system.app.data_sources import testnet_exchange_metadata as metadata_module


def _valid_symbol_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": "btcusdt",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "100"},
        ],
        "orderTypes": ["LIMIT", "MARKET"],
    }
    row.update(overrides)
    return row


def _exchange_info(row: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"symbols": [row or _valid_symbol_row()]}


def _valid_filters_with(replacement_filter: dict[str, Any]) -> list[dict[str, Any]]:
    filters = [
        {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
        {"filterType": "MIN_NOTIONAL", "notional": "100"},
    ]
    for index, filter_row in enumerate(filters):
        if filter_row["filterType"] == replacement_filter["filterType"]:
            filters[index] = replacement_filter
            return filters
    raise AssertionError("replacement filter type not found")


def test_load_testnet_exchange_metadata_preserves_valid_string_normalization(monkeypatch):
    monkeypatch.setattr(metadata_module, "fetch_futures_testnet_exchange_info", lambda: _exchange_info())

    metadata = metadata_module.load_testnet_exchange_metadata([" btcusdt "])

    assert metadata == {
        "BTCUSDT": {
            "quantity_step_size": 0.001,
            "price_tick_size": 0.1,
            "min_notional": 100.0,
            "allowed_order_types": ["LIMIT", "MARKET"],
        }
    }


@pytest.mark.parametrize("symbol", [123, True])
def test_load_testnet_exchange_metadata_rejects_present_non_string_symbol_row(monkeypatch, symbol):
    monkeypatch.setattr(
        metadata_module,
        "fetch_futures_testnet_exchange_info",
        lambda: _exchange_info(_valid_symbol_row(symbol=symbol)),
    )

    with pytest.raises(RuntimeError, match="symbol"):
        metadata_module.load_testnet_exchange_metadata()


@pytest.mark.parametrize("filter_type", [123, True])
def test_load_testnet_exchange_metadata_rejects_present_non_string_filter_type(monkeypatch, filter_type):
    row = _valid_symbol_row(
        filters=[
            {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
            {"filterType": filter_type, "stepSize": "0.001"},
        ]
    )
    monkeypatch.setattr(metadata_module, "fetch_futures_testnet_exchange_info", lambda: _exchange_info(row))

    with pytest.raises(RuntimeError, match="filterType"):
        metadata_module.load_testnet_exchange_metadata()


@pytest.mark.parametrize("order_type", [123, True])
def test_load_testnet_exchange_metadata_rejects_present_non_string_order_type(monkeypatch, order_type):
    monkeypatch.setattr(
        metadata_module,
        "fetch_futures_testnet_exchange_info",
        lambda: _exchange_info(_valid_symbol_row(orderTypes=["LIMIT", order_type])),
    )

    with pytest.raises(RuntimeError, match="orderTypes"):
        metadata_module.load_testnet_exchange_metadata()


@pytest.mark.parametrize("requested_symbol", [123, True])
def test_load_testnet_exchange_metadata_rejects_present_non_string_requested_symbol(monkeypatch, requested_symbol):
    monkeypatch.setattr(metadata_module, "fetch_futures_testnet_exchange_info", lambda: _exchange_info())

    with pytest.raises(RuntimeError, match="requested symbol"):
        metadata_module.load_testnet_exchange_metadata([requested_symbol])


@pytest.mark.parametrize(
    "invalid_value",
    [
        True,
        "",
        "   ",
        "not-a-number",
        float("nan"),
        float("inf"),
        -float("inf"),
    ],
)
def test_load_testnet_exchange_metadata_rejects_invalid_price_tick_size(monkeypatch, invalid_value):
    row = _valid_symbol_row(
        filters=_valid_filters_with({"filterType": "PRICE_FILTER", "tickSize": invalid_value})
    )
    monkeypatch.setattr(metadata_module, "fetch_futures_testnet_exchange_info", lambda: _exchange_info(row))

    with pytest.raises(RuntimeError, match=r"BTCUSDT\.tickSize"):
        metadata_module.load_testnet_exchange_metadata()


@pytest.mark.parametrize(
    "invalid_value",
    [
        True,
        "",
        "   ",
        "not-a-number",
        float("nan"),
        float("inf"),
        -float("inf"),
    ],
)
def test_load_testnet_exchange_metadata_rejects_invalid_lot_step_size(monkeypatch, invalid_value):
    row = _valid_symbol_row(filters=_valid_filters_with({"filterType": "LOT_SIZE", "stepSize": invalid_value}))
    monkeypatch.setattr(metadata_module, "fetch_futures_testnet_exchange_info", lambda: _exchange_info(row))

    with pytest.raises(RuntimeError, match=r"BTCUSDT\.stepSize"):
        metadata_module.load_testnet_exchange_metadata()


@pytest.mark.parametrize("field_name", ["notional", "minNotional"])
@pytest.mark.parametrize(
    "invalid_value",
    [
        True,
        "",
        "   ",
        "not-a-number",
        float("nan"),
        float("inf"),
        -float("inf"),
    ],
)
def test_load_testnet_exchange_metadata_rejects_invalid_min_notional(monkeypatch, field_name, invalid_value):
    row = _valid_symbol_row(
        filters=_valid_filters_with({"filterType": "MIN_NOTIONAL", field_name: invalid_value})
    )
    monkeypatch.setattr(metadata_module, "fetch_futures_testnet_exchange_info", lambda: _exchange_info(row))

    with pytest.raises(RuntimeError, match=rf"MIN_NOTIONAL\.{field_name}"):
        metadata_module.load_testnet_exchange_metadata()
