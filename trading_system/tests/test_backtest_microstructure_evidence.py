from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_system.app.backtest.microstructure_evidence import (
    build_microstructure_gate,
    write_microstructure_gate,
)


def test_builds_synthetic_microstructure_gate_when_coverage_is_sufficient(tmp_path: Path) -> None:
    manifest = {
        "evidence_source": {"type": "synthetic_fixture", "label": "unit-test-only"},
        "coverage": {
            "l2_snapshot_coverage": 0.995,
            "l2_update_coverage": 0.992,
            "tick_coverage": 0.991,
        },
    }

    gate = build_microstructure_gate(manifest, min_coverage=0.99)

    assert gate["schema_version"] == "market_microstructure_gate_input.v1"
    assert gate["evidence_source"] == {"type": "synthetic_fixture", "label": "unit-test-only"}
    assert gate["checks"] == {
        "l2_tick_coverage_met": True,
        "depth_driven_taker_met": False,
    }
    assert gate["coverage"] == {
        "l2_snapshot_coverage": 0.995,
        "l2_update_coverage": 0.992,
        "tick_coverage": 0.991,
        "min_required_coverage": 0.99,
    }
    assert gate["reasons"] == ["depth_driven_taker_evidence_missing"]

    output_path = write_microstructure_gate(manifest, tmp_path, min_coverage=0.99)
    assert output_path == tmp_path / "market_microstructure_gate.json"
    assert json.loads(output_path.read_text()) == gate


def test_rejects_missing_or_low_l2_tick_coverage() -> None:
    gate = build_microstructure_gate(
        {
            "coverage": {
                "l2_snapshot_coverage": 0.995,
                "l2_update_coverage": 0.50,
            }
        },
        min_coverage=0.99,
    )

    assert gate["evidence_source"] == {"type": "synthetic_fixture"}
    assert gate["checks"]["l2_tick_coverage_met"] is False
    assert gate["checks"]["depth_driven_taker_met"] is False
    assert gate["coverage"]["tick_coverage"] is None
    assert "l2_tick_coverage_below_threshold" in gate["reasons"]
    assert "depth_driven_taker_evidence_missing" in gate["reasons"]


def test_rejects_invalid_coverage_values() -> None:
    with pytest.raises(ValueError, match="l2_snapshot_coverage"):
        build_microstructure_gate(
            {"coverage": {"l2_snapshot_coverage": 1.5, "l2_update_coverage": 1, "tick_coverage": 1}}
        )
