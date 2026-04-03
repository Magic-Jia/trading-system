from __future__ import annotations

import json
from pathlib import Path

from trading_system.app.backtest.engine import replay_snapshot
from trading_system.app.backtest.dataset import load_historical_dataset
from trading_system.app.runtime_paths import build_runtime_paths


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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


def test_archive_runtime_fixture_account_snapshot_meta_tracks_phase1_provenance(
    fixture_dir: Path,
) -> None:
    runtime_root = fixture_dir / "archive_runtime" / "runtime"
    archive_root = fixture_dir / "archive_runtime" / "archive_dataset"
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="research")

    row = load_historical_dataset(archive_root)[0]
    runtime_account = _load_json(paths.account_snapshot_file)
    latest_summary = _load_json(paths.latest_summary_file)
    runtime_state = _load_json(paths.state_file)
    paper_intent = runtime_state["paper_trading"]["intents"][0]

    expected_meta = {
        "account_type": "paper",
        "snapshot_source": "paper_runtime_fixture",
        "source_bundle": latest_summary["source_bundle"],
        "source_run_id": latest_summary["source_run_id"],
        "source_mode": latest_summary["mode"],
        "source_runtime_env": latest_summary["runtime_env"],
        "source_finished_at": latest_summary["finished_at"],
    }

    assert runtime_account == row.account
    assert runtime_account["as_of"] == latest_summary["finished_at"]
    assert runtime_account["meta"] == expected_meta
    assert runtime_account["open_positions"][0]["symbol"] == paper_intent["symbol"]


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


def test_archive_runtime_fixture_runtime_state_tracks_phase1_paper_execution_summary(
    fixture_dir: Path,
) -> None:
    runtime_root = fixture_dir / "archive_runtime" / "runtime"
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="research")

    runtime_state = _load_json(paths.state_file)
    latest_summary = _load_json(paths.latest_summary_file)

    assert runtime_state["latest_allocations"] == [
        {
            "engine": "trend",
            "status": "ACCEPTED",
            "final_risk_budget": 0.01,
            "rank": 1,
            "execution": {"status": "FILLED", "intent_id": "paper-intent-001"},
        }
    ]
    assert runtime_state["paper_trading"] == {
        "mode": "paper",
        "ledger_path": "trading_system/tests/fixtures/archive_runtime/runtime/paper/research/paper_ledger.jsonl",
        "ledger_event_count": 1,
        "emitted_count": 1,
        "replayed_count": 0,
        "intents": [
            {
                "symbol": "BTCUSDT",
                "status": "FILLED",
                "intent_id": "paper-intent-001",
            }
        ],
    }
    assert Path(runtime_state["paper_trading"]["ledger_path"]).as_posix().endswith(
        "archive_runtime/runtime/paper/research/paper_ledger.jsonl"
    )
    assert latest_summary["paper_trading"] == {
        "mode": "paper",
        "ledger_event_count": 1,
        "emitted_count": 1,
        "replayed_count": 0,
    }


def test_archive_runtime_fixture_summary_counts_match_runtime_state_and_jsonl_artifacts(
    fixture_dir: Path,
) -> None:
    runtime_root = fixture_dir / "archive_runtime" / "runtime"
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="research")

    runtime_state = _load_json(paths.state_file)
    latest_summary = _load_json(paths.latest_summary_file)
    execution_events = _load_jsonl(paths.execution_log_file)
    ledger_events = _load_jsonl(paths.paper_ledger_file)

    assert runtime_state["latest_candidates"] == [{"engine": "trend", "symbol": "BTCUSDT", "score": 0.91}]
    assert latest_summary["state_written"] is True
    assert latest_summary["candidate_count"] == len(runtime_state["latest_candidates"]) == 1
    assert latest_summary["allocation_count"] == len(runtime_state["latest_allocations"]) == 1
    assert runtime_state["paper_trading"]["emitted_count"] == len(execution_events) == 1
    assert runtime_state["paper_trading"]["ledger_event_count"] == len(ledger_events) == 1
    assert latest_summary["paper_trading"]["emitted_count"] == len(execution_events) == 1
    assert latest_summary["paper_trading"]["ledger_event_count"] == len(ledger_events) == 1
    assert runtime_state["paper_trading"]["replayed_count"] == latest_summary["paper_trading"]["replayed_count"] == 0


def test_archive_runtime_fixture_paper_execution_artifacts_share_phase1_intent_contract(
    fixture_dir: Path,
) -> None:
    runtime_root = fixture_dir / "archive_runtime" / "runtime"
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="research")

    runtime_state = _load_json(paths.state_file)
    latest_summary = _load_json(paths.latest_summary_file)
    execution_events = _load_jsonl(paths.execution_log_file)
    ledger_events = _load_jsonl(paths.paper_ledger_file)
    allocation_execution = runtime_state["latest_allocations"][0]["execution"]
    paper_intent = runtime_state["paper_trading"]["intents"][0]
    execution_event = execution_events[0]
    ledger_event = ledger_events[0]

    assert len(execution_events) == 1
    assert len(ledger_events) == runtime_state["paper_trading"]["ledger_event_count"]
    assert len(ledger_events) == latest_summary["paper_trading"]["ledger_event_count"]
    assert execution_event["order"]["intent_id"] == allocation_execution["intent_id"]
    assert execution_event["order"]["intent_id"] == paper_intent["intent_id"]
    assert execution_event["result"]["ledger_event"] == {
        "event_type": "paper_fill",
        "intent_id": paper_intent["intent_id"],
        "recorded_at_bj": ledger_event["recorded_at_bj"],
    }
    assert execution_event["result"]["result"] == paper_intent["status"]
    assert ledger_event["event_type"] == "paper_fill"
    assert ledger_event["intent_id"] == paper_intent["intent_id"]
    assert ledger_event["symbol"] == paper_intent["symbol"] == execution_event["order"]["symbol"]
    assert ledger_event["replay_result"] == {
        "status": paper_intent["status"],
        "intent_id": paper_intent["intent_id"],
    }
