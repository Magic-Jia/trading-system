from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "promotion_evidence_bundle.v1"
_CANDIDATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_ARTIFACTS = (
    "trades.json",
    "exit_path_replay.json",
    "market_microstructure_gate.json",
    "passive_order_calibration_summary.json",
    "validation_gate.json",
    "runtime_safety_gate.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_path_is_safe(rel_path: str) -> bool:
    path = Path(rel_path)
    return bool(rel_path.strip()) and not path.is_absolute() and ".." not in path.parts


def _artifact_path_is_canonical(rel_path: str) -> bool:
    return rel_path == rel_path.strip() and rel_path == str(Path(rel_path))


def collect_promotion_evidence_bundle(
    source_dir: str | Path,
    bundle_dir: str | Path,
    *,
    candidate_id: str,
    evidence_source: Mapping[str, Any] | None = None,
    required_artifacts: tuple[str, ...] = REQUIRED_ARTIFACTS,
) -> Path:
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("candidate_id must be a non-empty string")
    if not _CANDIDATE_ID_RE.fullmatch(candidate_id):
        raise ValueError("candidate_id must be canonical")
    invalid_required_types = [index for index, name in enumerate(required_artifacts, start=1) if not isinstance(name, str)]
    if invalid_required_types:
        raise ValueError("required artifact path entries must be strings")
    blank_required = [name for name in required_artifacts if not name.strip()]
    if blank_required:
        raise ValueError("blank required artifact path")
    unsafe_required = [name for name in required_artifacts if not _artifact_path_is_safe(name)]
    if unsafe_required:
        raise ValueError("unsafe required artifact path(s): " + ", ".join(unsafe_required))
    noncanonical_required = [name for name in required_artifacts if not _artifact_path_is_canonical(name)]
    if noncanonical_required:
        raise ValueError("noncanonical required artifact path(s): " + ", ".join(noncanonical_required))
    source = Path(source_dir)
    destination = Path(bundle_dir)
    missing = [name for name in required_artifacts if not (source / name).is_file()]
    if missing:
        raise FileNotFoundError("missing required promotion evidence artifact(s): " + ", ".join(missing))

    if evidence_source is None:
        raise ValueError("evidence_source is required")
    if not isinstance(evidence_source, Mapping):
        raise ValueError("evidence_source must be an object")
    invalid_source_keys = [
        key for key in evidence_source if not isinstance(key, str) or not key.strip() or key != key.strip()
    ]
    if invalid_source_keys:
        raise ValueError("evidence_source keys must be canonical strings")
    source_payload = dict(evidence_source)
    source_payload.setdefault("type", "unknown_offline_records")
    unknown_source_fields = sorted(set(source_payload) - {"type", "run_id", "exported_at"})
    if unknown_source_fields:
        raise ValueError("unknown evidence_source field: " + ", ".join(unknown_source_fields))
    if not isinstance(source_payload.get("type"), str):
        raise ValueError("evidence_source type must be a string")
    if not source_payload["type"].strip():
        raise ValueError("evidence_source type must be non-empty")
    if source_payload["type"] != source_payload["type"].strip():
        raise ValueError("evidence_source type must be canonical")
    if source_payload["type"].strip().lower() in {
        "synthetic",
        "synthetic_fixture",
        "simulated",
        "offline_simulation",
        "unknown",
        "unknown_offline_records",
    }:
        raise ValueError("evidence_source type must be live-grade")
    for optional_field in ("run_id", "exported_at"):
        optional_value = source_payload.get(optional_field)
        if optional_value is not None and not isinstance(optional_value, str):
            raise ValueError(f"evidence_source {optional_field} must be a string")
        if isinstance(optional_value, str) and not optional_value.strip():
            raise ValueError(f"evidence_source {optional_field} must be non-empty")
        if isinstance(optional_value, str) and optional_value != optional_value.strip():
            raise ValueError(f"evidence_source {optional_field} must be canonical")

    destination.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []
    for name in required_artifacts:
        src = source / name
        dst = destination / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        artifacts.append(
            {
                "path": name,
                "source_path": str(src),
                "bytes": src.stat().st_size,
                "sha256": _sha256(src),
            }
        )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "decision": "bundle_complete",
        "evidence_source": source_payload,
        "required_artifacts": list(required_artifacts),
        "missing_artifacts": [],
        "artifacts": artifacts,
    }
    manifest_path = destination / "promotion_evidence_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return destination


