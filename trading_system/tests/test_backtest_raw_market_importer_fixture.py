from __future__ import annotations

import json
from pathlib import Path


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
