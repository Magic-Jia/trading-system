from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from trading_system.app.reporting.promotion_gate_decision import write_promotion_gate_decision_report
from trading_system.app.reporting.promotion_readiness_scorecard import write_promotion_readiness_scorecard
from trading_system.app.reporting.promotion_readiness_scorecard_trend import (
    write_promotion_readiness_scorecard_trend_report,
)
from trading_system.app.reporting.real_local_simulated_live_evidence_chain import (
    write_real_local_simulated_live_evidence_chain_checkpoint,
)
from trading_system.app.reporting.rolling_simulated_live_evidence_bundle import (
    REQUIRED_COMPONENTS,
    write_rolling_simulated_live_evidence_bundle,
)
from trading_system.app.reporting.simulated_live_artifact_inventory import ROLLING_BUNDLE_COMPONENT_ARTIFACTS
from trading_system.app.reporting.simulated_live_evidence_window import write_simulated_live_evidence_window_report


SCHEMA_VERSION = "simulated_live_cadence_result.v1"
FILENAME = "simulated_live_cadence_result.json"
RUNTIME_PROMOTION_READINESS_EVIDENCE_NAME = "promotion_readiness_evidence.json"
RUNTIME_CALIBRATION_FEEDBACK_NAME = "calibration_feedback_artifact.json"
RUNTIME_CALIBRATION_RECOMMENDATION_NAME = "calibration_assumption_update_recommendation.json"
OFFLINE_PROVENANCE = "offline_local_filesystem_only"

