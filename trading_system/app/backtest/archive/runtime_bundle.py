from __future__ import annotations

import json
import os
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


def _utc_timestamp(value: str | None = None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return _parse_timestamp(value).isoformat().replace("+00:00", "Z")


def _bundle_timestamp(
    *,
    account_payload: dict[str, Any],
    market_payload: dict[str, Any],
    derivatives_payload: dict[str, Any],
    archived_at: str,
) -> str:
    for payload in (market_payload, derivatives_payload, account_payload):
        raw_value = payload.get("as_of")
        if raw_value:
            return _utc_timestamp(str(raw_value))
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
    account_payload = _read_json_object(source_paths.account_snapshot)
    market_payload = _read_json_object(source_paths.market_context)
    derivatives_payload = _read_json_object(source_paths.derivatives_snapshot)
    state_payload = _read_json_object(source_paths.runtime_state)
    archived_timestamp = _utc_timestamp(archived_at)
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
            "account_as_of": str(account_payload.get("as_of") or ""),
            "market_as_of": str(market_payload.get("as_of") or ""),
            "derivatives_as_of": str(derivatives_payload.get("as_of") or ""),
            "runtime_state_updated_at_bj": str(state_payload.get("updated_at_bj") or ""),
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
