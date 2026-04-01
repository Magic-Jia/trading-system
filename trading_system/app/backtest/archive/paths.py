from __future__ import annotations

import re
from pathlib import Path

RUNTIME_BUNDLE_ARCHIVE_DIRNAME = "runtime-bundles"


def _normalize_run_id_fragment(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise ValueError("run_id must contain at least one alphanumeric character")
    return normalized


def bundle_timestamp_dirname(timestamp: str) -> str:
    return timestamp.strip().replace(":", "-")


def runtime_bundle_dir(base_dir: Path, *, timestamp: str, run_id: str) -> Path:
    return base_dir / f"{bundle_timestamp_dirname(timestamp)}--{_normalize_run_id_fragment(run_id)}"
