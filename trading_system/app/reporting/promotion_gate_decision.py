from __future__ import annotations

import argparse
import hashlib
import json
import re
from numbers import Real
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "promotion_gate_decision.v1"
FILENAME = "promotion_gate_decision.json"
EXECUTION_REALISM_THRESHOLDS = {
    "min_execution_samples": 10,
    "min_maker_fill_probability": 0.5,
    "max_taker_slippage_p95_bps": 10.0,
}

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_IDENTITY_WARNING_REASONS = {
    "duplicate_session_identity",
    "duplicate_day_identity",
    "duplicate_observed_at",
    "duplicate_evaluated_at",
    "non_monotonic_generated_at",
    "non_monotonic_as_of",
    "timestamp_ordering",
    "duplicate_scorecard",
}
_REJECT_REASONS = {
    "malformed_artifact",
    "missing_artifact",
    "schema_version_invalid",
    "mode_invalid",
    "decision_invalid",
    "source_mode_invalid",
    "side_effect_boundary_invalid",
    "mutation_boundary_invalid",
    "malformed_scorecard",
    "timestamp_ordering",
    "duplicate_scorecard",
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    parsed = float(value)
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else None


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _duplicate_rejecting_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"duplicate JSON field: {key}")
        payload[key] = value
    return payload


def _load_artifact(value: Mapping[str, Any] | str | Path) -> tuple[dict[str, Any] | None, dict[str, Any], str | None]:
    if isinstance(value, (str, Path)):
        path = Path(value)
        try:
            raw_bytes = path.read_bytes()
        except OSError:
            return None, {"path": str(path)}, "missing_artifact"
        try:
            payload = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_duplicate_rejecting_pairs)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None, {"path": str(path), "bytes": len(raw_bytes), "sha256": _sha256_bytes(raw_bytes)}, "malformed_artifact"
        if not isinstance(payload, dict):
            return None, {"path": str(path), "bytes": len(raw_bytes), "sha256": _sha256_bytes(raw_bytes)}, "malformed_artifact"
        return payload, {"path": str(path), "bytes": len(raw_bytes), "sha256": _sha256_bytes(raw_bytes)}, None
    if not isinstance(value, Mapping):
        return None, {}, "malformed_artifact"
    payload = dict(value)
    return payload, {"sha256": _sha256_bytes(_canonical_json_bytes(payload))}, None


def _generated_at(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _parse_timestamp(value, "generated_at")
    return value


def _parse_timestamp(value: Any, field: str) -> datetime:
    if type(value) is not str or _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} must be a canonical UTC timestamp") from exc
    return parsed.astimezone(UTC)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if type(item) is str]


def _artifact_identity(payload: Mapping[str, Any] | None, source: Mapping[str, Any], artifact_type: str) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "artifact_type": artifact_type,
        "schema_version": payload.get("schema_version") if payload is not None else None,
        "generated_at": payload.get("generated_at") if payload is not None else None,
        "source": dict(source),
    }
    if payload is None:
        return identity
    for key in ("decision", "mode", "side_effect_boundary", "strategy_config_mutation", "assumptions_file_mutation"):
        if key in payload:
            identity[key] = payload[key]
    if artifact_type == "calibration_assumption_update_recommendation":
        source_payload = payload.get("source")
        if isinstance(source_payload, Mapping):
            identity["source_artifact_id"] = source_payload.get("artifact_id")
    return identity


def _check_from_errors(*, status: str, errors: list[str], warnings: list[str], reasons: list[str]) -> dict[str, Any]:
    return {
        "status": status,
        "blocking_reasons": reasons,
        "errors": errors,
        "warnings": warnings,
    }


def _status_from_decision(decision: Any, *, pass_values: set[str], hold_values: set[str], reject_values: set[str]) -> str:
    if decision in pass_values:
        return "pass"
    if decision in hold_values:
        return "hold"
    if decision in reject_values:
        return "reject"
    return "reject"


