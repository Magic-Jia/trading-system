from __future__ import annotations

import json
from pathlib import Path

from trading_system.app.runtime.runtime_safety_evidence import (
    build_runtime_safety_gate,
    write_runtime_safety_gate,
)


def _passing_manifest() -> dict:
    return {
        "evidence_source": {"type": "synthetic_fixture"},
        "events": [
            {"type": "kill_switch_dry_run", "passed": True},
            {"type": "order_position_reconciliation", "passed": True},
            {"type": "runtime_fail_closed", "passed": True},
            {"type": "live_dust_before_scale", "passed": True},
            {"type": "live_trade_ledger", "passed": True},
            {"type": "runtime_explainability", "passed": True},
            {"type": "drift_guard", "passed": True},
        ],
    }


def test_builds_runtime_safety_gate_when_all_required_events_pass(tmp_path: Path) -> None:
    gate = build_runtime_safety_gate(_passing_manifest())

    assert gate["schema_version"] == "runtime_safety_gate_input.v1"
    assert gate["evidence_source"] == {"type": "synthetic_fixture"}
    assert gate["checks"] == {
        "kill_switch_dry_run_met": True,
        "order_position_reconciliation_met": True,
        "runtime_fail_closed_met": True,
        "live_dust_before_scale_met": True,
        "live_trade_ledger_met": True,
        "runtime_explainability_met": True,
        "drift_guard_met": True,
    }
    assert gate["summary"]["event_count"] == 7
    assert gate["reasons"] == []

    output = write_runtime_safety_gate(_passing_manifest(), tmp_path)
    assert output == tmp_path / "runtime_safety_gate.json"
    assert json.loads(output.read_text()) == gate


def test_runtime_safety_gate_reports_missing_or_failed_events() -> None:
    gate = build_runtime_safety_gate(
        {
            "evidence_source": {"type": "synthetic_fixture"},
            "events": [
                {"type": "kill_switch_dry_run", "passed": False},
                {"type": "order_position_reconciliation", "passed": True},
            ],
        }
    )

    assert gate["checks"]["kill_switch_dry_run_met"] is False
    assert gate["checks"]["order_position_reconciliation_met"] is True
    assert gate["checks"]["runtime_fail_closed_met"] is False
    assert gate["reasons"] == [
        "kill_switch_dry_run_missing",
        "runtime_fail_closed_missing",
        "live_dust_before_scale_missing",
        "live_trade_ledger_missing",
        "runtime_explainability_missing",
        "drift_guard_missing",
    ]


def test_runtime_safety_gate_rejects_non_object_evidence_source() -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = [("type", "paper_runtime_logs")]

    try:
        build_runtime_safety_gate(manifest)  # type: ignore[arg-type]
    except ValueError as exc:
        assert str(exc) == "evidence_source must be an object"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected non-object evidence_source to be rejected")


def test_runtime_safety_gate_accepts_event_type_alias_from_runtime_logs() -> None:
    manifest = _passing_manifest()
    for event in manifest["events"]:
        event["event_type"] = event.pop("type")

    gate = build_runtime_safety_gate(manifest)

    assert gate["reasons"] == []
    assert gate["checks"]["runtime_fail_closed_met"] is True
    assert gate["checks"]["live_dust_before_scale_met"] is True
    assert gate["summary"]["counts_by_type"]["runtime_fail_closed"] == 1


def test_runtime_safety_gate_rejects_non_string_event_type() -> None:
    manifest = _passing_manifest()
    manifest["events"][0]["type"] = 123

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "runtime safety event type must be a string"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected non-string runtime safety event type to be rejected")


def test_runtime_safety_gate_rejects_non_boolean_event_passed() -> None:
    manifest = _passing_manifest()
    manifest["events"][0]["passed"] = "true"

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "runtime safety event passed must be a boolean"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected non-boolean runtime safety event passed to be rejected")


def test_runtime_safety_gate_rejects_non_string_evidence_source_type() -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": 123}

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "evidence_source type must be a string"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected non-string evidence_source type to be rejected")


def test_runtime_safety_gate_rejects_non_string_evidence_source_run_id() -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": "paper_runtime_logs", "run_id": 123}

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "evidence_source run_id must be a string"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected non-string evidence_source run_id to be rejected")


def test_runtime_safety_gate_rejects_unknown_evidence_source_fields() -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": "paper_runtime_logs", "extra": "not-allowed"}

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "unknown evidence_source field: extra"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected unknown evidence_source field to be rejected")


def test_runtime_safety_gate_rejects_non_string_evidence_source_keys() -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": "paper_runtime_logs", 123: "not-allowed"}

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "evidence_source.<key> must be a string"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected non-string evidence_source key to be rejected")


def test_runtime_safety_gate_rejects_padded_evidence_source_keys() -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": "paper_runtime_logs", " run_id": "runtime-1"}

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "evidence_source.<key> must be canonical"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected padded evidence_source key to be rejected")


def test_runtime_safety_gate_rejects_blank_evidence_source_keys() -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": "paper_runtime_logs", " ": "not-allowed"}

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "evidence_source.<key> must be non-empty"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected blank evidence_source key to be rejected")


def test_runtime_safety_gate_copies_valid_evidence_source_payload() -> None:
    manifest = _passing_manifest()
    source = {
        "type": "paper_runtime_logs",
        "run_id": "runtime-1",
        "exported_at": "2026-05-08T12:00:00Z",
    }
    manifest["evidence_source"] = source

    gate = build_runtime_safety_gate(manifest)

    assert gate["evidence_source"] == source
    assert gate["evidence_source"] is not source


def test_runtime_safety_gate_rejects_unknown_manifest_fields() -> None:
    manifest = _passing_manifest()
    manifest["unexpected"] = "not-allowed"

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "unknown runtime safety manifest field: unexpected"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected unknown runtime safety manifest field to be rejected")

def test_runtime_safety_gate_rejects_unknown_event_fields() -> None:
    manifest = _passing_manifest()
    manifest["events"][0]["legacy_note"] = "ignored-before-hardening"

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "unknown runtime safety event field: legacy_note"
    else:  # pragma: no cover - RED path until nested producer schema is hardened
        raise AssertionError("expected unknown runtime safety event field to be rejected")

def test_runtime_safety_gate_rejects_padded_evidence_source_type() -> None:
    try:
        build_runtime_safety_gate(
            {
                "evidence_source": {"type": " paper_runtime_logs ", "run_id": "runtime-1"},
                "events": [
                    {"event_type": "kill_switch_dry_run", "passed": True},
                    {"event_type": "order_position_reconciliation", "passed": True},
                    {"event_type": "fail_closed", "passed": True},
                    {"event_type": "live_dust_before_scale", "passed": True},
                    {"event_type": "live_trade_ledger", "passed": True},
                    {"event_type": "runtime_explainability", "passed": True},
                    {"event_type": "drift_guard", "passed": True},
                ],
            }
        )
    except ValueError as exc:
        assert "evidence_source type must be canonical" in str(exc)
    else:
        raise AssertionError("expected padded evidence_source type to be rejected")

def test_runtime_safety_gate_rejects_padded_event_type() -> None:
    try:
        build_runtime_safety_gate(
            {
                "events": [
                    {"event_type": " kill_switch_dry_run ", "passed": True},
                ],
            }
        )
    except ValueError as exc:
        assert "runtime safety event type must be canonical" in str(exc)
    else:
        raise AssertionError("expected padded runtime safety event type to be rejected")