def _empty_promotion_bundle_audit_fields() -> dict[str, list[Any]]:
    return {
        "declared_missing_artifacts": [],
        "invalid_declared_missing_artifacts": [],
        "unsafe_declared_missing_artifacts": [],
        "noncanonical_declared_missing_artifacts": [],
        "missing_artifacts": [],
        "unchecked_required_artifacts": [],
        "invalid_required_artifacts": [],
        "unsafe_required_artifacts": [],
        "noncanonical_required_artifacts": [],
        "duplicate_required_artifacts": [],
        "omitted_default_required_artifacts": [],
        "missing_artifact_metadata": [],
        "invalid_artifact_metadata": [],
        "duplicate_artifact_paths": [],
        "unsafe_artifact_paths": [],
        "noncanonical_artifact_paths": [],
        "sha256_mismatches": [],
        "byte_size_mismatches": [],
        "checked_artifacts": [],
    }


def _promotion_artifact_metadata_reason_keys(metadata_entries: list[Any]) -> list[str]:
    reasons: list[str] = []
    if any(not isinstance(item, str) for item in metadata_entries):
        reasons.append("artifact_metadata_reason_entry_not_string")
    string_entries = [item for item in metadata_entries if isinstance(item, str)]
    if any(item.endswith(".path") for item in string_entries):
        reasons.append("artifact_path_not_string")
    if any(item.endswith(":sha256") for item in string_entries):
        reasons.append("artifact_sha256_invalid_format")
    if any(item.endswith(":source_path") for item in string_entries):
        reasons.append("artifact_source_path_not_string")
    if any(item.startswith("artifacts[") and not item.endswith(".path") for item in string_entries):
        reasons.append("artifact_entry_not_object")
    return reasons


