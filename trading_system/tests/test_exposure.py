import pytest

from trading_system.app.portfolio.exposure import exposure_snapshot


@pytest.mark.parametrize(
    ("account", "message"),
    [
        ({"equity": True, "open_positions": []}, "account.equity must be numeric, not boolean"),
        ({"equity": float("nan"), "open_positions": []}, "account.equity must be finite"),
        ({"equity": float("inf"), "open_positions": []}, "account.equity must be finite"),
        (
            {"equity": 1000.0, "open_positions": [{"symbol": "BTCUSDT", "notional": False}]},
            "position.BTCUSDT.notional must be numeric, not boolean",
        ),
        (
            {"equity": 1000.0, "open_positions": [{"symbol": "BTCUSDT", "notional": float("nan")}]},
            "position.BTCUSDT.notional must be finite",
        ),
        (
            {"equity": 1000.0, "open_positions": [{"symbol": "BTCUSDT", "notional": float("inf")}]},
            "position.BTCUSDT.notional must be finite",
        ),
        (
            {"equity": 1000.0, "open_positions": [{"symbol": "BTCUSDT", "qty": True, "entry_price": 100.0}]},
            "position.BTCUSDT.qty must be numeric, not boolean",
        ),
        (
            {"equity": 1000.0, "open_positions": [{"symbol": "BTCUSDT", "qty": float("nan"), "entry_price": 100.0}]},
            "position.BTCUSDT.qty must be finite",
        ),
        (
            {"equity": 1000.0, "open_positions": [{"symbol": "BTCUSDT", "qty": float("inf"), "entry_price": 100.0}]},
            "position.BTCUSDT.qty must be finite",
        ),
        (
            {"equity": 1000.0, "open_positions": [{"symbol": "BTCUSDT", "qty": 1.0, "mark_price": True}]},
            "position.BTCUSDT.mark_price must be numeric, not boolean",
        ),
        (
            {"equity": 1000.0, "open_positions": [{"symbol": "BTCUSDT", "qty": 1.0, "mark_price": float("nan")}]},
            "position.BTCUSDT.mark_price must be finite",
        ),
        (
            {"equity": 1000.0, "open_positions": [{"symbol": "BTCUSDT", "qty": 1.0, "mark_price": float("inf")}]},
            "position.BTCUSDT.mark_price must be finite",
        ),
        (
            {"equity": 1000.0, "open_positions": [{"symbol": "BTCUSDT", "qty": 1.0, "entry_price": False}]},
            "position.BTCUSDT.entry_price must be numeric, not boolean",
        ),
        (
            {"equity": 1000.0, "open_positions": [{"symbol": "BTCUSDT", "qty": 1.0, "entry_price": float("nan")}]},
            "position.BTCUSDT.entry_price must be finite",
        ),
        (
            {"equity": 1000.0, "open_positions": [{"symbol": "BTCUSDT", "qty": 1.0, "entry_price": float("inf")}]},
            "position.BTCUSDT.entry_price must be finite",
        ),
    ],
)
def test_exposure_snapshot_rejects_present_invalid_numeric_boundaries(account, message):
    with pytest.raises(ValueError, match=message):
        exposure_snapshot(account)


def test_exposure_snapshot_preserves_missing_notional_and_mark_price_fallbacks():
    snapshot = exposure_snapshot(
        {
            "equity": 1000.0,
            "open_positions": [
                {"symbol": "BTCUSDT", "side": "LONG", "qty": 2.0, "entry_price": 100.0},
                {"symbol": "ETHUSDT", "side": "SHORT", "qty": 1.0, "entry_price": 50.0},
            ],
        }
    )

    assert snapshot["gross_notional"] == 250.0
    assert snapshot["net_long_notional"] == 200.0
    assert snapshot["net_short_notional"] == 50.0
    assert snapshot["active_risk_pct"] == 0.25
