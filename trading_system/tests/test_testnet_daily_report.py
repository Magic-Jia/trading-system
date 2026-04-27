from __future__ import annotations

import json
from pathlib import Path

from trading_system.reporting.testnet_daily_report import build_report_payload, render_markdown


def test_testnet_daily_report_marks_stale_pending_and_protective_orders(tmp_path):
    bucket = tmp_path / "data" / "runtime" / "testnet" / "prod"
    bucket.mkdir(parents=True)
    (bucket / "latest.json").write_text(json.dumps({"status": "ok", "error": None, "mode": "testnet"}))
    (bucket / "account_snapshot.json").write_text(
        json.dumps(
            {
                "schema_version": "v2",
                "account_type": "testnet",
                "futures_wallet_balance": 1000,
                "open_orders": 0,
                "positions": [
                    {"symbol": "BTCUSDT", "side": "LONG", "qty": 0.1, "entry_price": 78000, "mark_price": 78100},
                ],
            }
        )
    )
    (bucket / "runtime_state.json").write_text(
        json.dumps(
            {
                "positions": {
                    "BTCUSDT": {"symbol": "BTCUSDT", "status": "OPEN", "qty": 0.1},
                    "LINKUSDT": {"symbol": "LINKUSDT", "status": "PENDING", "qty": 10},
                },
                "active_orders": {
                    "intent-link-long": {"symbol": "LINKUSDT", "status": "PENDING", "qty": 10},
                },
                "optimization_summary": {"recommendation_count": 0, "promotion_decision": "observe"},
            }
        )
    )
    opt = bucket / "optimization"
    opt.mkdir()
    (opt / "daily_metrics.json").write_text(json.dumps({"scope": "current_runtime", "open_count": 1}))
    (opt / "health_report.json").write_text(json.dumps({"status": "ok", "warnings": []}))
    (opt / "recommendations.json").write_text(json.dumps({"recommendation_count": 0}))
    (opt / "promotion_decision.json").write_text(json.dumps({"decision": "observe"}))

    payload = build_report_payload(
        bucket=bucket,
        report_date="2026-04-26",
        protective_orders=[
            {"symbol": "BTCUSDT", "type": "STOP_MARKET", "triggerPrice": "76000", "closePosition": True},
            {"symbol": "BTCUSDT", "type": "TAKE_PROFIT_MARKET", "triggerPrice": "79000", "closePosition": True},
        ],
        protective_orders_source="binance_testnet_openAlgoOrders",
    )

    assert payload["runtime_account_mismatches"] == [
        {"symbol": "LINKUSDT", "runtime_status": "PENDING", "runtime_qty": 10.0, "account_qty": 0.0}
    ]
    assert payload["protective_order_summary"]["BTCUSDT"]["has_stop"] is True
    assert payload["protective_order_summary"]["BTCUSDT"]["has_take_profit"] is True
    text = render_markdown(payload)
    assert "LINKUSDT" in text
    assert "binance_testnet_openAlgoOrders" in text


def test_testnet_daily_report_protective_orders_unconfirmed_when_api_missing(tmp_path):
    bucket = tmp_path / "data" / "runtime" / "testnet" / "prod"
    bucket.mkdir(parents=True)
    (bucket / "latest.json").write_text(json.dumps({"status": "ok", "mode": "testnet"}))
    (bucket / "account_snapshot.json").write_text(json.dumps({"positions": []}))
    (bucket / "runtime_state.json").write_text(json.dumps({"positions": {}, "active_orders": {}}))
    payload = build_report_payload(bucket=bucket, report_date="2026-04-26")
    assert payload["protective_orders_source"] == "unconfirmed"
    assert "真实保护单未确认" in render_markdown(payload)
