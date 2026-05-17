from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_system.app.execution import calibration
from trading_system.app.execution.calibration import (
    build_tca_calibration_report,
    load_calibration_records,
    summarize_calibration_records,
    write_calibration_summary,
    write_tca_calibration_report,
)


def _strict_record_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "intended_limit_price": 100.0,
        "signal_at": "2026-01-01T00:00:00Z",
        "decision_at": "2026-01-01T00:00:01Z",
        "submitted_at": "2026-01-01T00:00:02Z",
        "exchange_ack_at": "2026-01-01T00:00:03Z",
        "first_fill_at": "2026-01-01T00:00:04Z",
        "status": "filled",
    }
    payload.update(overrides)
    return payload


def _tca_assumptions(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "expected_slippage_bps": 2.0,
        "expected_fill_probability": 0.75,
        "expected_maker_rate": 0.75,
        "expected_taker_rate": 0.25,
        "expected_ack_latency_ms": 1000.0,
        "expected_fill_latency_ms": 1000.0,
        "expected_cancel_latency_ms": 3000.0,
        "expected_partial_fill_rate": 0.25,
        "expected_adverse_selection_bps": 3.0,
        "expected_fee_funding_bps": 2.0,
        "expected_reject_reason_rates": {"post_only_reject": 0.25},
    }
    payload.update(overrides)
    return payload


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
                        "signal_at": "2026-01-01T00:00:00Z",
                        "decision_at": "2026-01-01T00:00:01Z",
                        "submitted_at": "2026-01-01T00:00:02Z",
                        "exchange_ack_at": "2026-01-01T00:00:03Z",
                        "first_fill_at": "2026-01-01T00:00:04Z",
                        "last_fill_at": "2026-01-01T00:00:05Z",
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
                        "signal_at": "2026-01-01T00:00:00Z",
                        "decision_at": "2026-01-01T00:00:01Z",
                        "submitted_at": "2026-01-01T00:00:02Z",
                        "exchange_ack_at": "2026-01-01T00:00:03Z",
                        "first_fill_at": "2026-01-01T00:00:04Z",
                        "last_fill_at": "2026-01-01T00:00:04Z",
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
                        "signal_at": "2026-01-01T00:00:00Z",
                        "decision_at": "2026-01-01T00:00:01Z",
                        "submitted_at": "2026-01-01T00:00:02Z",
                        "exchange_ack_at": "2026-01-01T00:00:03Z",
                        "requested_qty": 1.0,
                        "filled_qty": 0.0,
                        "status": "expired",
                        "maker_taker": "maker",
                        "cancel_ack_at": "2026-01-01T00:00:10Z",
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
    assert summary["records"][0]["lifecycle_timestamps"] == {
        "signal_at": "2026-01-01T00:00:00Z",
        "decision_at": "2026-01-01T00:00:01Z",
        "submitted_at": "2026-01-01T00:00:02Z",
        "exchange_ack_at": "2026-01-01T00:00:03Z",
        "cancel_requested_at": None,
        "replace_requested_at": None,
        "replace_ack_at": None,
        "first_fill_at": "2026-01-01T00:00:04Z",
        "last_fill_at": "2026-01-01T00:00:05Z",
        "cancel_ack_at": None,
    }

    output = write_calibration_summary(source, tmp_path / "out", evidence_source={"type": "synthetic_fixture"})
    assert output == tmp_path / "out" / "passive_order_calibration_summary.json"
    assert json.loads(output.read_text()) == summary


