from __future__ import annotations

import argparse
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping

from trading_system import generate_simulated_live_cadence_runner as cadence_runner
from trading_system import run_cycle as run_cycle_module
from trading_system import scheduled_live_sim_generation
from trading_system.app.runtime_paths import build_runtime_paths
from trading_system.app.signals.entry_profile import ENTRY_PROFILE_ENV
from trading_system.bootstrap_live_sim_generation_inputs import bootstrap_live_sim_generation_inputs
from trading_system.generate_execution_calibration_records import (
    CALIBRATION_RECORDS_NAME,
    CALIBRATION_UNAVAILABLE_NAME,
    generate_execution_calibration_records,
)

SCOUT_PROFILE = "scout"
SAMPLER_RESULT_NAME = "paper_evidence_sampler_result.json"


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


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _sample_count(paths) -> int:
    health = _read_json(paths.bucket_dir / "execution_sample_collection_health.json")
    value = health.get("sample_count", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("execution_sample_collection_health.sample_count must be a non-negative integer")
    return value


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _canonical_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _refresh_generated_at(paths, requested_generated_at: str | None) -> str:
    candidates: list[datetime] = []
    requested = _parse_timestamp(requested_generated_at)
    if requested is not None:
        candidates.append(requested)
    else:
        candidates.append(datetime.now(UTC))
    for filename in ("account_snapshot.json", "market_context.json", "derivatives_snapshot.json"):
        snapshot_time = _parse_timestamp(_read_json(paths.bucket_dir / filename).get("as_of"))
        if snapshot_time is not None:
            candidates.append(snapshot_time)
    return _canonical_timestamp(max(candidates))


def _write_result(paths, payload: Mapping[str, Any]) -> None:
    paths.optimization_dir.mkdir(parents=True, exist_ok=True)
    (paths.optimization_dir / SAMPLER_RESULT_NAME).write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def run_paper_evidence_sampler(
    *,
    runtime_root: str | Path | None = None,
    runtime_env: str | None = "paper",
    generated_at: str | None = None,
    mode: str = "paper",
    max_evidence_age_seconds: int = 3600,
    min_tca_samples: int = 30,
) -> dict[str, Any]:
    if mode != "paper":
        raise ValueError("paper evidence sampler is paper-only")
    paths = build_runtime_paths(mode, runtime_root=runtime_root, runtime_env=runtime_env)
    sample_count_before = _sample_count(paths)
    with _temporary_env({ENTRY_PROFILE_ENV: SCOUT_PROFILE}):
        cycle_summary = run_cycle_module.run_cycle(mode, runtime_root=runtime_root, runtime_env=runtime_env)
    sample_count_after = _sample_count(paths)
    if sample_count_after <= sample_count_before:
        result = {
            "schema_version": "paper_evidence_sampler_result.v1",
            "status": "completed",
            "mode": mode,
            "runtime_env": paths.runtime_env,
            "entry_profile": SCOUT_PROFILE,
            "sample_count_before": sample_count_before,
            "sample_count_after": sample_count_after,
            "new_sample_count": 0,
            "sample_action": "no_new_sample",
            "cycle_status": cycle_summary.get("status"),
            "evidence_refresh_skipped_reason": "no_new_execution_sample",
        }
        _write_result(paths, result)
        return result

    refresh_generated_at = _refresh_generated_at(paths, generated_at)
    calibration_result = generate_execution_calibration_records(
        execution_log_file=paths.execution_log_file,
        paper_ledger_file=paths.paper_ledger_file,
        output_file=paths.optimization_dir / CALIBRATION_RECORDS_NAME,
        unavailable_marker_file=paths.optimization_dir / CALIBRATION_UNAVAILABLE_NAME,
    )
    bootstrap_result = bootstrap_live_sim_generation_inputs(
        legacy_root=paths.bucket_dir,
        mode=mode,
        runtime_root=runtime_root,
        runtime_env=runtime_env,
        generated_at=refresh_generated_at,
        max_evidence_age_seconds=max_evidence_age_seconds,
    )
    scheduled_result = scheduled_live_sim_generation.run_scheduled_generation(
        mode=mode,
        runtime_root=runtime_root,
        runtime_env=runtime_env,
        generated_at=refresh_generated_at,
        max_evidence_age_seconds=max_evidence_age_seconds,
        min_tca_samples=min_tca_samples,
    )
    cadence_result = cadence_runner.run_simulated_live_cadence(
        runtime_optimization_dir=paths.optimization_dir,
        output_dir=paths.optimization_dir,
        generated_at=refresh_generated_at,
    )
    result = {
        "schema_version": "paper_evidence_sampler_result.v1",
        "status": "completed",
        "mode": mode,
        "runtime_env": paths.runtime_env,
        "entry_profile": SCOUT_PROFILE,
        "sample_count_before": sample_count_before,
        "sample_count_after": sample_count_after,
        "new_sample_count": max(sample_count_after - sample_count_before, 0),
        "refresh_generated_at": refresh_generated_at,
        "sample_action": "sample_added" if sample_count_after > sample_count_before else "no_new_sample",
        "cycle_status": cycle_summary.get("status"),
        "calibration_status": calibration_result.get("status"),
        "bootstrap_status": bootstrap_result.get("status"),
        "scheduled_status": scheduled_result.get("status"),
        "cadence_status": cadence_result.get("status"),
        "cadence_decision": cadence_result.get("decision"),
    }
    _write_result(paths, result)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one paper-only scout evidence sampling cycle and refresh live-sim gates.")
    parser.add_argument("--runtime-root")
    parser.add_argument("--runtime-env", default="paper")
    parser.add_argument("--generated-at")
    parser.add_argument("--max-evidence-age-seconds", type=int, default=3600)
    parser.add_argument("--min-tca-samples", type=int, default=30)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_paper_evidence_sampler(
        runtime_root=args.runtime_root,
        runtime_env=args.runtime_env,
        generated_at=args.generated_at,
        max_evidence_age_seconds=args.max_evidence_age_seconds,
        min_tca_samples=args.min_tca_samples,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
