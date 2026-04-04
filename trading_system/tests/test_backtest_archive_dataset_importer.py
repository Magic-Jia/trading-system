from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trading_system.app.backtest.archive.importer import (
    build_phase1_dataset_bundle_materials,
    import_phase1_archive_dataset_root,
    validate_phase1_imported_dataset_root,
    write_phase1_dataset_bundle,
)
from trading_system.app.backtest.archive.raw_market import archive_raw_market_payload, load_phase1_raw_market_imports
from trading_system.app.backtest.dataset import load_historical_dataset


def _timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _archive_phase1_symbol_history(archive_root: Path, *, symbol: str) -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    hourly_rows: list[dict[str, str | int]] = []
    funding_rows: list[dict[str, str | int]] = []
    open_interest_rows: list[dict[str, str | int]] = []

    for index in range(60 * 24):
        observed_at = start + timedelta(hours=index)
        close = 50_000.0 + (index * 10.0)
        volume = 1_000.0 + index
        hourly_rows.append(
            {
                "open_time": _timestamp_ms(observed_at),
                "open": f"{close - 5.0:.6f}",
                "high": f"{close + 20.0:.6f}",
                "low": f"{close - 20.0:.6f}",
                "close": f"{close:.6f}",
                "volume": f"{volume:.6f}",
                "quote_asset_volume": f"{close * volume:.6f}",
            }
        )
        open_interest_rows.append(
            {
                "timestamp": _timestamp_ms(observed_at),
                "sumOpenInterest": f"{10_000.0 + (index * 10.0):.6f}",
            }
        )
        if index % 8 == 0:
            funding_rows.append(
                {
                    "fundingTime": _timestamp_ms(observed_at),
                    "fundingRate": f"{0.0001 + ((index // 8) * 0.000001):.8f}",
                }
            )

    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol=symbol,
        timeframe="1h",
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=len(hourly_rows))).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:30:00Z",
        endpoint="/fapi/v1/klines",
        payload={"symbol": symbol, "interval": "1h", "rows": hourly_rows},
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="funding",
        symbol=symbol,
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=len(hourly_rows))).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:31:00Z",
        endpoint="/fapi/v1/fundingRate",
        payload=funding_rows,
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="open_interest",
        symbol=symbol,
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=len(hourly_rows))).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:32:00Z",
        endpoint="/futures/data/openInterestHist",
        payload=open_interest_rows,
    )


