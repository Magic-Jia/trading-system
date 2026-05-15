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
from trading_system.app.backtest.archive.types import RuntimeBundleMetadata
from trading_system.app.backtest.dataset import load_historical_dataset
from trading_system.app.backtest.engine import replay_snapshot
from trading_system.app.runtime_paths import RuntimePaths, build_runtime_paths


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _bundle_state_payload() -> dict:
    return {
        "execution_mode": "paper",
        "updated_at_bj": "2026-04-01T09:00:00+08:00",
        "latest_candidates": [{"symbol": "BTCUSDT", "engine": "trend"}],
        "latest_allocations": [{"symbol": "BTCUSDT", "status": "ACCEPTED"}],
        "paper_trading": {"mode": "paper", "emitted_count": 1},
    }


def _raw_market_provenance() -> dict:
    return {
        "source": "phase1_raw_market_archive",
        "archive_root": "/archive/phase1",
        "coverage_start": "2026-03-01T00:00:00Z",
        "coverage_end": "2026-03-15T00:00:00Z",
        "fetched_at": "2026-03-15T00:01:00Z",
    }


def _runtime_bundle_metadata(
    *,
    source: object | None = None,
    input_timestamps: object | None = None,
) -> RuntimeBundleMetadata:
    return RuntimeBundleMetadata(
        timestamp="2026-03-15T00:00:00Z",
        run_id="paper-testnet-2026-04-01t01-02-03z",
        archived_at="2026-04-01T01:02:03Z",
        schema_version="runtime_bundle.v1",
        bundle_kind="runtime_cycle",
        mode="paper",
        runtime_env="testnet",
        source=source
        if source is not None
        else {
            "bucket_dir": "/runtime/paper/testnet",
            "account_snapshot": "/runtime/paper/testnet/account_snapshot.json",
        },
        input_timestamps=input_timestamps
        if input_timestamps is not None
        else {
            "account_as_of": "2026-03-15T00:00:00Z",
            "runtime_state_updated_at_bj": "2026-04-01T09:00:00+08:00",
        },
    )


def _write_runtime_bundle_sources(
    paths,
    *,
    account_payload: dict,
    market_payload: dict,
    derivatives_payload: dict,
    state_payload: dict | None = None,
) -> RuntimeBundleSourcePaths:
    paths.bucket_dir.mkdir(parents=True, exist_ok=True)
    account_path = paths.bucket_dir / "account_snapshot.json"
    market_path = paths.bucket_dir / "market_context.json"
    derivatives_path = paths.bucket_dir / "derivatives_snapshot.json"
    state_path = paths.state_file
    _write_json(account_path, account_payload)
    _write_json(market_path, market_payload)
    _write_json(derivatives_path, derivatives_payload)
    _write_json(state_path, state_payload if state_payload is not None else _bundle_state_payload())
    return RuntimeBundleSourcePaths(
        account_snapshot=account_path,
        market_context=market_path,
        derivatives_snapshot=derivatives_path,
        runtime_state=state_path,
    )


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "expected_message"),
    [
        ("source", [("bucket_dir", "/runtime/paper/testnet")], "runtime bundle metadata source must be a mapping"),
        (
            "input_timestamps",
            [("account_as_of", "2026-03-15T00:00:00Z")],
            "runtime bundle metadata input_timestamps must be a mapping",
        ),
        ("source", {"bucket_dir": Path("/runtime/paper/testnet")}, "runtime bundle metadata source.bucket_dir must be a string"),
        (
            "input_timestamps",
            {"account_as_of": 123},
            "runtime bundle metadata input_timestamps.account_as_of must be a string",
        ),
    ],
)
def test_runtime_bundle_metadata_as_dict_rejects_non_canonical_dict_fields(
    field_name: str,
    invalid_value: object,
    expected_message: str,
) -> None:
    metadata = _runtime_bundle_metadata(**{field_name: invalid_value})

    with pytest.raises(ValueError, match=expected_message):
        metadata.as_dict()


def test_build_runtime_paths_exposes_archive_bundle_root(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="prod")

    assert paths.archive_root == tmp_path / "archive"
    assert paths.archive_runtime_bundles_dir == tmp_path / "archive" / "runtime-bundles" / "paper" / "prod"