def test_loads_and_summarizes_cancel_replace_lifecycle_fields(tmp_path: Path) -> None:
    source = tmp_path / "orders.jsonl"
    source.write_text(
        json.dumps(
            _strict_record_payload(
                status="cancelled",
                terminal_status="cancelled",
                requested_qty=1.0,
                filled_qty=0.4,
                filled_notional=40.0,
                last_fill_at="2026-01-01T00:00:04Z",
                cancel_requested_at="2026-01-01T00:00:05Z",
                cancel_ack_at="2026-01-01T00:00:07Z",
                cancel_latency_ms=2000.0,
                partial_fill_before_cancel=True,
            )
        )
        + "\n",
        encoding="utf-8",
    )

    records = load_calibration_records(source)
    summary = summarize_calibration_records(records)

    record = records[0]
    assert record.cancel_requested_at.isoformat().replace("+00:00", "Z") == "2026-01-01T00:00:05Z"
    assert record.cancel_latency_ms == pytest.approx(2000.0)
    assert record.terminal_status == "cancelled"
    assert record.partial_fill_before_cancel is True
    payload = summary["records"][0]
    assert payload["terminal_status"] == "cancelled"
    assert payload["cancel_latency_ms"] == pytest.approx(2000.0)
    assert payload["partial_fill_before_cancel"] is True
    assert payload["lifecycle_timestamps"]["cancel_requested_at"] == "2026-01-01T00:00:05Z"


