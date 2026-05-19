from __future__ import annotations

from trading_system.fetch_coinbase_independent_source_snapshot import build_independent_source_snapshot

GENERATED_AT = "2026-05-19T02:45:00Z"


def test_build_coinbase_independent_source_snapshot_from_public_tickers() -> None:
    payloads = {
        "BTC-USDT": {"price": "100.01", "bid": "100.00", "ask": "100.02", "volume": "12.5", "time": "2026-05-19T02:44:59Z"},
        "ETH-USDT": {"price": "200.00", "bid": "199.95", "ask": "200.05", "volume": "20", "time": "2026-05-19T02:44:58.123456Z"},
    }

    snapshot = build_independent_source_snapshot(
        payloads,
        symbol_map={"BTCUSDT": "BTC-USDT", "ETHUSDT": "ETH-USDT"},
        generated_at=GENERATED_AT,
    )

    assert snapshot["schema_version"] == "local_independent_source_snapshot.v1"
    assert snapshot["source_id"] == "coinbase_exchange_public_ticker"
    assert snapshot["generated_at"] == GENERATED_AT
    assert snapshot["side_effect_boundary"] == {
        "real_orders": "forbidden",
        "testnet_orders": "forbidden",
        "credential_use": "forbidden",
        "account_endpoints": "forbidden",
        "signed_endpoints": "forbidden",
    }
    assert snapshot["observations"] == [
        {
            "symbol": "BTCUSDT",
            "source_symbol": "BTC-USDT",
            "mid_price": 100.01,
            "bid_price": 100.0,
            "ask_price": 100.02,
            "last_price": 100.01,
            "volume": 12.5,
            "observed_at": "2026-05-19T02:44:59Z",
        },
        {
            "symbol": "ETHUSDT",
            "source_symbol": "ETH-USDT",
            "mid_price": 200.0,
            "bid_price": 199.95,
            "ask_price": 200.05,
            "last_price": 200.0,
            "volume": 20.0,
            "observed_at": "2026-05-19T02:44:58.123456Z",
        },
    ]


def test_build_coinbase_independent_source_snapshot_fails_closed_for_bad_price() -> None:
    payloads = {"BTC-USDT": {"price": "NaN", "bid": "100", "ask": "101", "time": "2026-05-19T02:44:59Z"}}

    try:
        build_independent_source_snapshot(payloads, symbol_map={"BTCUSDT": "BTC-USDT"}, generated_at=GENERATED_AT)
    except ValueError as exc:
        assert "BTC-USDT.price" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected fail-closed bad price")
