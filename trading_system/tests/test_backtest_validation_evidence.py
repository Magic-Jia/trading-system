from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from trading_system.app.backtest.validation_evidence import (
    build_validation_gate,
    write_validation_gate,
)


def _passing_manifest() -> dict:
    return {
        "evidence_source": {"type": "synthetic_fixture"},
        "oos": {"baseline_net_pnl": 100.0, "oos_net_pnl": 90.0, "max_degradation_fraction": 0.2},
        "regimes": [
            {"net_pnl": 40.0, "trade_count": 20},
            {"net_pnl": 10.0, "trade_count": 12},
        ],
        "cost_stress": {"stressed_net_pnl": 5.0},
        "forward_contamination": {"absent": True, "audit_id": "fc-1"},
    }


def test_builds_validation_gate_when_all_checks_pass(tmp_path: Path) -> None:
    gate = build_validation_gate(_passing_manifest())

    assert gate["schema_version"] == "validation_gate_input.v1"
    assert gate["evidence_source"] == {"type": "synthetic_fixture"}
    assert gate["checks"] == {
        "oos_non_degraded_met": True,
        "multi_regime_resilience_met": True,
        "cost_stress_positive_met": True,
        "forward_contamination_absent_met": True,
    }
    assert gate["summary"]["oos_degradation_fraction"] == 0.1
    assert gate["summary"]["profitable_regime_count"] == 2
    assert gate["reasons"] == []

    output = write_validation_gate(_passing_manifest(), tmp_path)
    assert output == tmp_path / "validation_gate.json"
    assert json.loads(output.read_text()) == gate


def test_validation_gate_preserves_valid_evidence_source_payload() -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {
        "type": "walk_forward_oos_report",
        "run_id": "validation-1",
        "exported_at": "2026-05-08T12:00:00Z",
    }

    gate = build_validation_gate(manifest)

    assert gate["evidence_source"] == manifest["evidence_source"]


def test_validation_gate_result_provenance_has_canonical_shape() -> None:
    gate = build_validation_gate(_passing_manifest())

    assert set(gate["evidence_source"]) == {"type"}
    assert gate["evidence_source"]["type"] == "synthetic_fixture"
    assert "forward_contamination_audit_id" in gate["summary"]


@pytest.mark.parametrize(
    ("field_name", "bad_identifier", "expected_error"),
    [
        ("type", "walk forward", "evidence_source type must be a safe identifier"),
        ("run_id", "validation 1", "evidence_source run_id must be a safe identifier"),
    ],
)
def test_validation_gate_rejects_unsafe_evidence_source_identity_fields(
    field_name: str, bad_identifier: str, expected_error: str
) -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": "walk_forward_oos_report", "run_id": "validation-1"}
    manifest["evidence_source"][field_name] = bad_identifier

    with pytest.raises(ValueError, match=f"^{expected_error}$"):
        build_validation_gate(manifest)


@pytest.mark.parametrize(
    ("exported_at", "expected_error"),
    [
        ("2026-05-08T12:00:00+00:00", "evidence_source exported_at must be a canonical UTC timestamp"),
        (123, "evidence_source exported_at must be a string"),
    ],
)
def test_validation_gate_rejects_invalid_evidence_source_exported_at(
    exported_at: object, expected_error: str
) -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {
        "type": "walk_forward_oos_report",
        "run_id": "validation-1",
        "exported_at": exported_at,
    }

    with pytest.raises(ValueError, match=f"^{expected_error}$"):
        build_validation_gate(manifest)


def test_validation_gate_rejects_evidence_source_exported_at_string_subclass() -> None:
    class TimestampString(str):
        pass

    manifest = _passing_manifest()
    manifest["evidence_source"] = {
        "type": "walk_forward_oos_report",
        "run_id": "validation-1",
        "exported_at": TimestampString("2026-05-08T12:00:00Z"),
    }

    with pytest.raises(ValueError, match="^evidence_source exported_at must be a string$"):
        build_validation_gate(manifest)


