from __future__ import annotations

import json
import math
from pathlib import Path

from trading_system.app.runtime.runtime_safety_evidence import (
    RuntimeSafetyReason,
    build_runtime_safety_gate,
    write_runtime_safety_gate,
)


def _passing_kill_switch_decision() -> dict:
    observed_at = "2026-05-16T10:00:00Z"
    evaluated_at = "2026-05-16T10:00:30Z"
    return {
        "evaluated_at": evaluated_at,
        "decision": "allow",
        "max_evidence_age_seconds": 120,
        "evidence": {
            "market_data": {"ok": True, "observed_at": observed_at, "age_seconds": 30},
            "account_snapshot": {"ok": True, "observed_at": observed_at, "age_seconds": 30},
            "clock_skew": {"ok": True, "observed_at": evaluated_at, "skew_seconds": 1.25},
            "max_daily_loss": {"ok": True, "observed_at": evaluated_at, "value": 10.0, "limit": 100.0},
            "max_order_count": {"ok": True, "observed_at": evaluated_at, "value": 3, "limit": 20},
            "max_notional": {"ok": True, "observed_at": evaluated_at, "value": 250.0, "limit": 1000.0},
            "exchange_account_state": {"ok": True, "observed_at": evaluated_at},
        },
    }


def _passing_manifest() -> dict:
    return {
        "evidence_source": {"type": "synthetic_fixture"},
        "kill_switch_decision": _passing_kill_switch_decision(),
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
        "market_data_fresh_met": True,
        "account_snapshot_fresh_met": True,
        "clock_skew_within_limit_met": True,
        "max_daily_loss_within_limit_met": True,
        "max_order_count_within_limit_met": True,
        "max_notional_within_limit_met": True,
        "exchange_account_state_unambiguous_met": True,
    }
    assert gate["summary"]["event_count"] == 7
    assert gate["kill_switch_decision"] == _passing_kill_switch_decision()
    assert gate["reasons"] == []

    output = write_runtime_safety_gate(_passing_manifest(), tmp_path)
    assert output == tmp_path / "runtime_safety_gate.json"
    assert json.loads(output.read_text()) == gate


def test_runtime_safety_gate_reports_missing_or_failed_events() -> None:
    manifest = _passing_manifest()
    manifest["events"] = [
        {"type": "kill_switch_dry_run", "passed": False},
        {"type": "order_position_reconciliation", "passed": True},
    ]
    gate = build_runtime_safety_gate(manifest)

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


def test_runtime_safety_gate_rejects_blank_event_type() -> None:
    manifest = _passing_manifest()
    manifest["events"][0]["type"] = " "

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "runtime safety event type must be non-empty"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected blank runtime safety event type to be rejected")


def test_runtime_safety_gate_rejects_missing_event_type() -> None:
    manifest = _passing_manifest()
    del manifest["events"][0]["type"]

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "runtime safety event type must be present"
    else:  # pragma: no cover - RED path until producer is hardened
        raise AssertionError("expected missing runtime safety event type to be rejected")


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


def test_runtime_safety_gate_rejects_evidence_source_type_string_subclass() -> None:
    class SourceType(str):
        pass

    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": SourceType("paper_runtime_logs")}

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "evidence_source type must be a string"
    else:
        raise AssertionError("expected evidence_source type string subclass to be rejected")


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


def test_runtime_safety_gate_rejects_unsafe_evidence_source_run_id() -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": "paper_runtime_logs", "run_id": "runtime 1"}

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "evidence_source run_id must be a safe identifier"
    else:  # pragma: no cover - RED path until runtime provenance identity is hardened
        raise AssertionError("expected unsafe evidence_source run_id to be rejected")


def test_runtime_safety_gate_writer_rejects_noncanonical_evidence_source_exported_at_without_artifact_write(
    tmp_path: Path,
) -> None:
    manifest = _passing_manifest()
    manifest["evidence_source"] = {
        "type": "paper_runtime_logs",
        "run_id": "runtime-1",
        "exported_at": "2026-05-08T12:00:00+00:00",
    }
    output_dir = tmp_path / "runtime"

    try:
        write_runtime_safety_gate(manifest, output_dir)
    except ValueError as exc:
        assert str(exc) == "evidence_source exported_at must be a canonical UTC timestamp"
    else:  # pragma: no cover - RED path until timestamp metadata is hardened
        raise AssertionError("expected noncanonical evidence_source exported_at to be rejected")

    assert not (output_dir / "runtime_safety_gate.json").exists()
    assert not output_dir.exists()


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
    manifest = _passing_manifest()
    manifest["evidence_source"] = {"type": " paper_runtime_logs ", "run_id": "runtime-1"}
    manifest["events"][2]["event_type"] = "fail_closed"
    del manifest["events"][2]["type"]
    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert "evidence_source type must be canonical" in str(exc)
    else:
        raise AssertionError("expected padded evidence_source type to be rejected")

