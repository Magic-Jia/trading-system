from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from trading_system.app.backtest.promotion_evidence_bundle import (
    REQUIRED_ARTIFACTS,
    collect_promotion_evidence_bundle,
)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n")


def test_collects_required_evidence_artifacts_with_checksums(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name, "synthetic": True})

    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "synthetic_fixture"},
    )

    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema_version"] == "promotion_evidence_bundle.v1"
    assert manifest["candidate_id"] == "candidate-1"
    assert manifest["decision"] == "bundle_complete"
    assert manifest["evidence_source"] == {"type": "synthetic_fixture"}
    assert manifest["missing_artifacts"] == []
    assert [artifact["path"] for artifact in manifest["artifacts"]] == list(REQUIRED_ARTIFACTS)
    first = manifest["artifacts"][0]
    expected_digest = hashlib.sha256((source / first["path"]).read_bytes()).hexdigest()
    assert first["sha256"] == expected_digest
    assert (bundle_dir / first["path"]).exists()


def test_bundle_collector_fails_closed_when_required_artifact_missing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / "market_microstructure_gate.json", {"artifact": "market_microstructure_gate.json"})

    with pytest.raises(FileNotFoundError, match="passive_order_calibration_summary.json"):
        collect_promotion_evidence_bundle(source, tmp_path / "bundle", candidate_id="candidate-1")
