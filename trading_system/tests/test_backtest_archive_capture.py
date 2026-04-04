from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from trading_system.app.runtime_paths import build_runtime_paths
from trading_system.app.backtest.archive.capture import capture_runtime_env, capture_runtime_envs, main


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _bundle_state_payload() -> dict:
    return {
        "execution_mode": "paper",
        "updated_at_bj": "2026-04-04T21:40:00+08:00",
        "latest_candidates": [],
        "latest_allocations": [],
        "paper_trading": {"mode": "paper", "emitted_count": 0},
    }


def _prepare_runtime_bucket(runtime_root: Path, *, runtime_env: str, snapshot_as_of: str, finished_at: str) -> Path:
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env=runtime_env)
    paths.bucket_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        paths.account_snapshot_file,
        {
            "as_of": snapshot_as_of,
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
        paths.market_context_file,
        {
            "as_of": snapshot_as_of,
            "schema_version": "v2",
            "symbols": {"BTCUSDT": {"4h": {"close": 65000.0}}},
        },
    )
    _write_json(
        paths.derivatives_snapshot_file,
        {
            "as_of": snapshot_as_of,
            "schema_version": "v2",
            "rows": [{"symbol": "BTCUSDT", "funding_rate": 0.0001}],
        },
    )
    _write_json(paths.state_file, _bundle_state_payload())
    _write_json(
        paths.latest_summary_file,
        {
            "status": "ok",
            "mode": "paper",
            "runtime_env": runtime_env,
            "runtime_root": str(runtime_root),
            "bucket_dir": str(paths.bucket_dir),
            "state_file": str(paths.state_file),
            "finished_at": finished_at,
            "state_written": True,
            "candidate_count": 0,
            "allocation_count": 0,
            "paper_trading": {"mode": "paper", "ledger_event_count": 0, "emitted_count": 0, "replayed_count": 0, "intents": []},
        },
    )
    return paths


def test_capture_runtime_env_archives_latest_runtime_bucket(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    paths = _prepare_runtime_bucket(
        runtime_root,
        runtime_env="prod",
        snapshot_as_of="2026-04-04T13:05:01.856498Z",
        finished_at="2026-04-04T13:05:09.875678Z",
    )

    captured = capture_runtime_env(runtime_root=runtime_root, mode="paper", runtime_env="prod")

    assert captured.status == "archived"
    assert captured.runtime_env == "prod"
    assert captured.archived_at == "2026-04-04T13:05:09.875678Z"
    assert captured.bundle_dir == paths.archive_runtime_bundles_dir / "2026-04-04T13-05-01.856498Z--paper-prod-2026-04-04t13-05-09-875678z"
    assert (captured.bundle_dir / "metadata.json").exists()


def test_capture_runtime_env_skips_when_same_finished_at_already_archived(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _prepare_runtime_bucket(
        runtime_root,
        runtime_env="prod",
        snapshot_as_of="2026-04-04T13:05:01.856498Z",
        finished_at="2026-04-04T13:05:09.875678Z",
    )

    first = capture_runtime_env(runtime_root=runtime_root, mode="paper", runtime_env="prod")
    second = capture_runtime_env(runtime_root=runtime_root, mode="paper", runtime_env="prod")

    assert first.status == "archived"
    assert second.status == "already_archived"
    assert second.bundle_dir == first.bundle_dir


def test_capture_runtime_env_reuses_exact_existing_bundle_dir_when_input_timestamps_differ(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="prod")
    paths.bucket_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        paths.account_snapshot_file,
        {
            "as_of": "2026-04-04T13:05:01.111111Z",
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
        paths.market_context_file,
        {
            "as_of": "2026-04-04T13:05:01.222222Z",
            "schema_version": "v2",
            "symbols": {"BTCUSDT": {"4h": {"close": 65000.0}}},
        },
    )
    _write_json(
        paths.derivatives_snapshot_file,
        {
            "as_of": "2026-04-04T13:05:01.333333Z",
            "schema_version": "v2",
            "rows": [{"symbol": "BTCUSDT", "funding_rate": 0.0001}],
        },
    )
    _write_json(paths.state_file, _bundle_state_payload())
    _write_json(
        paths.latest_summary_file,
        {
            "status": "ok",
            "mode": "paper",
            "runtime_env": "prod",
            "runtime_root": str(runtime_root),
            "bucket_dir": str(paths.bucket_dir),
            "state_file": str(paths.state_file),
            "finished_at": "2026-04-04T13:05:09.875678Z",
            "state_written": True,
            "candidate_count": 0,
            "allocation_count": 0,
            "paper_trading": {"mode": "paper", "ledger_event_count": 0, "emitted_count": 0, "replayed_count": 0, "intents": []},
        },
    )

    first = capture_runtime_env(runtime_root=runtime_root, mode="paper", runtime_env="prod")
    second = capture_runtime_env(runtime_root=runtime_root, mode="paper", runtime_env="prod")

    assert first.bundle_dir.name.startswith("2026-04-04T13-05-01.222222Z")
    assert second.status == "already_archived"
    assert second.bundle_dir == first.bundle_dir


def test_capture_runtime_envs_and_main_emit_structured_results(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runtime_root = tmp_path / "runtime"
    _prepare_runtime_bucket(
        runtime_root,
        runtime_env="prod",
        snapshot_as_of="2026-04-04T13:05:01.856498Z",
        finished_at="2026-04-04T13:05:09.875678Z",
    )
    _prepare_runtime_bucket(
        runtime_root,
        runtime_env="paper",
        snapshot_as_of="2026-04-04T13:05:01.884146Z",
        finished_at="2026-04-04T13:05:09.519283Z",
    )

    captured = capture_runtime_envs(runtime_root=runtime_root, mode="paper", runtime_envs=("prod", "paper"))

    assert [item.runtime_env for item in captured] == ["prod", "paper"]
    assert [item.status for item in captured] == ["archived", "archived"]

    exit_code = main([
        "--runtime-root",
        str(runtime_root),
        "--mode",
        "paper",
        "--runtime-env",
        "prod",
        "--runtime-env",
        "paper",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert [item["runtime_env"] for item in payload] == ["prod", "paper"]
    assert [item["status"] for item in payload] == ["already_archived", "already_archived"]


def test_python_m_capture_emits_clean_json_only(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _prepare_runtime_bucket(
        runtime_root,
        runtime_env="prod",
        snapshot_as_of="2026-04-04T13:05:01.856498Z",
        finished_at="2026-04-04T13:05:09.875678Z",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.archive.capture",
            "--runtime-root",
            str(runtime_root),
            "--mode",
            "paper",
            "--runtime-env",
            "prod",
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload[0]["runtime_env"] == "prod"
    assert payload[0]["status"] == "archived"
