from __future__ import annotations

import json

import pytest

from trading_system import paper_snapshots


def test_to_float_rejects_bool_and_non_finite_values():
    with pytest.raises(RuntimeError, match="expected numeric test_field"):
        paper_snapshots._to_float(True, field="test_field")

    with pytest.raises(RuntimeError, match="expected finite numeric test_field"):
        paper_snapshots._to_float("nan", field="test_field")

    with pytest.raises(RuntimeError, match="expected finite numeric test_field"):
        paper_snapshots._to_float("inf", field="test_field")

    assert paper_snapshots._to_float("123.45", field="test_field") == pytest.approx(123.45)


def test_paper_symbols_rejects_blank_and_noncanonical_entries(monkeypatch):
    monkeypatch.setenv(paper_snapshots.PAPER_SYMBOLS_ENV, "BTCUSDT,   ")
    with pytest.raises(RuntimeError, match=paper_snapshots.PAPER_SYMBOLS_ENV):
        paper_snapshots._paper_symbols()

    monkeypatch.setenv(paper_snapshots.PAPER_SYMBOLS_ENV, "btcusdt")
    with pytest.raises(RuntimeError, match=paper_snapshots.PAPER_SYMBOLS_ENV):
        paper_snapshots._paper_symbols()


@pytest.mark.parametrize("raw_value", ["nan", "inf", "-inf", "", "   ", "0", "-1", "false"])
def test_paper_account_equity_rejects_invalid_values(monkeypatch, raw_value):
    monkeypatch.setenv(paper_snapshots.PAPER_ACCOUNT_EQUITY_ENV, raw_value)

    with pytest.raises(RuntimeError, match=paper_snapshots.PAPER_ACCOUNT_EQUITY_ENV):
        paper_snapshots._paper_account_equity()


def test_paper_account_equity_accepts_positive_numeric_string(monkeypatch):
    monkeypatch.setenv(paper_snapshots.PAPER_ACCOUNT_EQUITY_ENV, "12345.67")

    assert paper_snapshots._paper_account_equity() == pytest.approx(12345.67)


def _kline_rows(*, close: object = "100", high: object = "110", low: object = "90") -> list[list[object]]:
    return [[0, "95", high, low, close, "1"] for _ in range(15)]


@pytest.mark.parametrize("last_close", ["0", "-1"])
def test_atr_pct_rejects_non_positive_last_close(last_close):
    rows = _kline_rows()
    rows[-1][4] = last_close

    with pytest.raises(RuntimeError, match="close must be greater than zero"):
        paper_snapshots._atr_pct(rows)


@pytest.mark.parametrize(
    ("high", "low", "close"),
    [
        ("89", "90", "95"),
        ("110", "90", "111"),
        ("110", "90", "89"),
    ],
)
def test_atr_pct_rejects_impossible_ohlc_rows(high, low, close):
    rows = _kline_rows(high=high, low=low, close=close)

    with pytest.raises(RuntimeError, match="invalid OHLC row"):
        paper_snapshots._atr_pct(rows)


@pytest.mark.parametrize("first_value", ["0", "-1"])
def test_open_interest_change_24h_pct_rejects_non_positive_first_value(monkeypatch, first_value):
    def fake_public_get(base, path, params=None):
        assert path == "/futures/data/openInterestHist"
        return [
            {"sumOpenInterestValue": first_value},
            {"sumOpenInterestValue": "120"},
        ]

    monkeypatch.setattr(paper_snapshots, "public_get", fake_public_get)

    with pytest.raises(RuntimeError, match="sumOpenInterestValue must be greater than zero"):
        paper_snapshots._open_interest_change_24h_pct("BTCUSDT")


def test_open_interest_change_24h_pct_accepts_binance_numeric_strings(monkeypatch):
    def fake_public_get(base, path, params=None):
        assert path == "/futures/data/openInterestHist"
        return [
            {"sumOpenInterestValue": "100.0"},
            {"sumOpenInterestValue": "125.0"},
        ]

    monkeypatch.setattr(paper_snapshots, "public_get", fake_public_get)

    assert paper_snapshots._open_interest_change_24h_pct("BTCUSDT") == pytest.approx(0.25)


def test_derivatives_snapshot_rejects_non_positive_index_price_for_basis(monkeypatch):
    monkeypatch.setattr(
        paper_snapshots,
        "_futures_premium_index",
        lambda symbol: {"markPrice": "100", "indexPrice": "0", "lastFundingRate": "0.0001"},
    )
    monkeypatch.setattr(paper_snapshots, "_futures_ticker", lambda symbol: {"priceChangePercent": "1.5"})
    monkeypatch.setattr(paper_snapshots, "_open_interest_payload", lambda symbol: {"openInterest": "10"})
    monkeypatch.setattr(paper_snapshots, "_open_interest_change_24h_pct", lambda symbol: 0.1)
    monkeypatch.setattr(paper_snapshots, "_taker_buy_sell_ratio", lambda symbol: 1.2)

    with pytest.raises(RuntimeError, match="indexPrice must be greater than zero"):
        paper_snapshots._derivatives_snapshot_payload(["BTCUSDT"])


