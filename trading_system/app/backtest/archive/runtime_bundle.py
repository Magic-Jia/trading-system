from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...runtime_paths import RuntimePaths
from .paths import runtime_bundle_dir
from .types import ArchivedRuntimeBundle, RuntimeBundleMetadata, RuntimeBundleSourcePaths

ARCHIVE_RUNTIME_BUNDLE_ENV = "TRADING_ARCHIVE_RUNTIME_BUNDLE"
RUNTIME_BUNDLE_SCHEMA_VERSION = "runtime_bundle.v1"
RUNTIME_BUNDLE_KIND = "runtime_cycle"
_CANONICAL_SEGMENT_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"archive source file must contain a JSON object: {path}")
    return payload


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_timestamp(value: str | None = None, *, field_name: str = "timestamp", strict: bool = False) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        canonical = _parse_timestamp(value).isoformat().replace("+00:00", "Z")
    except ValueError:
        if strict:
            raise ValueError(f"{field_name} must be canonical UTC") from None
        raise
    if strict and value != canonical:
        raise ValueError(f"{field_name} must be canonical UTC")
    return canonical


def _validate_canonical_segment(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _CANONICAL_SEGMENT_PATTERN.fullmatch(value):
        raise ValueError(f"runtime bundle {field_name} must already be canonical")


def _validate_runtime_path_identity(paths: RuntimePaths) -> None:
    _validate_canonical_segment(paths.mode, field_name="mode")
    _validate_canonical_segment(paths.runtime_env, field_name="runtime_env")


def _validate_source_path(value: Path, *, field_name: str) -> None:
    if not value.is_absolute():
        raise ValueError(f"source {field_name} must be an absolute local path")


def _validate_source_paths(source_paths: RuntimeBundleSourcePaths) -> None:
    resolved_paths: list[Path] = []
    for source_path, field_name in (
        (source_paths.account_snapshot, "account_snapshot"),
        (source_paths.market_context, "market_context"),
        (source_paths.derivatives_snapshot, "derivatives_snapshot"),
        (source_paths.runtime_state, "runtime_state"),
    ):
        _validate_source_path(source_path, field_name=field_name)
        resolved_paths.append(source_path.resolve())
    if len(set(resolved_paths)) != len(resolved_paths):
        raise ValueError("runtime bundle source artifact paths must be unique")


def _optional_timestamp_string(payload: dict[str, Any], key: str, *, source_name: str) -> str:
    if key not in payload or payload[key] is None:
        return ""
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"{source_name} {key} must be a string")
    return value


def _optional_canonical_utc_timestamp_string(payload: dict[str, Any], key: str, *, source_name: str) -> str:
    value = _optional_timestamp_string(payload, key, source_name=source_name)
    if value:
        _utc_timestamp(value, field_name=f"{source_name} {key}", strict=True)
    return value


def _bundle_timestamp(
    *,
    account_payload: dict[str, Any],
    market_payload: dict[str, Any],
    derivatives_payload: dict[str, Any],
    archived_at: str,
) -> str:
    for payload, source_name in (
        (market_payload, "market_context.json"),
        (derivatives_payload, "derivatives_snapshot.json"),
        (account_payload, "account_snapshot.json"),
    ):
        raw_value = _optional_canonical_utc_timestamp_string(payload, "as_of", source_name=source_name)
        if raw_value:
            return _utc_timestamp(raw_value)
    return archived_at


def _run_id(paths: RuntimePaths, archived_at: str) -> str:
    archived_fragment = archived_at.replace(":", "-").replace("+", "-").replace(".", "-").lower()
    return f"{paths.mode}-{paths.runtime_env}-{archived_fragment}"


def _source_mapping(source_paths: RuntimeBundleSourcePaths) -> tuple[tuple[Path, str], ...]:
    return (
        (source_paths.account_snapshot, "account_snapshot.json"),
        (source_paths.market_context, "market_context.json"),
        (source_paths.derivatives_snapshot, "derivatives_snapshot.json"),
        (source_paths.runtime_state, "runtime_state.json"),
    )