def test_runtime_safety_gate_rejects_padded_event_type() -> None:
    manifest = _passing_manifest()
    manifest["events"] = [
        {"event_type": " kill_switch_dry_run ", "passed": True},
    ]
    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert "runtime safety event type must be canonical" in str(exc)
    else:
        raise AssertionError("expected padded runtime safety event type to be rejected")


def test_runtime_safety_gate_rejects_unknown_reason_code() -> None:
    manifest = _passing_manifest()
    manifest["reasons"] = [
        {
            "code": "unknown_preview_reason",
            "severity": "block",
            "category": "execution_preview",
            "source": "execution_preview",
        }
    ]

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "unknown runtime safety reason code: unknown_preview_reason"
    else:
        raise AssertionError("expected unknown runtime safety reason code to be rejected")


def test_runtime_safety_gate_rejects_duplicate_reason_conflict() -> None:
    manifest = _passing_manifest()
    manifest["reasons"] = [
        {
            "code": "symbol_not_allowed",
            "severity": "block",
            "category": "execution_preview",
            "source": "execution_preview",
        },
        {
            "code": "symbol_not_allowed",
            "severity": "warn",
            "category": "execution_preview",
            "source": "execution_preview",
        },
    ]

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "runtime safety reason duplicate conflicts for code: symbol_not_allowed"
    else:
        raise AssertionError("expected duplicate runtime safety reason conflict to be rejected")


def test_runtime_safety_gate_rejects_bool_reason_code() -> None:
    manifest = _passing_manifest()
    manifest["reasons"] = [
        {
            "code": True,
            "severity": "block",
            "category": "execution_preview",
            "source": "execution_preview",
        }
    ]

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "runtime safety reason code must be a string"
    else:
        raise AssertionError("expected bool runtime safety reason code to be rejected")


def test_runtime_safety_gate_rejects_missing_reason_source() -> None:
    manifest = _passing_manifest()
    manifest["reasons"] = [
        {
            "code": "symbol_not_allowed",
            "severity": "block",
            "category": "execution_preview",
        }
    ]

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "runtime safety reason source must be present"
    else:
        raise AssertionError("expected missing runtime safety reason source to be rejected")


def test_runtime_safety_gate_accepts_execution_preview_reason_codes() -> None:
    manifest = _passing_manifest()
    manifest["reasons"] = [
        {
            "code": "symbol_not_allowed",
            "severity": "block",
            "category": "execution_preview",
            "source": "execution_preview",
        },
        {
            "code": "missing_exchange_metadata",
            "severity": "block",
            "category": "execution_preview",
            "source": "execution_preview",
        },
    ]

    gate = build_runtime_safety_gate(manifest)

    assert gate["summary"]["reasons_by_code"] == {
        "symbol_not_allowed": 1,
        "missing_exchange_metadata": 1,
    }
    assert gate["summary"]["reason_count"] == 2
    assert gate["runtime_reasons"] == [
        {
            "code": "symbol_not_allowed",
            "severity": "block",
            "category": "execution_preview",
            "source": "execution_preview",
        },
        {
            "code": "missing_exchange_metadata",
            "severity": "block",
            "category": "execution_preview",
            "source": "execution_preview",
        },
    ]


def test_runtime_safety_gate_writer_preserves_runtime_reason_taxonomy_roundtrip(tmp_path: Path) -> None:
    manifest = _passing_manifest()
    manifest["reasons"] = [
        {
            "code": "symbol_not_allowed",
            "severity": "block",
            "category": "execution_preview",
            "source": "execution_preview",
        },
        {
            "code": "runtime_fail_closed_missing",
            "severity": "block",
            "category": "runtime_safety",
            "source": "runtime_safety_gate",
        },
    ]

    output = write_runtime_safety_gate(manifest, tmp_path)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["runtime_reasons"] == manifest["reasons"]
    assert RuntimeSafetyReason.canonicalize_many(payload["runtime_reasons"]) == [
        RuntimeSafetyReason(
            code="symbol_not_allowed",
            severity="block",
            category="execution_preview",
            source="execution_preview",
        ),
        RuntimeSafetyReason(
            code="runtime_fail_closed_missing",
            severity="block",
            category="runtime_safety",
            source="runtime_safety_gate",
        ),
    ]
    assert payload["summary"]["reason_count"] == 2
    assert payload["summary"]["reasons_by_code"] == {
        "symbol_not_allowed": 1,
        "runtime_fail_closed_missing": 1,
    }