def test_loads_replace_lifecycle_latency_fields(tmp_path: Path) -> None:
    source = tmp_path / "orders.jsonl"
    source.write_text(
        json.dumps(
            _strict_record_payload(
                replace_requested_at="2026-01-01T00:00:04Z",
                replace_ack_at="2026-01-01T00:00:06Z",
                first_fill_at="2026-01-01T00:00:07Z",
                replace_latency_ms=2000.0,
                terminal_status="filled",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    record = load_calibration_records(source)[0]

    assert record.replace_requested_at.isoformat().replace("+00:00", "Z") == "2026-01-01T00:00:04Z"
    assert record.replace_ack_at.isoformat().replace("+00:00", "Z") == "2026-01-01T00:00:06Z"
    assert record.replace_latency_ms == pytest.approx(2000.0)


def test_computes_latency_distribution_metrics_and_conservative_stress_summary() -> None:
    records = [
        _strict_record_payload(
            latency_ms=100.0,
            first_fill_at="2026-01-01T00:00:03.100000Z",
            cancel_requested_at="2026-01-01T00:00:04Z",
            cancel_ack_at="2026-01-01T00:00:04.250000Z",
            cancel_latency_ms=250.0,
            status="cancelled",
            terminal_status="cancelled",
            partial_fill_before_cancel=True,
        ),
        _strict_record_payload(
            latency_ms=150.0,
            first_fill_at="2026-01-01T00:00:03.300000Z",
            replace_requested_at="2026-01-01T00:00:04Z",
            replace_ack_at="2026-01-01T00:00:04.500000Z",
            replace_latency_ms=500.0,
        ),
        _strict_record_payload(
            latency_ms=250.0,
            first_fill_at=None,
            status="expired",
            terminal_status="expired",
        ),
        {
            "observed_at": "2026-01-01T00:00:05Z",
            "client_order_id": "client-4",
            "event_type": "fill",
            "status": "filled",
            "latency_ms": 900.0,
        },
    ]

    assert hasattr(calibration, "compute_latency_distribution_metrics")
    assert hasattr(calibration, "build_latency_stress_summary")

    metrics = calibration.compute_latency_distribution_metrics(
        records, evaluated_at="2026-01-01T00:00:10Z", stale_after_seconds=4
    )

    assert metrics["overall"]["count"] == 8
    assert metrics["overall"]["min"] == pytest.approx(100.0)
    assert metrics["overall"]["max"] == pytest.approx(900.0)
    assert metrics["overall"]["p50"] == pytest.approx(250.0)
    assert metrics["overall"]["p90"] == pytest.approx(620.0)
    assert metrics["overall"]["p99"] == pytest.approx(872.0)
    assert metrics["overall"]["mean"] == pytest.approx(318.75)
    assert metrics["overall"]["missing_rate"] == pytest.approx(0.0)
    assert metrics["overall"]["stale_rate"] == pytest.approx(1.0)
    assert metrics["by_event_type"]["ack"]["count"] == 3
    assert metrics["by_event_type"]["fill"]["count"] == 3
    assert metrics["by_event_type"]["cancel"]["max"] == pytest.approx(250.0)
    assert metrics["by_event_type"]["replace"]["max"] == pytest.approx(500.0)

    summary = calibration.build_latency_stress_summary(
        records, evaluated_at="2026-01-01T00:00:10Z", stale_after_seconds=4, min_samples=10
    )

    assert summary["schema_version"] == "latency_stress_calibration_summary.v1"
    assert summary["recommended_latency_buffer_ms"] == pytest.approx(900.0)
    assert summary["latency_quality"] == "stale"
    assert summary["sample_size_quality"] == "insufficient"
    assert summary["fail_closed_reason_codes"] == ["insufficient_latency_samples", "stale_latency_evidence"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda row: row.update({"latency_ms": True}), "latency record latency_ms must be numeric"),
        (lambda row: row.update({"latency_ms": float("nan")}), "latency record latency_ms must be finite"),
        (lambda row: row.update({"latency_ms": -1.0}), "latency record latency_ms must be non-negative"),
        (lambda row: row.update({"observed_at": "2026-01-01T00:00:00+00:00"}), "latency record observed_at must be a canonical UTC timestamp"),
        (lambda row: row.update({"event_type": "amend"}), "latency record event_type must be one of"),
        (lambda row: row.update({"status": "FILLED"}), "latency record status must be one of"),
    ],
)
def test_latency_distribution_metrics_fail_closed_for_malformed_event_records(mutate, message: str) -> None:
    record = {
        "observed_at": "2026-01-01T00:00:00Z",
        "client_order_id": "client-1",
        "event_type": "ack",
        "status": "acknowledged",
        "latency_ms": 100.0,
    }
    mutate(record)

    with pytest.raises(ValueError, match=message):
        calibration.compute_latency_distribution_metrics([record])


def test_latency_distribution_metrics_reject_duplicate_event_identity() -> None:
    record = {
        "observed_at": "2026-01-01T00:00:00Z",
        "client_order_id": "client-1",
        "event_type": "ack",
        "status": "acknowledged",
        "latency_ms": 100.0,
    }

    with pytest.raises(ValueError, match="duplicate latency event identity"):
        calibration.compute_latency_distribution_metrics([record, dict(record)])


def test_builds_tca_calibration_report_expected_vs_observed_and_checks() -> None:
    records = (
        _strict_record_payload(
            maker_taker="maker",
            requested_qty=1.0,
            filled_qty=1.0,
            filled_notional=100.02,
            slippage_bps=1.0,
            fees=0.02,
            funding=0.01,
            adverse_selection_bps=2.0,
        ),
        _strict_record_payload(
            maker_taker="taker",
            requested_qty=1.0,
            filled_qty=0.5,
            filled_notional=50.01,
            status="partially_filled",
            slippage_bps=3.0,
            fees=0.01,
            funding=0.0,
            adverse_selection_bps=4.0,
        ),
        _strict_record_payload(
            first_fill_at=None,
            maker_taker="maker",
            requested_qty=1.0,
            filled_qty=0.0,
            status="rejected",
            cancel_ack_at="2026-01-01T00:00:05Z",
            cancel_reason="post_only_reject",
        ),
        _strict_record_payload(
            maker_taker="maker",
            requested_qty=1.0,
            filled_qty=1.0,
            filled_notional=100.02,
            slippage_bps=2.0,
            fees=0.02,
            funding=-0.01,
            adverse_selection_bps=1.0,
        ),
    )

    report = build_tca_calibration_report(
        records,
        assumptions=_tca_assumptions(),
        evidence_source={"type": "testnet_exchange", "run_id": "paper-shadow-1", "exported_at": "2026-01-01T00:10:00Z"},
        evaluated_at="2026-01-01T00:11:00Z",
        min_samples=4,
        max_evidence_age_seconds=3600,
    )

    assert report["schema_version"] == "tca_calibration_report.v1"
    assert report["decision"] == "pass"
    assert report["checks"]["sample_count_met"] is True
    assert report["checks"]["evidence_fresh"] is True
    assert report["checks"]["all_metrics_within_tolerance"] is True
    assert report["sample_count"] == 4
    assert report["observed"]["fill_probability"] == 0.75
    assert report["observed"]["maker_rate"] == 0.75
    assert report["observed"]["taker_rate"] == 0.25
    assert report["observed"]["partial_fill_rate"] == 0.25
    assert report["observed"]["ack_latency_ms"]["median"] == 1000.0
    assert report["observed"]["fill_latency_ms"]["median"] == 1000.0
    assert report["observed"]["cancel_latency_ms"]["median"] == 3000.0
    assert report["observed"]["terminal_status"] == {
        "filled": {"count": 2, "rate": 0.5},
        "partially_filled": {"count": 1, "rate": 0.25},
        "rejected": {"count": 1, "rate": 0.25},
    }
    assert report["observed"]["slippage_bps"]["median"] == 2.0
    assert report["observed"]["adverse_selection_bps"]["median"] == 2.0
    assert report["observed"]["fees_funding_bps"]["median"] == pytest.approx(2.0, abs=0.001)
    assert report["observed"]["reject_reasons"] == {"post_only_reject": {"count": 1, "rate": 0.25}}
    assert report["comparisons"]["slippage_bps"]["delta"] == 0.0
    assert report["comparisons"]["fill_probability"]["delta"] == 0.0


def test_tca_calibration_report_fails_closed_for_insufficient_and_stale_evidence() -> None:
    report = build_tca_calibration_report(
        [load_calibration_records_from_payload(_strict_record_payload())[0]],
        assumptions=_tca_assumptions(),
        evidence_source={"type": "testnet_exchange", "exported_at": "2026-01-01T00:00:00Z"},
        evaluated_at="2026-01-01T02:00:01Z",
        min_samples=2,
        max_evidence_age_seconds=3600,
    )

    assert report["decision"] == "fail_closed"
    assert report["checks"]["sample_count_met"] is False
    assert report["checks"]["evidence_fresh"] is False
    assert "insufficient_sample_count" in report["reasons"]
    assert "stale_evidence" in report["reasons"]


def test_tca_calibration_report_fails_closed_for_missing_required_observed_metric() -> None:
    rows = [
        load_calibration_records_from_payload(
            _strict_record_payload(maker_taker="maker", first_fill_at=None, status="expired", cancel_ack_at="2026-01-01T00:00:05Z")
        )[0],
        load_calibration_records_from_payload(
            _strict_record_payload(maker_taker="maker", first_fill_at=None, status="expired", cancel_ack_at="2026-01-01T00:00:05Z")
        )[0],
    ]

    report = build_tca_calibration_report(
        rows,
        assumptions=_tca_assumptions(),
        evidence_source={"type": "testnet_exchange", "exported_at": "2026-01-01T00:00:00Z"},
        evaluated_at="2026-01-01T00:00:01Z",
        min_samples=2,
    )

    assert report["decision"] == "fail_closed"
    assert report["checks"]["required_metrics_present"] is False
    assert "missing_required_metric: slippage_bps" in report["reasons"]
    assert "missing_required_metric: fill_latency_ms" in report["reasons"]


def test_write_tca_calibration_report_from_jsonl(tmp_path: Path) -> None:
    source = tmp_path / "orders.jsonl"
    source.write_text(
        "\n".join(
            json.dumps(_strict_record_payload(maker_taker="maker", requested_qty=1.0, filled_qty=1.0, slippage_bps=2.0))
            for _ in range(2)
        )
        + "\n",
        encoding="utf-8",
    )

    output = write_tca_calibration_report(
        source,
        tmp_path / "out",
        assumptions=_tca_assumptions(expected_partial_fill_rate=0.0, expected_reject_reason_rates={}),
        evidence_source={"type": "testnet_exchange", "exported_at": "2026-01-01T00:10:00Z"},
        evaluated_at="2026-01-01T00:11:00Z",
        min_samples=2,
    )

    assert output == tmp_path / "out" / "tca_calibration_report.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "tca_calibration_report.v1"
    assert payload["checks"]["sample_count_met"] is True


def load_calibration_records_from_payload(payload: dict[str, object]) -> tuple[object, ...]:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "records.jsonl"
        source.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return load_calibration_records(source)


def test_rejects_non_object_calibration_rows(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(json.dumps(["not-an-object"]) + "\n")

    import pytest

    with pytest.raises(ValueError, match="calibration records must be objects"):
        load_calibration_records(source)


def test_rejects_boolean_intended_limit_price(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "side": "buy",
                "intended_limit_price": True,
                "signal_at": "2026-01-01T00:00:00Z",
                "decision_at": "2026-01-01T00:00:01Z",
                "submitted_at": "2026-01-01T00:00:02Z",
                "exchange_ack_at": "2026-01-01T00:00:03Z",
                "first_fill_at": "2026-01-01T00:00:04Z",
                "status": "filled",
            }
        )
        + "\n"
    )

    import pytest

    with pytest.raises(ValueError, match="intended_limit_price must be numeric"):
        load_calibration_records(source)