def test_validation_gate_reports_each_failed_requirement() -> None:
    gate = build_validation_gate(
        {
            "evidence_source": {"type": "synthetic_fixture"},
            "oos": {"baseline_net_pnl": 100.0, "oos_net_pnl": 60.0, "max_degradation_fraction": 0.2},
            "regimes": [{"net_pnl": 5.0, "trade_count": 20}],
            "cost_stress": {"stressed_net_pnl": 0.0},
            "forward_contamination": {"absent": False},
        }
    )

    assert gate["checks"] == {
        "oos_non_degraded_met": False,
        "multi_regime_resilience_met": False,
        "cost_stress_positive_met": False,
        "forward_contamination_absent_met": False,
    }
    assert gate["reasons"] == [
        "oos_degraded",
        "regime_single_point_survivor",
        "cost_stress_not_positive",
        "forward_contamination_unproven",
    ]


def test_validation_gate_rejects_non_object_evidence_source() -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = [("type", "walk_forward_oos_report")]

    try:
        build_validation_gate(manifest)  # type: ignore[arg-type]
    except ValueError as exc:
        assert str(exc) == "evidence_source must be an object"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected non-object evidence_source to be rejected")


def test_validation_gate_rejects_boolean_regime_trade_count() -> None:
    manifest = _passing_manifest()
    manifest["regimes"] = [
        {"net_pnl": 40.0, "trade_count": True},
        {"net_pnl": 10.0, "trade_count": 12},
    ]

    try:
        build_validation_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "regime trade_count must be an integer count"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected boolean regime trade_count to be rejected")


def test_validation_gate_rejects_boolean_oos_numeric_fields() -> None:
    manifest = _passing_manifest()
    manifest["oos"]["baseline_net_pnl"] = True

    try:
        build_validation_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "oos baseline_net_pnl must be a number"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected boolean oos numeric field to be rejected")


def test_validation_gate_rejects_string_oos_numeric_fields() -> None:
    manifest = _passing_manifest()
    manifest["oos"]["baseline_net_pnl"] = "100.0"

    try:
        build_validation_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "oos baseline_net_pnl must be a number"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected string oos numeric field to be rejected")


@pytest.mark.parametrize(
    ("section", "field_name", "error_name"),
    [
        ("oos", "baseline_net_pnl", "oos baseline_net_pnl"),
        ("oos", "oos_net_pnl", "oos oos_net_pnl"),
        ("oos", "max_degradation_fraction", "oos max_degradation_fraction"),
        ("cost_stress", "stressed_net_pnl", "cost_stress stressed_net_pnl"),
    ],
)
@pytest.mark.parametrize("non_finite", [math.nan, math.inf, -math.inf])
def test_validation_gate_rejects_non_finite_oos_and_cost_stress_numerics(
    section: str, field_name: str, error_name: str, non_finite: float
) -> None:
    manifest = _passing_manifest()
    manifest[section][field_name] = non_finite

    with pytest.raises(ValueError, match=f"^{error_name} must be a finite number$"):
        build_validation_gate(manifest)


@pytest.mark.parametrize("non_finite", [math.nan, math.inf, -math.inf])
def test_validation_gate_rejects_non_finite_regime_net_pnl(non_finite: float) -> None:
    manifest = _passing_manifest()
    manifest["regimes"] = [
        {"net_pnl": non_finite, "trade_count": 20},
        {"net_pnl": 10.0, "trade_count": 12},
    ]

    with pytest.raises(ValueError, match=r"^regimes\[0\]\.net_pnl must be a finite number$"):
        build_validation_gate(manifest)


def test_validation_gate_rejects_boolean_regime_net_pnl_with_field_path() -> None:
    manifest = _passing_manifest()
    manifest["regimes"] = [
        {"net_pnl": 40.0, "trade_count": 20},
        {"net_pnl": True, "trade_count": 12},
    ]

    with pytest.raises(ValueError, match=r"^regimes\[1\]\.net_pnl must be a number$"):
        build_validation_gate(manifest)


@pytest.mark.parametrize(
    ("section", "field_name", "bad_value", "expected_error"),
    [
        ("oos", "baseline_net_pnl", 0.0, "oos baseline_net_pnl must be positive"),
        ("oos", "baseline_net_pnl", -1.0, "oos baseline_net_pnl must be positive"),
        ("oos", "oos_net_pnl", -1.0, "oos oos_net_pnl must be non-negative"),
        ("cost_stress", "stressed_net_pnl", -1.0, "cost_stress stressed_net_pnl must be non-negative"),
    ],
)
def test_validation_gate_rejects_out_of_domain_oos_and_cost_stress_numerics(
    section: str, field_name: str, bad_value: float, expected_error: str
) -> None:
    manifest = _passing_manifest()
    manifest[section][field_name] = bad_value

    with pytest.raises(ValueError, match=f"^{expected_error}$"):
        build_validation_gate(manifest)