def test_testnet_account_snapshot_payload_rejects_invalid_symbol_and_position_side(monkeypatch):
    def fake_signed_get(base, path, params=None):
        if path == "/fapi/v2/account":
            return {
                "totalWalletBalance": "1000.5",
                "availableBalance": "900.25",
                "totalMarginBalance": "1012.84",
            }
        if path == "/fapi/v2/positionRisk":
            return [
                {
                    "symbol": " btcusdt ",
                    "positionSide": "HEDGE",
                    "positionAmt": "0.0256",
                    "entryPrice": "77971.95639649",
                    "markPrice": "78065.28472332",
                    "unRealizedProfit": "2.3892",
                    "notional": "1998.471",
                    "leverage": "20",
                }
            ]
        raise AssertionError(path)

    monkeypatch.setattr(paper_snapshots, "FUTURES_BASE", "https://testnet.binancefuture.com")
    monkeypatch.setattr(paper_snapshots, "signed_get", fake_signed_get)
    monkeypatch.setattr(paper_snapshots, "_futures_testnet_signed_params", lambda: {"timestamp": 123, "recvWindow": 5000})

    with pytest.raises(RuntimeError, match="symbol"):
        paper_snapshots._testnet_account_snapshot_payload()


def test_testnet_account_snapshot_payload_rejects_invalid_position_side(monkeypatch):
    def fake_signed_get(base, path, params=None):
        if path == "/fapi/v2/account":
            return {
                "totalWalletBalance": "1000.5",
                "availableBalance": "900.25",
                "totalMarginBalance": "1012.84",
            }
        if path == "/fapi/v2/positionRisk":
            return [
                {
                    "symbol": "BTCUSDT",
                    "positionSide": "HEDGE",
                    "positionAmt": "0.0256",
                    "entryPrice": "77971.95639649",
                    "markPrice": "78065.28472332",
                    "unRealizedProfit": "2.3892",
                    "notional": "1998.471",
                    "leverage": "20",
                }
            ]
        raise AssertionError(path)

    monkeypatch.setattr(paper_snapshots, "FUTURES_BASE", "https://testnet.binancefuture.com")
    monkeypatch.setattr(paper_snapshots, "signed_get", fake_signed_get)
    monkeypatch.setattr(paper_snapshots, "_futures_testnet_signed_params", lambda: {"timestamp": 123, "recvWindow": 5000})

    with pytest.raises(RuntimeError, match="positionSide"):
        paper_snapshots._testnet_account_snapshot_payload()


def test_testnet_account_snapshot_payload_rejects_nonzero_row_with_bad_numeric_value(monkeypatch):
    def fake_signed_get(base, path, params=None):
        if path == "/fapi/v2/account":
            return {
                "totalWalletBalance": "1000.5",
                "availableBalance": "900.25",
                "totalMarginBalance": "1012.84",
            }
        if path == "/fapi/v2/positionRisk":
            return [
                {
                    "symbol": "BTCUSDT",
                    "positionSide": "BOTH",
                    "positionAmt": "0.0256",
                    "entryPrice": "nan",
                    "markPrice": "78065.28472332",
                    "unRealizedProfit": "2.3892",
                    "notional": "1998.471",
                    "leverage": "20",
                }
            ]
        raise AssertionError(path)

    monkeypatch.setattr(paper_snapshots, "FUTURES_BASE", "https://testnet.binancefuture.com")
    monkeypatch.setattr(paper_snapshots, "signed_get", fake_signed_get)
    monkeypatch.setattr(paper_snapshots, "_futures_testnet_signed_params", lambda: {"timestamp": 123, "recvWindow": 5000})

    with pytest.raises(RuntimeError, match="expected finite numeric entryPrice"):
        paper_snapshots._testnet_account_snapshot_payload()