@pytest.mark.parametrize(
    ("mode", "runtime_env", "expected_message"),
    [
        ("paper ", "testnet", "runtime bundle mode must already be canonical"),
        ("Paper", "testnet", "runtime bundle mode must already be canonical"),
        ("paper/live", "testnet", "runtime bundle mode must already be canonical"),
        ("paper", "test net", "runtime bundle runtime_env must already be canonical"),
        ("paper", "../prod", "runtime bundle runtime_env must already be canonical"),
    ],
)
def test_archive_runtime_bundle_rejects_non_canonical_runtime_path_identity_before_writing_metadata(
    tmp_path: Path,
    account_snapshot_v2: dict,
    market_context_v2: dict,
    derivatives_snapshot_v2: dict,
    mode: str,
    runtime_env: str,
    expected_message: str,
) -> None:
    canonical_paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="testnet")
    paths = RuntimePaths(
        mode=mode,
        runtime_env=runtime_env,
        runtime_root=canonical_paths.runtime_root,
        bucket_dir=canonical_paths.bucket_dir,
        archive_root=canonical_paths.archive_root,
        archive_runtime_bundles_dir=canonical_paths.archive_runtime_bundles_dir,
        optimization_dir=canonical_paths.optimization_dir,
        state_file=canonical_paths.state_file,
        paper_ledger_file=canonical_paths.paper_ledger_file,
        signal_facts_file=canonical_paths.signal_facts_file,
        trade_outcomes_file=canonical_paths.trade_outcomes_file,
        daily_metrics_file=canonical_paths.daily_metrics_file,
        health_report_file=canonical_paths.health_report_file,
        execution_log_file=canonical_paths.execution_log_file,
        account_snapshot_file=canonical_paths.account_snapshot_file,
        market_context_file=canonical_paths.market_context_file,
        derivatives_snapshot_file=canonical_paths.derivatives_snapshot_file,
        latest_summary_file=canonical_paths.latest_summary_file,
        error_summary_file=canonical_paths.error_summary_file,
    )
    source_paths = _write_runtime_bundle_sources(
        canonical_paths,
        account_payload=account_snapshot_v2,
        market_payload=market_context_v2,
        derivatives_payload=derivatives_snapshot_v2,
    )

    with pytest.raises(ValueError, match=expected_message):
        archive_runtime_bundle(paths, source_paths, archived_at="2026-04-01T01:02:03Z")

    assert not paths.archive_runtime_bundles_dir.exists()


@pytest.mark.parametrize(
    ("payload_name", "timestamp_value", "expected_message"),
    [
        ("market", " 2026-03-15T00:00:00Z ", "market_context.json as_of must be canonical UTC"),
        ("market", "2026-03-15T01:00:00+01:00", "market_context.json as_of must be canonical UTC"),
        ("market", "2026-03-15T00:00:00+00:00", "market_context.json as_of must be canonical UTC"),
        ("market", "2026-03-15T00:00:00", "market_context.json as_of must be canonical UTC"),
        ("account", "2026-03-15T00:00:00+00:00", "account_snapshot.json as_of must be canonical UTC"),
        ("derivatives", "2026-03-15T00:00:00+00:00", "derivatives_snapshot.json as_of must be canonical UTC"),
    ],
)
def test_archive_runtime_bundle_rejects_non_canonical_source_utc_timestamps_before_writing_metadata(
    tmp_path: Path,
    account_snapshot_v2: dict,
    market_context_v2: dict,
    derivatives_snapshot_v2: dict,
    payload_name: str,
    timestamp_value: str,
    expected_message: str,
) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="testnet")
    payloads = {
        "account": dict(account_snapshot_v2),
        "market": dict(market_context_v2),
        "derivatives": dict(derivatives_snapshot_v2),
    }
    payloads[payload_name]["as_of"] = timestamp_value
    source_paths = _write_runtime_bundle_sources(
        paths,
        account_payload=payloads["account"],
        market_payload=payloads["market"],
        derivatives_payload=payloads["derivatives"],
    )

    with pytest.raises(ValueError, match=expected_message):
        archive_runtime_bundle(paths, source_paths, archived_at="2026-04-01T01:02:03Z")

    assert not paths.archive_runtime_bundles_dir.exists()


@pytest.mark.parametrize(
    ("archived_at", "expected_message"),
    [
        (" 2026-04-01T01:02:03Z ", "archived_at must be canonical UTC"),
        ("2026-04-01T02:02:03+01:00", "archived_at must be canonical UTC"),
        ("2026-04-01T01:02:03+00:00", "archived_at must be canonical UTC"),
        ("2026-04-01T01:02:03", "archived_at must be canonical UTC"),
    ],
)
def test_archive_runtime_bundle_rejects_non_canonical_archived_at_before_writing_metadata(
    tmp_path: Path,
    account_snapshot_v2: dict,
    market_context_v2: dict,
    derivatives_snapshot_v2: dict,
    archived_at: str,
    expected_message: str,
) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="testnet")
    source_paths = _write_runtime_bundle_sources(
        paths,
        account_payload=account_snapshot_v2,
        market_payload=market_context_v2,
        derivatives_payload=derivatives_snapshot_v2,
    )

    with pytest.raises(ValueError, match=expected_message):
        archive_runtime_bundle(paths, source_paths, archived_at=archived_at)

    assert not paths.archive_runtime_bundles_dir.exists()


