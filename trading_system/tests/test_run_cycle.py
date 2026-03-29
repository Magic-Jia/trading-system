from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading_system import run_cycle as run_cycle_module


def test_run_cycle_prepares_runtime_bucket_calls_main_and_writes_latest_summary(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    expected_bucket = runtime_root / "paper" / "testnet"
    expected_state_file = expected_bucket / "runtime_state.json"
    captured: dict[str, Path | str] = {}

    def fake_main() -> None:
        captured["mode"] = os.environ["TRADING_EXECUTION_MODE"]
        captured["runtime_env"] = os.environ["TRADING_RUNTIME_ENV"]
        captured["state_file"] = Path(os.environ["TRADING_STATE_FILE"])
        captured["state_file"].write_text(
            json.dumps(
                {
                    "execution_mode": "paper",
                    "latest_candidates": [{"symbol": "BTCUSDT"}],
                    "latest_allocations": [{"symbol": "BTCUSDT", "status": "ACCEPTED"}],
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(run_cycle_module, "run_main", fake_main)

    summary = run_cycle_module.run_cycle("paper", runtime_root=runtime_root, runtime_env="testnet")

    latest_path = expected_bucket / "latest.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8"))

    assert expected_bucket.is_dir()
    assert latest_path.exists()
    assert not (expected_bucket / "error.json").exists()
    assert captured == {
        "mode": "paper",
        "runtime_env": "testnet",
        "state_file": expected_state_file,
    }
    assert summary == latest
    assert latest["status"] == "ok"
    assert latest["mode"] == "paper"
    assert latest["runtime_env"] == "testnet"
    assert latest["bucket_dir"] == str(expected_bucket)
    assert latest["state_file"] == str(expected_state_file)
    assert latest["execution_mode"] == "paper"
    assert latest["candidate_count"] == 1
    assert latest["allocation_count"] == 1
    assert "finished_at" in latest


def test_run_cycle_writes_error_summary_and_latest_on_failure(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    expected_bucket = runtime_root / "paper" / "prod"
    expected_state_file = expected_bucket / "runtime_state.json"

    def fake_main() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(run_cycle_module, "run_main", fake_main)

    with pytest.raises(RuntimeError, match="boom"):
        run_cycle_module.run_cycle("paper", runtime_root=runtime_root, runtime_env="prod")

    error_path = expected_bucket / "error.json"
    latest_path = expected_bucket / "latest.json"
    error_summary = json.loads(error_path.read_text(encoding="utf-8"))
    latest_summary = json.loads(latest_path.read_text(encoding="utf-8"))

    assert expected_bucket.is_dir()
    assert error_summary == latest_summary
    assert error_summary["status"] == "error"
    assert error_summary["mode"] == "paper"
    assert error_summary["runtime_env"] == "prod"
    assert error_summary["state_file"] == str(expected_state_file)
    assert error_summary["error_type"] == "RuntimeError"
    assert error_summary["error_message"] == "boom"
    assert "finished_at" in error_summary