@pytest.mark.parametrize("max_degradation_fraction", [-0.1, 1.1])
def test_validation_gate_rejects_out_of_bounds_max_degradation_fraction(max_degradation_fraction: float) -> None:
    manifest = _passing_manifest()
    manifest["oos"]["max_degradation_fraction"] = max_degradation_fraction

    with pytest.raises(ValueError, match="^oos max_degradation_fraction must be between 0 and 1$"):
        build_validation_gate(manifest)


def test_validation_gate_rejects_non_boolean_forward_contamination_absent() -> None:
    manifest = _passing_manifest()
    manifest["forward_contamination"]["absent"] = "true"

    try:
        build_validation_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "forward_contamination absent must be a boolean"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected non-boolean forward contamination flag to be rejected")


def test_validation_gate_rejects_non_string_evidence_source_type() -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": 123}

    try:
        build_validation_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "evidence_source type must be a string"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected non-string evidence_source type to be rejected")


def test_validation_gate_rejects_evidence_source_type_string_subclass() -> None:
    class SourceType(str):
        pass

    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": SourceType("walk_forward_oos_report")}

    with pytest.raises(ValueError, match="^evidence_source type must be a string$"):
        build_validation_gate(manifest)


def test_validation_gate_rejects_non_string_evidence_source_run_id() -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": "walk_forward_oos_report", "run_id": 123}

    try:
        build_validation_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "evidence_source run_id must be a string"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected non-string evidence_source run_id to be rejected")


@pytest.mark.parametrize("bad_key", [123, None])
def test_validation_gate_rejects_non_string_evidence_source_keys(bad_key: object) -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": "walk_forward_oos_report", bad_key: "not-allowed"}

    with pytest.raises(ValueError, match=r"^evidence_source\.<key> must be a string$"):
        build_validation_gate(manifest)


@pytest.mark.parametrize("bad_key", ["", " "])
def test_validation_gate_rejects_blank_evidence_source_keys(bad_key: str) -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": "walk_forward_oos_report", bad_key: "not-allowed"}

    with pytest.raises(ValueError, match=r"^evidence_source\.<key> must be non-empty$"):
        build_validation_gate(manifest)


@pytest.mark.parametrize("bad_key", [" type", "type "])
def test_validation_gate_rejects_padded_evidence_source_keys(bad_key: str) -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": "walk_forward_oos_report", bad_key: "not-allowed"}

    with pytest.raises(ValueError, match=r"^evidence_source\.<key> must be canonical$"):
        build_validation_gate(manifest)


def test_validation_gate_rejects_unknown_evidence_source_fields() -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": "walk_forward_oos_report", "extra": "not-allowed"}

    try:
        build_validation_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "unknown evidence_source field: extra"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected unknown evidence_source field to be rejected")


def test_validation_gate_rejects_unknown_manifest_fields() -> None:
    manifest = _passing_manifest()
    manifest["unexpected"] = "not-allowed"

    try:
        build_validation_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "unknown validation manifest field: unexpected"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected unknown validation manifest field to be rejected")


def test_validation_gate_rejects_unknown_regime_fields() -> None:
    manifest = _passing_manifest()
    manifest["regimes"] = [{"trade_count": 1, "net_pnl": 10.0, "legacy_alias": "RISK_ON"}]

    try:
        build_validation_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "unknown validation regime field: legacy_alias"
    else:  # pragma: no cover - RED path until nested producer schema is hardened
        raise AssertionError("expected unknown validation regime field to be rejected")


@pytest.mark.parametrize("field_name", ["regime_id", "regime_name", "label", "name"])
def test_validation_gate_allows_safe_optional_regime_identifiers(field_name: str) -> None:
    manifest = _passing_manifest()
    manifest["regimes"][0][field_name] = "RISK_ON-TREND:1"

    gate = build_validation_gate(manifest)

    assert gate["summary"]["eligible_regime_count"] == 2