def test_rejects_string_intended_limit_price(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "side": "buy",
                "intended_limit_price": "100.0",
                "signal_at": "2026-01-01T00:00:00Z",
                "decision_at": "2026-01-01T00:00:01Z",
                "submitted_at": "2026-01-01T00:00:02Z",
                "exchange_ack_at": "2026-01-01T00:00:03Z",
                "first_fill_at": "2026-01-01T00:00:04Z",
                "status": "filled",
            }
        )
        + "\n"
    )

    import pytest

    with pytest.raises(ValueError, match="intended_limit_price must be numeric"):
        load_calibration_records(source)


def test_rejects_boolean_optional_numeric_fields(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "side": "buy",
                "intended_limit_price": 100.0,
                "signal_at": "2026-01-01T00:00:00Z",
                "decision_at": "2026-01-01T00:00:01Z",
                "submitted_at": "2026-01-01T00:00:02Z",
                "exchange_ack_at": "2026-01-01T00:00:03Z",
                "first_fill_at": "2026-01-01T00:00:04Z",
                "fees": False,
                "status": "filled",
            }
        )
        + "\n"
    )

    import pytest

    with pytest.raises(ValueError, match="calibration record fees must be numeric"):
        load_calibration_records(source)


def test_rejects_string_fees_before_calibration_load(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "side": "buy",
                "intended_limit_price": 100.0,
                "signal_at": "2026-01-01T00:00:00Z",
                "decision_at": "2026-01-01T00:00:01Z",
                "submitted_at": "2026-01-01T00:00:02Z",
                "exchange_ack_at": "2026-01-01T00:00:03Z",
                "first_fill_at": "2026-01-01T00:00:04Z",
                "fees": "0.01",
                "status": "filled",
            }
        )
        + "\n"
    )

    import pytest

    with pytest.raises(ValueError, match="calibration record fees must be numeric"):
        load_calibration_records(source)