def test_build_phase1_dataset_bundle_materials_returns_dataset_ready_bundle_and_writes_dataset_root(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    total_hours = 60 * 24
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)

    materials = build_phase1_dataset_bundle_materials(imported)

    assert materials
    latest = materials[-1]
    assert latest.run_id.startswith("phase1-import-")
    assert latest.metadata["timestamp"] == latest.timestamp.isoformat().replace("+00:00", "Z")
    assert latest.metadata["source"]["series_keys"] == [
        "binance:futures:funding:BTCUSDT",
        "binance:futures:ohlcv:BTCUSDT:1h",
        "binance:futures:open-interest:BTCUSDT",
    ]

    market_symbol = latest.market_context["symbols"]["BTCUSDT"]
    assert latest.market_context["as_of"] == latest.metadata["timestamp"]
    assert latest.market_context["schema_version"] == "imported_market_context.v1"
    assert market_symbol["sector"] == "majors"
    assert market_symbol["liquidity_tier"] == "top"
    assert market_symbol["1h"]["close"] == pytest.approx(64_390.0)
    assert market_symbol["1h"]["volume_usdt_24h"] > 0.0
    assert market_symbol["4h"]["return_pct_3d"] > 0.0
    assert market_symbol["daily"]["return_pct_7d"] > 0.0

    assert latest.derivatives_snapshot["as_of"] == latest.metadata["timestamp"]
    assert latest.derivatives_snapshot["schema_version"] == "imported_derivatives_snapshot.v1"
    latest_close = 50_000.0 + ((total_hours - 1) * 10.0)
    close_24h_ago = 50_000.0 + ((total_hours - 25) * 10.0)
    latest_open_interest = 10_000.0 + ((total_hours - 1) * 10.0)
    open_interest_24h_ago = 10_000.0 + ((total_hours - 25) * 10.0)
    latest_funding = 0.0001 + (((total_hours - 1) // 8) * 0.000001)
    assert latest.derivatives_snapshot["rows"] == [
        {
            "symbol": "BTCUSDT",
            "funding_rate": pytest.approx(latest_funding),
            "open_interest_usdt": pytest.approx(latest_open_interest * latest_close),
            "open_interest_change_24h_pct": pytest.approx(
                (latest_open_interest / open_interest_24h_ago) - 1.0,
                rel=1e-6,
            ),
            "mark_price_change_24h_pct": pytest.approx((latest_close / close_24h_ago) - 1.0, rel=1e-6),
            "taker_buy_sell_ratio": 1.0,
            "basis_bps": 0.0,
        }
    ]

    bundle_dir = write_phase1_dataset_bundle(latest, dataset_root)
    rows = load_historical_dataset(dataset_root)

    assert bundle_dir == dataset_root / "2024-02-29T23-00-00Z__phase1-import-2024-02-29T23-00-00Z"
    assert len(rows) == 1
    assert rows[0].timestamp == latest.timestamp
    assert rows[0].market["symbols"]["BTCUSDT"]["1h"]["close"] == pytest.approx(64_390.0)
    assert rows[0].derivatives[0]["open_interest_usdt"] == pytest.approx(latest_open_interest * latest_close)


def test_build_phase1_dataset_bundle_materials_requires_complete_phase1_symbol_set(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    start = datetime(2024, 1, 1, tzinfo=UTC)
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=80)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:33:00Z",
        endpoint="/fapi/v1/klines",
        payload={"rows": [{"open_time": _timestamp_ms(start), "close": "50000.0"}]},
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="funding",
        symbol="BTCUSDT",
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=80)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:34:00Z",
        endpoint="/fapi/v1/fundingRate",
        payload=[{"fundingTime": _timestamp_ms(start), "fundingRate": "0.0001"}],
    )

    imported = load_phase1_raw_market_imports(archive_root)

    with pytest.raises(ValueError, match="missing required phase1 raw-market series for symbol BTCUSDT: open-interest"):
        build_phase1_dataset_bundle_materials(imported)


def test_import_phase1_archive_dataset_root_materializes_loadable_dataset_root(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported_root = import_phase1_archive_dataset_root(archive_root, dataset_root)
    rows = load_historical_dataset(dataset_root)
    manifest = json.loads((dataset_root / "import_manifest.json").read_text(encoding="utf-8"))

    assert imported_root.archive_root == archive_root
    assert imported_root.dataset_root == dataset_root
    assert imported_root.snapshot_count == len(rows)
    assert imported_root.snapshot_count > 0
    assert imported_root.symbols == ("BTCUSDT",)
    assert imported_root.bundle_dirs[0] == rows[0].source_path
    assert imported_root.bundle_dirs[-1] == rows[-1].source_path
    assert imported_root.start_timestamp == rows[0].timestamp
    assert imported_root.end_timestamp == rows[-1].timestamp
    assert manifest["schema_version"] == "phase1_imported_dataset_root.v1"
    assert manifest["archive_root"] == str(archive_root)
    assert manifest["dataset_root"] == str(dataset_root)
    assert manifest["snapshot_count"] == imported_root.snapshot_count
    assert manifest["symbols"] == ["BTCUSDT"]
    assert manifest["start_timestamp"] == rows[0].timestamp.isoformat().replace("+00:00", "Z")
    assert manifest["end_timestamp"] == rows[-1].timestamp.isoformat().replace("+00:00", "Z")
    assert manifest["bundle_dirs"][0] == str(rows[0].source_path)
    assert manifest["bundle_dirs"][-1] == str(rows[-1].source_path)
    assert manifest["source"]["scope"] == "phase1_binance_futures"
    assert len(manifest["source"]["manifest_paths"]) == 3
    validated_rows = validate_phase1_imported_dataset_root(dataset_root)
    assert [row.timestamp for row in validated_rows] == [row.timestamp for row in rows]


def test_validate_phase1_imported_dataset_root_rejects_manifest_schema_version_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "phase1_imported_dataset_root.v2"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported phase1 dataset root manifest schema"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_dataset_root_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset_root"] = str(tmp_path / "other-dataset")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="dataset root manifest dataset_root mismatch"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_snapshot_count_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot_count"] += 1
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest snapshot_count did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_scope_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scope"] = "phase1_okx_futures"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest scope is out of phase1 importer scope"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_symbols_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["symbols"] = ["ETHUSDT"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest symbols did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_archive_root_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_root"] = str(tmp_path / "other-archive")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest archive_root did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_source_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["series_keys"] = ["binance:futures:ohlcv:BTCUSDT:1h"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest source did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_missing_source_manifest(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest = json.loads((dataset_root / "import_manifest.json").read_text(encoding="utf-8"))
    source_manifest_path = Path(manifest["source"]["manifest_paths"][0])
    source_manifest_path.unlink()

    with pytest.raises(FileNotFoundError, match="raw-market manifest missing"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_out_of_scope_source_manifest(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest = json.loads((dataset_root / "import_manifest.json").read_text(encoding="utf-8"))
    source_manifest_path = Path(manifest["source"]["manifest_paths"][0])
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_manifest["market"] = "spot"
    source_manifest_path.write_text(json.dumps(source_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="only binance futures raw-market datasets are supported in phase 1"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_timestamp_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)

    metadata_path = bundle_dir / "metadata.json"
    metadata = metadata_path.read_text(encoding="utf-8")
    metadata_path.write_text(
        metadata.replace(
            '"timestamp": "2024-02-29T23:00:00Z"',
            '"timestamp": "2024-03-01T00:00:00Z"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="timestamps did not round-trip"):
        validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=(bundle_dir,),
            expected_timestamps=(material.timestamp,),
        )


def test_validate_phase1_imported_dataset_root_rejects_bundle_metadata_run_id_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)

    metadata_path = bundle_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["run_id"] = "phase1-import-2024-03-01T00-00-00Z"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="bundle metadata run_id did not round-trip"):
        validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=(bundle_dir,),
            expected_timestamps=(material.timestamp,),
        )


def test_validate_phase1_imported_dataset_root_rejects_bundle_metadata_schema_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)

    metadata_path = bundle_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["schema_version"] = "phase1_import_bundle.v2"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="bundle metadata schema_version is out of phase1 importer scope"):
        validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=(bundle_dir,),
            expected_timestamps=(material.timestamp,),
        )


