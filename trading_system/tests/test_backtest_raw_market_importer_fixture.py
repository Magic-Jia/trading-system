from __future__ import annotations

import json
from pathlib import Path

from trading_system.app.backtest.config import load_backtest_config
from trading_system.app.backtest.dataset import load_historical_dataset, split_rows_by_windows
from trading_system.app.backtest.engine import replay_snapshot
from trading_system.app.backtest.experiments import run_regime_predictive_power_experiment


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
