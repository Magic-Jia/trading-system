from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "promotion_evidence_bundle.v1"
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
    return bool(rel_path) and not path.is_absolute() and ".." not in path.parts


def collect_promotion_evidence_bundle(
    source_dir: str | Path,
    bundle_dir: str | Path,
    *,
    candidate_id: str,
    evidence_source: Mapping[str, Any] | None = None,
    required_artifacts: tuple[str, ...] = REQUIRED_ARTIFACTS,
) -> Path:
    source = Path(source_dir)
    destination = Path(bundle_dir)
    missing = [name for name in required_artifacts if not (source / name).is_file()]
    if missing:
        raise FileNotFoundError("missing required promotion evidence artifact(s): " + ", ".join(missing))

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

    source_payload = dict(evidence_source or {"type": "unknown_offline_records"})
    source_payload.setdefault("type", "unknown_offline_records")
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
            "missing_artifacts": [],
            "unchecked_required_artifacts": [],
            "omitted_default_required_artifacts": [],
            "missing_artifact_metadata": [],
            "invalid_artifact_metadata": [],
            "unsafe_artifact_paths": [],
            "sha256_mismatches": [],
            "byte_size_mismatches": [],
            "checked_artifacts": [],
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
            "missing_artifacts": [],
            "unchecked_required_artifacts": [],
            "omitted_default_required_artifacts": [],
            "missing_artifact_metadata": [],
            "invalid_artifact_metadata": [],
            "unsafe_artifact_paths": [],
            "sha256_mismatches": [],
            "byte_size_mismatches": [],
            "checked_artifacts": [],
        }
    manifest = dict(manifest_payload) if isinstance(manifest_payload, Mapping) else {}
    if not isinstance(manifest_payload, Mapping):
        manifest_errors.append("manifest_not_object")
    schema_valid = manifest.get("schema_version") == SCHEMA_VERSION
    if not schema_valid:
        manifest_errors.append("invalid_schema_version")
    candidate_id = manifest.get("candidate_id")
    candidate_id_valid = isinstance(candidate_id, str) and bool(candidate_id.strip())
    if not candidate_id_valid:
        manifest_errors.append("missing_candidate_id")
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), list) else []
    missing: list[str] = []
    sha_mismatches: list[str] = []
    byte_mismatches: list[str] = []
    unsafe_paths: list[str] = []
    missing_metadata: list[str] = []
    invalid_metadata: list[str] = []
    checked: list[dict[str, Any]] = []
    checked_paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            continue
        rel_path = str(artifact.get("path") or "")
        if not rel_path:
            continue
        if not _artifact_path_is_safe(rel_path):
            unsafe_paths.append(rel_path)
            continue
        path = bundle / rel_path
        if not path.is_file():
            missing.append(rel_path)
            continue
        actual_sha = _sha256(path)
        expected_sha = artifact.get("sha256")
        actual_bytes = path.stat().st_size
        expected_bytes_raw = artifact.get("bytes")
        if not isinstance(expected_sha, str) or not expected_sha:
            missing_metadata.append(rel_path)
        elif actual_sha != expected_sha:
            sha_mismatches.append(rel_path)
        try:
            expected_bytes = int(expected_bytes_raw)
        except (TypeError, ValueError):
            invalid_metadata.append(rel_path)
            expected_bytes = None
        if expected_bytes is not None and expected_bytes < 0:
            invalid_metadata.append(rel_path)
            expected_bytes = None
        if expected_bytes is not None and actual_bytes != expected_bytes:
            byte_mismatches.append(rel_path)
        checked.append({"path": rel_path, "bytes": actual_bytes, "sha256": actual_sha})
        checked_paths.add(rel_path)
    manifest_required = [str(name) for name in manifest.get("required_artifacts", []) if str(name)]
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
        manifest_errors.append("unsafe_artifact_path")
    if missing_metadata:
        manifest_errors.append("artifact_metadata_missing")
    if invalid_metadata:
        manifest_errors.append("artifact_metadata_invalid")
    if unchecked_required:
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
        "missing_artifacts": sorted(missing),
        "unchecked_required_artifacts": sorted(unchecked_required),
        "omitted_default_required_artifacts": sorted(omitted_default_required),
        "missing_artifact_metadata": sorted(set(missing_metadata)),
        "invalid_artifact_metadata": sorted(set(invalid_metadata)),
        "unsafe_artifact_paths": sorted(unsafe_paths),
        "sha256_mismatches": sorted(sha_mismatches),
        "byte_size_mismatches": sorted(byte_mismatches),
        "checked_artifacts": checked,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect or verify promotion evidence bundle")
    parser.add_argument("--source-dir")
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--candidate-id")
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
    print(
        collect_promotion_evidence_bundle(
            args.source_dir,
            args.bundle_dir,
            candidate_id=args.candidate_id,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
