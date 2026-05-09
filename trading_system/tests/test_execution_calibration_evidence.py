from __future__ import annotations

import json
from pathlib import Path

from trading_system.app.execution.calibration import (
    load_calibration_records,
    summarize_calibration_records,
    write_calibration_summary,
)


def test_writes_passive_and_taker_calibration_summary_from_jsonl(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "symbol": "BTCUSDT",
                        "side": "buy",
                        "intended_limit_price": 100.0,
                        "submitted_at": "2026-01-01T00:00:00+00:00",
                        "first_fill_at": "2026-01-01T00:00:02+00:00",
                        "requested_qty": 1.0,
                        "filled_qty": 1.0,
                        "filled_notional": 100.0,
                        "status": "filled",
                        "maker_taker": "maker",
                        "fees": 0.01,
                        "ref_price": 100.2,
                        "setup_type": "RS_PULLBACK",
                    }
                ),
                json.dumps(
                    {
                        "symbol": "ETHUSDT",
                        "side": "sell",
                        "intended_limit_price": 50.0,
                        "submitted_at": "2026-01-01T00:00:00+00:00",
                        "first_fill_at": "2026-01-01T00:00:01+00:00",
                        "requested_qty": 2.0,
                        "filled_qty": 2.0,
                        "filled_notional": 99.8,
                        "status": "filled",
                        "maker_taker": "taker",
                        "fees": 0.02,
                        "slippage_bps": 4.0,
                    }
                ),
                json.dumps(
                    {
                        "symbol": "BTCUSDT",
                        "side": "buy",
                        "intended_limit_price": 99.0,
                        "submitted_at": "2026-01-01T00:00:00+00:00",
                        "requested_qty": 1.0,
                        "filled_qty": 0.0,
                        "status": "expired",
                        "maker_taker": "maker",
                    }
                ),
            ]
        )
        + "\n"
    )

    records = load_calibration_records(source)
    summary = summarize_calibration_records(records, evidence_source={"type": "synthetic_fixture"})

    assert summary["schema_version"] == "passive_order_calibration_summary.v1"
    assert summary["evidence_source"] == {"type": "synthetic_fixture"}
    assert summary["overall"]["attempt_count"] == 3
    assert summary["overall"]["fill_rate"] == 2 / 3
    assert summary["by_maker_taker"]["maker"]["attempt_count"] == 2
    assert summary["by_maker_taker"]["maker"]["fill_rate"] == 0.5
    assert summary["by_maker_taker"]["taker"]["attempt_count"] == 1
    assert summary["taker_slippage"]["sample_count"] == 1
    assert summary["taker_slippage"]["median_slippage_bps"] == 4.0

    output = write_calibration_summary(source, tmp_path / "out", evidence_source={"type": "synthetic_fixture"})
    assert output == tmp_path / "out" / "passive_order_calibration_summary.json"
    assert json.loads(output.read_text()) == summary


def test_rejects_boolean_intended_limit_price(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "side": "buy",
                "intended_limit_price": True,
                "submitted_at": "2026-01-01T00:00:00+00:00",
                "status": "filled",
            }
        )
        + "\n"
    )

    import pytest

    with pytest.raises(ValueError, match="intended_limit_price must be numeric"):
        load_calibration_records(source)
