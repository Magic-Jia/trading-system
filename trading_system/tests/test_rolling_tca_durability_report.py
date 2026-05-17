from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from trading_system.app.reporting.rolling_tca_durability_report import (
    build_rolling_tca_durability_report,
    write_rolling_tca_durability_report,
)


def _record(day: str, index: int, **overrides: object) -> dict[str, object]:
    hour = 0
    payload: dict[str, object] = {
        "symbol": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
        "side": "buy",
        "intended_limit_price": 100.0,
        "signal_at": f"{day}T{hour:02d}:00:00Z",
        "decision_at": f"{day}T{hour:02d}:00:01Z",
        "submitted_at": f"{day}T{hour:02d}:00:02Z",
        "exchange_ack_at": f"{day}T{hour:02d}:00:03Z",
        "first_fill_at": f"{day}T{hour:02d}:00:04Z",
        "last_fill_at": f"{day}T{hour:02d}:00:05Z",
        "requested_qty": 1.0,
        "filled_qty": 1.0,
        "filled_notional": 100.0,
        "status": "filled",
        "maker_taker": "maker" if index % 2 == 0 else "taker",
        "fees": 0.01,
        "funding": 0.0,
        "slippage_bps": 1.0 + (index % 5),
        "latency_ms": 100.0 + index,
        "setup_type": "RS_PULLBACK" if index % 2 == 0 else "MOMENTUM",
    }
    payload.update(overrides)
    return payload


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_rolling_tca_report_summarizes_multi_day_bucketed_windows_from_strict_files(tmp_path: Path) -> None:
    for offset, day in enumerate(("2026-05-14", "2026-05-15", "2026-05-16")):
        _write_jsonl(tmp_path / day / "passive_order_calibration_records.jsonl", [_record(day, offset * 4 + index) for index in range(4)])

    report = build_rolling_tca_durability_report(
        input_paths=[tmp_path],
        start_date="2026-05-14",
        end_date="2026-05-16",
        generated_at="2026-05-16T23:40:00Z",
        windows=("1d", "3d"),
        min_samples_per_bucket=2,
        bucket_dimensions=("global", "symbol", "setup_type", "maker_taker", "session_utc_hour"),
        thresholds={"max_p95_slippage_bps": 10.0, "max_p95_latency_ms": 500.0},
    )

    assert report["schema_version"] == "rolling_tca_durability_report.v1"
    assert report["mode"] == "simulated_live"
    assert report["decision"] == "durable"
    assert report["reasons"] == []
    assert report["canonical_dates"] == ["2026-05-14", "2026-05-15", "2026-05-16"]
    assert report["checks"]["all_expected_dates_present"] is True
    assert report["checks"]["all_bucket_windows_sufficiently_sampled"] is True
    assert report["windows"][0]["window"] == "1d"
    assert report["windows"][0]["start_date"] == "2026-05-16"
    assert report["windows"][0]["buckets"][0]["bucket"] == {"dimension": "global", "value": "all"}
    assert report["windows"][0]["buckets"][0]["metrics"]["sample_count"] == 4
    assert report["windows"][1]["window"] == "3d"
    assert report["windows"][1]["metrics"]["sample_count"] == 12
    assert {bucket["bucket"]["dimension"] for bucket in report["windows"][1]["buckets"]} == {
        "global",
        "maker_taker",
        "session_utc_hour",
        "setup_type",
        "symbol",
    }


def test_rolling_tca_report_holds_for_insufficient_bucket_samples_and_missing_dates(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "2026-05-14.jsonl", [_record("2026-05-14", 0), _record("2026-05-14", 1)])
    _write_jsonl(tmp_path / "2026-05-16.jsonl", [_record("2026-05-16", 2)])

    report = build_rolling_tca_durability_report(
        input_paths=[tmp_path],
        start_date="2026-05-14",
        end_date="2026-05-16",
        generated_at="2026-05-16T23:40:00Z",
        windows=("1d", "3d"),
        min_samples_per_bucket=2,
        bucket_dimensions=("global", "symbol"),
        thresholds={"max_p95_slippage_bps": 10.0, "max_p95_latency_ms": 500.0},
    )

    assert report["decision"] == "insufficient"
    assert report["reasons"] == ["missing_dates", "insufficient_bucket_sample_size"]
    assert report["missing_dates"] == ["2026-05-15"]
    assert report["checks"]["all_expected_dates_present"] is False
    assert report["windows"][0]["buckets"][0]["decision"] == "insufficient"


