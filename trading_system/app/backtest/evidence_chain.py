from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "backtest_evidence_chain.v1"
FILENAME = "backtest_evidence_chain.json"
SOURCE_MODE = "historical_backtest_local"
OFFLINE_PROVENANCE = "offline_local_filesystem_only"

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")


def _generated_at(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _parse_timestamp(value)
    return value


def _parse_timestamp(value: Any) -> datetime:
    if type(value) is not str or _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ValueError("generated_at must be a canonical UTC timestamp")
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "provenance": {
            "source": OFFLINE_PROVENANCE,
            "source_mode": SOURCE_MODE,
        },
    }


def _load_json(path: Path) -> tuple[Mapping[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"source_malformed:{path.name}:{type(exc).__name__}"
    if not isinstance(payload, Mapping):
        return None, f"source_malformed:{path.name}:not_object"
    return payload, None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _component(
    *,
    as_of: str,
    coverage_score: float,
    sample_count: int,
    status: str,
    reason_codes: list[str],
    trade_count: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "as_of": as_of,
        "coverage_score": max(0.0, min(1.0, coverage_score)),
        "sample_count": max(0, sample_count),
        "status": status,
        "reason_codes": reason_codes,
    }
    if trade_count is not None:
        payload["trade_count"] = max(0, trade_count)
    return payload


def _missing_component(source_name: str, generated_at: str) -> dict[str, Any]:
    return _component(
        as_of=generated_at,
        coverage_score=0.0,
        sample_count=0,
        status="hold",
        reason_codes=[f"source_missing:{source_name}"],
    )


def _extract_reason_codes(payload: Mapping[str, Any]) -> list[str]:
    raw_reasons = payload.get("reason_codes", payload.get("reasons", []))
    if raw_reasons is None:
        return []
    if not isinstance(raw_reasons, list):
        return ["malformed_reasons"]
    return [reason for reason in raw_reasons if isinstance(reason, str)]


def _summary_section(summary_payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(summary_payload, Mapping):
        return {}
    summary = summary_payload.get("summary")
    return summary if isinstance(summary, Mapping) else summary_payload


def _audit_section(audit_payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(audit_payload, Mapping):
        return {}
    audit = audit_payload.get("audit")
    return audit if isinstance(audit, Mapping) else audit_payload


def _historical_backtest(
    summary_payload: Mapping[str, Any] | None,
    audit_payload: Mapping[str, Any] | None,
    errors: list[str],
    generated_at: str,
) -> dict[str, Any]:
    reasons = list(errors)
    summary = _summary_section(summary_payload)
    audit = _audit_section(audit_payload)
    summary_trade_count = _non_negative_int(summary.get("trade_count"))
    audit_trade_count = _non_negative_int(audit.get("trade_count"))
    trade_count = audit_trade_count if audit_trade_count is not None else summary_trade_count
    if summary_payload is None:
        reasons.append("source_missing:summary")
    if audit_payload is None:
        reasons.append("source_missing:audit")
    if trade_count is None:
        reasons.append("trade_count_missing")
        trade_count = 0
    elif trade_count <= 0:
        reasons.append("trade_count_zero")
    if summary_trade_count is not None and audit_trade_count is not None and summary_trade_count != audit_trade_count:
        reasons.append("trade_count_mismatch")
    total_return = _number(summary.get("total_return"))
    max_drawdown = _number(summary.get("max_drawdown"))
    if total_return is None:
        reasons.append("total_return_missing")
    if max_drawdown is None:
        reasons.append("max_drawdown_missing")
    return _component(
        as_of=generated_at,
        coverage_score=1.0 if not reasons else 0.0,
        sample_count=trade_count,
        trade_count=trade_count,
        status="pass" if not reasons else "hold",
        reason_codes=reasons,
    )


def _exit_path_replay(payload: Mapping[str, Any] | None, errors: list[str], generated_at: str) -> dict[str, Any]:
    reasons = list(errors)
    if payload is None:
        reasons.append("source_missing:exit_path_replay")
        return _component(as_of=generated_at, coverage_score=0.0, sample_count=0, status="hold", reason_codes=reasons)
    replay = payload.get("exit_path_replay") if isinstance(payload.get("exit_path_replay"), Mapping) else payload
    trade_count = _non_negative_int(replay.get("trade_count"))
    if trade_count is None:
        trades = replay.get("trades")
        trade_count = len(trades) if isinstance(trades, list) else 0
    replayed_count = _non_negative_int(replay.get("replayed_count"))
    if replayed_count is None:
        replayed_count = trade_count
    reasons.extend(_extract_reason_codes(replay))
    if trade_count <= 0:
        reasons.append("exit_path_replay_empty")
    if replayed_count < trade_count:
        reasons.append("exit_path_replay_incomplete")
    return _component(
        as_of=generated_at,
        coverage_score=1.0 if not reasons else 0.0,
        sample_count=trade_count,
        status="pass" if not reasons else "hold",
        reason_codes=reasons,
    )


def _external_report(payload: Mapping[str, Any] | None, source_name: str, errors: list[str], generated_at: str) -> dict[str, Any]:
    if payload is None:
        return _missing_component(source_name, generated_at)
    reasons = list(errors) + _extract_reason_codes(payload)
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else payload
    decision = summary.get("decision")
    if decision is not None and decision != "pass":
        reasons.append(f"{source_name}_not_pass")
    sample_count = _non_negative_int(summary.get("trade_count"))
    if sample_count is None:
        scorecard = summary.get("out_of_sample_scorecard") if isinstance(summary.get("out_of_sample_scorecard"), Mapping) else {}
        sample_count = _non_negative_int(scorecard.get("trade_count")) or _non_negative_int(summary.get("scenario_count")) or 0
    if sample_count <= 0:
        reasons.append(f"{source_name}_sample_count_missing")
    return _component(
        as_of=str(payload.get("generated_at") or generated_at),
        coverage_score=1.0 if not reasons else 0.0,
        sample_count=sample_count,
        status="pass" if not reasons else "hold",
        reason_codes=reasons,
    )


def _execution_realism(
    unavailable_payload: Mapping[str, Any] | None,
    errors: list[str],
    generated_at: str,
) -> dict[str, Any]:
    reasons = list(errors)
    if unavailable_payload is None:
        return _component(
            as_of=generated_at,
            coverage_score=1.0,
            sample_count=0,
            status="pass",
            reason_codes=[],
        )
    reasons.append("execution_calibration_unavailable")
    reasons.extend(_extract_reason_codes(unavailable_payload))
    sample_count = _non_negative_int(unavailable_payload.get("record_count")) or 0
    return _component(
        as_of=str(unavailable_payload.get("generated_at") or generated_at),
        coverage_score=0.0,
        sample_count=sample_count,
        status="hold",
        reason_codes=reasons,
    )


def _data_quality(
    manifest_payload: Mapping[str, Any] | None,
    sources: Mapping[str, dict[str, Any]],
    errors: list[str],
    generated_at: str,
) -> dict[str, Any]:
    reasons = list(errors)
    if manifest_payload is None:
        reasons.append("source_missing:manifest")
        snapshot_count = 0
    else:
        snapshot_count = _non_negative_int(manifest_payload.get("snapshot_count")) or 0
        artifacts = manifest_payload.get("artifacts")
        if not isinstance(artifacts, list):
            reasons.append("manifest_artifacts_missing")
        else:
            for required in ("manifest.json", "summary.json", "audit.json", "exit_path_replay.json"):
                if required not in artifacts:
                    reasons.append(f"manifest_artifact_missing:{required}")
    for source_name in ("summary", "audit", "exit_path_replay"):
        if source_name not in sources:
            reasons.append(f"source_missing:{source_name}")
    if snapshot_count <= 0:
        reasons.append("snapshot_count_missing")
    return _component(
        as_of=generated_at,
        coverage_score=1.0 if not reasons else 0.0,
        sample_count=snapshot_count,
        status="pass" if not reasons else "hold",
        reason_codes=reasons,
    )


def build_backtest_evidence_chain(
    backtest_bundle_dir: str | Path,
    *,
    walk_forward_report_path: str | Path | None = None,
    cost_sensitivity_report_path: str | Path | None = None,
    execution_calibration_unavailable_path: str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    bundle_dir = Path(backtest_bundle_dir)
    evaluated_at = _generated_at(generated_at)
    source_specs = {
        "manifest": bundle_dir / "manifest.json",
        "summary": bundle_dir / "summary.json",
        "audit": bundle_dir / "audit.json",
        "exit_path_replay": bundle_dir / "exit_path_replay.json",
    }
    if walk_forward_report_path is not None:
        source_specs["walk_forward_oos"] = Path(walk_forward_report_path)
    if cost_sensitivity_report_path is not None:
        source_specs["cost_sensitivity"] = Path(cost_sensitivity_report_path)
    if execution_calibration_unavailable_path is not None:
        source_specs["execution_calibration_unavailable"] = Path(execution_calibration_unavailable_path)

    sources: dict[str, dict[str, Any]] = {}
    payloads: dict[str, Mapping[str, Any] | None] = {}
    source_errors: dict[str, list[str]] = {}
    missing_sources: list[str] = []
    malformed_sources: list[str] = []
    for source_name, path in source_specs.items():
        if not path.is_file():
            missing_sources.append(source_name)
            payloads[source_name] = None
            continue
        sources[source_name] = _source_record(path)
        payload, error = _load_json(path)
        payloads[source_name] = payload
        if error is not None:
            malformed_sources.append(error)
            source_errors.setdefault(source_name, []).append(error)

    evidence = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": evaluated_at,
        "source_mode": SOURCE_MODE,
        "backtest_bundle_dir": str(bundle_dir),
        "historical_backtest": _historical_backtest(
            payloads.get("summary"),
            payloads.get("audit"),
            source_errors.get("summary", []) + source_errors.get("audit", []),
            evaluated_at,
        ),
        "exit_path_replay": _exit_path_replay(
            payloads.get("exit_path_replay"),
            source_errors.get("exit_path_replay", []),
            evaluated_at,
        ),
        "walk_forward_oos": _external_report(
            payloads.get("walk_forward_oos"),
            "walk_forward_oos",
            source_errors.get("walk_forward_oos", []),
            evaluated_at,
        ),
        "cost_sensitivity": _external_report(
            payloads.get("cost_sensitivity"),
            "cost_sensitivity",
            source_errors.get("cost_sensitivity", []),
            evaluated_at,
        ),
        "execution_realism": _execution_realism(
            payloads.get("execution_calibration_unavailable"),
            source_errors.get("execution_calibration_unavailable", []),
            evaluated_at,
        ),
        "data_quality": _data_quality(
            payloads.get("manifest"),
            sources,
            [error for errors in source_errors.values() for error in errors],
            evaluated_at,
        ),
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
                "reads": "backtest_bundle_and_optional_reports_only",
            },
        },
    }
    component_names = (
        "historical_backtest",
        "exit_path_replay",
        "walk_forward_oos",
        "cost_sensitivity",
        "execution_realism",
        "data_quality",
    )
    statuses = {name: evidence[name]["status"] for name in component_names}
    evidence["summary"] = {
        "decision": "pass" if all(status == "pass" for status in statuses.values()) and not malformed_sources else "hold",
        "component_statuses": statuses,
    }
    return evidence


def write_backtest_evidence_chain(
    backtest_bundle_dir: str | Path,
    *,
    output_path: str | Path | None = None,
    walk_forward_report_path: str | Path | None = None,
    cost_sensitivity_report_path: str | Path | None = None,
    execution_calibration_unavailable_path: str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    payload = build_backtest_evidence_chain(
        backtest_bundle_dir,
        walk_forward_report_path=walk_forward_report_path,
        cost_sensitivity_report_path=cost_sensitivity_report_path,
        execution_calibration_unavailable_path=execution_calibration_unavailable_path,
        generated_at=generated_at,
    )
    path = Path(output_path) if output_path is not None else Path(backtest_bundle_dir) / FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build fail-closed backtest promotion evidence from local artifacts")
    parser.add_argument("--backtest-bundle-dir", required=True)
    parser.add_argument("--output-path")
    parser.add_argument("--walk-forward-report-path")
    parser.add_argument("--cost-sensitivity-report-path")
    parser.add_argument("--execution-calibration-unavailable-path")
    parser.add_argument("--generated-at")
    args = parser.parse_args(argv)
    output_path = args.output_path or str(Path(args.backtest_bundle_dir) / FILENAME)
    write_backtest_evidence_chain(
        args.backtest_bundle_dir,
        output_path=output_path,
        walk_forward_report_path=args.walk_forward_report_path,
        cost_sensitivity_report_path=args.cost_sensitivity_report_path,
        execution_calibration_unavailable_path=args.execution_calibration_unavailable_path,
        generated_at=args.generated_at,
    )
    print(f"BACKTEST_EVIDENCE_CHAIN_JSON={output_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
