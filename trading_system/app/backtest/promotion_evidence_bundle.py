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
            "missing_artifacts": [],
            "sha256_mismatches": [],
            "byte_size_mismatches": [],
            "checked_artifacts": [],
        }
    manifest = json.loads(manifest_path.read_text())
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), list) else []
    missing: list[str] = []
    sha_mismatches: list[str] = []
    byte_mismatches: list[str] = []
    checked: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            continue
        rel_path = str(artifact.get("path") or "")
        if not rel_path:
            continue
        path = bundle / rel_path
        if not path.is_file():
            missing.append(rel_path)
            continue
        actual_sha = _sha256(path)
        expected_sha = str(artifact.get("sha256") or "")
        actual_bytes = path.stat().st_size
        expected_bytes = int(artifact.get("bytes") or -1)
        if expected_sha and actual_sha != expected_sha:
            sha_mismatches.append(rel_path)
        if expected_bytes >= 0 and actual_bytes != expected_bytes:
            byte_mismatches.append(rel_path)
        checked.append({"path": rel_path, "bytes": actual_bytes, "sha256": actual_sha})
    required = [str(name) for name in manifest.get("required_artifacts", []) if str(name)]
    for rel_path in required:
        if not (bundle / rel_path).is_file() and rel_path not in missing:
            missing.append(rel_path)
    return {
        "schema_version": "promotion_evidence_bundle_verification.v1",
        "verified": not missing and not sha_mismatches and not byte_mismatches,
        "bundle_dir": str(bundle),
        "manifest_path": str(manifest_path),
        "manifest_present": True,
        "candidate_id": manifest.get("candidate_id"),
        "missing_artifacts": sorted(missing),
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
    args = parser.parse_args(argv)
    if args.verify_only:
        result = verify_promotion_evidence_bundle(args.bundle_dir)
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
