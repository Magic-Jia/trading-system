from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_system.app.execution.calibration import (
    load_calibration_records,
    summarize_calibration_records,
    write_calibration_summary,
)


def _strict_record_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "intended_limit_price": 100.0,
        "submitted_at": "2026-01-01T00:00:00+00:00",
        "status": "filled",
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
                "submitted_at": "2026-01-01T00:00:00+00:00",
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
                "submitted_at": "2026-01-01T00:00:00+00:00",
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
                "submitted_at": "2026-01-01T00:00:00+00:00",
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
                "submitted_at": "2026-01-01T00:00:00+00:00",
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
        "slippage_bps",
        "ref_price",
        "latency_ms",
    ],
)
def test_rejects_string_optional_numeric_fields_before_calibration_load(tmp_path: Path, field: str) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(json.dumps(_strict_record_payload(**{field: "1.0"})) + "\n")

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
    source.write_text(
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "side": "buy",
                "intended_limit_price": 100.0,
                "submitted_at": "2026-01-01T00:00:00",
                "status": "filled",
            }
        )
        + "\n"
    )

    import pytest

    with pytest.raises(ValueError, match="calibration record submitted_at must include a timezone"):
        load_calibration_records(source)


def test_rejects_first_fill_at_before_submitted_at_before_calibration_load(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "side": "buy",
                "intended_limit_price": 100.0,
                "submitted_at": "2026-01-01T00:00:05+00:00",
                "first_fill_at": "2026-01-01T00:00:04+00:00",
                "status": "filled",
            }
        )
        + "\n"
    )

    import pytest

    with pytest.raises(ValueError, match="calibration record first_fill_at must be at or after submitted_at"):
        load_calibration_records(source)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("symbol", " btcusdt ", "calibration record symbol must be an uppercase symbol"),
        ("side", " BUY ", "calibration record side must be buy or sell"),
        ("status", " Filled ", "calibration record status must be canonical"),
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
                submitted_at="2026-01-01T00:00:05+00:00",
                last_fill_at="2026-01-01T00:00:04+00:00",
            )
        )
        + "\n"
    )

    with pytest.raises(ValueError, match="calibration record last_fill_at must be at or after submitted_at"):
        load_calibration_records(source)


def test_rejects_last_fill_at_before_first_fill_at_before_calibration_load(tmp_path: Path) -> None:
    source = tmp_path / "dust_orders.jsonl"
    source.write_text(
        json.dumps(
            _strict_record_payload(
                first_fill_at="2026-01-01T00:00:05+00:00",
                last_fill_at="2026-01-01T00:00:04+00:00",
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