def test_runtime_safety_gate_reports_failed_hard_kill_switch_evidence() -> None:
    manifest = _passing_manifest()
    manifest["kill_switch_decision"]["decision"] = "kill"
    manifest["kill_switch_decision"]["evidence"]["market_data"]["ok"] = False
    manifest["kill_switch_decision"]["evidence"]["max_notional"]["ok"] = False
    manifest["kill_switch_decision"]["evidence"]["exchange_account_state"]["ok"] = False

    gate = build_runtime_safety_gate(manifest)

    assert gate["checks"]["market_data_fresh_met"] is False
    assert gate["checks"]["max_notional_within_limit_met"] is False
    assert gate["checks"]["exchange_account_state_unambiguous_met"] is False
    assert "market_data_stale" in gate["reasons"]
    assert "max_notional_exceeded" in gate["reasons"]
    assert "exchange_account_state_ambiguous" in gate["reasons"]
    assert gate["kill_switch_decision"] == manifest["kill_switch_decision"]


def test_runtime_safety_gate_rejects_missing_hard_kill_switch_decision() -> None:
    manifest = _passing_manifest()
    del manifest["kill_switch_decision"]

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "kill_switch_decision must be present"
    else:
        raise AssertionError("expected missing kill_switch_decision to be rejected")


def test_runtime_safety_gate_rejects_stale_market_data_evidence() -> None:
    manifest = _passing_manifest()
    manifest["kill_switch_decision"]["evidence"]["market_data"]["age_seconds"] = 121

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "kill_switch_decision evidence market_data is stale"
    else:
        raise AssertionError("expected stale market data evidence to be rejected")


def test_runtime_safety_gate_rejects_future_account_snapshot_evidence() -> None:
    manifest = _passing_manifest()
    manifest["kill_switch_decision"]["evidence"]["account_snapshot"]["observed_at"] = "2026-05-16T10:00:31Z"

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "kill_switch_decision evidence account_snapshot observed_at must not be in the future"
    else:
        raise AssertionError("expected future account snapshot evidence to be rejected")


def test_runtime_safety_gate_rejects_noncanonical_kill_switch_timestamp() -> None:
    manifest = _passing_manifest()
    manifest["kill_switch_decision"]["evaluated_at"] = "2026-05-16T10:00:30+00:00"

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "kill_switch_decision evaluated_at must be a canonical UTC timestamp"
    else:
        raise AssertionError("expected noncanonical kill-switch timestamp to be rejected")


def test_runtime_safety_gate_rejects_bool_and_nonfinite_numeric_kill_switch_evidence() -> None:
    manifest = _passing_manifest()
    manifest["kill_switch_decision"]["evidence"]["clock_skew"]["skew_seconds"] = math.inf

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "kill_switch_decision evidence clock_skew skew_seconds must be finite"
    else:
        raise AssertionError("expected nonfinite clock skew evidence to be rejected")

    manifest = _passing_manifest()
    manifest["kill_switch_decision"]["evidence"]["max_order_count"]["value"] = True

    try:
        build_runtime_safety_gate(manifest)
    except ValueError as exc:
        assert str(exc) == "kill_switch_decision evidence max_order_count value must be numeric, not boolean"
    else:
        raise AssertionError("expected boolean max_order_count evidence to be rejected")


def test_runtime_safety_reason_taxonomy_rejects_wrong_source_for_code() -> None:
    try:
        RuntimeSafetyReason.canonicalize_many(
            [
                {
                    "code": "symbol_not_allowed",
                    "severity": "block",
                    "category": "execution_preview",
                    "source": "runtime_safety_gate",
                }
            ]
        )
    except ValueError as exc:
        assert str(exc) == "runtime safety reason taxonomy mismatch for code: symbol_not_allowed"
    else:
        raise AssertionError("expected runtime safety reason source taxonomy mismatch to be rejected")


def test_runtime_safety_gate_preserves_identical_duplicate_reason_counts() -> None:
    manifest = _passing_manifest()
    manifest["reasons"] = [
        {
            "code": "symbol_not_allowed",
            "severity": "block",
            "category": "execution_preview",
            "source": "execution_preview",
        },
        {
            "code": "symbol_not_allowed",
            "severity": "block",
            "category": "execution_preview",
            "source": "execution_preview",
        },
    ]

    gate = build_runtime_safety_gate(manifest)

    assert gate["summary"]["reason_count"] == 2
    assert gate["summary"]["reasons_by_code"] == {"symbol_not_allowed": 2}
    assert gate["runtime_reasons"] == manifest["reasons"]


def test_runtime_safety_reason_taxonomy_rejects_conflicting_duplicate_code() -> None:
    try:
        RuntimeSafetyReason.canonicalize_many(
            [
                {
                    "code": "symbol_not_allowed",
                    "severity": "block",
                    "category": "execution_preview",
                    "source": "execution_preview",
                },
                {
                    "code": "symbol_not_allowed",
                    "severity": "warn",
                    "category": "execution_preview",
                    "source": "execution_preview",
                },
            ]
        )
    except ValueError as exc:
        assert str(exc) == "runtime safety reason duplicate conflicts for code: symbol_not_allowed"
    else:
        raise AssertionError("expected conflicting duplicate reason code to be rejected")
