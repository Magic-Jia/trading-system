from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


CROSS_SOURCE_PARITY_SCHEMA_VERSION = "cross_source_parity_report.v1"
CROSS_SOURCE_PARITY_FILENAME = "cross_source_parity_report.json"
VENUE_RULEBOOK_FRESHNESS_SCHEMA_VERSION = "venue_rulebook_catalog_freshness.v1"
VENUE_RULEBOOK_FRESHNESS_FILENAME = "venue_rulebook_catalog_freshness.json"
SOURCE_MODE = "simulated_live_local"

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_REQUIRED_RUNTIME_INPUTS: tuple[tuple[str, str, str], ...] = (
    ("paper_live_sim_evidence_bundle", "paper_live_sim_evidence_bundle.json", "paper_live_sim_evidence_bundle.v1"),
    ("paper_live_sim_evidence_manifest", "paper_live_sim_evidence_manifest.json", "paper_live_sim_evidence_bundle.v1"),
    ("runtime_safety_gate", "runtime_safety_gate.json", "runtime_safety_gate_input.v1"),
    ("paper_live_shadow_drift_contract", "paper_live_shadow_drift_contract.json", "paper_live_shadow_drift_contract.v1"),
)
_INDEPENDENT_SOURCE_CANDIDATES: tuple[str, ...] = (
    "local_independent_source_snapshot.json",
    "independent_source_snapshot.json",
    "cross_source_inputs.json",
)
_VENUE_CATALOG_CANDIDATES: tuple[str, ...] = (
    "venue_rulebook_catalog.json",
    "venue_rulebooks/venue_rulebook_catalog.json",
)


def _is_canonical_utc_timestamp(value: str) -> bool:
    if _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC).isoformat().replace("+00:00", "Z") == value


def _generated_at(value: str | None) -> str:
    generated_at = value or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if type(generated_at) is not str or not _is_canonical_utc_timestamp(generated_at):
        raise ValueError("generated_at must be a canonical UTC timestamp")
    return generated_at


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _duplicate_rejecting_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"duplicate JSON field: {key}")
        payload[key] = value
    return payload


def _load_json_object(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any], str | None]:
    source: dict[str, Any] = {"path": str(path)}
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return None, source, "runtime_input_missing"
    source.update({"bytes": len(raw_bytes), "sha256": _sha256_bytes(raw_bytes)})
    try:
        payload = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_duplicate_rejecting_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, source, "malformed_runtime_input"
    if not isinstance(payload, dict):
        return None, source, "malformed_runtime_input"
    return payload, source, None


