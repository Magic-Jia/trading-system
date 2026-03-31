from __future__ import annotations

from pathlib import Path

from trading_system.app.backtest.dataset import load_historical_dataset
from trading_system.app.backtest.engine import replay_snapshot


def test_replay_snapshot_records_layer_artifacts(fixture_dir: Path) -> None:
    rows = load_historical_dataset(fixture_dir / "backtest" / "sample_dataset")

    result = replay_snapshot(rows[0])

    assert result["regime"]["label"].startswith("RISK_")
    assert "rotation_suppressed" in result["suppression"]
    assert result["universes"]["rotation_count"] >= 0
    assert set(result["raw_candidates"]) == {"trend", "rotation", "short"}
    assert isinstance(result["validated_candidates"], list)
    assert isinstance(result["allocations"], list)
    assert result["execution_assumptions"]["fee_bps"] == 0.0
