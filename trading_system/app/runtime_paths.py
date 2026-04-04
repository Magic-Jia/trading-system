from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = BASE / "data" / "runtime"
RUNTIME_ENV_ENV = "TRADING_RUNTIME_ENV"
DEFAULT_RUNTIME_ENV = "default"


def _normalize_segment(value: str, *, name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise ValueError(f"{name} must contain at least one alphanumeric character")
    return normalized


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    mode: str
    runtime_env: str
    runtime_root: Path
    bucket_dir: Path
    archive_root: Path
    archive_runtime_bundles_dir: Path
    state_file: Path
    paper_ledger_file: Path
    execution_log_file: Path
    account_snapshot_file: Path
    market_context_file: Path
    derivatives_snapshot_file: Path
    latest_summary_file: Path
    error_summary_file: Path


def build_runtime_paths(mode: str, runtime_root: Path | str | None = None, runtime_env: str | None = None) -> RuntimePaths:
    normalized_mode = _normalize_segment(mode, name="mode")
    normalized_runtime_env = _normalize_segment(
        runtime_env or os.environ.get(RUNTIME_ENV_ENV, DEFAULT_RUNTIME_ENV),
        name="runtime_env",
    )
    resolved_runtime_root = Path(runtime_root) if runtime_root is not None else DEFAULT_RUNTIME_ROOT
    bucket_dir = resolved_runtime_root / normalized_mode / normalized_runtime_env
    archive_root = resolved_runtime_root.parent / "archive"
    return RuntimePaths(
        mode=normalized_mode,
        runtime_env=normalized_runtime_env,
        runtime_root=resolved_runtime_root,
        bucket_dir=bucket_dir,
        archive_root=archive_root,
        archive_runtime_bundles_dir=archive_root / "runtime-bundles" / normalized_mode / normalized_runtime_env,
        state_file=bucket_dir / "runtime_state.json",
        paper_ledger_file=bucket_dir / "paper_ledger.jsonl",
        execution_log_file=bucket_dir / "execution_log.jsonl",
        account_snapshot_file=bucket_dir / "account_snapshot.json",
        market_context_file=bucket_dir / "market_context.json",
        derivatives_snapshot_file=bucket_dir / "derivatives_snapshot.json",
        latest_summary_file=bucket_dir / "latest.json",
        error_summary_file=bucket_dir / "error.json",
    )


__all__ = [
    "DEFAULT_RUNTIME_ENV",
    "DEFAULT_RUNTIME_ROOT",
    "RUNTIME_ENV_ENV",
    "RuntimePaths",
    "build_runtime_paths",
]
