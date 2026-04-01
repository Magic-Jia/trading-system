from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading_system import run_cycle as run_cycle_module
from trading_system.app.backtest.archive.runtime_bundle import (
    ARCHIVE_RUNTIME_BUNDLE_ENV,
    RuntimeBundleSourcePaths,
    archive_runtime_bundle,
)
from trading_system.app.runtime_paths import build_runtime_paths


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _bundle_state_payload() -> dict:
    return {
        "execution_mode": "paper",
        "updated_at_bj": "2026-04-01T09:00:00+08:00",
        "latest_candidates": [{"symbol": "BTCUSDT", "engine": "trend"}],
        "latest_allocations": [{"symbol": "BTCUSDT", "status": "ACCEPTED"}],
        "paper_trading": {"mode": "paper", "emitted_count": 1},
    }


def test_build_runtime_paths_exposes_archive_bundle_root(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="prod")

    assert paths.archive_root == tmp_path / "archive"
    assert paths.archive_runtime_bundles_dir == tmp_path / "archive" / "runtime-bundles" / "paper" / "prod"


def test_archive_runtime_bundle_copies_inputs_into_immutable_strategy_bundle(
    tmp_path: Path,
    account_snapshot_v2: dict,
    market_context_v2: dict,
    derivatives_snapshot_v2: dict,
) -> None:
    runtime_root = tmp_path / "runtime"
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="testnet")
    paths.bucket_dir.mkdir(parents=True, exist_ok=True)

    account_path = paths.bucket_dir / "account_snapshot.json"
    market_path = paths.bucket_dir / "market_context.json"
    derivatives_path = paths.bucket_dir / "derivatives_snapshot.json"
    state_path = paths.state_file

    _write_json(account_path, account_snapshot_v2)
    _write_json(market_path, market_context_v2)
    _write_json(derivatives_path, derivatives_snapshot_v2)
    _write_json(state_path, _bundle_state_payload())

    archived = archive_runtime_bundle(
        paths,
        RuntimeBundleSourcePaths(
            account_snapshot=account_path,
            market_context=market_path,
            derivatives_snapshot=derivatives_path,
            runtime_state=state_path,
        ),
        archived_at="2026-04-01T01:02:03Z",
    )

    expected_dir = paths.archive_runtime_bundles_dir / "2026-03-15T00-00-00Z--paper-testnet-2026-04-01t01-02-03z"
    metadata = json.loads((expected_dir / "metadata.json").read_text(encoding="utf-8"))

    assert archived.bundle_dir == expected_dir
    assert metadata["timestamp"] == "2026-03-15T00:00:00Z"
    assert metadata["run_id"] == "paper-testnet-2026-04-01t01-02-03z"
    assert metadata["mode"] == "paper"
    assert metadata["runtime_env"] == "testnet"
    assert metadata["source"]["bucket_dir"] == str(paths.bucket_dir)
    assert metadata["source"]["runtime_state"] == str(state_path)
    assert metadata["input_timestamps"] == {
        "account_as_of": "2026-03-15T00:00:00Z",
        "market_as_of": "2026-03-15T00:00:00Z",
        "derivatives_as_of": "2026-03-15T00:00:00Z",
        "runtime_state_updated_at_bj": "2026-04-01T09:00:00+08:00",
    }
    assert json.loads((expected_dir / "account_snapshot.json").read_text(encoding="utf-8")) == account_snapshot_v2
    assert json.loads((expected_dir / "market_context.json").read_text(encoding="utf-8")) == market_context_v2
    assert json.loads((expected_dir / "derivatives_snapshot.json").read_text(encoding="utf-8")) == derivatives_snapshot_v2
    assert json.loads((expected_dir / "runtime_state.json").read_text(encoding="utf-8"))["latest_candidates"] == [
        {"symbol": "BTCUSDT", "engine": "trend"}
    ]

    with pytest.raises(FileExistsError, match="immutable"):
        archive_runtime_bundle(
            paths,
            RuntimeBundleSourcePaths(
                account_snapshot=account_path,
                market_context=market_path,
                derivatives_snapshot=derivatives_path,
                runtime_state=state_path,
            ),
            archived_at="2026-04-01T01:02:03Z",
        )


def test_run_cycle_archive_hook_is_opt_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    archive_root = tmp_path / "archive"

    def fake_prepare(paths) -> None:
        _write_json(
            paths.bucket_dir / "account_snapshot.json",
            {
                "as_of": "2026-03-30T00:00:00Z",
                "schema_version": "v2",
                "equity": 100000.0,
                "available_balance": 100000.0,
                "futures_wallet_balance": 100000.0,
                "open_positions": [],
                "open_orders": [],
                "meta": {"account_type": "paper"},
            },
        )
        _write_json(
            paths.bucket_dir / "market_context.json",
            {
                "as_of": "2026-03-30T00:00:00Z",
                "schema_version": "v2",
                "symbols": {"BTCUSDT": {"4h": {"close": 64000.0}}},
            },
        )
        _write_json(
            paths.bucket_dir / "derivatives_snapshot.json",
            {
                "as_of": "2026-03-30T00:00:00Z",
                "schema_version": "v2",
                "rows": [{"symbol": "BTCUSDT", "funding_rate": 0.0001}],
            },
        )

    def fake_main() -> None:
        state_file = Path(os.environ["TRADING_STATE_FILE"])
        _write_json(state_file, _bundle_state_payload())

    monkeypatch.setattr(run_cycle_module, "prepare_paper_runtime_inputs", fake_prepare, raising=False)
    monkeypatch.setattr(run_cycle_module, "run_main", fake_main)

    summary_without_archive = run_cycle_module.run_cycle("paper", runtime_root=runtime_root, runtime_env="paper")

    assert "archive_bundle_dir" not in summary_without_archive
    assert not archive_root.exists()

    monkeypatch.setenv(ARCHIVE_RUNTIME_BUNDLE_ENV, "1")

    summary_with_archive = run_cycle_module.run_cycle("paper", runtime_root=runtime_root, runtime_env="paper")

    archive_bundle_dir = Path(summary_with_archive["archive_bundle_dir"])
    assert archive_bundle_dir.exists()
    assert archive_bundle_dir.parent == archive_root / "runtime-bundles" / "paper" / "paper"
    assert (archive_bundle_dir / "metadata.json").exists()
