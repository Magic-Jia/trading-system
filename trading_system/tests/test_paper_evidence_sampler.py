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

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    paths.optimization_dir.mkdir(parents=True, exist_ok=True)
    independent_snapshot = paths.optimization_dir / "local_independent_source_snapshot.json"
    independent_snapshot.write_text(
        json.dumps({"schema_version": "local_independent_source_snapshot.v1", "source_id": "test", "observations": []}),
        encoding="utf-8",
    )

    def fake_run_cycle(mode, *, runtime_root=None, runtime_env=None):
        calls.append(("run_cycle", (mode, runtime_root, runtime_env, os.environ.get("TRADING_ENTRY_PROFILE"))))
        paths = build_runtime_paths(mode, runtime_root=runtime_root, runtime_env=runtime_env)
        paths.bucket_dir.mkdir(parents=True, exist_ok=True)
        (paths.bucket_dir / "execution_sample_collection_health.json").write_text(
            json.dumps({"sample_count": 3, "status": "available"}), encoding="utf-8"
        )
        (paths.bucket_dir / "account_snapshot.json").write_text(
            json.dumps({"as_of": "2026-05-19T02:00:05Z", "equity": 1000.0}), encoding="utf-8"
        )
        (paths.bucket_dir / "market_context.json").write_text(
            json.dumps({"as_of": "2026-05-19T02:00:04Z", "symbols": {"BTCUSDT": {"daily": {"close": 100.0}}}}),
            encoding="utf-8",
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
    assert calls[1][1]["independent_source_snapshot_file"] == independent_snapshot
    assert calls[2][1]["generated_at"] == "2026-05-19T02:00:05Z"
    assert calls[3][1]["generated_at"] == "2026-05-19T02:00:05Z"
    assert calls[4][1]["generated_at"] == "2026-05-19T02:00:05Z"


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


def test_paper_evidence_sampler_can_append_paper_cancel_lifecycle_sample(monkeypatch, tmp_path):
    from trading_system import paper_evidence_sampler as sampler
    from trading_system.app.execution.calibration import load_calibration_records

    calls: list[tuple[str, object]] = []
    runtime_root = tmp_path / "runtime"

    def forbid_run_cycle(*args, **kwargs):
        raise AssertionError("cancel lifecycle sampling must not run a scout execution cycle")

    monkeypatch.setattr(sampler.run_cycle_module, "run_cycle", forbid_run_cycle)
    monkeypatch.setattr(
        sampler,
        "bootstrap_live_sim_generation_inputs",
        lambda **kwargs: calls.append(("bootstrap", kwargs)) or {"status": "ok"},
    )
    monkeypatch.setattr(
        sampler.scheduled_live_sim_generation,
        "run_scheduled_generation",
        lambda **kwargs: calls.append(("scheduled", kwargs)) or {"status": "ok"},
    )
    monkeypatch.setattr(
        sampler.cadence_runner,
        "run_simulated_live_cadence",
        lambda **kwargs: calls.append(("cadence", kwargs)) or {"status": "completed", "decision": "reject"},
    )

    result = sampler.run_paper_evidence_sampler(
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-19T02:00:00Z",
        sample_cancel_lifecycle=True,
    )

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    records = load_calibration_records(paths.optimization_dir / "passive_order_calibration_records.jsonl")

    assert result["status"] == "completed"
    assert result["sample_action"] == "paper_cancel_lifecycle_sample_added"
    assert result["new_sample_count"] == 1
    assert result["calibration_status"] == "ok"
    assert [name for name, _ in calls] == ["bootstrap", "scheduled", "cadence"]
    assert len(records) == 1
    record = records[0]
    assert record.status == "cancelled"
    assert record.terminal_status == "cancelled"
    assert record.filled_qty == 0.0
    assert record.filled_notional is None
    assert record.first_fill_at is None
    assert record.last_fill_at is None
    assert record.cancel_requested_at.isoformat().replace("+00:00", "Z") == "2026-05-19T01:59:59.800000Z"
    assert record.cancel_ack_at.isoformat().replace("+00:00", "Z") == "2026-05-19T01:59:59.950000Z"
    assert record.cancel_latency_ms == pytest.approx(150.0)
    assert record.latency_ms == pytest.approx(100.0)


def test_paper_evidence_sampler_cancel_lifecycle_sample_is_idempotent(monkeypatch, tmp_path):
    from trading_system import paper_evidence_sampler as sampler
    from trading_system.app.execution.calibration import load_calibration_records

    runtime_root = tmp_path / "runtime"
    refresh_calls: list[str] = []
    monkeypatch.setattr(sampler.run_cycle_module, "run_cycle", lambda *args, **kwargs: pytest.fail("unexpected run_cycle"))
    monkeypatch.setattr(
        sampler,
        "bootstrap_live_sim_generation_inputs",
        lambda **kwargs: refresh_calls.append("bootstrap") or {"status": "ok"},
    )
    monkeypatch.setattr(
        sampler.scheduled_live_sim_generation,
        "run_scheduled_generation",
        lambda **kwargs: refresh_calls.append("scheduled") or {"status": "ok"},
    )
    monkeypatch.setattr(
        sampler.cadence_runner,
        "run_simulated_live_cadence",
        lambda **kwargs: refresh_calls.append("cadence") or {"status": "completed", "decision": "reject"},
    )

    first = sampler.run_paper_evidence_sampler(
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-19T02:00:00Z",
        sample_cancel_lifecycle=True,
    )
    second = sampler.run_paper_evidence_sampler(
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-19T02:00:00Z",
        sample_cancel_lifecycle=True,
    )

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    records = load_calibration_records(paths.optimization_dir / "passive_order_calibration_records.jsonl")
    assert first["sample_action"] == "paper_cancel_lifecycle_sample_added"
    assert second["sample_action"] == "paper_cancel_lifecycle_sample_already_present"
    assert second["new_sample_count"] == 0
    assert second["evidence_refresh_skipped_reason"] == "no_new_cancel_lifecycle_sample"
    assert refresh_calls == ["bootstrap", "scheduled", "cadence"]
    assert len(records) == 1


def test_paper_evidence_sampler_rejects_non_paper_mode(tmp_path):
    from trading_system import paper_evidence_sampler as sampler

    with pytest.raises(ValueError, match="paper-only"):
        sampler.run_paper_evidence_sampler(runtime_root=tmp_path / "runtime", runtime_env="paper", mode="live")