def test_testnet_account_snapshot_payload_skips_zero_qty_rows_but_not_invalid_nonzero_rows(monkeypatch):
    def fake_signed_get(base, path, params=None):
        if path == "/fapi/v2/account":
            return {
                "totalWalletBalance": "1000.5",
                "availableBalance": "900.25",
                "totalMarginBalance": "1012.84",
            }
        if path == "/fapi/v2/positionRisk":
            return [
                {
                    "symbol": "BTCUSDT",
                    "positionSide": "BOTH",
                    "positionAmt": "0",
                    "entryPrice": True,
                    "markPrice": "78065.28472332",
                    "unRealizedProfit": "2.3892",
                    "notional": "1998.471",
                    "leverage": "20",
                },
                {
                    "symbol": "ETHUSDT",
                    "positionSide": "BOTH",
                    "positionAmt": "1",
                    "entryPrice": "2300.0",
                    "markPrice": "2310.0",
                    "unRealizedProfit": "1.5",
                    "notional": True,
                    "leverage": "20",
                },
            ]
        raise AssertionError(path)

    monkeypatch.setattr(paper_snapshots, "FUTURES_BASE", "https://testnet.binancefuture.com")
    monkeypatch.setattr(paper_snapshots, "signed_get", fake_signed_get)
    monkeypatch.setattr(paper_snapshots, "_futures_testnet_signed_params", lambda: {"timestamp": 123, "recvWindow": 5000})

    with pytest.raises(RuntimeError, match="expected numeric notional"):
        paper_snapshots._testnet_account_snapshot_payload()


def test_testnet_account_snapshot_payload_reads_real_futures_positions(monkeypatch):
    calls = []

    def fake_signed_get(base, path, params=None):
        calls.append((base, path, params))
        if path == "/fapi/v2/account":
            return {
                "totalWalletBalance": "1000.5",
                "availableBalance": "900.25",
                "totalUnrealizedProfit": "12.34",
                "totalMarginBalance": "1012.84",
            }
        if path == "/fapi/v2/positionRisk":
            return [
                {
                    "symbol": "BTCUSDT",
                    "positionSide": "BOTH",
                    "positionAmt": "0.0256",
                    "entryPrice": "77971.95639649",
                    "markPrice": "78065.28472332",
                    "unRealizedProfit": "2.3892",
                    "notional": "1998.471",
                    "leverage": "20",
                },
                {
                    "symbol": "ETHUSDT",
                    "positionSide": "BOTH",
                    "positionAmt": "0",
                    "entryPrice": "0",
                    "markPrice": "2330",
                    "unRealizedProfit": "0",
                    "notional": "0",
                    "leverage": "20",
                },
            ]
        raise AssertionError(path)

    monkeypatch.setattr(paper_snapshots, "FUTURES_BASE", "https://testnet.binancefuture.com")
    monkeypatch.setattr(paper_snapshots, "signed_get", fake_signed_get)
    monkeypatch.setattr(paper_snapshots, "_futures_testnet_signed_params", lambda: {"timestamp": 123, "recvWindow": 5000})
    monkeypatch.setattr(paper_snapshots, "_timestamp", lambda: "2026-04-26T15:00:00Z")

    payload = paper_snapshots._testnet_account_snapshot_payload()

    assert payload["equity"] == pytest.approx(1012.84)
    assert payload["available_balance"] == pytest.approx(900.25)
    assert payload["futures_wallet_balance"] == pytest.approx(1000.5)
    assert payload["meta"]["account_type"] == "testnet"
    assert payload["meta"]["snapshot_source"] == "binance_futures_testnet"
    assert payload["open_positions"] == [
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "qty": 0.0256,
            "entry_price": 77971.95639649,
            "mark_price": 78065.28472332,
            "unrealized_pnl": 2.3892,
            "notional": 1998.471,
            "leverage": 20.0,
        }
    ]
    assert calls[0][1] == "/fapi/v2/account"
    assert calls[1][1] == "/fapi/v2/positionRisk"


def test_prepare_paper_runtime_inputs_uses_real_testnet_account_snapshot(monkeypatch, tmp_path):
    class Paths:
        bucket_dir = tmp_path
        mode = "testnet"

    monkeypatch.setattr(paper_snapshots, "_paper_symbols", lambda: ["BTCUSDT"])
    monkeypatch.setattr(paper_snapshots, "_market_context_payload", lambda symbols: {"symbols": symbols})
    monkeypatch.setattr(paper_snapshots, "_derivatives_snapshot_payload", lambda symbols: {"symbols": symbols})
    monkeypatch.setattr(
        paper_snapshots,
        "_testnet_account_snapshot_payload",
        lambda: {
            "schema_version": "v2",
            "equity": 1000.0,
            "available_balance": 900.0,
            "futures_wallet_balance": 1000.0,
            "open_positions": [{"symbol": "BTCUSDT", "qty": 0.0256}],
            "open_orders": [],
            "meta": {"account_type": "testnet", "snapshot_source": "binance_futures_testnet"},
        },
    )
    monkeypatch.setattr(paper_snapshots, "_paper_account_snapshot_payload", lambda: pytest.fail("paper snapshot must not be used for testnet mode"))

    paper_snapshots.prepare_paper_runtime_inputs(Paths())

    payload = json.loads((tmp_path / paper_snapshots.PAPER_ACCOUNT_SNAPSHOT_NAME).read_text())
    assert payload["meta"]["account_type"] == "testnet"
    assert payload["open_positions"] == [{"symbol": "BTCUSDT", "qty": 0.0256}]
