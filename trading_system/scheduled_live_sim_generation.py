from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from trading_system.app.execution.calibration import (
    load_calibration_records,
    write_calibration_summary,
    write_tca_calibration_report,
)
from trading_system.app.reporting.daily_quality_gate_report import write_daily_quality_gate_report
from trading_system.app.runtime.paper_live_sim_evidence import write_paper_live_sim_evidence_bundle
from trading_system.app.runtime_paths import build_runtime_paths

EVIDENCE_MANIFEST_NAME = "paper_live_sim_evidence_manifest.json"
CALIBRATION_RECORDS_NAME = "passive_order_calibration_records.jsonl"
CALIBRATION_UNAVAILABLE_NAME = "calibration_records_unavailable.json"
TCA_ASSUMPTIONS_NAME = "tca_assumptions.json"
TCA_TOLERANCES_NAME = "tca_tolerances.json"
DRIFT_CONTRACT_NAME = "paper_live_shadow_drift_contract.json"
RECONCILIATION_NAME = "runtime_safety_gate.json"
ERROR_NAME = "scheduled_live_sim_generation_error.json"
BOOTSTRAP_METADATA_NAME = "bootstrap_input_metadata.json"


def _canonical_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path.name} must contain a JSON object")
    return dict(payload)


def _optional_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _read_json_object(path)


def _require_input_files(paths: list[Path]) -> None:
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(str(path))


def _p95_slippage_from_tca(report: Mapping[str, Any]) -> float:
    observed = report.get("observed")
    if not isinstance(observed, Mapping):
        raise ValueError("tca_calibration_report observed must be an object")
    slippage = observed.get("slippage_bps")
    if not isinstance(slippage, Mapping):
        raise ValueError("tca_calibration_report observed.slippage_bps must be an object")
    p95 = slippage.get("p95")
    if isinstance(p95, bool) or not isinstance(p95, (int, float)):
        raise ValueError("tca_calibration_report observed.slippage_bps.p95 must be numeric")
    return float(p95)


def _daily_tca_input(report: Mapping[str, Any], *, max_p95_slippage_bps: float) -> dict[str, Any]:
    return {
        "sample_size": report.get("sample_count"),
        "p95_slippage_bps": _p95_slippage_from_tca(report),
        "max_p95_slippage_bps": max_p95_slippage_bps,
    }


def _calibration_unavailable_input(marker: Mapping[str, Any], *, max_p95_slippage_bps: float) -> dict[str, Any]:
    if marker.get("schema_version") != "calibration_records_unavailable.v1":
        raise ValueError("calibration_records_unavailable schema_version is invalid")
    if marker.get("reason") != "calibration_records_unavailable":
        raise ValueError("calibration_records_unavailable reason is invalid")
    return {
        "sample_size": 0,
        "min_sample_size": None,
        "p95_slippage_bps": 0.0,
        "max_p95_slippage_bps": max_p95_slippage_bps,
        "availability_reason": "calibration_records_unavailable",
    }


def _freshness_input(
    *,
    output_dir: Path,
    max_evidence_age_seconds: int,
) -> dict[str, Any]:
    items: dict[str, dict[str, Any]] = {
        "paper_live_sim_evidence_bundle": {"age_seconds": 0},
        "tca_calibration_report": {"age_seconds": 0},
        "runtime_reconciliation": {"age_seconds": 0},
    }
    bootstrap_metadata = _optional_json_object(output_dir / BOOTSTRAP_METADATA_NAME)
    if bootstrap_metadata:
        quality = bootstrap_metadata.get("source_timestamp_quality")
        if isinstance(quality, Mapping):
            account_quality = quality.get("account_snapshot.json")
            if isinstance(account_quality, Mapping) and account_quality.get("freshness_met") is False:
                item: dict[str, Any] = {"age_seconds": max_evidence_age_seconds + 1}
                reason = account_quality.get("reason")
                if isinstance(reason, str) and reason:
                    item["reason"] = reason
                items["account_snapshot"] = item
    return {"max_age_seconds": max_evidence_age_seconds, "items": items}


