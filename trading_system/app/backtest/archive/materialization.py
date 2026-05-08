from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TextIO

from .importer import (
    build_phase1_dataset_bundle_materials,
    _material_market_context_symbol_keys,
    validate_phase1_imported_dataset_root,
    write_phase1_dataset_bundle,
    write_phase1_dataset_root_manifest,
)
from .raw_market import _utc_datetime, load_phase1_raw_market_imports_from_manifest_paths

_OHLCV_TIMEFRAMES = ("1h", "1m", "5m", "15m", "30m")
_OPTIONAL_CONTEXT_DATASETS = {"funding", "mark-price", "open-interest"}
_EXECUTION_DATASETS = {"order-book", "trades"}
_WINDOW_IMPORT_HISTORY_WARMUP = timedelta(days=50)


def _read_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"raw-market manifest must contain a JSON object: {path}")
    return payload


def _manifest_string(manifest: Mapping[str, Any], field: str) -> str:
    value = manifest.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"raw-market manifest field '{field}' must be a string")
    if value != value.strip():
        raise ValueError(f"raw-market manifest field '{field}' must be canonical")
    return value


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
    if _manifest_string(manifest, "exchange") != "binance" or _manifest_string(manifest, "market") != "futures":
        return False
    symbol = _manifest_string(manifest, "symbol").upper()
    if symbols is not None and symbol not in symbols:
        return False
    dataset = _manifest_string(manifest, "dataset")
    if dataset == "ohlcv":
        return _manifest_string(manifest, "timeframe") in _OHLCV_TIMEFRAMES
    return dataset in _OPTIONAL_CONTEXT_DATASETS or dataset in _EXECUTION_DATASETS


def _manifest_coverage_bounds(manifest: Mapping[str, Any]) -> tuple[datetime, datetime] | None:
    start = manifest.get("coverage_start")
    end = manifest.get("coverage_end")
    if start is None or end is None:
        return None
    return _manifest_coverage_timestamp(start, field="coverage_start"), _manifest_coverage_timestamp(end, field="coverage_end")


