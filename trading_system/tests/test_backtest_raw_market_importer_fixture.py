from __future__ import annotations

import json
from pathlib import Path

from trading_system.app.backtest.config import load_backtest_config
from trading_system.app.backtest.dataset import load_historical_dataset, split_rows_by_windows
from trading_system.app.backtest.engine import replay_snapshot
from trading_system.app.backtest.experiments import run_regime_predictive_power_experiment


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dataset_root_timestamp_summary(dataset_root: Path) -> dict[str, str | list[str] | None]:
    bundle_dirs = sorted(path for path in dataset_root.iterdir() if path.is_dir())
    bundle_timestamps = [_load_json(path / "metadata.json")["timestamp"] for path in bundle_dirs]
    return {
        "bundle_timestamps": bundle_timestamps,
        "start_timestamp": bundle_timestamps[0] if bundle_timestamps else None,
        "end_timestamp": bundle_timestamps[-1] if bundle_timestamps else None,
    }


def _imported_dataset_root_identity_manifest(
    fixture_dir: Path,
    dataset_root: Path,
    rows: list,
) -> dict[str, int | list[str] | str]:
    archive_runtime_root = (fixture_dir / "archive_runtime").resolve()
    importer_manifest = _load_json(archive_runtime_root / "raw_market" / "importer_manifest.json")

    return {
        "snapshot_count": len(rows),
        "symbols": sorted({symbol for row in rows for symbol in row.market["symbols"]}),
        "archive_root": dataset_root.resolve().relative_to(archive_runtime_root).as_posix(),
        "source": importer_manifest["source"],
    }


def test_raw_market_importer_manifest_is_binance_first_and_futures_first(fixture_dir: Path) -> None:
    manifest_path = fixture_dir / "archive_runtime" / "raw_market" / "importer_manifest.json"

    assert manifest_path.exists()
    manifest = _load_json(manifest_path)

    assert manifest["source"] == "binance"
    assert manifest["selection_policy"] == "binance-first"
    assert manifest["market"] == "futures"
    assert manifest["priority"] == "futures-first"
    assert manifest["coverage_policy"] == "coverage-driven"
    assert manifest["datasets"] == ["ohlcv", "funding", "open_interest"]