def _write_error(path: Path, exc: Exception) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "scheduled_live_sim_generation_error.v1",
        "status": "fail_closed",
        "generated_at": _canonical_now(),
        "error_type": type(exc).__name__,
        "error_message": str(exc),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_scheduled_generation(
    *,
    mode: str = "paper",
    runtime_root: str | Path | None = None,
    runtime_env: str | None = None,
    generated_at: str | None = None,
    max_evidence_age_seconds: int = 3600,
    min_tca_samples: int = 30,
    max_p95_slippage_bps: float = 5.0,
) -> dict[str, Any]:
    paths = build_runtime_paths(mode, runtime_root=runtime_root, runtime_env=runtime_env)
    output_dir = paths.optimization_dir
    evaluated_at = generated_at or _canonical_now()
    calibration_records = output_dir / CALIBRATION_RECORDS_NAME
    _require_input_files(
        [
            calibration_records,
            output_dir / EVIDENCE_MANIFEST_NAME,
            output_dir / TCA_ASSUMPTIONS_NAME,
            output_dir / DRIFT_CONTRACT_NAME,
            output_dir / RECONCILIATION_NAME,
        ]
    )

    evidence_manifest = _read_json_object(output_dir / EVIDENCE_MANIFEST_NAME)
    assumptions = _read_json_object(output_dir / TCA_ASSUMPTIONS_NAME)
    drift = _read_json_object(output_dir / DRIFT_CONTRACT_NAME)
    reconciliation = _read_json_object(output_dir / RECONCILIATION_NAME)
    tolerances = _optional_json_object(output_dir / TCA_TOLERANCES_NAME)
    calibration_unavailable = _optional_json_object(output_dir / CALIBRATION_UNAVAILABLE_NAME)
    calibration_record_rows = load_calibration_records(calibration_records)
    calibration_records_available = bool(calibration_record_rows)
    if not calibration_records_available and calibration_unavailable is None:
        raise ValueError(f"{CALIBRATION_RECORDS_NAME} contains no calibration records")

    evidence_path = write_paper_live_sim_evidence_bundle(evidence_manifest, output_dir)
    generated_artifacts = {
        "paper_live_sim_evidence_bundle": str(evidence_path),
    }
    if calibration_records_available:
        if calibration_unavailable is not None:
            (output_dir / CALIBRATION_UNAVAILABLE_NAME).unlink(missing_ok=True)
        calibration_summary_path = write_calibration_summary(
            calibration_records,
            output_dir,
            evidence_source={
                "type": "paper_live_sim",
                "run_id": f"{paths.mode}-{paths.runtime_env}-scheduled-calibration",
                "exported_at": evaluated_at,
            },
        )
        tca_path = write_tca_calibration_report(
            calibration_records,
            output_dir,
            assumptions=assumptions,
            evidence_source={
                "type": "paper_live_sim",
                "run_id": f"{paths.mode}-{paths.runtime_env}-scheduled-tca",
                "exported_at": evaluated_at,
            },
            evaluated_at=evaluated_at,
            min_samples=min_tca_samples,
            max_evidence_age_seconds=max_evidence_age_seconds,
            tolerance_thresholds=tolerances,
        )
        tca_report = _read_json_object(tca_path)
        daily_tca = _daily_tca_input(tca_report, max_p95_slippage_bps=max_p95_slippage_bps)
        generated_artifacts.update(
            {
                "passive_order_calibration_summary": str(calibration_summary_path),
                "tca_calibration_report": str(tca_path),
            }
        )
    else:
        daily_tca = _calibration_unavailable_input(
            calibration_unavailable,
            max_p95_slippage_bps=max_p95_slippage_bps,
        )
        generated_artifacts["calibration_records_unavailable"] = str(output_dir / CALIBRATION_UNAVAILABLE_NAME)
    gate = write_daily_quality_gate_report(
        output_dir / "daily_quality_gate_report.json",
        evidence_bundle={"verified": True, "manifest_present": True},
        drift=drift,
        reconciliation=reconciliation,
        tca=daily_tca,
        freshness={
            **_freshness_input(output_dir=output_dir, max_evidence_age_seconds=max_evidence_age_seconds),
        },
        min_sample_size=min_tca_samples,
        generated_at=evaluated_at,
    )
    gate_path = output_dir / "daily_quality_gate_report.json"

    return {
        "schema_version": "scheduled_live_sim_generation_result.v1",
        "status": "ok",
        "mode": paths.mode,
        "runtime_env": paths.runtime_env,
        "generated_at": evaluated_at,
        "daily_quality_gate_decision": gate["decision"],
        "generated_artifacts": {
            **generated_artifacts,
            "daily_quality_gate_report": str(gate_path),
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate scheduled simulated-live evidence artifacts.")
    parser.add_argument("--mode", default="paper")
    parser.add_argument("--runtime-root")
    parser.add_argument("--runtime-env")
    parser.add_argument("--generated-at")
    parser.add_argument("--max-evidence-age-seconds", type=int, default=3600)
    parser.add_argument("--min-tca-samples", type=int, default=30)
    parser.add_argument("--max-p95-slippage-bps", type=float, default=5.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = build_runtime_paths(args.mode, runtime_root=args.runtime_root, runtime_env=args.runtime_env)
    try:
        result = run_scheduled_generation(
            mode=args.mode,
            runtime_root=args.runtime_root,
            runtime_env=args.runtime_env,
            generated_at=args.generated_at,
            max_evidence_age_seconds=args.max_evidence_age_seconds,
            min_tca_samples=args.min_tca_samples,
            max_p95_slippage_bps=args.max_p95_slippage_bps,
        )
    except Exception as exc:
        _write_error(paths.optimization_dir / ERROR_NAME, exc)
        print(paths.optimization_dir / ERROR_NAME)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
