from __future__ import annotations

import json

import pytest

from trading_system import paper_snapshots


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