def runtime_bundle_archive_enabled() -> bool:
    return os.environ.get(ARCHIVE_RUNTIME_BUNDLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def archive_runtime_bundle(
    paths: RuntimePaths,
    source_paths: RuntimeBundleSourcePaths,
    *,
    archived_at: str | None = None,
) -> ArchivedRuntimeBundle:
    _validate_runtime_path_identity(paths)
    _validate_source_paths(source_paths)
    account_payload = _read_json_object(source_paths.account_snapshot)
    market_payload = _read_json_object(source_paths.market_context)
    derivatives_payload = _read_json_object(source_paths.derivatives_snapshot)
    state_payload = _read_json_object(source_paths.runtime_state)
    archived_timestamp = _utc_timestamp(archived_at, field_name="archived_at", strict=archived_at is not None)
    bundle_timestamp = _bundle_timestamp(
        account_payload=account_payload,
        market_payload=market_payload,
        derivatives_payload=derivatives_payload,
        archived_at=archived_timestamp,
    )
    metadata = RuntimeBundleMetadata(
        timestamp=bundle_timestamp,
        run_id=_run_id(paths, archived_timestamp),
        archived_at=archived_timestamp,
        schema_version=RUNTIME_BUNDLE_SCHEMA_VERSION,
        bundle_kind=RUNTIME_BUNDLE_KIND,
        mode=paths.mode,
        runtime_env=paths.runtime_env,
        source={
            "bucket_dir": str(paths.bucket_dir),
            "account_snapshot": str(source_paths.account_snapshot),
            "market_context": str(source_paths.market_context),
            "derivatives_snapshot": str(source_paths.derivatives_snapshot),
            "runtime_state": str(source_paths.runtime_state),
        },
        input_timestamps={
            "account_as_of": _optional_canonical_utc_timestamp_string(
                account_payload,
                "as_of",
                source_name="account_snapshot.json",
            ),
            "market_as_of": _optional_canonical_utc_timestamp_string(
                market_payload,
                "as_of",
                source_name="market_context.json",
            ),
            "derivatives_as_of": _optional_canonical_utc_timestamp_string(
                derivatives_payload,
                "as_of",
                source_name="derivatives_snapshot.json",
            ),
            "runtime_state_updated_at_bj": _optional_timestamp_string(
                state_payload,
                "updated_at_bj",
                source_name="runtime_state.json",
            ),
        },
    )
    bundle_dir = runtime_bundle_dir(paths.archive_runtime_bundles_dir, timestamp=metadata.timestamp, run_id=metadata.run_id)
    bundle_dir.parent.mkdir(parents=True, exist_ok=True)
    if bundle_dir.exists():
        raise FileExistsError(f"immutable runtime bundle already exists: {bundle_dir}")
    bundle_dir.mkdir()
    _write_json(bundle_dir / "metadata.json", metadata.as_dict())
    for source_path, filename in _source_mapping(source_paths):
        shutil.copyfile(source_path, bundle_dir / filename)
    return ArchivedRuntimeBundle(bundle_dir=bundle_dir, metadata=metadata)


def archive_runtime_bundle_from_environment(
    paths: RuntimePaths,
    *,
    archived_at: str | None = None,
) -> ArchivedRuntimeBundle:
    from ...main import resolve_runtime_input_paths

    input_paths = resolve_runtime_input_paths()
    return archive_runtime_bundle(
        paths,
        RuntimeBundleSourcePaths(
            account_snapshot=input_paths.account_snapshot,
            market_context=input_paths.market_context,
            derivatives_snapshot=input_paths.derivatives_snapshot,
            runtime_state=input_paths.state_file,
        ),
        archived_at=archived_at,
    )


__all__ = [
    "ARCHIVE_RUNTIME_BUNDLE_ENV",
    "ArchivedRuntimeBundle",
    "RuntimeBundleSourcePaths",
    "archive_runtime_bundle",
    "archive_runtime_bundle_from_environment",
    "runtime_bundle_archive_enabled",
]