_CANONICAL_UTC_TIMESTAMP_RE = (
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_ROLLING_BUNDLE_NAME = "rolling_simulated_live_evidence_bundle.json"
_REPLAY_LINEAGE_NAMES = {
    "replay_simulated_live_evidence_bundle.json",
    _ROLLING_BUNDLE_NAME,
    "simulated_live_evidence_window.json",
}
_STEPS = (
    "rolling_simulated_live_evidence_bundle",
    "simulated_live_evidence_window",
    "promotion_readiness_scorecard",
    "promotion_readiness_scorecard_trend",
    "real_local_simulated_live_evidence_chain_checkpoint",
    "promotion_gate_decision",
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _parse_generated_at(value: str | None) -> str:
    generated_at = value or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if not isinstance(generated_at, str):
        raise ValueError("generated_at must be a canonical UTC timestamp")
    if re.fullmatch(_CANONICAL_UTC_TIMESTAMP_RE, generated_at) is None:
        raise ValueError("generated_at must be a canonical UTC timestamp")
    parsed = datetime.fromisoformat(generated_at[:-1] + "+00:00").astimezone(UTC)
    if parsed.isoformat().replace("+00:00", "Z") != generated_at:
        raise ValueError("generated_at must be a canonical UTC timestamp")
    return generated_at


def _day_from_generated_at(generated_at: str) -> str:
    return generated_at[:10]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_json_if_present(path: Path) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _artifact_record(path: Path, *, generated_by: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "provenance": {
            "source": OFFLINE_PROVENANCE,
            "generated_by": generated_by,
        },
    }


def _component_paths(runtime_dir: Path) -> dict[str, Path]:
    artifact_paths = {spec["artifact"]: spec["path"] for spec in ROLLING_BUNDLE_COMPONENT_ARTIFACTS}
    return {component: runtime_dir / artifact_paths[component] for component in REQUIRED_COMPONENTS}


def _missing_required_artifacts(runtime_dir: Path) -> list[str]:
    missing = [
        f"{component}:{path.name}"
        for component, path in _component_paths(runtime_dir).items()
        if not path.is_file()
    ]
    scorecard_evidence = runtime_dir / RUNTIME_PROMOTION_READINESS_EVIDENCE_NAME
    if not scorecard_evidence.is_file():
        missing.append(f"promotion_readiness_evidence:{RUNTIME_PROMOTION_READINESS_EVIDENCE_NAME}")
    return missing


def _replay_provenance_reasons(runtime_dir: Path) -> list[str]:
    reasons: list[str] = []
    for name in sorted(_REPLAY_LINEAGE_NAMES):
        path = runtime_dir / name
        if not path.is_file():
            continue
        payload = _load_json_if_present(path)
        if payload is None:
            continue
        if payload.get("source_mode") == "replay" or "replay_lineage" in payload:
            reasons.append(f"replay_provenance_not_local_simulated_live:{name}")
    return reasons


def _skipped_steps(reason: str) -> dict[str, dict[str, Any]]:
    return {step: {"status": "skipped", "reason": reason} for step in _STEPS}


def _persist_result(output_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    result_path = output_dir / FILENAME
    _write_json(result_path, result)
    result["artifacts"]["simulated_live_cadence_result"] = {
        "path": str(result_path),
        "sha256": _sha256_file(result_path),
        "provenance": {
            "source": OFFLINE_PROVENANCE,
            "generated_by": "run_simulated_live_cadence",
            "hash_scope": "payload_before_self_reference",
        },
    }
    _write_json(result_path, result)
    return result


def _base_result(
    *,
    runtime_optimization_dir: Path,
    output_dir: Path,
    generated_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "runtime_optimization_dir": str(runtime_optimization_dir),
        "output_dir": str(output_dir),
        "status": "pending",
        "decision": "hold",
        "blocking_reasons": [],
        "missing_required_artifacts": [],
        "steps": {},
        "artifacts": {},
        "provenance": {
            "source": OFFLINE_PROVENANCE,
            "side_effect_boundary": {
                "real_orders": "forbidden",
                "testnet_orders": "forbidden",
                "exchange_api_calls": "forbidden",
                "credential_use": "forbidden",
                "writes": "output_dir_only",
            },
        },
    }


def run_simulated_live_cadence(
    *,
    runtime_optimization_dir: str | Path,
    output_dir: str | Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    runtime_dir = Path(runtime_optimization_dir)
    cadence_output_dir = Path(output_dir)
    cadence_output_dir.mkdir(parents=True, exist_ok=True)
    evaluated_at = _parse_generated_at(generated_at)
    result = _base_result(
        runtime_optimization_dir=runtime_dir,
        output_dir=cadence_output_dir,
        generated_at=evaluated_at,
    )

    missing = _missing_required_artifacts(runtime_dir)
    replay_reasons = _replay_provenance_reasons(runtime_dir)
    if missing or replay_reasons:
        result["status"] = "fail_closed"
        result["decision"] = "hold"
        result["missing_required_artifacts"] = missing
        result["blocking_reasons"] = [*missing, *replay_reasons]
        result["steps"] = _skipped_steps("missing_or_non_local_simulated_live_input")
        return _persist_result(cadence_output_dir, result)

    component_paths = _component_paths(runtime_dir)
    scorecard_evidence_path = runtime_dir / RUNTIME_PROMOTION_READINESS_EVIDENCE_NAME
    scorecard_evidence = _load_json_if_present(scorecard_evidence_path)
    if scorecard_evidence is None:
        result["status"] = "fail_closed"
        result["decision"] = "hold"
        result["blocking_reasons"] = [f"malformed_required_artifact:{RUNTIME_PROMOTION_READINESS_EVIDENCE_NAME}"]
        result["steps"] = _skipped_steps("malformed_required_artifact")
        return _persist_result(cadence_output_dir, result)

    artifacts: dict[str, dict[str, Any]] = {}
    steps: dict[str, dict[str, Any]] = {}
    calibration_artifacts = [
        path
        for path in (
            runtime_dir / RUNTIME_CALIBRATION_FEEDBACK_NAME,
            runtime_dir / RUNTIME_CALIBRATION_RECOMMENDATION_NAME,
        )
        if path.is_file()
    ]
    try:
        rolling_path = cadence_output_dir / "rolling_simulated_live_evidence_bundle.json"
        rolling = write_rolling_simulated_live_evidence_bundle(
            rolling_path,
            components=component_paths,
            generated_at=evaluated_at,
            max_artifact_age_seconds=86_400,
        )
        rolling = {
            **rolling,
            "session_id": f"phase9-cadence-{_day_from_generated_at(evaluated_at)}",
            "day": _day_from_generated_at(evaluated_at),
            "observed_at": evaluated_at,
            "evaluated_at": evaluated_at,
        }
        _write_json(rolling_path, rolling)
        steps["rolling_simulated_live_evidence_bundle"] = {
            "status": "generated",
            "decision": rolling["decision"],
            "reason_codes": rolling["reason_codes"],
        }
        artifacts["rolling_simulated_live_evidence_bundle"] = _artifact_record(
            rolling_path,
            generated_by="write_rolling_simulated_live_evidence_bundle",
        )

        window_path = cadence_output_dir / "simulated_live_evidence_window.json"
        window = write_simulated_live_evidence_window_report(
            window_path,
            bundles=[rolling_path],
            generated_at=evaluated_at,
            min_distinct_sessions=1,
        )
        steps["simulated_live_evidence_window"] = {
            "status": "generated",
            "decision": window["decision"],
            "reason_codes": window["reason_codes"],
        }
        artifacts["simulated_live_evidence_window"] = _artifact_record(
            window_path,
            generated_by="write_simulated_live_evidence_window_report",
        )

        scorecard_path = cadence_output_dir / "promotion_readiness_scorecard.json"
        scorecard = write_promotion_readiness_scorecard(
            scorecard_path,
            evidence=scorecard_evidence,
            generated_at=evaluated_at,
        )
        steps["promotion_readiness_scorecard"] = {
            "status": "generated",
            "decision": scorecard["decision"],
            "score": scorecard["scores"]["promotion_readiness"],
        }
        artifacts["promotion_readiness_scorecard"] = _artifact_record(
            scorecard_path,
            generated_by="write_promotion_readiness_scorecard",
        )

        trend_path = cadence_output_dir / "promotion_readiness_scorecard_trend.json"
        trend = write_promotion_readiness_scorecard_trend_report(
            trend_path,
            scorecards=[scorecard_path],
            generated_at=evaluated_at,
            min_sample_count=1,
        )
        steps["promotion_readiness_scorecard_trend"] = {
            "status": "generated",
            "decision": trend["decision"],
            "reason_codes": trend["reasons"],
        }
        artifacts["promotion_readiness_scorecard_trend"] = _artifact_record(
            trend_path,
            generated_by="write_promotion_readiness_scorecard_trend_report",
        )

        chain_path = cadence_output_dir / "real_local_simulated_live_evidence_chain_checkpoint.json"
        chain = write_real_local_simulated_live_evidence_chain_checkpoint(
            chain_path,
            evidence_window_path=window_path,
            scorecard_trend_path=trend_path,
            generated_at=evaluated_at,
        )
        steps["real_local_simulated_live_evidence_chain_checkpoint"] = {
            "status": "generated",
            "decision": chain["final_chain_decision"],
            "reason_codes": chain["final_reason_codes"],
        }
        artifacts["real_local_simulated_live_evidence_chain_checkpoint"] = _artifact_record(
            chain_path,
            generated_by="write_real_local_simulated_live_evidence_chain_checkpoint",
        )

        gate_path = cadence_output_dir / "promotion_gate_decision.json"
        gate = write_promotion_gate_decision_report(
            gate_path,
            simulated_live_evidence_window=window_path,
            promotion_readiness_scorecard_trend=trend_path,
            calibration_artifacts=calibration_artifacts,
            generated_at=evaluated_at,
        )
        steps["promotion_gate_decision"] = {
            "status": "generated",
            "decision": gate["decision"],
            "blocking_reasons": gate["blocking_reasons"],
        }
        artifacts["promotion_gate_decision"] = _artifact_record(
            gate_path,
            generated_by="write_promotion_gate_decision_report",
        )
    except Exception as exc:
        result["status"] = "fail_closed"
        result["decision"] = "hold"
        result["blocking_reasons"] = [f"cadence_generation_failed:{type(exc).__name__}:{exc}"]
        result["steps"] = {
            **steps,
            **{step: {"status": "skipped", "reason": "upstream_generation_failed"} for step in _STEPS if step not in steps},
        }
        result["artifacts"] = artifacts
        return _persist_result(cadence_output_dir, result)

    result["status"] = "completed"
    result["decision"] = gate["decision"]
    result["blocking_reasons"] = list(gate["blocking_reasons"])
    result["steps"] = steps
    result["artifacts"] = artifacts
    return _persist_result(cadence_output_dir, result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the offline fail-closed simulated-live cadence chain")
    parser.add_argument("--runtime-optimization-dir", required=True, help="Directory containing local runtime artifacts")
    parser.add_argument("--output-dir", required=True, help="Directory for generated cadence artifacts")
    parser.add_argument("--generated-at", default=None, help="Canonical UTC generation timestamp")
    args = parser.parse_args()

    payload = run_simulated_live_cadence(
        runtime_optimization_dir=args.runtime_optimization_dir,
        output_dir=args.output_dir,
        generated_at=args.generated_at,
    )
    print(
        "SIMULATED_LIVE_CADENCE_RESULT_JSON",
        json.dumps(
            {
                "output": str(Path(args.output_dir) / FILENAME),
                "status": payload["status"],
                "decision": payload["decision"],
                "blocking_reasons": payload["blocking_reasons"],
            },
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    main()


__all__ = [
    "FILENAME",
    "OFFLINE_PROVENANCE",
    "RUNTIME_CALIBRATION_FEEDBACK_NAME",
    "RUNTIME_CALIBRATION_RECOMMENDATION_NAME",
    "RUNTIME_PROMOTION_READINESS_EVIDENCE_NAME",
    "SCHEMA_VERSION",
    "run_simulated_live_cadence",
]
