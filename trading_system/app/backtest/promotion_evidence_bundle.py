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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect promotion evidence bundle")
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--candidate-id", required=True)
    args = parser.parse_args(argv)
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
