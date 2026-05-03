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
