from __future__ import annotations

import json
from pathlib import Path

from trading_system.fetch_binance_futures_rulebook_catalog import build_rulebook_catalog_from_exchange_info

GENERATED_AT = "2026-05-19T02:30:00Z"


def _exchange_info_payload() -> dict:
    return {
        "timezone": "UTC",
        "serverTime": 1779148815486,
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10", "minPrice": "556.80", "maxPrice": "4529764"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
                    {"filterType": "MIN_NOTIONAL", "notional": "50"},
                ],
            },
            {
                "symbol": "ETHUSDT",
                "contractType": "PERPETUAL",
                "status": "SETTLING",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "20"},
                ],
            },
        ],
    }


def test_build_rulebook_catalog_from_binance_futures_exchange_info_filters_trading_symbols():
    catalog = build_rulebook_catalog_from_exchange_info(
        _exchange_info_payload(),
        generated_at=GENERATED_AT,
        symbols=["BTCUSDT"],
    )

    assert catalog["schema_version"] == "venue_rulebook_catalog.v1"
    assert catalog["generated_at"] == GENERATED_AT
    assert catalog["coverage_report"]["quality_status"] == "pass"
    assert [rulebook["symbol"] for rulebook in catalog["rulebooks"]] == ["BTCUSDT"]
    rulebook = catalog["rulebooks"][0]
    assert rulebook["venue"] == "binance_futures"
    assert rulebook["product_type"] == "usdt_perpetual"
    assert rulebook["constraints"] == {
        "price_tick_size": 0.1,
        "quantity_step_size": 0.001,
        "min_notional": 50.0,
        "post_only_policy": "reject_would_cross",
        "reduce_only_policy": "allow",
    }
    assert rulebook["source"] == "https://fapi.binance.com/fapi/v1/exchangeInfo"
    assert rulebook["rulebook_version"].startswith("binance-futures-BTCUSDT-")


def test_build_rulebook_catalog_fails_closed_when_required_filter_missing():
    payload = _exchange_info_payload()
    payload["symbols"][0]["filters"] = [{"filterType": "PRICE_FILTER", "tickSize": "0.10"}]

    try:
        build_rulebook_catalog_from_exchange_info(payload, generated_at=GENERATED_AT, symbols=["BTCUSDT"])
    except ValueError as exc:
        assert "LOT_SIZE" in str(exc)
    else:
        raise AssertionError("expected missing LOT_SIZE to fail closed")
