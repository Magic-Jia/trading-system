from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .importer import (
    build_phase1_dataset_bundle_materials,
    validate_phase1_imported_dataset_root,
    write_phase1_dataset_bundle,
    write_phase1_dataset_root_manifest,
)
from .raw_market import load_phase1_raw_market_imports_from_manifest_paths

_OHLCV_TIMEFRAMES = ("1h", "1m", "5m", "15m", "30m")
_OPTIONAL_CONTEXT_DATASETS = {"funding", "mark-price", "open-interest"}
_EXECUTION_DATASETS = {"order-book", "trades"}


def _read_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"raw-market manifest must contain a JSON object: {path}")
    return payload


def _archive_root_from_input(path: str | Path) -> Path:
    current = Path(path)
    parts = current.parts
    if "raw-market" not in parts:
        return current
    raw_index = parts.index("raw-market")
    if raw_index == 0:
        return Path(".")
    return Path(*parts[:raw_index])


def _manifest_is_selected(
    manifest: Mapping[str, Any],
    *,
    symbols: set[str] | None,
) -> bool:
    if str(manifest.get("exchange") or "") != "binance" or str(manifest.get("market") or "") != "futures":
        return False
    symbol = str(manifest.get("symbol") or "").upper()
    if symbols is not None and symbol not in symbols:
        return False
    dataset = str(manifest.get("dataset") or "")
    if dataset == "ohlcv":
        return str(manifest.get("timeframe") or "") in _OHLCV_TIMEFRAMES
    return dataset in _OPTIONAL_CONTEXT_DATASETS or dataset in _EXECUTION_DATASETS


def _selected_manifest_paths(
    archive_root: Path,
    *,
    symbols: Sequence[str] | None,
) -> tuple[Path, ...]:
    raw_market_root = archive_root / "raw-market"
    selected_symbols = {symbol.upper() for symbol in symbols} if symbols else None
    manifest_paths: list[Path] = []
    for manifest_path in sorted(raw_market_root.rglob("*.manifest.json")):
        manifest = _read_manifest(manifest_path)
        if _manifest_is_selected(manifest, symbols=selected_symbols):
            manifest_paths.append(manifest_path)
    return tuple(manifest_paths)


def _materialize_dataset_root(
    *,
    archive_root: Path,
    dataset_root: Path,
    materials: Sequence[Any],
) -> dict[str, Any]:
    if dataset_root.exists():
        if not dataset_root.is_dir():
            raise NotADirectoryError(f"dataset root is not a directory: {dataset_root}")
        if any(dataset_root.iterdir()):
            raise FileExistsError(f"dataset root must be empty before materialization: {dataset_root}")
    else:
        dataset_root.mkdir(parents=True, exist_ok=True)

    try:
        bundle_dirs = tuple(write_phase1_dataset_bundle(material, dataset_root) for material in materials)
        symbols = tuple(
            sorted(
                {
                    str(symbol)
                    for material in materials
                    for symbol in dict(material.market_context.get("symbols") or {}).keys()
                }
            )
        )
        write_phase1_dataset_root_manifest(
            archive_root,
            dataset_root,
            symbols=symbols,
            materials=materials,
            bundle_dirs=bundle_dirs,
        )
        rows = validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=bundle_dirs,
            expected_timestamps=tuple(material.timestamp for material in materials),
        )
    except Exception:
        if dataset_root.exists():
            shutil.rmtree(dataset_root)
        raise

    manifest = _read_manifest(dataset_root / "import_manifest.json")
    return {
        "dataset_root": str(dataset_root),
        "snapshot_count": len(rows),
        "start_timestamp": manifest["start_timestamp"],
        "end_timestamp": manifest["end_timestamp"],
        "coverage": manifest["coverage"],
    }


def _execution_evidence_gap(coverage: Mapping[str, Any]) -> dict[str, Any]:
    execution = coverage.get("execution_evidence") if isinstance(coverage, Mapping) else {}
    if not isinstance(execution, Mapping):
        execution = {}
    materialized = execution.get("materialized") or {}
    if not isinstance(materialized, Mapping):
        materialized = {}
    missing = [
        evidence_type
        for evidence_type in ("order_book", "trades")
        if int(materialized.get(evidence_type) or 0) <= 0
    ]
    return {"missing_execution_evidence": missing}


def materialize_phase1_evidence_windows(
    archive_root: str | Path,
    output_root: str | Path,
    *,
    symbols: Sequence[str] | None = None,
    windows_days: Iterable[int] = (30, 90, 180),
) -> dict[str, Any]:
    resolved_archive_root = _archive_root_from_input(archive_root)
    selected_manifests = _selected_manifest_paths(resolved_archive_root, symbols=symbols)
    if not selected_manifests:
        raise FileNotFoundError(f"no matching raw-market manifests found under: {resolved_archive_root}")

    imported_series = load_phase1_raw_market_imports_from_manifest_paths(selected_manifests)
    all_materials = build_phase1_dataset_bundle_materials(imported_series)
    if not all_materials:
        raise ValueError("selected raw-market manifests did not yield any eligible dataset bundles")

    end_exclusive = max(material.timestamp for material in all_materials) + timedelta(hours=1)
    output_path = Path(output_root)
    output_path.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "schema_version": "phase1_evidence_window_materialization.v1",
        "archive_root": str(resolved_archive_root),
        "output_root": str(output_path),
        "symbols": sorted({series.symbol for series in imported_series}),
        "selected_manifest_count": len(selected_manifests),
        "selected_manifest_paths": [str(path) for path in selected_manifests],
        "windows": {},
    }

    for days in windows_days:
        window_name = f"{int(days)}d"
        start = end_exclusive - timedelta(days=int(days))
        materials = tuple(material for material in all_materials if start <= material.timestamp < end_exclusive)
        if not materials:
            report["windows"][window_name] = {
                "status": "empty",
                "start_timestamp": start.isoformat().replace("+00:00", "Z"),
                "end_timestamp": end_exclusive.isoformat().replace("+00:00", "Z"),
            }
            continue
        window_report = _materialize_dataset_root(
            archive_root=resolved_archive_root,
            dataset_root=output_path / window_name,
            materials=materials,
        )
        window_report["status"] = "materialized"
        window_report["evidence_gap"] = _execution_evidence_gap(window_report["coverage"])
        report["windows"][window_name] = window_report

    (output_path / "coverage_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