def test_rolling_tca_report_rejects_threshold_breach_and_latency_regression(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "records.jsonl",
        [_record("2026-05-16", index, slippage_bps=30.0, latency_ms=900.0) for index in range(4)],
    )

    report = build_rolling_tca_durability_report(
        input_paths=[tmp_path / "records.jsonl"],
        start_date="2026-05-16",
        end_date="2026-05-16",
        generated_at="2026-05-16T23:40:00Z",
        min_samples_per_bucket=2,
        thresholds={"max_p95_slippage_bps": 5.0, "max_p95_latency_ms": 250.0},
    )

    assert report["decision"] == "rejected"
    assert report["reasons"] == ["rolling_slippage_exceeds_threshold", "bucket_latency_regression"]
    assert report["windows"][0]["buckets"][0]["decision"] == "rejected"
    assert "rolling_slippage_exceeds_threshold" in report["windows"][0]["buckets"][0]["reasons"]


def test_rolling_tca_report_fails_closed_for_malformed_timestamp(tmp_path: Path) -> None:
    malformed = _record("2026-05-16", 0)
    malformed["submitted_at"] = "2026-05-16 00:00:02"
    _write_jsonl(tmp_path / "bad.jsonl", [malformed])

    report = build_rolling_tca_durability_report(
        input_paths=[tmp_path / "bad.jsonl"],
        start_date="2026-05-16",
        end_date="2026-05-16",
        generated_at="2026-05-16T23:40:00Z",
    )

    assert report["decision"] == "rejected"
    assert report["reasons"] == ["malformed_records"]
    assert report["malformed_inputs"][0]["reason"] == "malformed_records"


def test_rolling_tca_report_rejects_unknown_bucket_dimensions_and_preserves_reason_order(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "records.jsonl", [_record("2026-05-16", index, slippage_bps=30.0) for index in range(1)])

    report = build_rolling_tca_durability_report(
        input_paths=[tmp_path / "records.jsonl"],
        start_date="2026-05-14",
        end_date="2026-05-16",
        generated_at="2026-05-16T23:40:00Z",
        min_samples_per_bucket=2,
        bucket_dimensions=("unknown_bucket", "global"),
        thresholds={"max_p95_slippage_bps": 5.0},
    )

    assert report["decision"] == "rejected"
    assert report["reasons"] == [
        "missing_dates",
        "unknown_bucket_fields",
        "insufficient_bucket_sample_size",
        "rolling_slippage_exceeds_threshold",
    ]
    assert report["malformed_inputs"] == [{"field": "bucket_dimensions[0]", "reason": "unknown_bucket_fields", "value": "unknown_bucket"}]


def test_write_rolling_tca_report_outputs_machine_readable_json(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "records.jsonl", [_record("2026-05-16", index) for index in range(3)])
    output = tmp_path / "out" / "rolling_tca_durability_report.json"

    payload = write_rolling_tca_durability_report(
        output,
        input_paths=[tmp_path / "records.jsonl"],
        start_date="2026-05-16",
        end_date="2026-05-16",
        generated_at="2026-05-16T23:40:00Z",
        min_samples_per_bucket=2,
    )

    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8")) == payload


def test_generate_rolling_tca_report_cli_writes_report(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "records.jsonl", [_record("2026-05-16", index) for index in range(3)])
    output = tmp_path / "rolling.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.generate_rolling_tca_durability_report",
            "--input",
            str(tmp_path / "records.jsonl"),
            "--output",
            str(output),
            "--start-date",
            "2026-05-16",
            "--end-date",
            "2026-05-16",
            "--generated-at",
            "2026-05-16T23:40:00Z",
            "--window",
            "1d",
            "--bucket",
            "global",
            "--min-samples-per-bucket",
            "2",
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"] == "durable"
    assert "ROLLING_TCA_DURABILITY_REPORT_JSON" in result.stdout
