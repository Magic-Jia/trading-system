from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping as ABCMapping
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from trading_system.app.config import BASE_DIR_ENV, normalize_engine_names, normalize_setup_types
from trading_system.app.signals.entry_profile import ENTRY_PROFILE_ENV, resolve_entry_profile
from trading_system.app.backtest.archive import (
    archive_runtime_bundle_from_environment,
    runtime_bundle_archive_enabled,
)
from trading_system.app.main import (
    ACCOUNT_SNAPSHOT_FILE_ENV,
    DERIVATIVES_SNAPSHOT_FILE_ENV,
    MARKET_CONTEXT_FILE_ENV,
    STATE_FILE_ENV,
    main as run_main,
)
from trading_system.app.runtime_paths import RUNTIME_ENV_ENV, RuntimePaths, build_runtime_paths
from trading_system.paper_snapshots import (
    PAPER_ACCOUNT_SNAPSHOT_NAME,
    PAPER_DERIVATIVES_SNAPSHOT_NAME,
    PAPER_MARKET_CONTEXT_NAME,
    prepare_paper_runtime_inputs,
)

EXECUTION_MODE_ENV = "TRADING_EXECUTION_MODE"
EFFECTIVE_ENV_FILE_ENV = "TRADING_EFFECTIVE_ENV_FILE"
EFFECTIVE_LOG_FILE_ENV = "TRADING_EFFECTIVE_LOG_FILE"
LATEST_SUMMARY_NAME = "latest.json"
ERROR_SUMMARY_NAME = "error.json"
PAPER_RUNTIME_ENV = "paper"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def _temporary_env(overrides: Mapping[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _base_summary(paths: RuntimePaths, *, status: str, finished_at: str) -> dict[str, Any]:
    return {
        "status": status,
        "mode": paths.mode,
        "runtime_env": paths.runtime_env,
        "env_file": os.environ.get(EFFECTIVE_ENV_FILE_ENV),
        "wrapper_log_file": os.environ.get(EFFECTIVE_LOG_FILE_ENV),
        "runtime_root": str(paths.runtime_root),
        "bucket_dir": str(paths.bucket_dir),
        "state_file": str(paths.state_file),
        "finished_at": finished_at,
    }


def _sequence_field(state: ABCMapping[str, Any], field_name: str, *, item_kind: str, field_path: str | None = None) -> list[Any]:
    resolved_field_path = field_path or f"runtime_state.{field_name}"
    if field_name not in state:
        return []
    value = state[field_name]
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{resolved_field_path} must be a sequence of {item_kind}")
    return list(value)


def _sequence_of_mappings(
    state: ABCMapping[str, Any], field_name: str, *, field_path: str | None = None
) -> list[ABCMapping[str, Any]]:
    resolved_field_path = field_path or f"runtime_state.{field_name}"
    values = _sequence_field(state, field_name, item_kind="mappings", field_path=resolved_field_path)
    for index, value in enumerate(values):
        if not isinstance(value, ABCMapping):
            raise ValueError(f"{resolved_field_path}[{index}] must be a mapping")
    return values


def _sequence_of_strings(
    state: ABCMapping[str, Any], field_name: str, *, field_path: str | None = None
) -> list[str]:
    resolved_field_path = field_path or f"runtime_state.{field_name}"
    values = _sequence_field(state, field_name, item_kind="strings", field_path=resolved_field_path)
    normalized: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str):
            raise ValueError(f"{resolved_field_path}[{index}] must be a string")
        normalized.append(value)
    return normalized


def _mapping_field(state: ABCMapping[str, Any], field_name: str) -> dict[str, Any]:
    if field_name not in state:
        return {}
    value = state[field_name]
    if not isinstance(value, ABCMapping):
        raise ValueError(f"runtime_state.{field_name} must be a mapping")
    return dict(value)


def _non_negative_int(summary: ABCMapping[str, Any], field_path: str) -> int:
    if "candidate_count" not in summary:
        return 0
    value = summary["candidate_count"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_path} must be a non-negative int")
    return value


def _entry_profile_name(state: ABCMapping[str, Any], *, env_entry_profile: str) -> str:
    if "latest_entry_profile" not in state:
        return env_entry_profile
    latest_entry_profile = _mapping_field(state, "latest_entry_profile")
    if "name" not in latest_entry_profile:
        return env_entry_profile
    name = latest_entry_profile["name"]
    if not isinstance(name, str) or not name.strip():
        raise ValueError("runtime_state.latest_entry_profile.name must be a canonical non-empty string")
    try:
        resolved_name = resolve_entry_profile(name).name
    except ValueError as exc:
        raise ValueError("runtime_state.latest_entry_profile.name must be a canonical entry profile name") from exc
    if resolved_name != name.strip():
        raise ValueError("runtime_state.latest_entry_profile.name must be a canonical entry profile name")
    return resolved_name


def _state_summary(paths: RuntimePaths) -> dict[str, Any]:
    env_entry_profile = resolve_entry_profile(os.environ.get(ENTRY_PROFILE_ENV)).name
    if not paths.state_file.exists():
        return {
            "state_written": False,
            "execution_mode": None,
            "entry_profile": env_entry_profile,
            "candidate_count": 0,
            "allocation_count": 0,
            "disabled_setup_type_filtered_count": 0,
            "disabled_setup_type_filtered_candidates": [],
            "suppression_rules": [],
            "disabled_engines": list(normalize_engine_names(os.environ.get("TRADING_DISABLED_ENGINES"))),
            "disabled_setup_types": list(normalize_setup_types(os.environ.get("TRADING_DISABLED_SETUP_TYPES"))),
            "trend_candidate_count": 0,
            "rotation_candidate_count": 0,
            "short_candidate_count": 0,
            "short_accepted_symbols": [],
            "short_deferred_execution_symbols": [],
            "paper_trading": {},
        }

    state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    if not isinstance(state, ABCMapping):
        raise ValueError("runtime_state must be a mapping")

    latest_candidates = _sequence_of_mappings(state, "latest_candidates")
    latest_allocations = _sequence_of_mappings(state, "latest_allocations")
    disabled_setup_type_filtered_candidates = _sequence_of_mappings(state, "disabled_setup_type_filtered_candidates")
    regime = _mapping_field(state, "latest_regime")
    trend_summary = _mapping_field(state, "trend_summary")
    rotation_summary = _mapping_field(state, "rotation_summary")
    short_summary = _mapping_field(state, "short_summary")
    paper_trading = _mapping_field(state, "paper_trading")
    suppression_rules = _sequence_of_strings(
        regime, "suppression_rules", field_path="runtime_state.latest_regime.suppression_rules"
    )
    short_accepted_symbols = _sequence_of_strings(
        short_summary, "accepted_symbols", field_path="runtime_state.short_summary.accepted_symbols"
    )
    short_deferred_execution_symbols = _sequence_of_strings(
        short_summary,
        "deferred_execution_symbols",
        field_path="runtime_state.short_summary.deferred_execution_symbols",
    )

    return {
        "state_written": True,
        "execution_mode": state.get("execution_mode"),
        "entry_profile": _entry_profile_name(state, env_entry_profile=env_entry_profile),
        "candidate_count": len(latest_candidates),
        "allocation_count": len(latest_allocations),
        "disabled_setup_type_filtered_count": len(disabled_setup_type_filtered_candidates),
        "disabled_setup_type_filtered_candidates": disabled_setup_type_filtered_candidates,
        "suppression_rules": suppression_rules,
        "disabled_engines": list(normalize_engine_names(os.environ.get("TRADING_DISABLED_ENGINES"))),
        "disabled_setup_types": list(normalize_setup_types(os.environ.get("TRADING_DISABLED_SETUP_TYPES"))),
        "trend_candidate_count": _non_negative_int(trend_summary, "runtime_state.trend_summary.candidate_count"),
        "rotation_candidate_count": _non_negative_int(
            rotation_summary, "runtime_state.rotation_summary.candidate_count"
        ),
        "short_candidate_count": _non_negative_int(short_summary, "runtime_state.short_summary.candidate_count"),
        "short_accepted_symbols": short_accepted_symbols,
        "short_deferred_execution_symbols": short_deferred_execution_symbols,
        "paper_trading": paper_trading,
    }


def _resolve_runtime_root(runtime_root: Path | str | None) -> Path | str | None:
    if runtime_root is not None:
        return runtime_root

    base_dir = os.environ.get(BASE_DIR_ENV)
    if base_dir:
        return Path(base_dir) / "data" / "runtime"

    return None


def _resolve_runtime_env(mode: str, runtime_env: str | None) -> str | None:
    if runtime_env is not None:
        return runtime_env

    env_value = os.environ.get(RUNTIME_ENV_ENV)
    if env_value:
        return env_value

    if mode.strip().lower() == "paper":
        return PAPER_RUNTIME_ENV

    return None


def run_cycle(mode: str, *, runtime_root: Path | str | None = None, runtime_env: str | None = None) -> dict[str, Any]:
    paths = build_runtime_paths(
        mode,
        runtime_root=_resolve_runtime_root(runtime_root),
        runtime_env=_resolve_runtime_env(mode, runtime_env),
    )
    paths.bucket_dir.mkdir(parents=True, exist_ok=True)
    archived_bundle_dir: str | None = None
    finished_at: str | None = None

    env_overrides = {
        EXECUTION_MODE_ENV: paths.mode,
        RUNTIME_ENV_ENV: paths.runtime_env,
        STATE_FILE_ENV: str(paths.state_file),
    }

    try:
        if paths.mode in {"paper", "testnet"}:
            prepare_paper_runtime_inputs(paths)
            env_overrides.update(
                {
                    ACCOUNT_SNAPSHOT_FILE_ENV: str(paths.bucket_dir / PAPER_ACCOUNT_SNAPSHOT_NAME),
                    MARKET_CONTEXT_FILE_ENV: str(paths.bucket_dir / PAPER_MARKET_CONTEXT_NAME),
                    DERIVATIVES_SNAPSHOT_FILE_ENV: str(paths.bucket_dir / PAPER_DERIVATIVES_SNAPSHOT_NAME),
                }
            )
        with _temporary_env(env_overrides):
            run_main()
            finished_at = _timestamp()
            if runtime_bundle_archive_enabled():
                archived_bundle = archive_runtime_bundle_from_environment(paths, archived_at=finished_at)
                archived_bundle_dir = str(archived_bundle.bundle_dir)
    except Exception as exc:
        summary = {
            **_base_summary(paths, status="error", finished_at=finished_at or _timestamp()),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
        _write_json(paths.bucket_dir / ERROR_SUMMARY_NAME, summary)
        _write_json(paths.bucket_dir / LATEST_SUMMARY_NAME, summary)
        raise

    summary = {
        **_base_summary(paths, status="ok", finished_at=finished_at or _timestamp()),
        **_state_summary(paths),
    }
    if archived_bundle_dir is not None:
        summary["archive_bundle_dir"] = archived_bundle_dir
    _write_json(paths.bucket_dir / LATEST_SUMMARY_NAME, summary)
    error_path = paths.bucket_dir / ERROR_SUMMARY_NAME
    if error_path.exists():
        error_path.unlink()
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one trading cycle and emit runtime summaries.")
    parser.add_argument("--mode", default=os.environ.get(EXECUTION_MODE_ENV, "paper"))
    parser.add_argument("--runtime-root")
    parser.add_argument("--runtime-env")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    summary = run_cycle(args.mode, runtime_root=args.runtime_root, runtime_env=args.runtime_env)
    print(Path(summary["bucket_dir"]) / LATEST_SUMMARY_NAME)


if __name__ == "__main__":
    main()
