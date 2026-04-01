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