@pytest.mark.parametrize("field_name", ["regime_id", "regime_name", "label", "name"])
@pytest.mark.parametrize(
    ("bad_identifier", "expected_error"),
    [
        (123, "must be a string"),
        ("", "must be non-empty"),
        (" RISK_ON", "must be canonical"),
        ("RISK ON", "must be a safe identifier"),
    ],
)
def test_validation_gate_rejects_unsafe_optional_regime_identifiers(
    field_name: str, bad_identifier: object, expected_error: str
) -> None:
    manifest = _passing_manifest()
    manifest["regimes"][0][field_name] = bad_identifier

    with pytest.raises(ValueError, match=rf"^regimes\[0\]\.{field_name} {expected_error}$"):
        build_validation_gate(manifest)


def test_validation_gate_rejects_duplicate_regime_identifiers() -> None:
    manifest = _passing_manifest()
    manifest["regimes"] = [
        {"regime_id": "RISK_ON", "net_pnl": 40.0, "trade_count": 20},
        {"regime_name": "RISK_ON", "net_pnl": 10.0, "trade_count": 12},
    ]

    with pytest.raises(ValueError, match="^duplicate validation regime identifier: RISK_ON$"):
        build_validation_gate(manifest)


def test_validation_gate_rejects_unknown_oos_fields() -> None:
    manifest = _passing_manifest()
    manifest["oos"]["legacy_ratio"] = 0.9

    try:
        build_validation_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "unknown validation oos field: legacy_ratio"
    else:  # pragma: no cover - RED path until nested producer schema is hardened
        raise AssertionError("expected unknown validation oos field to be rejected")

def test_validation_gate_rejects_unknown_cost_stress_fields() -> None:
    manifest = _passing_manifest()
    manifest["cost_stress"]["legacy_passed"] = True

    try:
        build_validation_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "unknown validation cost_stress field: legacy_passed"
    else:  # pragma: no cover - RED path until nested producer schema is hardened
        raise AssertionError("expected unknown validation cost_stress field to be rejected")

def test_validation_gate_rejects_unknown_forward_contamination_fields() -> None:
    manifest = _passing_manifest()
    manifest["forward_contamination"]["legacy_audit_complete"] = True

    try:
        build_validation_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "unknown validation forward_contamination field: legacy_audit_complete"
    else:  # pragma: no cover - RED path until nested producer schema is hardened
        raise AssertionError("expected unknown validation forward_contamination field to be rejected")

def test_validation_gate_rejects_padded_evidence_source_type() -> None:
    try:
        build_validation_gate(
            {
                "evidence_source": {"type": " walk_forward_oos_report ", "run_id": "validation-1"},
                "oos": {"baseline_net_pnl": 100.0, "oos_net_pnl": 80.0},
                "regimes": [{"trade_count": 1, "net_pnl": 20.0}],
                "cost_stress": {"stressed_net_pnl": 10.0},
                "forward_contamination": {"absent": True},
            }
        )
    except ValueError as exc:
        assert "evidence_source type must be canonical" in str(exc)
    else:
        raise AssertionError("expected padded evidence_source type to be rejected")

def test_validation_gate_rejects_non_string_forward_contamination_audit_id() -> None:
    try:
        build_validation_gate(
            {
                "oos": {"baseline_net_pnl": 100.0, "oos_net_pnl": 80.0},
                "regimes": [{"trade_count": 1, "net_pnl": 20.0}],
                "cost_stress": {"stressed_net_pnl": 10.0},
                "forward_contamination": {"absent": True, "audit_id": 123},
            }
        )
    except ValueError as exc:
        assert "forward_contamination audit_id must be a string" in str(exc)
    else:
        raise AssertionError("expected non-string forward contamination audit_id to be rejected")


@pytest.mark.parametrize(
    ("audit_id", "expected_error"),
    [
        ("", "forward_contamination audit_id must be non-empty"),
        (" audit-1", "forward_contamination audit_id must be canonical"),
        ("audit 1", "forward_contamination audit_id must be a safe identifier"),
    ],
)
def test_validation_gate_rejects_unsafe_forward_contamination_audit_id(
    audit_id: str, expected_error: str
) -> None:
    manifest = _passing_manifest()
    manifest["forward_contamination"]["audit_id"] = audit_id

    with pytest.raises(ValueError, match=f"^{expected_error}$"):
        build_validation_gate(manifest)
