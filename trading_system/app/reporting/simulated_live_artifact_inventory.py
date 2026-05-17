from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "simulated_live_artifact_inventory.v1"
FILENAME = "simulated_live_artifact_inventory.json"
SOURCE_MODE = "simulated_live_local"

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")

ROLLING_BUNDLE_COMPONENT_ARTIFACTS: tuple[dict[str, str], ...] = (
    {
        "artifact": "daily_quality_gate",
        "path": "daily_quality_gate_report.json",
        "cadence_stage": "rolling_bundle",
        "schema_version": "daily_quality_gate_report.v1",
    },
    {
        "artifact": "rolling_tca_durability",
        "path": "rolling_tca_durability_report.json",
        "cadence_stage": "rolling_bundle",
        "schema_version": "rolling_tca_durability_report.v1",
    },
    {
        "artifact": "l2_longitudinal_replay_calibration",
        "path": "l2_longitudinal_replay_calibration.json",
        "cadence_stage": "rolling_bundle",
        "schema_version": "l2_longitudinal_replay_calibration.v1",
    },
    {
        "artifact": "cross_source_parity",
        "path": "cross_source_parity_report.json",
        "cadence_stage": "rolling_bundle",
        "schema_version": "cross_source_parity_report.v1",
    },
    {
        "artifact": "venue_rulebook_catalog_freshness",
        "path": "venue_rulebook_catalog_freshness.json",
        "cadence_stage": "rolling_bundle",
        "schema_version": "venue_rulebook_catalog_freshness.v1",
    },
    {
        "artifact": "execution_race_evidence",
        "path": "execution_race_evidence.json",
        "cadence_stage": "rolling_bundle",
        "schema_version": "execution_race_evidence.v1",
    },
)

PROMOTION_GATE_CADENCE_ARTIFACTS: tuple[dict[str, str], ...] = (
    {
        "artifact": "rolling_simulated_live_evidence_bundle",
        "path": "rolling_simulated_live_evidence_bundle.json",
        "cadence_stage": "promotion_gate",
        "schema_version": "rolling_simulated_live_evidence_bundle.v1",
    },
    {
        "artifact": "simulated_live_evidence_window",
        "path": "simulated_live_evidence_window.json",
        "cadence_stage": "promotion_gate",
        "schema_version": "simulated_live_evidence_window.v1",
    },
    {
        "artifact": "promotion_readiness_scorecard_trend",
        "path": "promotion_readiness_scorecard_trend.json",
        "cadence_stage": "promotion_gate",
        "schema_version": "promotion_readiness_scorecard_trend.v1",
    },
    {
        "artifact": "real_local_simulated_live_evidence_chain_checkpoint",
        "path": "real_local_simulated_live_evidence_chain_checkpoint.json",
        "cadence_stage": "promotion_gate",
        "schema_version": "real_local_simulated_live_evidence_chain_checkpoint.v1",
    },
    {
        "artifact": "promotion_gate_decision",
        "path": "promotion_gate_decision.json",
        "cadence_stage": "promotion_gate",
        "schema_version": "promotion_gate_decision.v1",
    },
)

REQUIRED_ARTIFACTS: tuple[dict[str, str], ...] = (
    *ROLLING_BUNDLE_COMPONENT_ARTIFACTS,
    *PROMOTION_GATE_CADENCE_ARTIFACTS,
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


def _runtime_dir_safe(path: Path) -> bool:
    return ".." not in path.parts


def _artifact_path_safe(relative_path: str) -> bool:
    path = Path(relative_path)
    return not path.is_absolute() and ".." not in path.parts and relative_path.strip() == relative_path and relative_path != ""


def _artifact_spec(raw: Mapping[str, Any]) -> dict[str, str]:
    spec: dict[str, str] = {}
    for field in ("artifact", "path", "cadence_stage", "schema_version"):
        value = raw.get(field)
        if type(value) is not str or not value.strip() or value != value.strip():
            raise ValueError(f"required artifact {field} must be a canonical string")
        spec[field] = value
    return spec


def _load_json_object(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any], str | None]:
    source: dict[str, Any] = {"path": str(path)}
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return None, source, "missing_required_artifact"
    source.update({"bytes": len(raw_bytes), "sha256": _sha256_bytes(raw_bytes)})
    try:
        payload = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_duplicate_rejecting_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, source, "malformed_required_artifact"
    if not isinstance(payload, dict):
        return None, source, "malformed_required_artifact"
    return payload, source, None


def _inspect_artifact(root: Path, spec: Mapping[str, str]) -> tuple[str, dict[str, Any]]:
    item = {
        "artifact": spec["artifact"],
        "cadence_stage": spec["cadence_stage"],
        "path": spec["path"],
        "expected_schema_version": spec["schema_version"],
    }
    if not _artifact_path_safe(spec["path"]):
        return "unsafe", {**item, "error": "unsafe_required_artifact_path"}

    payload, source, error = _load_json_object(root / spec["path"])
    if error is not None:
        return "missing" if error == "missing_required_artifact" else "malformed", {**item, "source": source, "error": error}

    assert payload is not None
    observed_schema = payload.get("schema_version")
    present = {
        **item,
        "schema_version": observed_schema if type(observed_schema) is str else None,
        "generated_at": payload.get("generated_at") if type(payload.get("generated_at")) is str else None,
        "source": source,
    }
    if observed_schema != spec["schema_version"]:
        return "malformed", {**present, "error": "schema_version_invalid"}
    return "present", present


