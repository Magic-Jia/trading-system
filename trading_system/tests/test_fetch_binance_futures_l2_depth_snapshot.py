from __future__ import annotations

import pytest

from trading_system.fetch_binance_futures_l2_depth_snapshot import build_l2_depth_snapshot

GENERATED_AT = "2026-05-19T03:05:00Z"


def test_build_l2_depth_snapshot_from_binance_futures_depth_payloads() -> None:
    payloads = {
        "BTCUSDT": {
            "lastUpdateId": 123,
            "E": 1770000000123,
            "T": 1770000000111,
            "bids": [["65000.00", "0.25"], ["64999.50", "0.10"]],
            "asks": [["65001.00", "0.20"], ["65002.00", "0.10"]],
        },
        "ETHUSDT": {
            "lastUpdateId": 456,
            "E": 1770000001123,
            "T": 1770000001111,
            "bids": [["3200.00", "1.5"]],
            "asks": [["3200.50", "1.0"]],
        },
    }

    snapshot = build_l2_depth_snapshot(payloads, generated_at=GENERATED_AT, source_url="https://fapi.binance.com/fapi/v1/depth")

    assert snapshot["schema_version"] == "local_l2_order_book_snapshot.v1"
    assert snapshot["source_id"] == "binance_usdm_futures_public_depth"
    assert snapshot["generated_at"] == GENERATED_AT
    assert snapshot["depth_count"] == 2
    assert snapshot["symbols"] == ["BTCUSDT", "ETHUSDT"]
    btc = snapshot["books"][0]
    assert btc["symbol"] == "BTCUSDT"
    assert btc["best_bid"] == 65000.0
    assert btc["best_ask"] == 65001.0
    assert round(btc["spread_bps"], 6) == round(((65001.0 - 65000.0) / 65000.5) * 10000.0, 6)
    assert btc["bid_depth_notional"] == 16250.0 + 6499.95
    assert btc["ask_depth_notional"] == 13000.2 + 6500.2
    assert btc["levels"] == {"bids": 2, "asks": 2}


def test_build_l2_depth_snapshot_fails_closed_for_crossed_or_missing_book() -> None:
    with pytest.raises(ValueError, match="best_ask must be greater than best_bid"):
        build_l2_depth_snapshot(
            {"BTCUSDT": {"lastUpdateId": 1, "bids": [["101", "1"]], "asks": [["100", "1"]]}},
            generated_at=GENERATED_AT,
            source_url="https://fapi.binance.com/fapi/v1/depth",
        )

    with pytest.raises(ValueError, match="bids must contain at least one level"):
        build_l2_depth_snapshot(
            {"BTCUSDT": {"lastUpdateId": 1, "bids": [], "asks": [["100", "1"]]}},
            generated_at=GENERATED_AT,
            source_url="https://fapi.binance.com/fapi/v1/depth",
        )
