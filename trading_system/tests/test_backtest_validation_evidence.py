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
            {"name": "trend", "net_pnl": 40.0, "trade_count": 20},
            {"name": "chop", "net_pnl": 10.0, "trade_count": 12},
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
            "regimes": [{"name": "trend", "net_pnl": 5.0, "trade_count": 20}],
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