def _manifest_coverage_timestamp(value: Any, *, field: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{field} must be a string or numeric milliseconds")
    try:
        return _utc_datetime(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid timestamp") from exc


def _selected_manifest_paths(
    archive_root: Path,
    *,
    symbols: Sequence[str] | None,
    start_timestamp: datetime | None = None,
    end_timestamp: datetime | None = None,
) -> tuple[Path, ...]:
    raw_market_root = archive_root / "raw-market"
    selected_symbols = {symbol.upper() for symbol in symbols} if symbols else None
    manifest_paths: list[Path] = []
    for manifest_path in sorted(raw_market_root.rglob("*.manifest.json")):
        manifest = _read_manifest(manifest_path)
        if not _manifest_is_selected(manifest, symbols=selected_symbols):
            continue
        bounds = _manifest_coverage_bounds(manifest)
        if bounds is not None:
            coverage_start, coverage_end = bounds
            if start_timestamp is not None and coverage_end < start_timestamp:
                continue
            if end_timestamp is not None and coverage_start >= end_timestamp:
                continue
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
                    symbol
                    for material in materials
                    for symbol in _material_market_context_symbol_keys(material)
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


def _execution_evidence_count(value: Any, *, field: str) -> int:
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _execution_evidence_gap(coverage: Mapping[str, Any]) -> dict[str, Any]:
    execution = coverage.get("execution_evidence") if isinstance(coverage, Mapping) else {}
    if not isinstance(execution, Mapping):
        execution = {}
    materialized = execution.get("materialized") if "materialized" in execution else {}
    if materialized is None:
        materialized = {}
    if not isinstance(materialized, Mapping):
        raise ValueError("execution_evidence.materialized must be an object")
    missing = []
    for evidence_type in ("order_book", "trades"):
        count = _execution_evidence_count(
            materialized.get(evidence_type),
            field=f"execution_evidence.materialized.{evidence_type}",
        )
        if count <= 0:
            missing.append(evidence_type)
    return {"missing_execution_evidence": missing}


def materialize_phase1_evidence_windows(
    archive_root: str | Path,
    output_root: str | Path,
    *,
    symbols: Sequence[str] | None = None,
    windows_days: Iterable[int] = (30, 90, 180),
    progress_stream: TextIO | None = None,
) -> dict[str, Any]:
    resolved_archive_root = _archive_root_from_input(archive_root)
    resolved_windows_days = tuple(int(days) for days in windows_days)
    if not resolved_windows_days:
        raise ValueError("windows_days must contain at least one window")

    def emit_progress(message: str) -> None:
        print(message, file=progress_stream or sys.stderr)

    def write_coverage_report() -> None:
        (output_path / "coverage_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    initial_manifests = _selected_manifest_paths(resolved_archive_root, symbols=symbols)
    if not initial_manifests:
        raise FileNotFoundError(f"no matching raw-market manifests found under: {resolved_archive_root}")
    emit_progress(f"selected manifests initial count={len(initial_manifests)}")

    coverage_ends = [
        bounds[1]
        for manifest_path in initial_manifests
        if (bounds := _manifest_coverage_bounds(_read_manifest(manifest_path))) is not None
    ]
    if not coverage_ends:
        raise ValueError("selected raw-market manifests did not expose coverage_end timestamps")
    end_exclusive = max(coverage_ends) + timedelta(hours=1)
    output_path = Path(output_root)
    output_path.mkdir(parents=True, exist_ok=True)
    materialized_symbols: set[str] = set()
    selected_manifest_paths: set[Path] = set()
    report: dict[str, Any] = {
        "schema_version": "phase1_evidence_window_materialization.v1",
        "archive_root": str(resolved_archive_root),
        "output_root": str(output_path),
        "symbols": [],
        "selected_manifest_count": 0,
        "selected_manifest_paths": [],
        "windows": {},
    }

    for days in resolved_windows_days:
        started_at = time.perf_counter()
        window_name = f"{int(days)}d"
        start = end_exclusive - timedelta(days=int(days))
        import_start = start - _WINDOW_IMPORT_HISTORY_WARMUP
        emit_progress(
            f"window {window_name} start start={start.isoformat().replace('+00:00', 'Z')} "
            f"end={end_exclusive.isoformat().replace('+00:00', 'Z')}"
        )
        selected_manifests = _selected_manifest_paths(
            resolved_archive_root,
            symbols=symbols,
            start_timestamp=import_start,
            end_timestamp=end_exclusive,
        )
        selected_manifest_paths.update(selected_manifests)
        emit_progress(f"selected manifests window={window_name} count={len(selected_manifests)}")
        imported_series = (
            load_phase1_raw_market_imports_from_manifest_paths(
                selected_manifests,
                start_timestamp=import_start,
                end_timestamp=end_exclusive,
            )
            if selected_manifests
            else ()
        )
        materialized_symbols.update(series.symbol for series in imported_series)
        emit_progress(f"window {window_name} imported series count={len(imported_series)}")
        materials = (
            build_phase1_dataset_bundle_materials(
                imported_series,
                start_timestamp=start,
                end_timestamp=end_exclusive,
            )
            if imported_series
            else ()
        )
        if not materials:
            report["windows"][window_name] = {
                "status": "empty",
                "start_timestamp": start.isoformat().replace("+00:00", "Z"),
                "end_timestamp": end_exclusive.isoformat().replace("+00:00", "Z"),
                "selected_manifest_count": len(selected_manifests),
                "selected_manifest_paths": [str(path) for path in selected_manifests],
                "imported_series_count": len(imported_series),
            }
            report["symbols"] = sorted(materialized_symbols)
            report["selected_manifest_count"] = len(selected_manifest_paths)
            report["selected_manifest_paths"] = [str(path) for path in sorted(selected_manifest_paths, key=str)]
            write_coverage_report()
            elapsed = time.perf_counter() - started_at
            emit_progress(f"window {window_name} end status=empty snapshot count=0 elapsed seconds={elapsed:.3f}")
            continue
        window_report = _materialize_dataset_root(
            archive_root=resolved_archive_root,
            dataset_root=output_path / window_name,
            materials=materials,
        )
        window_report["status"] = "materialized"
        window_report["selected_manifest_count"] = len(selected_manifests)
        window_report["selected_manifest_paths"] = [str(path) for path in selected_manifests]
        window_report["imported_series_count"] = len(imported_series)
        window_report["evidence_gap"] = _execution_evidence_gap(window_report["coverage"])
        report["windows"][window_name] = window_report
        report["symbols"] = sorted(materialized_symbols)
        report["selected_manifest_count"] = len(selected_manifest_paths)
        report["selected_manifest_paths"] = [str(path) for path in sorted(selected_manifest_paths, key=str)]
        write_coverage_report()
        elapsed = time.perf_counter() - started_at
        emit_progress(
            f"window {window_name} end status=materialized snapshot count={window_report['snapshot_count']} "
            f"elapsed seconds={elapsed:.3f}"
        )

    return report