@pytest.mark.parametrize(
    "field",
    [
        "requested_qty",
        "requested_notional",
        "filled_qty",
        "filled_notional",
        "cancel_latency_ms",
        "replace_latency_ms",
        "slippage_bps",
        "ref_price",
        "latency_ms",
        "funding",
        "adverse_selection_bps",
    ],
)
def test_rejects_string_optional_numeric_fields_before_calibration_load(tmp_path: Path, field: str) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(json.dumps(_strict_record_payload(**{field: "1.0"})) + "\n")

    with pytest.raises(ValueError, match=f"calibration record {field} must be numeric"):
        load_calibration_records(source)


@pytest.mark.parametrize("field", ["funding", "adverse_selection_bps"])
def test_rejects_boolean_tca_numeric_fields_before_calibration_load(tmp_path: Path, field: str) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(json.dumps(_strict_record_payload(**{field: True})) + "\n")

    with pytest.raises(ValueError, match=f"calibration record {field} must be numeric"):
        load_calibration_records(source)


@pytest.mark.parametrize("maker_taker", [" Maker ", "MAKER", "post_only", 123, True])
def test_rejects_noncanonical_maker_taker_before_calibration_load(tmp_path: Path, maker_taker: object) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(json.dumps(_strict_record_payload(maker_taker=maker_taker)) + "\n")

    with pytest.raises(ValueError, match="calibration record maker_taker must be maker or taker"):
        load_calibration_records(source)