def build_simulated_live_artifact_inventory_report(
    optimization_dir: str | Path,
    *,
    generated_at: str | None = None,
    extra_required_artifacts: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    report_generated_at = _generated_at(generated_at)
    root = Path(optimization_dir)
    runtime_safe = _runtime_dir_safe(root)
    specs = [
        *[_artifact_spec(spec) for spec in REQUIRED_ARTIFACTS],
        *[_artifact_spec(spec) for spec in extra_required_artifacts],
    ]

    present: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    unsafe: list[dict[str, Any]] = []
    if runtime_safe:
        for spec in specs:
            status, artifact = _inspect_artifact(root, spec)
            if status == "present":
                present.append(artifact)
            elif status == "missing":
                missing.append(artifact)
            elif status == "unsafe":
                unsafe.append(artifact)
                missing.append(artifact)
            else:
                malformed.append(artifact)
    else:
        missing = [
            {
                "artifact": spec["artifact"],
                "cadence_stage": spec["cadence_stage"],
                "path": spec["path"],
                "expected_schema_version": spec["schema_version"],
                "error": "runtime_directory_not_scanned",
            }
            for spec in specs
        ]

    reason_codes: list[str] = []
    if not runtime_safe:
        reason_codes.append("unsafe_runtime_directory")
    if unsafe:
        reason_codes.append("unsafe_required_artifact_path")
    if malformed:
        reason_codes.append("malformed_required_artifact")
    if missing:
        reason_codes.extend(["missing_required_artifacts", "runtime_directory_missing_required_phase9_artifacts"])

    if not runtime_safe or unsafe or malformed:
        decision = "reject"
    elif missing:
        decision = "hold"
    else:
        decision = "pass"

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": report_generated_at,
        "source_mode": SOURCE_MODE,
        "decision": decision,
        "reason_codes": sorted(dict.fromkeys(reason_codes)),
        "optimization_dir": str(root),
        "required_artifacts": specs,
        "present_artifacts": present,
        "missing_artifacts": missing,
        "malformed_artifacts": malformed,
        "checks": {
            "runtime_directory_safe": runtime_safe,
            "all_required_artifacts_present": not missing and runtime_safe,
            "all_required_artifacts_well_formed": not malformed and not unsafe and runtime_safe,
        },
        "side_effect_boundary": {
            "real_orders": "forbidden",
            "testnet_orders": "forbidden",
            "exchange_api_calls": "forbidden",
            "credential_use": "forbidden",
        },
        "provenance": {
            "decision_policy": "fail_closed",
            "scan_scope": "local_optimization_runtime_directory",
            "required_artifact_count": len(specs),
            "present_artifact_count": len(present),
            "missing_artifact_count": len(missing),
            "malformed_artifact_count": len(malformed),
        },
    }


def write_simulated_live_artifact_inventory_report(
    output_path: str | Path,
    optimization_dir: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    payload = build_simulated_live_artifact_inventory_report(optimization_dir, **kwargs)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a fail-closed Phase 9 simulated-live artifact inventory")
    parser.add_argument(
        "--optimization-dir",
        default=None,
        help="Local optimization runtime directory to inspect",
    )
    parser.add_argument(
        "--runtime-root",
        default="trading_system/data/runtime",
        help="Runtime root used when --optimization-dir is omitted",
    )
    parser.add_argument("--mode", default="paper", help="Runtime mode segment for the default optimization path")
    parser.add_argument("--runtime-env", default="paper", help="Runtime env segment for the default optimization path")
    parser.add_argument("--output", required=True, help="Output JSON report path")
    parser.add_argument("--generated-at", default=None, help="Canonical UTC generation timestamp")
    args = parser.parse_args()

    optimization_dir = (
        Path(args.optimization_dir)
        if args.optimization_dir is not None
        else Path(args.runtime_root) / args.mode / args.runtime_env / "optimization"
    )
    payload = write_simulated_live_artifact_inventory_report(
        args.output,
        optimization_dir,
        generated_at=args.generated_at,
    )
    print(
        "SIMULATED_LIVE_ARTIFACT_INVENTORY_JSON",
        json.dumps(
            {
                "output": args.output,
                "decision": payload["decision"],
                "reason_codes": payload["reason_codes"],
                "source_mode": payload["source_mode"],
            },
            sort_keys=True,
        ),
    )


__all__ = [
    "FILENAME",
    "PROMOTION_GATE_CADENCE_ARTIFACTS",
    "REQUIRED_ARTIFACTS",
    "ROLLING_BUNDLE_COMPONENT_ARTIFACTS",
    "SCHEMA_VERSION",
    "SOURCE_MODE",
    "build_simulated_live_artifact_inventory_report",
    "write_simulated_live_artifact_inventory_report",
]
