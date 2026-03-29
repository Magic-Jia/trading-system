from __future__ import annotations

import argparse
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from trading_system.app.main import STATE_FILE_ENV, main as run_main
from trading_system.app.runtime_paths import RUNTIME_ENV_ENV, RuntimePaths, build_runtime_paths

EXECUTION_MODE_ENV = "TRADING_EXECUTION_MODE"
LATEST_SUMMARY_NAME = "latest.json"
ERROR_SUMMARY_NAME = "error.json"


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
        "runtime_root": str(paths.runtime_root),
        "bucket_dir": str(paths.bucket_dir),
        "state_file": str(paths.state_file),
        "finished_at": finished_at,
    }


def _state_summary(paths: RuntimePaths) -> dict[str, Any]:
    if not paths.state_file.exists():
        return {
            "state_written": False,
            "execution_mode": None,
            "candidate_count": 0,
            "allocation_count": 0,
            "paper_trading": {},
        }

    state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    paper_trading = state.get("paper_trading")
    if not isinstance(paper_trading, dict):
        paper_trading = {}
    return {
        "state_written": True,
        "execution_mode": state.get("execution_mode"),
        "candidate_count": len(state.get("latest_candidates") or []),
        "allocation_count": len(state.get("latest_allocations") or []),
        "paper_trading": paper_trading,
    }


def run_cycle(mode: str, *, runtime_root: Path | str | None = None, runtime_env: str | None = None) -> dict[str, Any]:
    paths = build_runtime_paths(mode, runtime_root=runtime_root, runtime_env=runtime_env)
    paths.bucket_dir.mkdir(parents=True, exist_ok=True)

    env_overrides = {
        EXECUTION_MODE_ENV: paths.mode,
        RUNTIME_ENV_ENV: paths.runtime_env,
        STATE_FILE_ENV: str(paths.state_file),
    }

    try:
        with _temporary_env(env_overrides):
            run_main()
    except Exception as exc:
        summary = {
            **_base_summary(paths, status="error", finished_at=_timestamp()),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
        _write_json(paths.bucket_dir / ERROR_SUMMARY_NAME, summary)
        _write_json(paths.bucket_dir / LATEST_SUMMARY_NAME, summary)
        raise

    summary = {
        **_base_summary(paths, status="ok", finished_at=_timestamp()),
        **_state_summary(paths),
    }
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
