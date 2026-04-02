from __future__ import annotations

import json
from pathlib import Path

from trading_system.app.backtest.engine import replay_snapshot
from trading_system.app.backtest.dataset import load_historical_dataset
from trading_system.app.runtime_paths import build_runtime_paths


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_archive_runtime_fixture_matches_runtime_paths_contract(fixture_dir: Path) -> None:
    runtime_root = fixture_dir / "archive_runtime" / "runtime"
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="research")

    assert paths.bucket_dir == runtime_root / "paper" / "research"
    assert paths.state_file.exists()
    assert paths.paper_ledger_file.exists()
    assert paths.execution_log_file.exists()
    assert paths.account_snapshot_file.exists()
    assert paths.market_context_file.exists()
    assert paths.derivatives_snapshot_file.exists()
    assert paths.latest_summary_file.exists()
    assert not paths.error_summary_file.exists()


def test_archive_runtime_fixture_snapshots_match_archive_bundle_shape(fixture_dir: Path) -> None:
    runtime_root = fixture_dir / "archive_runtime" / "runtime"
    archive_root = fixture_dir / "archive_runtime" / "archive_dataset"
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="research")

    rows = load_historical_dataset(archive_root)
    row = rows[0]
    latest_summary = _load_json(paths.latest_summary_file)
    bundle_metadata = _load_json(archive_root / "2026-03-31T00-15-00Z" / "metadata.json")

    assert [item.run_id for item in rows] == ["paper-research-2026-03-31t00-15-00z"]
    assert row.market == _load_json(paths.market_context_file)
    assert row.account == _load_json(paths.account_snapshot_file)
    assert row.derivatives == _load_json(paths.derivatives_snapshot_file)["rows"]
    assert bundle_metadata["timestamp"] == latest_summary["finished_at"]
    assert Path(latest_summary["bucket_dir"]).as_posix().endswith("archive_runtime/runtime/paper/research")
    assert Path(latest_summary["state_file"]).name == paths.state_file.name


def test_archive_runtime_fixture_runtime_state_tracks_replayed_regime_and_universes(
    fixture_dir: Path,
) -> None:
    runtime_root = fixture_dir / "archive_runtime" / "runtime"
    archive_root = fixture_dir / "archive_runtime" / "archive_dataset"
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="research")

    row = load_historical_dataset(archive_root)[0]
    replayed = replay_snapshot(row)
    runtime_state = _load_json(paths.state_file)

    expected_regime = {
        key: replayed["regime"][key]
        for key in ("label", "confidence", "risk_multiplier", "execution_policy", "bucket_targets", "suppression_rules")
    }

    assert runtime_state["execution_mode"] == "paper"
    assert {key: runtime_state["latest_regime"][key] for key in expected_regime} == expected_regime
    assert runtime_state["latest_universes"] == replayed["universes"]
