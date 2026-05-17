from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "real_local_simulated_live_evidence_chain_checkpoint.v1"
SOURCE_MODE = "simulated_live_local"
FILENAME = "real_local_simulated_live_evidence_chain_checkpoint.json"

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _duplicate_rejecting_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"duplicate JSON field: {key}")
        payload[key] = value
    return payload


def _is_canonical_utc_timestamp(value: str) -> bool:
    if _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC).isoformat().replace("+00:00", "Z") == value


def _parse_generated_at(value: str | None) -> str:
    generated_at = value or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if type(generated_at) is not str or not _is_canonical_utc_timestamp(generated_at):
        raise ValueError("generated_at must be a canonical UTC timestamp")
    return generated_at


def _load_json_artifact(path: str | Path, artifact_name: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    artifact_path = Path(path)
    source: dict[str, Any] = {"path": str(artifact_path)}
    try:
        raw_bytes = artifact_path.read_bytes()
    except OSError:
        source["error"] = "missing_required_artifact"
        return None, source
    source.update({"bytes": len(raw_bytes), "sha256": _sha256_bytes(raw_bytes)})
    try:
        payload = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_duplicate_rejecting_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        source["error"] = "malformed_artifact"
        return None, source
    if not isinstance(payload, dict):
        source["error"] = "malformed_artifact"
        return None, source
    source["schema_version"] = payload.get("schema_version") if isinstance(payload.get("schema_version"), str) else None
    source["artifact_name"] = artifact_name
    return payload, source


def _generated_at_datetime(payload: Mapping[str, Any] | None) -> datetime | None:
    if payload is None:
        return None
    generated_at = payload.get("generated_at")
    if type(generated_at) is not str or not _is_canonical_utc_timestamp(generated_at):
        return None
    return datetime.fromisoformat(generated_at[:-1] + "+00:00").astimezone(UTC)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return ["malformed_reason_codes"]
    reasons: list[str] = []
    for reason in value:
        if not isinstance(reason, str):
            return ["malformed_reason_codes"]
        reasons.append(reason)
    return reasons


def _decision(value: Any, *, allowed: set[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _required_failure_summary(reason: str) -> dict[str, Any]:
    return {
        "decision": "reject",
        "reason_codes": [reason],
        "checks": {},
    }


def _evidence_window_summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return _required_failure_summary("missing_required_artifact")
    if payload.get("schema_version") != "simulated_live_evidence_window.v1":
        return _required_failure_summary("malformed_required_artifact")
    decision = _decision(payload.get("decision"), allowed={"pass", "hold"})
    if decision is None:
        return _required_failure_summary("malformed_required_artifact")
    checks = payload.get("checks")
    return {
        "decision": decision,
        "reason_codes": _string_list(payload.get("reason_codes")),
        "checks": checks if isinstance(checks, Mapping) else {},
    }


def _scorecard_trend_summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return _required_failure_summary("missing_required_artifact")
    if payload.get("schema_version") != "promotion_readiness_scorecard_trend.v1":
        return _required_failure_summary("malformed_required_artifact")
    if payload.get("mode") != "simulated_live":
        return _required_failure_summary("malformed_required_artifact")
    decision = _decision(payload.get("decision"), allowed={"pass", "hold", "reject"})
    if decision is None:
        return _required_failure_summary("malformed_required_artifact")
    checks = payload.get("checks")
    return {
        "decision": decision,
        "reason_codes": _string_list(payload.get("reasons")),
        "checks": checks if isinstance(checks, Mapping) else {},
    }


def _calibration_summary(payload: Mapping[str, Any] | None, source: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None and source is None:
        return {
            "provided": False,
            "feedback_decision": None,
            "recommendation_decision": None,
            "reason_codes": [],
            "human_review_summary": {
                "required": False,
                "reason_codes": [],
                "recommended_update_count": 0,
            },
        }
    if payload is None:
        return {
            "provided": True,
            "feedback_decision": None,
            "recommendation_decision": "reject",
            "reason_codes": [str(source.get("error", "malformed_artifact")) if source else "malformed_artifact"],
            "human_review_summary": {
                "required": True,
                "reason_codes": ["calibration_artifact_invalid"],
                "recommended_update_count": 0,
            },
        }

    schema_version = payload.get("schema_version")
    if schema_version == "calibration_feedback_artifact.v1":
        decision = _decision(payload.get("decision"), allowed={"ready", "fail_closed"})
        reasons = _string_list(payload.get("reasons", []))
        return {
            "provided": True,
            "feedback_decision": decision or "fail_closed",
            "recommendation_decision": None,
            "reason_codes": reasons if decision is not None else ["malformed_calibration_artifact"],
            "human_review_summary": {
                "required": decision != "ready",
                "reason_codes": reasons if decision != "ready" else [],
                "recommended_update_count": 0,
            },
        }
    if schema_version == "calibration_assumption_update_recommendation.v1":
        decision = _decision(payload.get("decision"), allowed={"no_change", "review", "reject"})
        rationale = payload.get("rationale")
        reason_codes = _string_list(rationale.get("reason_codes", []) if isinstance(rationale, Mapping) else [])
        updates = payload.get("recommended_assumption_updates")
        update_count = len(updates) if isinstance(updates, list) else 0
        return {
            "provided": True,
            "feedback_decision": None,
            "recommendation_decision": decision or "reject",
            "reason_codes": reason_codes if decision is not None else ["malformed_calibration_artifact"],
            "human_review_summary": {
                "required": decision != "no_change",
                "reason_codes": reason_codes if decision != "no_change" else [],
                "recommended_update_count": update_count,
            },
        }
    return {
        "provided": True,
        "feedback_decision": None,
        "recommendation_decision": "reject",
        "reason_codes": ["malformed_calibration_artifact"],
        "human_review_summary": {
            "required": True,
            "reason_codes": ["calibration_artifact_invalid"],
            "recommended_update_count": 0,
        },
    }


def _final_decision(
    evidence_window: Mapping[str, Any],
    scorecard_trend: Mapping[str, Any],
    calibration: Mapping[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    reject = False

    window_decision = evidence_window["decision"]
    if window_decision == "reject":
        reject = True
        reasons.extend(evidence_window["reason_codes"])
    elif window_decision == "hold":
        reasons.append("evidence_window_hold")
        reasons.extend(evidence_window["reason_codes"])

    trend_decision = scorecard_trend["decision"]
    if trend_decision == "reject":
        reject = True
        reasons.append("scorecard_trend_reject")
        reasons.extend(scorecard_trend["reason_codes"])
    elif trend_decision == "hold":
        reasons.append("scorecard_trend_hold")
        reasons.extend(scorecard_trend["reason_codes"])

    if calibration["provided"]:
        calibration_decision = calibration["recommendation_decision"] or calibration["feedback_decision"]
        if calibration_decision in {"reject", "fail_closed"}:
            reject = True
            reasons.append("calibration_reject")
        elif calibration_decision in {"review"}:
            reasons.append("calibration_human_review_required")

    if reject:
        return "reject", sorted(set(reasons))
    if reasons:
        return "hold", sorted(set(reasons))
    return "pass", []


def build_real_local_simulated_live_evidence_chain_checkpoint(
    *,
    evidence_window_path: str | Path,
    scorecard_trend_path: str | Path,
    calibration_feedback_path: str | Path | None = None,
    calibration_recommendation_path: str | Path | None = None,
    generated_at: str | None = None,
    max_required_artifact_age_seconds: int | float | None = None,
) -> dict[str, Any]:
    if calibration_feedback_path is not None and calibration_recommendation_path is not None:
        raise ValueError("provide only one calibration artifact path")
    if max_required_artifact_age_seconds is not None:
        if isinstance(max_required_artifact_age_seconds, bool) or not isinstance(max_required_artifact_age_seconds, (int, float)):
            raise ValueError("max_required_artifact_age_seconds must be numeric")
        if max_required_artifact_age_seconds < 0:
            raise ValueError("max_required_artifact_age_seconds must be non-negative")

    checkpoint_generated_at = _parse_generated_at(generated_at)
    checkpoint_time = datetime.fromisoformat(checkpoint_generated_at[:-1] + "+00:00").astimezone(UTC)
    window_payload, window_source = _load_json_artifact(evidence_window_path, "evidence_window")
    trend_payload, trend_source = _load_json_artifact(scorecard_trend_path, "scorecard_trend")

    calibration_payload = None
    calibration_source = None
    calibration_path = calibration_recommendation_path or calibration_feedback_path
    calibration_key = "calibration_recommendation" if calibration_recommendation_path is not None else "calibration_feedback"
    if calibration_path is not None:
        calibration_payload, calibration_source = _load_json_artifact(calibration_path, calibration_key)

    for payload, source in ((window_payload, window_source), (trend_payload, trend_source)):
        if source.get("error") is not None:
            continue
        artifact_time = _generated_at_datetime(payload)
        if artifact_time is None:
            source["error"] = "malformed_artifact"
            continue
        if artifact_time > checkpoint_time:
            source["error"] = "non_monotonic_required_artifact"
            continue
        if (
            max_required_artifact_age_seconds is not None
            and (checkpoint_time - artifact_time).total_seconds() > float(max_required_artifact_age_seconds)
        ):
            source["error"] = "stale_required_artifact"

    evidence_window = _evidence_window_summary(window_payload if window_source.get("error") is None else None)
    scorecard_trend = _scorecard_trend_summary(trend_payload if trend_source.get("error") is None else None)
    if window_source.get("error") == "malformed_artifact":
        evidence_window = _required_failure_summary("malformed_required_artifact")
    if window_source.get("error") in {"stale_required_artifact", "non_monotonic_required_artifact"}:
        evidence_window = _required_failure_summary(str(window_source["error"]))
    if trend_source.get("error") == "malformed_artifact":
        scorecard_trend = _required_failure_summary("malformed_required_artifact")
    if trend_source.get("error") in {"stale_required_artifact", "non_monotonic_required_artifact"}:
        scorecard_trend = _required_failure_summary(str(trend_source["error"]))
    calibration = _calibration_summary(
        calibration_payload if calibration_source is None or calibration_source.get("error") is None else None,
        calibration_source,
    )
    final_decision, final_reasons = _final_decision(evidence_window, scorecard_trend, calibration)

    input_paths = {
        "evidence_window": str(Path(evidence_window_path)),
        "scorecard_trend": str(Path(scorecard_trend_path)),
    }
    artifacts = {
        "evidence_window": window_source,
        "scorecard_trend": trend_source,
    }
    if calibration_path is not None and calibration_source is not None:
        input_paths[calibration_key] = str(Path(calibration_path))
        artifacts[calibration_key] = calibration_source

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": checkpoint_generated_at,
        "source_mode": SOURCE_MODE,
        "input_artifact_paths": input_paths,
        "lineage": {
            "artifacts": artifacts,
            "required_artifacts_present": "error" not in window_source and "error" not in trend_source,
            "required_artifacts_well_formed": evidence_window["decision"] != "reject" and scorecard_trend["decision"] != "reject",
        },
        "evidence_window": evidence_window,
        "scorecard_trend": scorecard_trend,
        "calibration": calibration,
        "final_chain_decision": final_decision,
        "final_reason_codes": final_reasons,
        "side_effect_boundary": "offline_local_filesystem_only",
        "caveats": [
            "Simulated-live local checkpoint only; no real orders, testnet orders, exchange API calls, or credential discovery.",
            "Missing, malformed, duplicate, non-monotonic, stale, or insufficient upstream evidence fails closed through upstream decisions.",
        ],
    }


def write_real_local_simulated_live_evidence_chain_checkpoint(
    output_path: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    payload = build_real_local_simulated_live_evidence_chain_checkpoint(**kwargs)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a real-local simulated-live evidence chain checkpoint")
    parser.add_argument("--evidence-window", required=True, help="Local simulated_live_evidence_window JSON path")
    parser.add_argument("--scorecard-trend", required=True, help="Local promotion_readiness_scorecard_trend JSON path")
    parser.add_argument("--calibration-feedback", default=None, help="Optional local calibration feedback JSON path")
    parser.add_argument("--calibration-recommendation", default=None, help="Optional local calibration recommendation JSON path")
    parser.add_argument("--output", required=True, help="Output checkpoint JSON path")
    parser.add_argument("--generated-at", default=None, help="Canonical UTC generation timestamp")
    args = parser.parse_args()

    payload = write_real_local_simulated_live_evidence_chain_checkpoint(
        args.output,
        evidence_window_path=args.evidence_window,
        scorecard_trend_path=args.scorecard_trend,
        calibration_feedback_path=args.calibration_feedback,
        calibration_recommendation_path=args.calibration_recommendation,
        generated_at=args.generated_at,
    )
    print(
        "REAL_LOCAL_SIMULATED_LIVE_EVIDENCE_CHAIN_JSON",
        json.dumps(
            {
                "output": args.output,
                "decision": payload["final_chain_decision"],
                "reason_codes": payload["final_reason_codes"],
                "source_mode": payload["source_mode"],
            },
            sort_keys=True,
        ),
    )


__all__ = [
    "FILENAME",
    "SCHEMA_VERSION",
    "SOURCE_MODE",
    "build_real_local_simulated_live_evidence_chain_checkpoint",
    "write_real_local_simulated_live_evidence_chain_checkpoint",
]