def test_rejects_malformed_commission_asset_before_calibration_load(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "side": "buy",
                "intended_limit_price": 100.0,
                "submitted_at": "2026-01-01T00:00:00+00:00",
                "fees": 0.01,
                "commissionAsset": "bnb",
                "status": "filled",
            }
        )
        + "\n"
    )

    import pytest

    with pytest.raises(ValueError, match="calibration record commissionAsset must be an uppercase asset code"):
        load_calibration_records(source)


def test_rejects_boolean_commission_before_calibration_load(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "side": "buy",
                "intended_limit_price": 100.0,
                "submitted_at": "2026-01-01T00:00:00+00:00",
                "commission": True,
                "commissionAsset": "BNB",
                "status": "filled",
            }
        )
        + "\n"
    )

    import pytest

    with pytest.raises(ValueError, match="calibration record commission must be numeric"):
        load_calibration_records(source)


def test_rejects_naive_submitted_at_before_calibration_load(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(json.dumps(_strict_record_payload(submitted_at="2026-01-01T00:00:00")) + "\n")

    import pytest

    with pytest.raises(ValueError, match="calibration record submitted_at must be a canonical UTC timestamp"):
        load_calibration_records(source)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("signal_at", None, "calibration record missing signal_at"),
        ("decision_at", None, "calibration record missing decision_at"),
        ("submitted_at", None, "calibration record missing submitted_at"),
        ("exchange_ack_at", None, "calibration record missing exchange_ack_at"),
        ("signal_at", True, "calibration record signal_at must be a canonical UTC timestamp"),
        ("decision_at", "2026-01-01T00:00:01+00:00", "calibration record decision_at must be a canonical UTC timestamp"),
        ("submitted_at", " 2026-01-01T00:00:02Z ", "calibration record submitted_at must be a canonical UTC timestamp"),
        ("exchange_ack_at", "2026-01-01T00:00:03", "calibration record exchange_ack_at must be a canonical UTC timestamp"),
    ],
)
def test_rejects_missing_or_noncanonical_required_lifecycle_timestamps_before_calibration_load(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    source = tmp_path / "dust_orders.jsonl"
    payload = _strict_record_payload()
    if value is None:
        payload.pop(field)
    else:
        payload[field] = value
    source.write_text(json.dumps(payload) + "\n")

    with pytest.raises(ValueError, match=message):
        load_calibration_records(source)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("first_fill_at", "2026-01-01T00:00:04+00:00"),
        ("last_fill_at", "2026-01-01T00:00:05+00:00"),
        ("cancel_requested_at", "2026-01-01T00:00:06+00:00"),
        ("cancel_ack_at", "2026-01-01T00:00:06+00:00"),
        ("replace_requested_at", "2026-01-01T00:00:04+00:00"),
        ("replace_ack_at", "2026-01-01T00:00:05+00:00"),
    ],
)
def test_rejects_noncanonical_optional_lifecycle_timestamps_before_calibration_load(
    tmp_path: Path, field: str, value: object
) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(json.dumps(_strict_record_payload(**{field: value})) + "\n")

    with pytest.raises(ValueError, match=f"calibration record {field} must be a canonical UTC timestamp"):
        load_calibration_records(source)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"decision_at": "2025-12-31T23:59:59Z"}, "calibration record decision_at must be at or after signal_at"),
        ({"submitted_at": "2026-01-01T00:00:00Z"}, "calibration record submitted_at must be after decision_at"),
        ({"exchange_ack_at": "2026-01-01T00:00:01Z"}, "calibration record exchange_ack_at must be at or after submitted_at"),
        ({"first_fill_at": "2026-01-01T00:00:02Z"}, "calibration record first_fill_at must be at or after exchange_ack_at"),
        (
            {"first_fill_at": "2026-01-01T00:00:04Z", "cancel_ack_at": "2026-01-01T00:00:04Z"},
            "calibration record cancel_ack_at must be after last fill timestamp",
        ),
        (
            {"cancel_requested_at": "2026-01-01T00:00:02Z"},
            "calibration record cancel_requested_at must be at or after exchange_ack_at",
        ),
        (
            {"replace_ack_at": "2026-01-01T00:00:05Z"},
            "calibration record replace_ack_at requires replace_requested_at",
        ),
        (
            {"replace_requested_at": "2026-01-01T00:00:05Z", "replace_ack_at": "2026-01-01T00:00:04Z"},
            "calibration record replace_ack_at must be at or after replace_requested_at",
        ),
    ],
)
def test_rejects_non_monotonic_lifecycle_timestamps_before_calibration_load(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(json.dumps(_strict_record_payload(**overrides)) + "\n")

    with pytest.raises(ValueError, match=message):
        load_calibration_records(source)


def test_rejects_cancel_ack_without_cancelled_terminal_state_before_calibration_load(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(json.dumps(_strict_record_payload(cancel_ack_at="2026-01-01T00:00:10Z")) + "\n")

    with pytest.raises(ValueError, match="calibration record cancel_ack_at requires a cancelled, expired, or rejected status"):
        load_calibration_records(source)


def test_rejects_fill_after_terminal_cancel_without_race_marker_before_calibration_load(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(
        json.dumps(
            _strict_record_payload(
                status="cancelled",
                first_fill_at="2026-01-01T00:00:08Z",
                last_fill_at="2026-01-01T00:00:08Z",
                cancel_requested_at="2026-01-01T00:00:04Z",
                cancel_ack_at="2026-01-01T00:00:07Z",
                partial_fill_before_cancel=True,
            )
        )
        + "\n"
    )

    with pytest.raises(ValueError, match="calibration record fill after terminal cancel requires exchange_race_partial_before_cancel_ack"):
        load_calibration_records(source)


def test_rejects_filled_status_without_fill_timestamps_before_calibration_load(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    payload = _strict_record_payload()
    payload.pop("first_fill_at")
    source.write_text(json.dumps(payload) + "\n")

    with pytest.raises(ValueError, match="calibration record filled status requires first_fill_at"):
        load_calibration_records(source)


def test_rejects_first_fill_at_before_submitted_at_before_calibration_load(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(json.dumps(_strict_record_payload(first_fill_at="2026-01-01T00:00:02Z")) + "\n")

    import pytest

    with pytest.raises(ValueError, match="calibration record first_fill_at must be at or after exchange_ack_at"):
        load_calibration_records(source)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("symbol", " btcusdt ", "calibration record symbol must be an uppercase symbol"),
        ("side", " BUY ", "calibration record side must be buy or sell"),
        ("status", " Filled ", "calibration record status must be canonical"),
        ("status", "unknown", "calibration record status must be a known lifecycle status"),
        ("terminal_status", "unknown", "calibration record terminal_status must be a known lifecycle status"),
        ("cancel_reason", " expired ", "calibration record cancel_reason must be canonical"),
        ("expire_reason", " timeout ", "calibration record expire_reason must be canonical"),
        ("setup_type", "rs_pullback", "calibration record setup_type must be canonical"),
    ],
)
def test_rejects_noncanonical_provenance_fields_before_calibration_load(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(json.dumps(_strict_record_payload(**{field: value})) + "\n")

    with pytest.raises(ValueError, match=message):
        load_calibration_records(source)


def test_rejects_last_fill_at_before_submitted_at_before_calibration_load(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(
        json.dumps(
                _strict_record_payload(
                    submitted_at="2026-01-01T00:00:05Z",
                    exchange_ack_at="2026-01-01T00:00:05Z",
                    last_fill_at="2026-01-01T00:00:04Z",
                )
        )
        + "\n"
    )

    with pytest.raises(ValueError, match="calibration record first_fill_at must be at or after exchange_ack_at"):
        load_calibration_records(source)


def test_rejects_last_fill_at_before_first_fill_at_before_calibration_load(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(
        json.dumps(
                _strict_record_payload(
                    first_fill_at="2026-01-01T00:00:05Z",
                    last_fill_at="2026-01-01T00:00:04Z",
                )
        )
        + "\n"
    )

    with pytest.raises(ValueError, match="calibration record last_fill_at must be at or after first_fill_at"):
        load_calibration_records(source)


def test_rejects_conflicting_fee_asset_aliases_before_calibration_load(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(json.dumps(_strict_record_payload(fee_asset="BNB", commissionAsset="USDT")) + "\n")

    with pytest.raises(ValueError, match="calibration record fee asset aliases conflict"):
        load_calibration_records(source)


@pytest.mark.parametrize("commission", ["0.01", float("nan"), -0.01])
def test_rejects_noncanonical_commission_before_calibration_load(tmp_path: Path, commission: object) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(json.dumps(_strict_record_payload(commission=commission, commissionAsset="BNB")) + "\n")

    with pytest.raises(ValueError, match="calibration record commission must be numeric, finite, and non-negative"):
        load_calibration_records(source)


def test_rejects_non_mapping_evidence_source_before_summary() -> None:
    with pytest.raises(ValueError, match="calibration summary evidence_source must be an object"):
        summarize_calibration_records([], evidence_source="synthetic_fixture")  # type: ignore[arg-type]


def test_preserves_valid_calibration_summary_evidence_source_payload() -> None:
    evidence_source = {
        "type": "passive_order_probe",
        "run_id": "calibration-1",
        "exported_at": "2026-05-08T12:00:00Z",
    }

    summary = summarize_calibration_records([], evidence_source=evidence_source)

    assert summary["evidence_source"] == evidence_source


def test_rejects_noncanonical_evidence_source_type_before_summary() -> None:
    with pytest.raises(ValueError, match="calibration summary evidence_source.type must be canonical"):
        summarize_calibration_records([], evidence_source={"type": " synthetic_fixture "})


@pytest.mark.parametrize(
    ("field_name", "bad_identifier", "expected_error"),
    [
        ("type", "passive order probe", "calibration summary evidence_source.type must be a safe identifier"),
        ("run_id", "calibration 1", "calibration summary evidence_source.run_id must be a safe identifier"),
    ],
)
def test_rejects_unsafe_calibration_summary_evidence_source_identifiers(
    field_name: str, bad_identifier: str, expected_error: str
) -> None:
    evidence_source = {"type": "passive_order_probe", "run_id": "calibration-1"}
    evidence_source[field_name] = bad_identifier

    with pytest.raises(ValueError, match=f"^{expected_error}$"):
        summarize_calibration_records([], evidence_source=evidence_source)


@pytest.mark.parametrize(
    ("exported_at", "expected_error"),
    [
        ("2026-05-08T12:00:00+00:00", "calibration summary evidence_source.exported_at must be a canonical UTC timestamp"),
        (123, "calibration summary evidence_source.exported_at must be a string"),
    ],
)
def test_rejects_invalid_calibration_summary_evidence_source_exported_at(
    exported_at: object, expected_error: str
) -> None:
    with pytest.raises(ValueError, match=f"^{expected_error}$"):
        summarize_calibration_records(
            [],
            evidence_source={
                "type": "passive_order_probe",
                "run_id": "calibration-1",
                "exported_at": exported_at,
            },
        )


def test_rejects_unknown_calibration_summary_evidence_source_fields() -> None:
    with pytest.raises(ValueError, match="^unknown calibration summary evidence_source field: extra$"):
        summarize_calibration_records([], evidence_source={"type": "passive_order_probe", "extra": "not-allowed"})


@pytest.mark.parametrize("bad_key", [123, ""])
def test_rejects_noncanonical_calibration_summary_evidence_source_keys(bad_key: object) -> None:
    with pytest.raises(ValueError, match=r"^calibration summary evidence_source\.<key> must be"):
        summarize_calibration_records([], evidence_source={"type": "passive_order_probe", bad_key: "not-allowed"})


def test_rejects_calibration_summary_evidence_source_string_subclasses() -> None:
    class SourceType(str):
        pass

    with pytest.raises(ValueError, match="^calibration summary evidence_source.type must be a string$"):
        summarize_calibration_records([], evidence_source={"type": SourceType("passive_order_probe")})