def _inspect_required_runtime_inputs(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    present: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    for name, filename, expected_schema in _REQUIRED_RUNTIME_INPUTS:
        path = root / filename
        payload, source, error = _load_json_object(path)
        item = {
            "name": name,
            "path": filename,
            "expected_schema_version": expected_schema,
            "source": source,
        }
        if error == "runtime_input_missing":
            missing.append({**item, "error": error})
            continue
        if error is not None:
            malformed.append({**item, "error": error})
            continue
        assert payload is not None
        observed_schema = payload.get("schema_version")
        record = {
            **item,
            "schema_version": observed_schema if type(observed_schema) is str else None,
            "generated_at": payload.get("generated_at") if type(payload.get("generated_at")) is str else None,
        }
        if observed_schema != expected_schema:
            malformed.append({**record, "error": "runtime_input_schema_version_invalid"})
        else:
            present.append(record)
    return present, missing, malformed


def _load_first_candidate(root: Path, filenames: tuple[str, ...]) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    malformed: list[dict[str, Any]] = []
    for filename in filenames:
        path = root / filename
        payload, source, error = _load_json_object(path)
        if error == "runtime_input_missing":
            continue
        if error is not None:
            malformed.append({"path": filename, "source": source, "error": error})
            continue
        assert payload is not None
        return payload, {"path": filename, "source": source}, malformed
    return None, None, malformed


def _side_effect_boundary() -> dict[str, str]:
    return {
        "real_orders": "forbidden",
        "testnet_orders": "forbidden",
        "exchange_api_calls": "forbidden",
        "credential_use": "forbidden",
    }


def _base_report(
    *,
    schema_version: str,
    artifact_id: str,
    generated_at: str,
    optimization_dir: Path,
    present_inputs: list[dict[str, Any]],
    missing_inputs: list[dict[str, Any]],
    malformed_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "artifact_id": artifact_id,
        "generated_at": generated_at,
        "source_mode": SOURCE_MODE,
        "optimization_dir": str(optimization_dir),
        "status": "hold",
        "decision": "hold",
        "reason_codes": [],
        "present_inputs": present_inputs,
        "missing_inputs": missing_inputs,
        "malformed_inputs": malformed_inputs,
        "side_effect_boundary": _side_effect_boundary(),
        "provenance": {
            "source": "offline_local_filesystem_only",
            "decision_policy": "fail_closed",
            "required_runtime_input_count": len(_REQUIRED_RUNTIME_INPUTS),
        },
    }


def _runtime_input_reasons(missing_inputs: list[dict[str, Any]], malformed_inputs: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    if missing_inputs:
        reasons.append("runtime_input_missing")
    if malformed_inputs:
        reasons.append("malformed_runtime_input")
    return reasons


def build_cross_source_parity_report(
    optimization_dir: str | Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    root = Path(optimization_dir)
    report_generated_at = _generated_at(generated_at)
    present, missing, malformed = _inspect_required_runtime_inputs(root)
    source_payload, source_record, source_malformed = _load_first_candidate(root, _INDEPENDENT_SOURCE_CANDIDATES)
    malformed = [*malformed, *source_malformed]

    reasons = _runtime_input_reasons(missing, malformed)
    checks = {
        "required_runtime_inputs_present": not missing,
        "required_runtime_inputs_well_formed": not malformed,
        "independent_source_available": source_payload is not None,
        "cross_source_threshold_evaluable": False,
    }
    if source_payload is None:
        reasons.append("independent_source_unavailable")
    else:
        reasons.append("cross_source_threshold_not_evaluable")

    status = "hold" if missing or malformed or source_payload is None else "review"
    report = _base_report(
        schema_version=CROSS_SOURCE_PARITY_SCHEMA_VERSION,
        artifact_id=f"cross_source_parity:{report_generated_at[:10]}",
        generated_at=report_generated_at,
        optimization_dir=root,
        present_inputs=present,
        missing_inputs=missing,
        malformed_inputs=malformed,
    )
    report.update(
        {
            "status": status,
            "decision": status,
            "reason_codes": sorted(dict.fromkeys(reasons)),
            "checks": checks,
            "independent_source": source_record,
            "parity_observations": [],
        }
    )
    return report


def build_venue_rulebook_catalog_freshness_report(
    optimization_dir: str | Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    root = Path(optimization_dir)
    report_generated_at = _generated_at(generated_at)
    present, missing, malformed = _inspect_required_runtime_inputs(root)
    catalog, catalog_record, catalog_malformed = _load_first_candidate(root, _VENUE_CATALOG_CANDIDATES)
    malformed = [*malformed, *catalog_malformed]

    catalog_schema_valid = bool(catalog is not None and catalog.get("schema_version") == "venue_rulebook_catalog.v1")
    reasons = _runtime_input_reasons(missing, malformed)
    if catalog is None:
        reasons.append("venue_rulebook_catalog_unavailable")
    elif not catalog_schema_valid:
        reasons.append("venue_rulebook_catalog_invalid")
        malformed.append(
            {
                "name": "venue_rulebook_catalog",
                "path": catalog_record["path"] if catalog_record is not None else "venue_rulebook_catalog.json",
                "schema_version": catalog.get("schema_version"),
                "expected_schema_version": "venue_rulebook_catalog.v1",
                "error": "runtime_input_schema_version_invalid",
            }
        )
    catalog_quality = catalog.get("coverage_report") if isinstance(catalog, Mapping) else None
    catalog_quality_pass = bool(
        isinstance(catalog_quality, Mapping) and catalog_quality.get("quality_status") == "pass"
    )
    freshness_evaluable = bool(catalog_schema_valid and catalog_quality_pass)
    if catalog_schema_valid and not catalog_quality_pass:
        reasons.append("venue_rulebook_catalog_quality_not_pass")

    status = "hold" if missing or malformed or catalog is None or not catalog_schema_valid else ("pass" if freshness_evaluable else "review")
    rulebook_count = len(catalog.get("rulebooks", [])) if isinstance(catalog, Mapping) and isinstance(catalog.get("rulebooks"), list) else 0
    report = _base_report(
        schema_version=VENUE_RULEBOOK_FRESHNESS_SCHEMA_VERSION,
        artifact_id=f"venue_rulebook_catalog_freshness:{report_generated_at[:10]}",
        generated_at=report_generated_at,
        optimization_dir=root,
        present_inputs=present,
        missing_inputs=missing,
        malformed_inputs=malformed,
    )
    report.update(
        {
            "status": status,
            "decision": status,
            "reason_codes": sorted(dict.fromkeys(reasons)),
            "checks": {
                "required_runtime_inputs_present": not missing,
                "required_runtime_inputs_well_formed": not malformed,
                "venue_rulebook_catalog_available": catalog is not None,
                "venue_rulebook_catalog_schema_valid": catalog_schema_valid,
                "venue_rulebook_freshness_threshold_evaluable": freshness_evaluable,
            },
            "venue_rulebook_catalog": catalog_record,
            "catalog_summary": {
                "rulebook_count": rulebook_count,
                "catalog_quality": catalog.get("coverage_report") if isinstance(catalog, Mapping) else None,
            },
        }
    )
    return report


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_cross_source_parity_report(
    optimization_dir: str | Path,
    *,
    output_path: str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    root = Path(optimization_dir)
    payload = build_cross_source_parity_report(root, generated_at=generated_at)
    _write_json(Path(output_path) if output_path is not None else root / CROSS_SOURCE_PARITY_FILENAME, payload)
    return payload


def write_venue_rulebook_catalog_freshness_report(
    optimization_dir: str | Path,
    *,
    output_path: str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    root = Path(optimization_dir)
    payload = build_venue_rulebook_catalog_freshness_report(root, generated_at=generated_at)
    _write_json(Path(output_path) if output_path is not None else root / VENUE_RULEBOOK_FRESHNESS_FILENAME, payload)
    return payload


def _default_optimization_dir(args: argparse.Namespace) -> Path:
    return Path(args.runtime_root) / args.mode / args.runtime_env / "optimization"


def cross_source_parity_main() -> None:
    parser = argparse.ArgumentParser(description="Generate local fail-closed cross-source parity evidence")
    parser.add_argument("--optimization-dir", default=None)
    parser.add_argument("--runtime-root", default="trading_system/data/runtime")
    parser.add_argument("--mode", default="paper")
    parser.add_argument("--runtime-env", default="paper")
    parser.add_argument("--output", default=None)
    parser.add_argument("--generated-at", default=None)
    args = parser.parse_args()

    optimization_dir = Path(args.optimization_dir) if args.optimization_dir is not None else _default_optimization_dir(args)
    payload = write_cross_source_parity_report(
        optimization_dir,
        output_path=args.output,
        generated_at=args.generated_at,
    )
    print(
        "CROSS_SOURCE_PARITY_REPORT_JSON",
        json.dumps({"decision": payload["decision"], "output": str(args.output or optimization_dir / CROSS_SOURCE_PARITY_FILENAME)}, sort_keys=True),
    )


def venue_rulebook_catalog_freshness_main() -> None:
    parser = argparse.ArgumentParser(description="Generate local fail-closed venue rulebook catalog freshness evidence")
    parser.add_argument("--optimization-dir", default=None)
    parser.add_argument("--runtime-root", default="trading_system/data/runtime")
    parser.add_argument("--mode", default="paper")
    parser.add_argument("--runtime-env", default="paper")
    parser.add_argument("--output", default=None)
    parser.add_argument("--generated-at", default=None)
    args = parser.parse_args()

    optimization_dir = Path(args.optimization_dir) if args.optimization_dir is not None else _default_optimization_dir(args)
    payload = write_venue_rulebook_catalog_freshness_report(
        optimization_dir,
        output_path=args.output,
        generated_at=args.generated_at,
    )
    print(
        "VENUE_RULEBOOK_CATALOG_FRESHNESS_JSON",
        json.dumps({"decision": payload["decision"], "output": str(args.output or optimization_dir / VENUE_RULEBOOK_FRESHNESS_FILENAME)}, sort_keys=True),
    )


__all__ = [
    "CROSS_SOURCE_PARITY_FILENAME",
    "CROSS_SOURCE_PARITY_SCHEMA_VERSION",
    "SOURCE_MODE",
    "VENUE_RULEBOOK_FRESHNESS_FILENAME",
    "VENUE_RULEBOOK_FRESHNESS_SCHEMA_VERSION",
    "build_cross_source_parity_report",
    "build_venue_rulebook_catalog_freshness_report",
    "write_cross_source_parity_report",
    "write_venue_rulebook_catalog_freshness_report",
]