@pytest.mark.parametrize(
    ("field_name", "path_value", "expected_message"),
    [
        ("account_snapshot", Path("relative/account_snapshot.json"), "source account_snapshot must be an absolute local path"),
        ("market_context", Path("s3://bucket/market_context.json"), "source market_context must be an absolute local path"),
        ("derivatives_snapshot", Path("https://example.test/derivatives_snapshot.json"), "source derivatives_snapshot must be an absolute local path"),
        ("runtime_state", Path("runtime_state.json"), "source runtime_state must be an absolute local path"),
    ],
)
def test_archive_runtime_bundle_rejects_unsafe_source_path_metadata_before_writing_metadata(
    tmp_path: Path,
    account_snapshot_v2: dict,
    market_context_v2: dict,
    derivatives_snapshot_v2: dict,
    field_name: str,
    path_value: Path,
    expected_message: str,
) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="testnet")
    valid_source_paths = _write_runtime_bundle_sources(
        paths,
        account_payload=account_snapshot_v2,
        market_payload=market_context_v2,
        derivatives_payload=derivatives_snapshot_v2,
    )
    source_paths = RuntimeBundleSourcePaths(
        account_snapshot=path_value if field_name == "account_snapshot" else valid_source_paths.account_snapshot,
        market_context=path_value if field_name == "market_context" else valid_source_paths.market_context,
        derivatives_snapshot=path_value if field_name == "derivatives_snapshot" else valid_source_paths.derivatives_snapshot,
        runtime_state=path_value if field_name == "runtime_state" else valid_source_paths.runtime_state,
    )

    with pytest.raises(ValueError, match=expected_message):
        archive_runtime_bundle(paths, source_paths, archived_at="2026-04-01T01:02:03Z")

    assert not paths.archive_runtime_bundles_dir.exists()


def test_archive_runtime_bundle_rejects_duplicate_source_artifacts_before_writing_metadata(
    tmp_path: Path,
    account_snapshot_v2: dict,
    market_context_v2: dict,
    derivatives_snapshot_v2: dict,
) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="testnet")
    valid_source_paths = _write_runtime_bundle_sources(
        paths,
        account_payload=account_snapshot_v2,
        market_payload=market_context_v2,
        derivatives_payload=derivatives_snapshot_v2,
    )
    source_paths = RuntimeBundleSourcePaths(
        account_snapshot=valid_source_paths.account_snapshot,
        market_context=valid_source_paths.account_snapshot,
        derivatives_snapshot=valid_source_paths.derivatives_snapshot,
        runtime_state=valid_source_paths.runtime_state,
    )

    with pytest.raises(ValueError, match="source artifact paths must be unique"):
        archive_runtime_bundle(paths, source_paths, archived_at="2026-04-01T01:02:03Z")

    assert not paths.archive_runtime_bundles_dir.exists()


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


def test_archive_runtime_bundle_preserves_raw_market_provenance_in_metadata(
    tmp_path: Path,
    account_snapshot_v2: dict,
    market_context_v2: dict,
    derivatives_snapshot_v2: dict,
) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="testnet")
    raw_market = _raw_market_provenance()
    source_paths = _write_runtime_bundle_sources(
        paths,
        account_payload=account_snapshot_v2,
        market_payload={**market_context_v2, "raw_market": raw_market},
        derivatives_payload=derivatives_snapshot_v2,
    )

    archived = archive_runtime_bundle(paths, source_paths, archived_at="2026-04-01T01:02:03Z")

    metadata = _load_json(archived.bundle_dir / "metadata.json")
    assert metadata["raw_market"] == raw_market


def test_archive_runtime_bundle_rejects_raw_market_provenance_drift_before_writing_metadata(
    tmp_path: Path,
    account_snapshot_v2: dict,
    market_context_v2: dict,
    derivatives_snapshot_v2: dict,
) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="testnet")
    raw_market = _raw_market_provenance()
    source_paths = _write_runtime_bundle_sources(
        paths,
        account_payload={**account_snapshot_v2, "raw_market": raw_market},
        market_payload={**market_context_v2, "raw_market": {**raw_market, "archive_root": "/archive/other"}},
        derivatives_payload=derivatives_snapshot_v2,
    )

    with pytest.raises(ValueError, match="market_context.json raw_market provenance must match"):
        archive_runtime_bundle(paths, source_paths, archived_at="2026-04-01T01:02:03Z")

    assert not paths.archive_runtime_bundles_dir.exists()