def _normalize_window(value: Mapping[str, Any] | str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload, source, load_error = _load_artifact(value)
    reasons: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    if load_error is not None:
        reasons.append(f"simulated_live_evidence_window:{load_error}")
        errors.append(load_error)
        return _check_from_errors(status="reject", errors=errors, warnings=warnings, reasons=reasons), _artifact_identity(payload, source, "simulated_live_evidence_window")

    assert payload is not None
    if payload.get("schema_version") != "simulated_live_evidence_window.v1":
        reasons.append("simulated_live_evidence_window:schema_version_invalid")
        errors.append("schema_version_invalid")
    decision = payload.get("decision")
    if decision not in {"pass", "hold"}:
        reasons.append("simulated_live_evidence_window:decision_invalid")
        errors.append("decision_invalid")
    for reason in _string_list(payload.get("reason_codes")):
        reasons.append(f"simulated_live_evidence_window:{reason}")
        if reason in _IDENTITY_WARNING_REASONS:
            warnings.append(reason)
    if decision == "hold" and not reasons:
        reasons.append("simulated_live_evidence_window:decision_hold")

    status = _status_from_decision(decision, pass_values={"pass"}, hold_values={"hold"}, reject_values=set())
    if errors:
        status = "reject"
    return _check_from_errors(status=status, errors=errors, warnings=warnings, reasons=reasons), _artifact_identity(payload, source, "simulated_live_evidence_window")


def _normalize_trend(value: Mapping[str, Any] | str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload, source, load_error = _load_artifact(value)
    reasons: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    if load_error is not None:
        reasons.append(f"promotion_readiness_scorecard_trend:{load_error}")
        errors.append(load_error)
        return _check_from_errors(status="reject", errors=errors, warnings=warnings, reasons=reasons), _artifact_identity(payload, source, "promotion_readiness_scorecard_trend")

    assert payload is not None
    if payload.get("schema_version") != "promotion_readiness_scorecard_trend.v1":
        reasons.append("promotion_readiness_scorecard_trend:schema_version_invalid")
        errors.append("schema_version_invalid")
    if payload.get("mode") != "simulated_live":
        reasons.append("promotion_readiness_scorecard_trend:mode_invalid")
        errors.append("mode_invalid")
    decision = payload.get("decision")
    if decision not in {"pass", "hold", "reject"}:
        reasons.append("promotion_readiness_scorecard_trend:decision_invalid")
        errors.append("decision_invalid")
    for reason in _string_list(payload.get("reasons")):
        reasons.append(f"promotion_readiness_scorecard_trend:{reason}")
        if reason in _IDENTITY_WARNING_REASONS:
            warnings.append(reason)
        if reason in _REJECT_REASONS:
            errors.append(reason)
    if decision in {"hold", "reject"} and not reasons:
        reasons.append(f"promotion_readiness_scorecard_trend:decision_{decision}")

    status = _status_from_decision(decision, pass_values={"pass"}, hold_values={"hold"}, reject_values={"reject"})
    if errors:
        status = "reject"
    return _check_from_errors(status=status, errors=errors, warnings=warnings, reasons=reasons), _artifact_identity(payload, source, "promotion_readiness_scorecard_trend")


def _normalize_calibration_artifact(value: Mapping[str, Any] | str | Path, index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    payload, source, load_error = _load_artifact(value)
    reasons: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    artifact_type = f"calibration_artifact_{index}"
    if load_error is not None:
        reasons.append(f"calibration:{load_error}")
        errors.append(load_error)
        return _check_from_errors(status="reject", errors=errors, warnings=warnings, reasons=reasons), _artifact_identity(payload, source, artifact_type)

    assert payload is not None
    schema_version = payload.get("schema_version")
    decision = payload.get("decision")
    status = "reject"
    if schema_version == "calibration_feedback_artifact.v1":
        artifact_type = "calibration_feedback_artifact"
        if payload.get("side_effect_boundary") != "offline_local_only":
            reasons.append("calibration:side_effect_boundary_invalid")
            errors.append("side_effect_boundary_invalid")
        if payload.get("strategy_config_mutation") != "forbidden":
            reasons.append("calibration:mutation_boundary_invalid")
            errors.append("mutation_boundary_invalid")
        if decision == "ready":
            status = "pass"
        elif decision == "fail_closed":
            status = "reject"
            reasons.append("calibration:decision_fail_closed")
        else:
            reasons.append("calibration:decision_invalid")
            errors.append("decision_invalid")
        for reason in _string_list(payload.get("reasons")):
            reasons.append(f"calibration:{reason}")
    elif schema_version == "calibration_assumption_update_recommendation.v1":
        artifact_type = "calibration_assumption_update_recommendation"
        if payload.get("side_effect_boundary") != "offline_local_only":
            reasons.append("calibration:side_effect_boundary_invalid")
            errors.append("side_effect_boundary_invalid")
        if payload.get("assumptions_file_mutation") != "forbidden":
            reasons.append("calibration:mutation_boundary_invalid")
            errors.append("mutation_boundary_invalid")
        rationale = payload.get("rationale")
        rationale_reasons = _string_list(rationale.get("reason_codes") if isinstance(rationale, Mapping) else None)
        if decision == "no_change":
            status = "pass"
        elif decision == "review":
            status = "hold"
            warnings.append("human_review_required_for_assumption_update")
            for reason in rationale_reasons:
                reasons.append(f"calibration:{reason}")
            if "calibration:review_required_for_assumption_update" not in reasons:
                reasons.append("calibration:review_required_for_assumption_update")
        elif decision == "reject":
            status = "reject"
            for reason in rationale_reasons:
                reasons.append(f"calibration:{reason}")
            if not rationale_reasons:
                reasons.append("calibration:decision_reject")
        else:
            reasons.append("calibration:decision_invalid")
            errors.append("decision_invalid")
    else:
        reasons.append("calibration:schema_version_invalid")
        errors.append("schema_version_invalid")

    if errors:
        status = "reject"
    check = _check_from_errors(status=status, errors=errors, warnings=warnings, reasons=reasons)
    return check, _artifact_identity(payload, source, artifact_type)


def _normalize_calibration(artifacts: Sequence[Mapping[str, Any] | str | Path]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []
    for index, artifact in enumerate(artifacts):
        check, identity = _normalize_calibration_artifact(artifact, index)
        checks.append(check)
        identities.append(identity)

    statuses = [check["status"] for check in checks]
    if not statuses:
        status = "hold"
        reasons = ["calibration:missing_calibration_artifact"]
    elif "reject" in statuses:
        status = "reject"
        reasons = []
    elif "hold" in statuses:
        status = "hold"
        reasons = []
    else:
        status = "pass"
        reasons = []

    for check in checks:
        reasons.extend(check["blocking_reasons"])

    return {
        "status": status,
        "blocking_reasons": sorted(dict.fromkeys(reasons)),
        "artifact_count": len(artifacts),
        "artifacts": checks,
        "assumptions_file_mutation": "forbidden",
        "strategy_config_mutation": "forbidden",
    }, identities


def _normalize_professional_evidence_chain(
    value: Mapping[str, Any] | str | Path | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if value is None:
        return {
            "status": "pass",
            "blocking_reasons": [],
            "errors": [],
            "warnings": [],
        }, None
    payload, source, load_error = _load_artifact(value)
    reasons: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    if load_error is not None:
        reasons.append(f"professional_evidence_chain:{load_error}")
        errors.append(load_error)
        return _check_from_errors(
            status="reject", errors=errors, warnings=warnings, reasons=reasons
        ), _artifact_identity(payload, source, "professional_evidence_chain")

    assert payload is not None
    if payload.get("schema_version") != "backtest_evidence_chain.v1":
        reasons.append("professional_evidence_chain:schema_version_invalid")
        errors.append("schema_version_invalid")
    raw_summary = payload.get("summary")
    summary = raw_summary if isinstance(raw_summary, Mapping) else {}
    decision = summary.get("decision")
    if decision == "hold":
        reasons.append("professional_evidence_chain:decision_hold")
    elif decision not in {"pass", "hold"}:
        reasons.append("professional_evidence_chain:decision_invalid")
        errors.append("decision_invalid")
    raw_execution_realism = payload.get("execution_realism")
    execution_realism = raw_execution_realism if isinstance(raw_execution_realism, Mapping) else {}
    execution_status = execution_realism.get("status")
    if execution_status != "pass":
        reasons.append("professional_evidence_chain:execution_realism_hold")
        for reason in _string_list(execution_realism.get("reason_codes")):
            reasons.append(f"professional_evidence_chain:execution_realism:{reason}")
    sample_count = execution_realism.get("sample_count")
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < EXECUTION_REALISM_THRESHOLDS["min_execution_samples"]:
        reasons.append("professional_evidence_chain:execution_realism_sample_count_below_floor")
    maker_fill_probability = _finite_number(execution_realism.get("maker_fill_probability"))
    if maker_fill_probability is None or maker_fill_probability < EXECUTION_REALISM_THRESHOLDS["min_maker_fill_probability"]:
        reasons.append("professional_evidence_chain:maker_fill_probability_below_floor")
    raw_taker_slippage = execution_realism.get("taker_slippage_bps")
    taker_slippage = raw_taker_slippage if isinstance(raw_taker_slippage, Mapping) else {}
    taker_slippage_p95 = _finite_number(taker_slippage.get("p95"))
    if taker_slippage_p95 is None or taker_slippage_p95 > EXECUTION_REALISM_THRESHOLDS["max_taker_slippage_p95_bps"]:
        reasons.append("professional_evidence_chain:taker_slippage_p95_above_ceiling")

    status = "reject" if errors else ("hold" if reasons else "pass")
    check = _check_from_errors(status=status, errors=errors, warnings=warnings, reasons=reasons)
    check["thresholds"] = dict(EXECUTION_REALISM_THRESHOLDS)
    return check, _artifact_identity(
        payload, source, "professional_evidence_chain"
    )


def build_promotion_gate_decision_report(
    *,
    simulated_live_evidence_window: Mapping[str, Any] | str | Path,
    promotion_readiness_scorecard_trend: Mapping[str, Any] | str | Path,
    calibration_artifacts: Sequence[Mapping[str, Any] | str | Path] = (),
    professional_evidence_chain: Mapping[str, Any] | str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    evaluated_at = _generated_at(generated_at)
    window_check, window_identity = _normalize_window(simulated_live_evidence_window)
    trend_check, trend_identity = _normalize_trend(promotion_readiness_scorecard_trend)
    calibration_check, calibration_identities = _normalize_calibration(calibration_artifacts)
    professional_evidence_check, professional_evidence_identity = _normalize_professional_evidence_chain(
        professional_evidence_chain
    )

    checks = {
        "simulated_live_evidence_window": window_check,
        "promotion_readiness_scorecard_trend": trend_check,
        "calibration": calibration_check,
        "professional_evidence_chain": professional_evidence_check,
    }
    statuses = [
        window_check["status"],
        trend_check["status"],
        calibration_check["status"],
        professional_evidence_check["status"],
    ]
    blocking_reasons = sorted(
        dict.fromkeys(
            window_check["blocking_reasons"]
            + trend_check["blocking_reasons"]
            + calibration_check["blocking_reasons"]
            + professional_evidence_check["blocking_reasons"]
        )
    )
    if "reject" in statuses:
        decision = "reject"
    elif "hold" in statuses:
        decision = "hold"
    else:
        decision = "candidate_for_paper_promotion"

    identity_warnings = window_check["warnings"] + trend_check["warnings"]
    checks["identity_continuity"] = {
        "non_monotonic_or_duplicate_inputs_present": bool(identity_warnings),
        "warnings": sorted(dict.fromkeys(identity_warnings)),
    }
    human_review_required = decision != "candidate_for_paper_promotion" or any(
        "review_required_for_assumption_update" in reason for reason in blocking_reasons
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": evaluated_at,
        "decision": decision,
        "blocking_reasons": blocking_reasons,
        "checks": checks,
        "included_artifact_identities": [
            item
            for item in [window_identity, trend_identity, *calibration_identities, professional_evidence_identity]
            if item is not None
        ],
        "human_review_required": human_review_required,
        "source_mode": {
            "mode": "simulated_live",
            "side_effect_boundary": "offline_local_filesystem_only",
            "real_orders": "forbidden",
            "testnet_orders": "forbidden",
            "exchange_api_calls": "forbidden",
            "credential_use": "forbidden",
        },
        "provenance": {
            "input_artifact_count": 2 + len(calibration_artifacts) + (1 if professional_evidence_chain is not None else 0),
            "decision_policy": "fail_closed",
            "promotion_scope": "paper_promotion_candidate_only",
        },
        "caveats": [
            "This report consumes local machine-readable simulated-live artifacts only.",
            "It does not place real orders, testnet orders, or call exchange APIs.",
            "Calibration assumption recommendations are human-review inputs only and never mutate assumptions.",
        ],
    }


def write_promotion_gate_decision_report(output_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    payload = build_promotion_gate_decision_report(**kwargs)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a fail-closed simulated-live promotion gate decision report")
    parser.add_argument("--simulated-live-evidence-window", required=True, help="Local simulated-live evidence window JSON")
    parser.add_argument("--promotion-readiness-scorecard-trend", required=True, help="Local promotion readiness trend JSON")
    parser.add_argument("--calibration-artifact", action="append", default=[], help="Local calibration feedback/recommendation JSON")
    parser.add_argument("--professional-evidence-chain", default=None, help="Optional backtest_evidence_chain.json to enforce professional execution realism gates")
    parser.add_argument("--output", required=True, help="Output JSON report path")
    parser.add_argument("--generated-at", default=None, help="Canonical UTC generation timestamp")
    args = parser.parse_args()

    payload = write_promotion_gate_decision_report(
        args.output,
        simulated_live_evidence_window=Path(args.simulated_live_evidence_window),
        promotion_readiness_scorecard_trend=Path(args.promotion_readiness_scorecard_trend),
        calibration_artifacts=[Path(path) for path in args.calibration_artifact],
        professional_evidence_chain=(Path(args.professional_evidence_chain) if args.professional_evidence_chain else None),
        generated_at=args.generated_at,
    )
    print(
        "PROMOTION_GATE_DECISION_JSON",
        json.dumps(
            {
                "output": args.output,
                "decision": payload["decision"],
                "blocking_reasons": payload["blocking_reasons"],
                "human_review_required": payload["human_review_required"],
            },
            sort_keys=True,
        ),
    )


__all__ = [
    "FILENAME",
    "SCHEMA_VERSION",
    "build_promotion_gate_decision_report",
    "write_promotion_gate_decision_report",
]