def test_validate_phase1_imported_dataset_root_rejects_bundle_payload_schema_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)

    market_context_path = bundle_dir / "market_context.json"
    market_context = json.loads(market_context_path.read_text(encoding="utf-8"))
    market_context["schema_version"] = "imported_market_context.v2"
    market_context_path.write_text(json.dumps(market_context, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="bundle payload schema_version is out of phase1 importer scope"):
        validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=(bundle_dir,),
            expected_timestamps=(material.timestamp,),
        )


def test_validate_phase1_imported_dataset_root_rejects_bundle_payload_as_of_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)

    market_context_path = bundle_dir / "market_context.json"
    market_context = json.loads(market_context_path.read_text(encoding="utf-8"))
    market_context["as_of"] = "2024-03-01T00:00:00Z"
    market_context_path.write_text(json.dumps(market_context, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="bundle payload as_of did not round-trip"):
        validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=(bundle_dir,),
            expected_timestamps=(material.timestamp,),
        )


def test_validate_phase1_imported_dataset_root_rejects_bundle_dir_mismatch(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)
    renamed_bundle_dir = bundle_dir.with_name(f"{bundle_dir.name}__renamed")
    bundle_dir.rename(renamed_bundle_dir)

    with pytest.raises(ValueError, match="bundle directories did not round-trip"):
        validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=(bundle_dir,),
            expected_timestamps=(material.timestamp,),
        )


def test_validate_phase1_imported_dataset_root_rejects_bundle_dir_name_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle_dir = Path(manifest["bundle_dirs"][-1])
    renamed_bundle_dir = bundle_dir.with_name("renamed-bundle")
    bundle_dir.rename(renamed_bundle_dir)
    manifest["bundle_dirs"][-1] = str(renamed_bundle_dir)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="bundle directory name did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_bundle_timestamps_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_timestamps"][-1] = "2024-03-01T00:00:00Z"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest bundle_timestamps did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_start_timestamp_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["start_timestamp"] = "2024-03-01T00:00:00Z"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest start_timestamp did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_end_timestamp_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["end_timestamp"] = "2024-03-01T00:00:00Z"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest end_timestamp did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)