@pytest.mark.parametrize(
    ("field_name", "field_value", "expected_message"),
    [
        ("source", "", r"runtime bundle metadata raw_market\.source must be canonical"),
        ("source", 1, r"runtime bundle metadata raw_market\.source must be a string"),
        ("archive_root", True, r"runtime bundle metadata raw_market\.archive_root must be a string"),
        (
            "coverage_start",
            "2026-03-01T01:00:00+01:00",
            r"runtime bundle metadata raw_market\.coverage_start must be a canonical UTC Z timestamp",
        ),
        (
            "fetched_at",
            False,
            r"runtime bundle metadata raw_market\.fetched_at must be a string",
        ),
    ],
)
def test_archive_runtime_bundle_rejects_ambiguous_raw_market_provenance_before_writing_metadata(
    tmp_path: Path,
    account_snapshot_v2: dict,
    market_context_v2: dict,
    derivatives_snapshot_v2: dict,
    field_name: str,
    field_value: object,
    expected_message: str,
) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="testnet")
    raw_market = {**_raw_market_provenance(), field_name: field_value}
    source_paths = _write_runtime_bundle_sources(
        paths,
        account_payload=account_snapshot_v2,
        market_payload={**market_context_v2, "raw_market": raw_market},
        derivatives_payload=derivatives_snapshot_v2,
    )

    with pytest.raises(ValueError, match=expected_message):
        archive_runtime_bundle(paths, source_paths, archived_at="2026-04-01T01:02:03Z")

    assert not paths.archive_runtime_bundles_dir.exists()


@pytest.mark.parametrize(
    ("payload_name", "field_name", "expected_message"),
    [
        ("account", "as_of", "account_snapshot.json as_of must be a string"),
        ("market", "as_of", "market_context.json as_of must be a string"),
        ("derivatives", "as_of", "derivatives_snapshot.json as_of must be a string"),
        ("state", "updated_at_bj", "runtime_state.json updated_at_bj must be a string"),
    ],
)
def test_archive_runtime_bundle_rejects_present_non_string_input_timestamps_before_writing_metadata(
    tmp_path: Path,
    account_snapshot_v2: dict,
    market_context_v2: dict,
    derivatives_snapshot_v2: dict,
    payload_name: str,
    field_name: str,
    expected_message: str,
) -> None:
    runtime_root = tmp_path / "runtime"
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="testnet")
    paths.bucket_dir.mkdir(parents=True, exist_ok=True)

    payloads = {
        "account": dict(account_snapshot_v2),
        "market": dict(market_context_v2),
        "derivatives": dict(derivatives_snapshot_v2),
        "state": _bundle_state_payload(),
    }
    payloads[payload_name][field_name] = 123

    account_path = paths.bucket_dir / "account_snapshot.json"
    market_path = paths.bucket_dir / "market_context.json"
    derivatives_path = paths.bucket_dir / "derivatives_snapshot.json"
    state_path = paths.state_file

    _write_json(account_path, payloads["account"])
    _write_json(market_path, payloads["market"])
    _write_json(derivatives_path, payloads["derivatives"])
    _write_json(state_path, payloads["state"])

    with pytest.raises(ValueError, match=expected_message):
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

    assert not paths.archive_runtime_bundles_dir.exists()


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


def test_archive_runtime_fixture_universe_liquidity_meta_matches_archived_snapshot_inputs(
    fixture_dir: Path,
) -> None:
    runtime_root = fixture_dir / "archive_runtime" / "runtime"
    archive_root = fixture_dir / "archive_runtime" / "archive_dataset"
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="research")

    row = load_historical_dataset(archive_root)[0]
    runtime_state = _load_json(paths.state_file)
    derivatives_by_symbol = {entry["symbol"]: entry for entry in row.derivatives}

    for universe_name in ("major_universe", "short_universe"):
        for universe_row in runtime_state["latest_universes"][universe_name]:
            symbol = universe_row["symbol"]
            market_symbol = row.market["symbols"][symbol]
            derivatives_row = derivatives_by_symbol[symbol]
            liquidity_meta = universe_row["liquidity_meta"]
            expected_spot_volume = market_symbol["daily"]["volume_usdt_24h"]

            assert liquidity_meta["rolling_notional"] == expected_spot_volume
            assert liquidity_meta["depth_proxy_notional"] == expected_spot_volume * 0.2
            assert liquidity_meta["slippage_bps"] == 2.0
            assert liquidity_meta["listing_age_days"] == 3650.0
            assert liquidity_meta["rolling_notional_ok"] is True
            assert liquidity_meta["depth_proxy_ok"] is True
            assert liquidity_meta["slippage_ok"] is True
            assert liquidity_meta["listing_age_ok"] is True
            assert liquidity_meta["wick_risk_ok"] is True
            assert liquidity_meta["spot_volume_usdt_24h"] == expected_spot_volume
            assert liquidity_meta["open_interest_usdt"] == derivatives_row["open_interest_usdt"]
            assert liquidity_meta["liquidity_source"] == "volume_usdt_24h"

    assert runtime_state["latest_universes"]["rotation_universe"] == []


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