def test_raw_market_importer_manifest_maps_fixture_files_to_canonical_archive_paths(fixture_dir: Path) -> None:
    raw_market_root = fixture_dir / "archive_runtime" / "raw_market"
    manifest_path = raw_market_root / "importer_manifest.json"

    assert manifest_path.exists()
    manifest = _load_json(manifest_path)
    coverage = manifest["coverage"]
    imports = manifest["imports"]

    assert coverage["required_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert coverage["required_datasets"] == ["ohlcv", "funding", "open_interest"]
    assert coverage["required_intervals"] == {"ohlcv": ["1h"], "open_interest": ["5m"]}
    assert len(imports) == 6

    for item in imports:
        archive_path = item["archive_path"]
        archive_file = raw_market_root / "archive" / archive_path
        parts = Path(archive_path).parts

        assert parts[:2] == ("binance", "futures")
        assert parts[2] in {"ohlcv", "funding", "open_interest"}
        assert "runtime" not in parts
        assert "paper" not in parts
        assert archive_file.exists()


def test_raw_market_importer_phase1_assembly_expectations_match_dataset_bundle(fixture_dir: Path) -> None:
    archive_runtime_root = fixture_dir / "archive_runtime"
    raw_market_root = archive_runtime_root / "raw_market"
    manifest = _load_json(raw_market_root / "importer_manifest.json")
    expectations = _load_json(archive_runtime_root / "assembly_expectations.json")
    coverage = manifest["coverage"]

    bundle_dir = archive_runtime_root / "archive_dataset" / expectations["bundle"]
    metadata = _load_json(bundle_dir / "metadata.json")
    market_context = _load_json(bundle_dir / "market_context.json")
    derivatives_rows = {
        row["symbol"]: row for row in _load_json(bundle_dir / "derivatives_snapshot.json")["rows"]
    }

    imports_by_key = {
        (item["dataset"], item["symbol"]): _load_json(raw_market_root / "archive" / item["archive_path"])
        for item in manifest["imports"]
    }

    assert metadata["timestamp"] == expectations["timestamp"]
    assert sorted(expectations["symbols"]) == coverage["required_symbols"]

    for symbol, expected in expectations["symbols"].items():
        ohlcv_payload = imports_by_key[("ohlcv", symbol)]
        funding_payload = imports_by_key[("funding", symbol)]
        open_interest_payload = imports_by_key[("open_interest", symbol)]
        market_daily = market_context["symbols"][symbol]["daily"]
        derivatives_row = derivatives_rows[symbol]

        assert ohlcv_payload["rows"][-1]["close"] == expected["market_close"]
        assert market_daily["close"] == expected["market_close"]
        assert funding_payload["rows"][-1]["funding_rate"] == expected["funding_rate"]
        assert derivatives_row["funding_rate"] == expected["funding_rate"]
        assert open_interest_payload["rows"][-1]["open_interest_usdt"] == expected["open_interest_usdt"]
        assert derivatives_row["open_interest_usdt"] == expected["open_interest_usdt"]


def test_raw_market_importer_phase1_bundle_timestamp_tracks_latest_open_interest_cutoff(
    fixture_dir: Path,
) -> None:
    archive_runtime_root = fixture_dir / "archive_runtime"
    raw_market_root = archive_runtime_root / "raw_market"
    expectations = _load_json(archive_runtime_root / "assembly_expectations.json")
    bundle_dir = archive_runtime_root / "archive_dataset" / expectations["bundle"]
    metadata = _load_json(bundle_dir / "metadata.json")
    market_context = _load_json(bundle_dir / "market_context.json")
    derivatives_snapshot = _load_json(bundle_dir / "derivatives_snapshot.json")
    manifest = _load_json(raw_market_root / "importer_manifest.json")

    ohlcv_timestamps = set()
    funding_timestamps = set()
    open_interest_timestamps = set()

    for item in manifest["imports"]:
        payload = _load_json(raw_market_root / "archive" / item["archive_path"])
        row = payload["rows"][-1]

        if item["dataset"] == "ohlcv":
            ohlcv_timestamps.add(row["open_time"])
        elif item["dataset"] == "funding":
            funding_timestamps.add(row["funding_time"])
        elif item["dataset"] == "open_interest":
            open_interest_timestamps.add(row["timestamp"])

    assert ohlcv_timestamps == {"2026-03-31T00:00:00Z"}
    assert funding_timestamps == {"2026-03-31T00:00:00Z"}
    assert open_interest_timestamps == {"2026-03-31T00:15:00Z"}
    assert metadata["timestamp"] == expectations["timestamp"] == "2026-03-31T00:15:00Z"
    assert market_context["as_of"] == metadata["timestamp"]
    assert derivatives_snapshot["as_of"] == metadata["timestamp"]
    assert metadata["timestamp"] not in ohlcv_timestamps
    assert metadata["timestamp"] not in funding_timestamps


def test_raw_market_importer_phase1_materializes_loader_valid_dataset_root_for_validation(fixture_dir: Path) -> None:
    config_path = fixture_dir / "archive_runtime" / "imported_dataset_backtest_config.json"

    config = load_backtest_config(config_path)
    rows = load_historical_dataset(config.dataset_root)
    split = split_rows_by_windows(rows, config.sample_windows)

    assert config.dataset_root == fixture_dir / "archive_runtime" / "archive_dataset"
    assert [row.run_id for row in rows] == ["paper-research-2026-03-31t00-15-00z"]
    assert rows[0].source_path == config.dataset_root / "2026-03-31T00-15-00Z"
    assert split["train"] == []
    assert [row.run_id for row in split["validation"]] == ["paper-research-2026-03-31t00-15-00z"]


def test_raw_market_importer_phase1_imported_dataset_root_preserves_timestamp_continuity(
    fixture_dir: Path,
) -> None:
    archive_runtime_root = fixture_dir / "archive_runtime"
    config = load_backtest_config(archive_runtime_root / "imported_dataset_backtest_config.json")
    rows = load_historical_dataset(config.dataset_root)
    timestamp_summary = _dataset_root_timestamp_summary(config.dataset_root)

    assert timestamp_summary == {
        "bundle_timestamps": ["2026-03-31T00:15:00Z"],
        "start_timestamp": "2026-03-31T00:15:00Z",
        "end_timestamp": "2026-03-31T00:15:00Z",
    }
    assert [row.timestamp.isoformat().replace("+00:00", "Z") for row in rows] == timestamp_summary["bundle_timestamps"]
    assert rows[0].source_path == config.dataset_root / "2026-03-31T00-15-00Z"
    assert _load_json(rows[0].source_path / "metadata.json")["timestamp"] == timestamp_summary["end_timestamp"]
    assert config.metadata["source_timestamp"] == timestamp_summary["end_timestamp"]


def test_raw_market_importer_phase1_bundle_is_replayable_by_core_without_download_enrichment(
    fixture_dir: Path,
) -> None:
    config_path = fixture_dir / "archive_runtime" / "imported_dataset_backtest_config.json"

    config = load_backtest_config(config_path)
    rows = load_historical_dataset(config.dataset_root)

    result = replay_snapshot(rows[0])

    assert result["regime"]["label"] == "RISK_OFF"
    assert result["suppression"]["rotation_suppressed"] is True
    assert result["universes"]["major_count"] == 2
    assert result["universes"]["rotation_count"] == 0
    assert result["universes"]["short_count"] == 2
    assert [row["symbol"] for row in result["universes"]["major_universe"]] == ["BTCUSDT", "ETHUSDT"]
    assert [row["symbol"] for row in result["universes"]["short_universe"]] == ["BTCUSDT", "ETHUSDT"]
    assert result["raw_candidates"] == {"trend": [], "rotation": [], "short": []}
    assert result["validated_candidates"] == []
    assert result["allocations"] == []


def test_raw_market_importer_phase1_bundle_exposes_forward_return_contract_for_experiments(
    fixture_dir: Path,
) -> None:
    config_path = fixture_dir / "archive_runtime" / "imported_dataset_backtest_config.json"

    config = load_backtest_config(config_path)
    rows = load_historical_dataset(config.dataset_root)
    result = run_regime_predictive_power_experiment(rows)

    assert [window.name for window in config.forward_return_windows] == ["1d", "3d"]
    assert rows[0].forward_returns == {"1d": 0.018, "3d": 0.031}
    assert rows[0].forward_drawdowns == {"1d": -0.009, "3d": -0.014}
    assert result["metadata"] == {"snapshot_count": 1, "regime_count": 1}
    assert result["by_regime"]["RISK_OFF"]["forward_return_by_window"] == {"1d": 0.018, "3d": 0.031}
    assert result["by_regime"]["RISK_OFF"]["forward_drawdown_by_window"] == {"1d": -0.009, "3d": -0.014}


def test_raw_market_importer_phase1_config_pins_archive_bundle_provenance_metadata(
    fixture_dir: Path,
) -> None:
    archive_runtime_root = fixture_dir / "archive_runtime"
    config_path = archive_runtime_root / "imported_dataset_backtest_config.json"

    config = load_backtest_config(config_path)
    bundle_metadata = _load_json(archive_runtime_root / "archive_dataset" / "2026-03-31T00-15-00Z" / "metadata.json")
    latest_summary = _load_json(archive_runtime_root / "runtime" / "paper" / "research" / "latest.json")

    assert config.metadata == {
        "phase": "importer_phase1",
        "source_bundle": "2026-03-31T00-15-00Z",
        "source_timestamp": bundle_metadata["timestamp"],
        "source_run_id": bundle_metadata["run_id"],
        "source_mode": bundle_metadata["source_mode"],
        "source_runtime_env": bundle_metadata["source_runtime_env"],
        "source_finished_at": latest_summary["finished_at"],
    }


def test_raw_market_importer_phase1_bundle_preserves_provenance_metadata_for_dataset_rows(
    fixture_dir: Path,
) -> None:
    archive_runtime_root = fixture_dir / "archive_runtime"
    config_path = archive_runtime_root / "imported_dataset_backtest_config.json"

    config = load_backtest_config(config_path)
    rows = load_historical_dataset(config.dataset_root)
    row = rows[0]
    bundle_metadata = _load_json(row.source_path / "metadata.json")
    latest_summary = _load_json(archive_runtime_root / "runtime" / "paper" / "research" / "latest.json")

    assert bundle_metadata["source_bundle"] == row.source_path.name
    assert bundle_metadata["source_finished_at"] == latest_summary["finished_at"]
    assert row.meta["source_bundle"] == row.source_path.name
    assert row.meta["source_finished_at"] == latest_summary["finished_at"]
    assert row.meta["source_mode"] == bundle_metadata["source_mode"]
    assert row.meta["source_runtime_env"] == bundle_metadata["source_runtime_env"]


def test_raw_market_importer_phase1_provenance_identity_stays_aligned_across_config_bundle_and_runtime(
    fixture_dir: Path,
) -> None:
    archive_runtime_root = fixture_dir / "archive_runtime"
    config_path = archive_runtime_root / "imported_dataset_backtest_config.json"

    config = load_backtest_config(config_path)
    rows = load_historical_dataset(config.dataset_root)
    row = rows[0]
    bundle_metadata = _load_json(row.source_path / "metadata.json")
    latest_summary = _load_json(archive_runtime_root / "runtime" / "paper" / "research" / "latest.json")

    expected_shared = {
        "source_bundle": row.source_path.name,
        "source_mode": latest_summary["mode"],
        "source_runtime_env": latest_summary["runtime_env"],
        "source_finished_at": latest_summary["finished_at"],
    }

    assert {key: bundle_metadata[key] for key in expected_shared} == expected_shared
    assert {key: row.meta[key] for key in expected_shared} == expected_shared
    assert {key: config.metadata[key] for key in expected_shared} == expected_shared
    assert config.metadata["source_run_id"] == row.run_id == bundle_metadata["run_id"]
    assert config.metadata["source_timestamp"] == bundle_metadata["timestamp"]


def test_raw_market_importer_phase1_runtime_summary_exposes_provenance_pointers(
    fixture_dir: Path,
) -> None:
    archive_runtime_root = fixture_dir / "archive_runtime"
    config_path = archive_runtime_root / "imported_dataset_backtest_config.json"

    config = load_backtest_config(config_path)
    rows = load_historical_dataset(config.dataset_root)
    row = rows[0]
    bundle_metadata = _load_json(row.source_path / "metadata.json")
    latest_summary = _load_json(archive_runtime_root / "runtime" / "paper" / "research" / "latest.json")

    expected_provenance = {
        "source_bundle": row.source_path.name,
        "source_run_id": row.run_id,
        "source_timestamp": bundle_metadata["timestamp"],
    }

    assert {key: latest_summary[key] for key in expected_provenance} == expected_provenance
    assert {key: config.metadata[key] for key in expected_provenance} == expected_provenance


def test_raw_market_importer_phase1_bundle_account_snapshot_preserves_provenance_metadata(
    fixture_dir: Path,
) -> None:
    archive_runtime_root = fixture_dir / "archive_runtime"
    config_path = archive_runtime_root / "imported_dataset_backtest_config.json"

    config = load_backtest_config(config_path)
    row = load_historical_dataset(config.dataset_root)[0]
    bundle_metadata = _load_json(row.source_path / "metadata.json")
    latest_summary = _load_json(archive_runtime_root / "runtime" / "paper" / "research" / "latest.json")

    expected_meta = {
        "account_type": "paper",
        "snapshot_source": "paper_runtime_fixture",
        "source_bundle": row.source_path.name,
        "source_run_id": row.run_id,
        "source_mode": config.metadata["source_mode"],
        "source_runtime_env": config.metadata["source_runtime_env"],
        "source_finished_at": latest_summary["finished_at"],
    }

    assert row.account["as_of"] == bundle_metadata["timestamp"] == latest_summary["finished_at"]
    assert row.account["meta"] == expected_meta


def test_raw_market_importer_phase1_imported_dataset_root_identity_manifest_stays_aligned_with_archive_inputs(
    fixture_dir: Path,
) -> None:
    archive_runtime_root = (fixture_dir / "archive_runtime").resolve()
    raw_config = _load_json(archive_runtime_root / "imported_dataset_backtest_config.json")
    config = load_backtest_config(archive_runtime_root / "imported_dataset_backtest_config.json")
    rows = load_historical_dataset(config.dataset_root)
    identity_manifest = _imported_dataset_root_identity_manifest(fixture_dir, config.dataset_root, rows)
    importer_manifest = _load_json(archive_runtime_root / "raw_market" / "importer_manifest.json")
    expectations = _load_json(archive_runtime_root / "assembly_expectations.json")
    bundle_metadata = _load_json(rows[0].source_path / "metadata.json")

    archive_sources = set()
    imported_symbols_by_dataset: dict[str, set[str]] = {}
    archive_roots = set()
    for item in importer_manifest["imports"]:
        imported_symbols_by_dataset.setdefault(item["dataset"], set()).add(item["symbol"])
        archive_payload = _load_json(archive_runtime_root / "raw_market" / "archive" / item["archive_path"])
        archive_sources.add(archive_payload["source"])
        archive_roots.add("/".join(Path(item["archive_path"]).parts[:2]))

    expected_manifest = {
        "snapshot_count": 1,
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "archive_root": "archive_dataset",
        "source": "binance",
    }
    dataset_root_bundle_count = len([path for path in config.dataset_root.iterdir() if path.is_dir()])
    loader_market_symbols = sorted(rows[0].market["symbols"])
    loader_derivatives_symbols = sorted(row["symbol"] for row in rows[0].derivatives)

    assert identity_manifest == expected_manifest
    assert identity_manifest["snapshot_count"] == len(rows) == dataset_root_bundle_count
    assert identity_manifest["archive_root"] == raw_config["dataset_root"]
    assert rows[0].source_path.name == raw_config["metadata"]["source_bundle"]
    assert identity_manifest["symbols"] == importer_manifest["coverage"]["required_symbols"]
    assert identity_manifest["symbols"] == sorted(expectations["symbols"])
    assert identity_manifest["symbols"] == loader_market_symbols == loader_derivatives_symbols
    assert all(sorted(symbols) == identity_manifest["symbols"] for symbols in imported_symbols_by_dataset.values())
    assert archive_sources == {identity_manifest["source"]}
    assert archive_roots == {f"{identity_manifest['source']}/futures"}
    assert rows[0].source_path.parent.resolve() == archive_runtime_root / identity_manifest["archive_root"]
    assert rows[0].source_path.name == bundle_metadata["source_bundle"]
