from __future__ import annotations

import json
import subprocess
import sys
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


def test_backtest_cli_runs_fixture_experiment(
    fixture_dir: Path,
    tmp_path: Path,
) -> None:
    config_path = fixture_dir / "backtest" / "minimal_config.json"
    output_dir = tmp_path / "research-output"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.cli",
            "run",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    bundle_dir = output_dir / "regime_research__current_policy__no_rotation_suppression"
    summary_path = bundle_dir / "summary.json"
    scorecard_path = bundle_dir / "scorecard.json"
    manifest_path = bundle_dir / "manifest.json"
    assert summary_path.exists()
    assert scorecard_path.exists()
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["experiment_kind"] == "regime_research"
    assert manifest["dataset_root"].endswith("sample_dataset")
    assert manifest["snapshot_count"] == 3

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["metadata"]["snapshot_count"] == 3
    assert summary["metadata"]["baseline_name"] == "current_policy"
    assert summary["metadata"]["variant_name"] == "no_rotation_suppression"


def test_backtest_cli_rejects_invalid_config(
    fixture_dir: Path,
    tmp_path: Path,
) -> None:
    invalid_config_path = tmp_path / "invalid_backtest_config.json"
    invalid_config_path.write_text(
        json.dumps({"experiment_kind": "regime_research"}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.cli",
            "run",
            "--config",
            str(invalid_config_path),
            "--output-dir",
            str(tmp_path / "unused"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "missing required field" in result.stderr
