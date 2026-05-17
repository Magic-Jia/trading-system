from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "promotion_readiness_evidence.v1"
FILENAME = "promotion_readiness_evidence.json"
SOURCE_MODE = "simulated_live_local"
OFFLINE_PROVENANCE = "offline_local_filesystem_only"

SOURCE_FILES = {
    "paper_live_sim_evidence_bundle": "paper_live_sim_evidence_bundle.json",
    "daily_quality_gate_report": "daily_quality_gate_report.json",
    "rolling_tca_durability_report": "rolling_tca_durability_report.json",
    "paper_live_shadow_drift_contract": "paper_live_shadow_drift_contract.json",
    "runtime_safety_gate": "runtime_safety_gate.json",
    "passive_order_calibration_records": "passive_order_calibration_records.jsonl",
    "venue_rulebook_catalog_freshness": "venue_rulebook_catalog_freshness.json",
}

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")


def _generated_at(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _parse_timestamp(value)
    return value


def _parse_timestamp(value: Any) -> datetime:
    if type(value) is not str or _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ValueError("generated_at must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("generated_at must be a canonical UTC timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("generated_at must be a canonical UTC timestamp")
    return parsed.astimezone(UTC)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> tuple[Mapping[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"source_malformed:{path.name}:{type(exc).__name__}"
    if not isinstance(payload, Mapping):
        return None, f"source_malformed:{path.name}:not_object"
    return payload, None


def _load_jsonl(path: Path) -> tuple[list[Mapping[str, Any]] | None, str | None]:
    rows: list[Mapping[str, Any]] = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, Mapping):
                return None, f"source_malformed:{path.name}:line_{line_number}_not_object"
            rows.append(row)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"source_malformed:{path.name}:{type(exc).__name__}"
    return rows, None


def _source_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "provenance": {
            "source": OFFLINE_PROVENANCE,
            "source_mode": SOURCE_MODE,
        },
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _bools_true(payload: Mapping[str, Any], names: Iterable[str]) -> tuple[int, int]:
    checks = payload.get("checks") if isinstance(payload.get("checks"), Mapping) else {}
    total = 0
    passed = 0
    for name in names:
        total += 1
        if checks.get(name) is True:
            passed += 1
    return passed, total


def _coverage(passed: int, total: int, *, missing_floor: float = 0.0) -> float:
    if total <= 0:
        return missing_floor
    return round(max(0.0, min(1.0, passed / total)), 4)


def _component(
    *,
    as_of: str,
    coverage_score: float,
    sample_count: int,
    status: str,
    reason_codes: list[str],
    duration_hours: float | None = None,
    max_parity_drift_bps: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "as_of": as_of,
        "coverage_score": coverage_score,
        "sample_count": max(0, sample_count),
        "status": status,
        "reason_codes": reason_codes,
    }
    if duration_hours is not None:
        payload["duration_hours"] = max(0.0, duration_hours)
    if max_parity_drift_bps is not None:
        payload["max_parity_drift_bps"] = max(0.0, max_parity_drift_bps)
    return payload


def _missing_component(component_source: str, generated_at: str) -> dict[str, Any]:
    return _component(
        as_of=generated_at,
        coverage_score=0.0,
        sample_count=0,
        status="hold",
        reason_codes=[f"source_missing:{component_source}"],
    )


def _missing_durability_component(component_source: str, generated_at: str) -> dict[str, Any]:
    payload = _missing_component(component_source, generated_at)
    payload["duration_hours"] = 0.0
    return payload


def _reasons(payload: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    raw_reasons = payload.get("reasons", [])
    if not isinstance(raw_reasons, list):
        return ["malformed_reasons"]
    return [reason for reason in raw_reasons if isinstance(reason, str)]


def _latest_timestamp(*payloads: Mapping[str, Any] | None, fallback: str) -> str:
    values = [
        payload.get("generated_at")
        for payload in payloads
        if isinstance(payload, Mapping) and isinstance(payload.get("generated_at"), str)
    ]
    return max(values) if values else fallback


def _sample_count_from_daily(daily: Mapping[str, Any] | None) -> int:
    if not isinstance(daily, Mapping):
        return 0
    inputs = daily.get("inputs") if isinstance(daily.get("inputs"), Mapping) else {}
    tca = inputs.get("tca") if isinstance(inputs.get("tca"), Mapping) else {}
    sample_size = tca.get("sample_size")
    return sample_size if isinstance(sample_size, int) and not isinstance(sample_size, bool) and sample_size >= 0 else 0


def _sample_count_from_rolling(rolling: Mapping[str, Any] | None) -> int:
    if not isinstance(rolling, Mapping):
        return 0
    best = 0
    for window in rolling.get("windows", []):
        if not isinstance(window, Mapping):
            continue
        metrics = window.get("metrics") if isinstance(window.get("metrics"), Mapping) else {}
        sample_count = metrics.get("sample_count")
        if isinstance(sample_count, int) and not isinstance(sample_count, bool):
            best = max(best, sample_count)
    return max(best, _sample_count_from_daily(rolling))


def _calibration_record_count(rows: list[Mapping[str, Any]] | None) -> int:
    return len(rows) if isinstance(rows, list) else 0


def _duration_hours(bundle: Mapping[str, Any] | None) -> float | None:
    if not isinstance(bundle, Mapping):
        return None
    summary = bundle.get("summary") if isinstance(bundle.get("summary"), Mapping) else {}
    first = summary.get("first_as_of")
    last = summary.get("last_as_of")
    if not isinstance(first, str) or not isinstance(last, str):
        return None
    try:
        start = _parse_timestamp(first)
        end = _parse_timestamp(last)
    except ValueError:
        return None
    return round(max(0.0, (end - start).total_seconds() / 3600.0), 1)


def _max_parity_drift_bps(drift: Mapping[str, Any] | None) -> float | None:
    if not isinstance(drift, Mapping):
        return None
    values: list[float] = []
    comparisons = drift.get("comparisons")
    if isinstance(comparisons, Mapping):
        for comparison in comparisons.values():
            if not isinstance(comparison, Mapping):
                continue
            for field in ("slippage_bps_delta", "max_parity_drift_bps"):
                value = _number(comparison.get(field))
                if value is not None:
                    values.append(abs(value))
    return max(values) if values else None


def _data_quality(daily: Mapping[str, Any] | None, generated_at: str) -> dict[str, Any]:
    if daily is None:
        return _missing_component("daily_quality_gate_report", generated_at)
    passed, total = _bools_true(
        daily,
        (
            "evidence_bundle_verified",
            "evidence_bundle_manifest_present",
            "sufficient_sample_size",
            "tca_slippage_within_threshold",
            "rolling_tca_durability_passed",
        ),
    )
    reasons = _reasons(daily)
    decision = daily.get("decision")
    if decision != "pass_for_continued_paper" and not reasons:
        reasons = ["daily_quality_gate_not_pass"]
    return _component(
        as_of=str(daily.get("generated_at") or generated_at),
        coverage_score=_coverage(passed, total),
        sample_count=max(_sample_count_from_daily(daily), 0),
        status="pass" if not reasons and passed == total else "hold",
        reason_codes=reasons,
    )


def _execution_realism(
    runtime_safety: Mapping[str, Any] | None,
    calibration_rows: list[Mapping[str, Any]] | None,
    generated_at: str,
) -> dict[str, Any]:
    reasons: list[str] = []
    if runtime_safety is None:
        reasons.append("source_missing:runtime_safety_gate")
    if runtime_safety is not None:
        reasons.extend(_reasons(runtime_safety))
    if calibration_rows is None:
        reasons.append("source_missing:passive_order_calibration_records")
    passed, total = _bools_true(
        runtime_safety or {},
        (
            "execution_event_chain_met",
            "order_position_reconciliation_met",
            "kill_switch_dry_run_met",
        ),
    )
    sample_count = _calibration_record_count(calibration_rows)
    if sample_count == 0 and "source_missing:passive_order_calibration_records" not in reasons:
        reasons.append("calibration_records_unavailable")
    return _component(
        as_of=str(
            runtime_safety.get("generated_at")
            or (
                runtime_safety.get("evidence_source", {}).get("exported_at")
                if isinstance(runtime_safety.get("evidence_source"), Mapping)
                else None
            )
            or generated_at
        )
        if isinstance(runtime_safety, Mapping)
        else generated_at,
        coverage_score=_coverage(passed, total),
        sample_count=sample_count,
        status="pass" if not reasons and passed == total and sample_count > 0 else "hold",
        reason_codes=reasons,
    )


def _venue_rulebook(
    venue: Mapping[str, Any] | None,
    daily: Mapping[str, Any] | None,
    rolling: Mapping[str, Any] | None,
    generated_at: str,
) -> dict[str, Any]:
    if venue is None:
        daily_checks = daily.get("checks") if isinstance(daily, Mapping) and isinstance(daily.get("checks"), Mapping) else {}
        daily_rulebook_checks = (
            "venue_rulebook_catalog_present",
            "venue_rulebook_schema_valid",
            "venue_rulebook_freshness_valid",
            "exchange_filters_covered",
        )
        if all(daily_checks.get(check) is True for check in daily_rulebook_checks):
            return _component(
                as_of=_latest_timestamp(daily, rolling, fallback=generated_at),
                coverage_score=1.0,
                sample_count=0,
                status="pass",
                reason_codes=[],
            )
        return _component(
            as_of=generated_at,
            coverage_score=0.0,
            sample_count=0,
            status="hold",
            reason_codes=["source_missing:venue_rulebook_catalog_freshness"],
        )
    passed, total = _bools_true(
        venue,
        (
            "rulebook_catalog_present",
            "rulebook_schema_valid",
            "rulebook_freshness_valid",
            "exchange_filters_covered",
        ),
    )
    reasons = _reasons(venue)
    return _component(
        as_of=str(venue.get("generated_at") or generated_at),
        coverage_score=_coverage(passed, total),
        sample_count=0,
        status="pass" if not reasons and passed == total else "hold",
        reason_codes=reasons,
    )


def _derivatives_risk(runtime_safety: Mapping[str, Any] | None, daily: Mapping[str, Any] | None, generated_at: str) -> dict[str, Any]:
    if runtime_safety is None and daily is None:
        return _missing_component("runtime_safety_gate", generated_at)
    passed, total = _bools_true(
        runtime_safety or {},
        (
            "kill_switch_dry_run_met",
            "runtime_safety_artifact_schema_valid",
        ),
    )
    reasons = [reason for reason in _reasons(runtime_safety) if reason]
    return _component(
        as_of=_latest_timestamp(runtime_safety, daily, fallback=generated_at),
        coverage_score=_coverage(passed, total),
        sample_count=max(30 if runtime_safety is not None else 0, _sample_count_from_daily(daily)),
        status="pass" if not reasons and passed == total else "hold",
        reason_codes=reasons,
    )


def _cross_source_parity(drift: Mapping[str, Any] | None, generated_at: str) -> dict[str, Any]:
    if drift is None:
        return _missing_component("paper_live_shadow_drift_contract", generated_at)
    passed, total = _bools_true(
        drift,
        (
            "paper_live_shadow_drift_contract_schema_valid",
            "paper_live_shadow_material_drift_absent",
            "offline_simulated_evidence_only",
            "fail_closed",
        ),
    )
    reasons = _reasons(drift)
    drift_bps = _max_parity_drift_bps(drift)
    return _component(
        as_of=str(drift.get("generated_at") or generated_at),
        coverage_score=_coverage(passed, total),
        sample_count=40 if drift_bps is not None else 0,
        status="pass" if not reasons and passed == total and drift_bps is not None else "hold",
        reason_codes=reasons,
        max_parity_drift_bps=drift_bps,
    )


def _live_sim_durability(bundle: Mapping[str, Any] | None, rolling: Mapping[str, Any] | None, generated_at: str) -> dict[str, Any]:
    if bundle is None:
        return _missing_durability_component("paper_live_sim_evidence_bundle", generated_at)
    passed, total = _bools_true(
        bundle,
        (
            "paper_live_sim_evidence_complete",
            "paper_live_sim_schema_valid",
            "paper_live_sim_freshness_valid",
            "paper_live_sim_reconciled",
        ),
    )
    reasons = _reasons(bundle)
    duration_hours = _duration_hours(bundle)
    if duration_hours is None:
        duration_hours = 0.0
        reasons.append("duration_hours_missing")
    return _component(
        as_of=str(bundle.get("generated_at") or generated_at),
        coverage_score=_coverage(passed, total),
        sample_count=max(_sample_count_from_rolling(rolling), 0),
        status="pass" if not reasons and passed == total else "hold",
        reason_codes=reasons,
        duration_hours=duration_hours,
    )


def build_promotion_readiness_evidence(
    runtime_optimization_dir: str | Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    runtime_dir = Path(runtime_optimization_dir)
    evaluated_at = _generated_at(generated_at)
    sources: dict[str, dict[str, Any]] = {}
    missing_sources: list[str] = []
    malformed_sources: list[str] = []
    json_sources: dict[str, Mapping[str, Any] | None] = {}
    calibration_rows: list[Mapping[str, Any]] | None = None

    for source_name, filename in SOURCE_FILES.items():
        path = runtime_dir / filename
        if not path.is_file():
            if source_name != "venue_rulebook_catalog_freshness":
                missing_sources.append(source_name)
            continue
        sources[source_name] = _source_record(path)
        if filename.endswith(".jsonl"):
            calibration_rows, error = _load_jsonl(path)
        else:
            payload, error = _load_json(path)
            json_sources[source_name] = payload
        if error is not None:
            malformed_sources.append(error)
            if filename.endswith(".jsonl"):
                calibration_rows = []

    paper_live_bundle = json_sources.get("paper_live_sim_evidence_bundle")
    daily = json_sources.get("daily_quality_gate_report")
    rolling = json_sources.get("rolling_tca_durability_report")
    drift = json_sources.get("paper_live_shadow_drift_contract")
    runtime_safety = json_sources.get("runtime_safety_gate")
    venue = json_sources.get("venue_rulebook_catalog_freshness")

    evidence = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": evaluated_at,
        "source_mode": SOURCE_MODE,
        "runtime_optimization_dir": str(runtime_dir),
        "data_quality": _data_quality(daily, evaluated_at),
        "execution_realism": _execution_realism(runtime_safety, calibration_rows, evaluated_at),
        "venue_rulebook_coverage": _venue_rulebook(venue, daily, rolling, evaluated_at),
        "derivatives_risk": _derivatives_risk(runtime_safety, daily, evaluated_at),
        "cross_source_parity": _cross_source_parity(drift, evaluated_at),
        "live_sim_durability": _live_sim_durability(paper_live_bundle, rolling, evaluated_at),
        "sources": sources,
        "missing_sources": missing_sources,
        "malformed_sources": malformed_sources,
        "provenance": {
            "source": OFFLINE_PROVENANCE,
            "source_mode": SOURCE_MODE,
            "side_effect_boundary": {
                "real_orders": "forbidden",
                "testnet_orders": "forbidden",
                "exchange_api_calls": "forbidden",
                "credential_use": "forbidden",
                "reads": "runtime_optimization_dir_only",
            },
        },
        "caveats": [
            "Evidence is derived from local simulated-live runtime artifacts only.",
            "Missing or malformed inputs are emitted as hold evidence rather than fabricated pass evidence.",
        ],
    }
    component_decisions = [
        evidence[component]["status"]
        for component in (
            "data_quality",
            "execution_realism",
            "venue_rulebook_coverage",
            "derivatives_risk",
            "cross_source_parity",
            "live_sim_durability",
        )
    ]
    evidence["summary"] = {
        "decision": "pass" if all(decision == "pass" for decision in component_decisions) and not malformed_sources else "hold",
        "component_statuses": dict(
            zip(
                (
                    "data_quality",
                    "execution_realism",
                    "venue_rulebook_coverage",
                    "derivatives_risk",
                    "cross_source_parity",
                    "live_sim_durability",
                ),
                component_decisions,
                strict=True,
            )
        ),
    }
    return evidence


def write_promotion_readiness_evidence(
    runtime_optimization_dir: str | Path,
    *,
    output_path: str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    payload = build_promotion_readiness_evidence(runtime_optimization_dir, generated_at=generated_at)
    path = Path(output_path) if output_path is not None else Path(runtime_optimization_dir) / FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build fail-closed promotion readiness evidence from local simulated-live artifacts")
    parser.add_argument("--runtime-optimization-dir", required=True, help="Directory containing local runtime artifacts")
    parser.add_argument("--output-path", help=f"Output JSON path; defaults to <runtime-optimization-dir>/{FILENAME}")
    parser.add_argument("--generated-at", help="Canonical UTC generation timestamp")
    args = parser.parse_args(argv)

    output_path = args.output_path or str(Path(args.runtime_optimization_dir) / FILENAME)
    write_promotion_readiness_evidence(
        args.runtime_optimization_dir,
        output_path=output_path,
        generated_at=args.generated_at,
    )
    print(f"PROMOTION_READINESS_EVIDENCE_JSON={output_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
