from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading_system.app.runtime_paths import build_runtime_paths


def test_paper_evidence_sampler_runs_scout_cycle_and_refreshes_evidence(monkeypatch, tmp_path):
    from trading_system import paper_evidence_sampler as sampler

    calls: list[tuple[str, object]] = []
    runtime_root = tmp_path / "runtime"

    def fake_run_cycle(mode, *, runtime_root=None, runtime_env=None):
        calls.append(("run_cycle", (mode, runtime_root, runtime_env, os.environ.get("TRADING_ENTRY_PROFILE"))))
        paths = build_runtime_paths(mode, runtime_root=runtime_root, runtime_env=runtime_env)
        paths.bucket_dir.mkdir(parents=True, exist_ok=True)
        (paths.bucket_dir / "execution_sample_collection_health.json").write_text(
            json.dumps({"sample_count": 3, "status": "available"}), encoding="utf-8"
        )
        return {"status": "ok", "bucket_dir": str(paths.bucket_dir)}

    monkeypatch.setattr(sampler.run_cycle_module, "run_cycle", fake_run_cycle)
    monkeypatch.setattr(sampler, "generate_execution_calibration_records", lambda **kwargs: calls.append(("calibration", kwargs)) or {"status": "ok"})
    monkeypatch.setattr(sampler, "bootstrap_live_sim_generation_inputs", lambda **kwargs: calls.append(("bootstrap", kwargs)) or {"status": "ok"})
    monkeypatch.setattr(sampler.scheduled_live_sim_generation, "run_scheduled_generation", lambda **kwargs: calls.append(("scheduled", kwargs)) or {"status": "ok"})
    monkeypatch.setattr(sampler.cadence_runner, "run_simulated_live_cadence", lambda **kwargs: calls.append(("cadence", kwargs)) or {"status": "completed", "decision": "reject"})

    result = sampler.run_paper_evidence_sampler(
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-19T02:00:00Z",
    )

    assert result["status"] == "completed"
    assert result["mode"] == "paper"
    assert result["entry_profile"] == "scout"
    assert result["sample_count_after"] == 3
    assert calls[0] == ("run_cycle", ("paper", runtime_root, "paper", "scout"))
    assert [name for name, _ in calls[1:]] == ["calibration", "bootstrap", "scheduled", "cadence"]


def test_paper_evidence_sampler_skips_refresh_when_cooldown_adds_no_sample(monkeypatch, tmp_path):
    from trading_system import paper_evidence_sampler as sampler

    calls: list[str] = []
    runtime_root = tmp_path / "runtime"
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    paths.bucket_dir.mkdir(parents=True, exist_ok=True)
    (paths.bucket_dir / "execution_sample_collection_health.json").write_text(
        json.dumps({"sample_count": 2, "status": "available"}), encoding="utf-8"
    )

    def fake_run_cycle(mode, *, runtime_root=None, runtime_env=None):
        calls.append("run_cycle")
        (paths.bucket_dir / "execution_sample_collection_health.json").write_text(
            json.dumps({"sample_count": 2, "status": "available"}), encoding="utf-8"
        )
        return {"status": "ok", "bucket_dir": str(paths.bucket_dir)}

    monkeypatch.setattr(sampler.run_cycle_module, "run_cycle", fake_run_cycle)
    monkeypatch.setattr(sampler, "generate_execution_calibration_records", lambda **kwargs: calls.append("calibration"))

    result = sampler.run_paper_evidence_sampler(runtime_root=runtime_root, runtime_env="paper")

    assert result["status"] == "completed"
    assert result["sample_action"] == "no_new_sample"
    assert result["new_sample_count"] == 0
    assert calls == ["run_cycle"]


def test_paper_evidence_sampler_rejects_non_paper_mode(tmp_path):
    from trading_system import paper_evidence_sampler as sampler

    with pytest.raises(ValueError, match="paper-only"):
        sampler.run_paper_evidence_sampler(runtime_root=tmp_path / "runtime", runtime_env="paper", mode="live")
