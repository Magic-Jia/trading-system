from __future__ import annotations

import json
from pathlib import Path

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


def test_validation_gate_reports_each_failed_requirement() -> None:
    gate = build_validation_gate(
        {
            "evidence_source": {"type": "synthetic_fixture"},
            "oos": {"baseline_net_pnl": 100.0, "oos_net_pnl": 60.0, "max_degradation_fraction": 0.2},
            "regimes": [{"net_pnl": 5.0, "trade_count": 20}],
            "cost_stress": {"stressed_net_pnl": -1.0},
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


def test_validation_gate_rejects_non_string_evidence_source_run_id() -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": "walk_forward_oos_report", "run_id": 123}

    try:
        build_validation_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "evidence_source run_id must be a string"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected non-string evidence_source run_id to be rejected")


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
    manifest["regimes"] = [{"trade_count": 1, "net_pnl": 10.0, "label": "legacy-alias"}]

    try:
        build_validation_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "unknown validation regime field: label"
    else:  # pragma: no cover - RED path until nested producer schema is hardened
        raise AssertionError("expected unknown validation regime field to be rejected")


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