def verify_promotion_evidence_bundle(bundle_dir: str | Path) -> dict[str, Any]:
    bundle = Path(bundle_dir)
    manifest_path = bundle / "promotion_evidence_manifest.json"
    if not manifest_path.is_file():
        return {
            "schema_version": "promotion_evidence_bundle_verification.v1",
            "verified": False,
            "bundle_dir": str(bundle),
            "manifest_path": str(manifest_path),
            "manifest_present": False,
            "manifest_errors": ["missing_manifest"],
            "schema_valid": False,
            "candidate_id_valid": False,
            **_empty_promotion_bundle_audit_fields(),
        }
    manifest_errors: list[str] = []
    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "schema_version": "promotion_evidence_bundle_verification.v1",
            "verified": False,
            "bundle_dir": str(bundle),
            "manifest_path": str(manifest_path),
            "manifest_present": True,
            "manifest_errors": ["invalid_manifest_json"],
            "manifest_parse_error": str(exc),
            "schema_valid": False,
            "candidate_id_valid": False,
            **_empty_promotion_bundle_audit_fields(),
        }
    manifest = dict(manifest_payload) if isinstance(manifest_payload, Mapping) else {}
    if not isinstance(manifest_payload, Mapping):
        manifest_errors.append("manifest_not_object")
    schema_valid = manifest.get("schema_version") == SCHEMA_VERSION
    allowed_manifest_fields = {
        "schema_version",
        "candidate_id",
        "decision",
        "evidence_source",
        "required_artifacts",
        "missing_artifacts",
        "artifacts",
    }
    unknown_manifest_fields = sorted(set(manifest) - allowed_manifest_fields)
    if unknown_manifest_fields:
        schema_valid = False
    for field in unknown_manifest_fields:
        manifest_errors.append(f"unknown_top_level_field: {field}")
    if not schema_valid:
        manifest_errors.append("invalid_schema_version")
    manifest_decision = manifest.get("decision")
    if manifest_decision is None:
        schema_valid = False
        manifest_errors.append("missing_manifest_decision")
    elif manifest_decision != "bundle_complete":
        schema_valid = False
        manifest_errors.append("invalid_manifest_decision")
    candidate_id = manifest.get("candidate_id")
    candidate_id_present = isinstance(candidate_id, str) and bool(candidate_id.strip())
    candidate_id_valid = candidate_id_present and bool(_CANDIDATE_ID_RE.fullmatch(candidate_id))
    if not candidate_id_present:
        schema_valid = False
        manifest_errors.append("missing_candidate_id")
    elif not candidate_id_valid:
        schema_valid = False
        manifest_errors.append("invalid_candidate_id")
    evidence_source_raw = manifest.get("evidence_source")
    if evidence_source_raw is None:
        schema_valid = False
        manifest_errors.append("evidence_source_missing")
    elif not isinstance(evidence_source_raw, Mapping):
        schema_valid = False
        manifest_errors.append("evidence_source_not_object")
    if isinstance(evidence_source_raw, Mapping):
        unknown_evidence_source_fields = sorted(set(evidence_source_raw) - {"type", "run_id", "exported_at"})
        if unknown_evidence_source_fields:
            schema_valid = False
        for field in unknown_evidence_source_fields:
            manifest_errors.append(f"unknown_evidence_source_field: {field}")
        evidence_source_type = evidence_source_raw.get("type")
        if evidence_source_type is None:
            schema_valid = False
            manifest_errors.append("evidence_source_type_missing")
        elif not isinstance(evidence_source_type, str):
            schema_valid = False
            manifest_errors.append("evidence_source_type_not_string")
        if isinstance(evidence_source_type, str) and not evidence_source_type.strip():
            schema_valid = False
            manifest_errors.append("evidence_source_type_blank")
        if isinstance(evidence_source_type, str) and evidence_source_type != evidence_source_type.strip():
            schema_valid = False
            manifest_errors.append("evidence_source_type_noncanonical")
        if isinstance(evidence_source_type, str) and evidence_source_type.strip().lower() in {
            "synthetic",
            "synthetic_fixture",
            "simulated",
            "offline_simulation",
            "unknown",
            "unknown_offline_records",
        }:
            schema_valid = False
            manifest_errors.append("promotion_evidence_source_not_live_grade")
        for optional_field in ("run_id", "exported_at"):
            optional_value = evidence_source_raw.get(optional_field)
            if optional_value is not None and not isinstance(optional_value, str):
                schema_valid = False
                manifest_errors.append(f"evidence_source_{optional_field}_not_string")
            elif isinstance(optional_value, str) and not optional_value.strip():
                schema_valid = False
                manifest_errors.append(f"evidence_source_{optional_field}_blank")
            elif isinstance(optional_value, str) and optional_value != optional_value.strip():
                schema_valid = False
                manifest_errors.append(f"evidence_source_{optional_field}_noncanonical")
    artifacts_raw = manifest.get("artifacts")
    declared_missing_artifacts_raw = manifest.get("missing_artifacts", [])
    if "missing_artifacts" not in manifest:
        declared_missing_artifacts: list[Any] = []
    elif isinstance(declared_missing_artifacts_raw, list):
        declared_missing_artifacts = declared_missing_artifacts_raw
    else:
        schema_valid = False
        declared_missing_artifacts = []
        manifest_errors.append("missing_artifacts_not_list")
    invalid_declared_missing_artifacts = [
        f"missing_artifacts[{index}]"
        for index, item in enumerate(declared_missing_artifacts, start=1)
        if not isinstance(item, str)
    ]
    blank_declared_missing_artifacts = [
        f"missing_artifacts[{index}]"
        for index, item in enumerate(declared_missing_artifacts, start=1)
        if isinstance(item, str) and not item.strip()
    ]
    invalid_declared_missing_artifacts.extend(blank_declared_missing_artifacts)
    unsafe_declared_missing_artifacts = [
        item
        for item in declared_missing_artifacts
        if isinstance(item, str) and item and not _artifact_path_is_safe(item)
    ]
    noncanonical_declared_missing_artifacts = [
        item
        for item in declared_missing_artifacts
        if isinstance(item, str) and item and _artifact_path_is_safe(item) and not _artifact_path_is_canonical(item)
    ]
    if invalid_declared_missing_artifacts:
        schema_valid = False
        manifest_errors.append("missing_artifact_entry_invalid")
    if any(
        not isinstance(item, str)
        for item in declared_missing_artifacts
    ):
        schema_valid = False
        manifest_errors.append("missing_artifact_entry_not_string")
    if blank_declared_missing_artifacts:
        schema_valid = False
        manifest_errors.append("missing_artifact_entry_blank")
    if unsafe_declared_missing_artifacts:
        schema_valid = False
        manifest_errors.append("missing_artifact_path_unsafe")
    if noncanonical_declared_missing_artifacts:
        schema_valid = False
        manifest_errors.append("missing_artifact_path_noncanonical")
    canonical_declared_missing_artifacts = [
        item
        for item in declared_missing_artifacts
        if isinstance(item, str)
        and item.strip()
        and _artifact_path_is_safe(item)
        and _artifact_path_is_canonical(item)
    ]
    if declared_missing_artifacts:
        manifest_errors.append("manifest_declares_missing_artifacts")
    if artifacts_raw is None:
        artifacts = []
        manifest_errors.append("artifacts_missing")
    elif not isinstance(artifacts_raw, list):
        artifacts = []
        manifest_errors.append("artifacts_not_list")
    else:
        artifacts = artifacts_raw
    missing: list[str] = []
    sha_mismatches: list[str] = []
    byte_mismatches: list[str] = []
    unsafe_paths: list[str] = []
    missing_metadata: list[str] = []
    invalid_metadata: list[str] = []
    source_path_blank_metadata: list[str] = []
    source_path_noncanonical_metadata: list[str] = []
    checked: list[dict[str, Any]] = []
    checked_paths: set[str] = set()
    seen_artifact_paths: set[str] = set()
    duplicate_artifact_paths: list[str] = []
    noncanonical_artifact_paths: list[str] = []
    for artifact_index, artifact in enumerate(artifacts, start=1):
        if not isinstance(artifact, Mapping):
            invalid_metadata.append(f"artifacts[{artifact_index}]")
            continue
        unknown_artifact_fields = sorted(set(artifact) - {"path", "sha256", "bytes", "source_path"})
        rel_path_raw = artifact.get("path")
        if rel_path_raw is None or (isinstance(rel_path_raw, str) and not rel_path_raw.strip()):
            missing_metadata.append(f"artifacts[{artifact_index}].path")
            continue
        if not isinstance(rel_path_raw, str):
            invalid_metadata.append(f"artifacts[{artifact_index}].path")
            continue
        rel_path = rel_path_raw
        artifact_metadata_label = rel_path
        if unknown_artifact_fields:
            for field in unknown_artifact_fields:
                invalid_metadata.append(f"{artifact_metadata_label}:{field}")
        if rel_path in seen_artifact_paths:
            duplicate_artifact_paths.append(rel_path)
        seen_artifact_paths.add(rel_path)
        if not _artifact_path_is_safe(rel_path):
            unsafe_paths.append(rel_path)
            continue
        if not _artifact_path_is_canonical(rel_path):
            noncanonical_artifact_paths.append(rel_path)
        path = bundle / rel_path
        if not path.is_file():
            missing.append(rel_path)
            continue
        source_path_raw = artifact.get("source_path")
        if source_path_raw is not None and not isinstance(source_path_raw, str):
            invalid_metadata.append(f"{rel_path}:source_path")
        if isinstance(source_path_raw, str) and not source_path_raw.strip():
            source_path_blank_metadata.append(f"{rel_path}:source_path")
        if isinstance(source_path_raw, str) and source_path_raw.strip() and source_path_raw != source_path_raw.strip():
            invalid_metadata.append(f"{rel_path}:source_path")
            source_path_noncanonical_metadata.append(f"{rel_path}:source_path")
        actual_sha = _sha256(path)
        expected_sha = artifact.get("sha256")
        actual_bytes = path.stat().st_size
        expected_bytes_raw = artifact.get("bytes")
        if not isinstance(expected_sha, str) or not expected_sha:
            missing_metadata.append(rel_path)
        elif not _SHA256_HEX_RE.fullmatch(expected_sha):
            invalid_metadata.append(f"{rel_path}:sha256")
        elif actual_sha != expected_sha:
            sha_mismatches.append(rel_path)
        if not isinstance(expected_bytes_raw, int) or isinstance(expected_bytes_raw, bool):
            invalid_metadata.append(f"{rel_path}:bytes")
            expected_bytes = None
        else:
            expected_bytes = expected_bytes_raw
        if expected_bytes is not None and expected_bytes < 0:
            invalid_metadata.append(f"{rel_path}:bytes")
            expected_bytes = None
        if expected_bytes is not None and actual_bytes != expected_bytes:
            byte_mismatches.append(rel_path)
        checked.append({"path": rel_path, "bytes": actual_bytes, "sha256": actual_sha})
        checked_paths.add(rel_path)
    required_artifacts_raw = manifest.get("required_artifacts", [])
    invalid_required_artifacts: list[str] = []
    non_string_required_artifacts: list[str] = []
    blank_required_artifacts: list[str] = []
    noncanonical_required_artifacts: list[str] = []
    unsafe_required_artifacts: list[str] = []
    seen_required_artifacts: set[str] = set()
    duplicate_required_artifacts: list[str] = []
    if required_artifacts_raw is None:
        schema_valid = False
        manifest_required = []
        manifest_errors.append("required_artifacts_not_list")
    elif not isinstance(required_artifacts_raw, list):
        schema_valid = False
        manifest_required = []
        manifest_errors.append("required_artifacts_not_list")
    else:
        manifest_required = []
        for required_index, name in enumerate(required_artifacts_raw, start=1):
            if not isinstance(name, str):
                invalid_key = f"required_artifacts[{required_index}]"
                invalid_required_artifacts.append(invalid_key)
                non_string_required_artifacts.append(invalid_key)
                continue
            if not name.strip():
                invalid_key = f"required_artifacts[{required_index}]"
                invalid_required_artifacts.append(invalid_key)
                blank_required_artifacts.append(invalid_key)
                continue
            if not _artifact_path_is_safe(name):
                unsafe_required_artifacts.append(name)
            if _artifact_path_is_safe(name) and not _artifact_path_is_canonical(name):
                noncanonical_required_artifacts.append(name)
            if name in seen_required_artifacts:
                duplicate_required_artifacts.append(name)
            seen_required_artifacts.add(name)
            manifest_required.append(name)
    required = list(dict.fromkeys([*REQUIRED_ARTIFACTS, *manifest_required]))
    omitted_default_required = [name for name in REQUIRED_ARTIFACTS if name not in manifest_required]
    if omitted_default_required:
        manifest_errors.append("default_required_artifact_omitted")
    unchecked_required: list[str] = []
    for rel_path in required:
        if not _artifact_path_is_safe(rel_path):
            if rel_path not in unsafe_paths:
                unsafe_paths.append(rel_path)
            continue
        if not (bundle / rel_path).is_file() and rel_path not in missing:
            missing.append(rel_path)
        if rel_path not in checked_paths:
            unchecked_required.append(rel_path)
    if unsafe_paths:
        schema_valid = False
        manifest_errors.append("unsafe_artifact_path")
    if noncanonical_artifact_paths:
        schema_valid = False
        manifest_errors.append("artifact_path_noncanonical")
    if missing_metadata:
        schema_valid = False
        manifest_errors.append("artifact_metadata_missing")
    if any(str(item).endswith(".path") for item in missing_metadata):
        schema_valid = False
        manifest_errors.append("artifact_path_missing")
    if invalid_metadata:
        schema_valid = False
        manifest_errors.append("artifact_metadata_invalid")
    metadata_reason_keys = _promotion_artifact_metadata_reason_keys(invalid_metadata)
    if metadata_reason_keys:
        schema_valid = False
        manifest_errors.extend(metadata_reason_keys)
    if source_path_blank_metadata:
        schema_valid = False
        manifest_errors.append("artifact_source_path_blank")
    if source_path_noncanonical_metadata:
        schema_valid = False
        manifest_errors.append("artifact_source_path_noncanonical")
    unknown_artifact_field_names = sorted(
        {
            item.split(":", 1)[1]
            for item in invalid_metadata
            if isinstance(item, str)
            and ":" in item
            and item.rsplit(":", 1)[1] not in {"sha256", "source_path", "bytes"}
        }
    )
    for field in unknown_artifact_field_names:
        manifest_errors.append(f"unknown_artifact_field: {field}")
    if duplicate_artifact_paths:
        schema_valid = False
        manifest_errors.append("duplicate_artifact_path")
    if non_string_required_artifacts:
        schema_valid = False
        manifest_errors.append("required_artifact_entry_not_string")
    if blank_required_artifacts:
        schema_valid = False
        manifest_errors.append("required_artifact_entry_blank")
    if unsafe_required_artifacts:
        schema_valid = False
        manifest_errors.append("required_artifact_path_unsafe")
    if noncanonical_required_artifacts:
        schema_valid = False
        manifest_errors.append("required_artifact_path_noncanonical")
    if duplicate_required_artifacts:
        schema_valid = False
        manifest_errors.append("duplicate_required_artifact")
    if unchecked_required:
        schema_valid = False
        manifest_errors.append("required_artifact_missing_manifest_entry")
    return {
        "schema_version": "promotion_evidence_bundle_verification.v1",
        "verified": not manifest_errors and not missing and not sha_mismatches and not byte_mismatches,
        "bundle_dir": str(bundle),
        "manifest_path": str(manifest_path),
        "manifest_present": True,
        "manifest_errors": sorted(set(manifest_errors)),
        "schema_valid": schema_valid,
        "candidate_id_valid": candidate_id_valid,
        "candidate_id": candidate_id,
        "declared_missing_artifacts": sorted(canonical_declared_missing_artifacts),
        "invalid_declared_missing_artifacts": sorted(invalid_declared_missing_artifacts),
        "unsafe_declared_missing_artifacts": sorted(unsafe_declared_missing_artifacts),
        "noncanonical_declared_missing_artifacts": sorted(noncanonical_declared_missing_artifacts),
        "missing_artifacts": sorted(missing),
        "unchecked_required_artifacts": sorted(unchecked_required),
        "invalid_required_artifacts": sorted(set(invalid_required_artifacts)),
        "unsafe_required_artifacts": sorted(unsafe_required_artifacts),
        "noncanonical_required_artifacts": sorted(noncanonical_required_artifacts),
        "duplicate_required_artifacts": sorted(set(duplicate_required_artifacts)),
        "omitted_default_required_artifacts": sorted(omitted_default_required),
        "missing_artifact_metadata": sorted(set(missing_metadata)),
        "invalid_artifact_metadata": sorted(set([*invalid_metadata, *source_path_blank_metadata])),
        "duplicate_artifact_paths": sorted(set(duplicate_artifact_paths)),
        "unsafe_artifact_paths": sorted(unsafe_paths),
        "noncanonical_artifact_paths": sorted(noncanonical_artifact_paths),
        "sha256_mismatches": sorted(sha_mismatches),
        "byte_size_mismatches": sorted(byte_mismatches),
        "checked_artifacts": checked,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect or verify promotion evidence bundle")
    parser.add_argument("--source-dir")
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--candidate-id")
    parser.add_argument("--evidence-source-type")
    parser.add_argument("--evidence-source-run-id")
    parser.add_argument("--evidence-source-exported-at")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--verification-report-out", help="Write verify-only JSON result to this path")
    args = parser.parse_args(argv)
    if args.verify_only:
        result = verify_promotion_evidence_bundle(args.bundle_dir)
        if args.verification_report_out:
            report_path = Path(args.verification_report_out)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["verified"] else 1
    if not args.source_dir or not args.candidate_id:
        parser.error("--source-dir and --candidate-id are required unless --verify-only is used")
    if not args.evidence_source_type:
        parser.error("--evidence-source-type is required unless --verify-only is used")
    evidence_source = {"type": args.evidence_source_type}
    if args.evidence_source_run_id is not None:
        evidence_source["run_id"] = args.evidence_source_run_id
    if args.evidence_source_exported_at is not None:
        evidence_source["exported_at"] = args.evidence_source_exported_at
    print(
        collect_promotion_evidence_bundle(
            args.source_dir,
            args.bundle_dir,
            candidate_id=args.candidate_id,
            evidence_source=evidence_source,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
