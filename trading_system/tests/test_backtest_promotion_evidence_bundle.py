from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import trading_system.app.backtest.promotion_evidence_bundle as promotion_bundle
from trading_system.app.backtest.promotion_evidence_bundle import (
    REQUIRED_ARTIFACTS,
    collect_promotion_evidence_bundle,
    verify_promotion_evidence_bundle,
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
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )

    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema_version"] == "promotion_evidence_bundle.v1"
    assert manifest["candidate_id"] == "candidate-1"
    assert manifest["decision"] == "bundle_complete"
    assert manifest["evidence_source"] == {"type": "promotion_bundle_export", "run_id": "bundle-1"}
    assert manifest["missing_artifacts"] == []
    assert [artifact["path"] for artifact in manifest["artifacts"]] == list(REQUIRED_ARTIFACTS)
    first = manifest["artifacts"][0]
    expected_digest = hashlib.sha256((source / first["path"]).read_bytes()).hexdigest()
    assert first["sha256"] == expected_digest
    assert (bundle_dir / first["path"]).exists()


def test_collect_rejects_noncanonical_candidate_id(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})

    with pytest.raises(ValueError, match="candidate_id must be canonical"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate/../evil",
            evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
        )


def test_collect_rejects_non_mapping_evidence_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})

    with pytest.raises(ValueError, match="evidence_source must be an object"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate-1",
            evidence_source=[("type", "promotion_bundle_export")],  # type: ignore[arg-type]
        )


def test_collect_rejects_non_string_required_artifact_entries(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / "123", {"artifact": "coerced"})

    with pytest.raises(ValueError, match="required artifact path entries must be strings"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate-1",
            evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
            required_artifacts=(123,),  # type: ignore[arg-type]
        )


def test_collected_bundle_can_be_consumed_by_live_readiness_smoke(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_json(
        source / "trades.json",
        {
            "trades": [
                {
                    "trade_id": "t1",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "net_pnl": 100.0,
                    "gross_pnl": 120.0,
                    "fee_paid": 10.0,
                    "slippage_paid": 10.0,
                    "fill_quality": "evidence_backed",
                    "execution_price_source": "trade_print",
                    "exit_fill_quality": "evidence_backed",
                    "exit_price_source": "trade_print",
                    "simulated_exit_reason": "take_profit",
                }
            ]
        },
    )
    _write_json(
        source / "exit_path_replay.json",
        {
            "schema_version": "exit_path_replay.v1",
            "evidence_source": {"type": "trade_print_path_replay", "run_id": "exit-path-1"},
            "trades": [{"trade_id": "t1"}],
        },
    )
    _write_json(
        source / "market_microstructure_gate.json",
        {
            "schema_version": "market_microstructure_gate_input.v1",
            "evidence_source": {"type": "historical_l2_tick_archive", "run_id": "microstructure-1"},
            "checks": {"l2_tick_coverage_met": True, "depth_driven_taker_met": True},
            "summary": {"min_l2_tick_coverage": 0.995},
        },
    )
    _write_json(
        source / "passive_order_calibration_summary.json",
        {
            "schema_version": "passive_order_calibration_summary.v1",
            "evidence_source": {"type": "testnet_exchange", "run_id": "passive-calibration-1"},
            "overall": {"attempt_count": 10, "fill_rate": 0.8},
            "provenance": {"source": "testnet_exchange", "real_exchange_records": True},
        },
    )
    _write_json(
        source / "validation_gate.json",
        {
            "schema_version": "validation_gate_input.v1",
            "evidence_source": {"type": "walk_forward_oos_report", "run_id": "validation-1"},
            "checks": {
                "oos_non_degraded_met": True,
                "multi_regime_met": True,
                "cost_stress_positive_met": True,
                "forward_contamination_absent_met": True,
            }
        },
    )
    _write_json(
        source / "runtime_safety_gate.json",
        {
            "schema_version": "runtime_safety_gate_input.v1",
            "evidence_source": {"type": "paper_runtime_logs", "run_id": "runtime-1"},
            "checks": {
                "kill_switch_dry_run_met": True,
                "order_position_reconciliation_met": True,
                "fail_closed_met": True,
                "dust_before_scale_met": True,
                "live_trade_ledger_met": True,
                "runtime_explainability_met": True,
                "drift_guard_met": True,
            }
        },
    )

    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )

    from trading_system.app.backtest.live_readiness import write_live_readiness_smoke_report

    report = write_live_readiness_smoke_report(
        bundle_dir,
        tmp_path / "out",
        require_microstructure_evidence=True,
        require_validation_evidence=True,
        require_runtime_safety_evidence=True,
        require_passive_calibration=True,
        require_exit_path_replay_rows=True,
        min_passive_calibration_attempts=5,
    )
    reasons = set(report["promotion_gate"]["reasons"])
    assert "microstructure_evidence_missing" not in reasons
    assert "validation_evidence_missing" not in reasons
    assert "runtime_safety_evidence_missing" not in reasons
    assert "passive_calibration_missing" not in reasons
    assert "exit_path_replay_missing_trades" not in reasons


def test_bundle_verifier_detects_missing_and_tampered_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name, "synthetic": True})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )

    verified = verify_promotion_evidence_bundle(bundle_dir)
    assert verified["schema_version"] == "promotion_evidence_bundle_verification.v1"
    assert verified["verified"] is True
    assert verified["missing_artifacts"] == []
    assert verified["sha256_mismatches"] == []

    (bundle_dir / REQUIRED_ARTIFACTS[0]).write_text("tampered\n", encoding="utf-8")
    tampered = verify_promotion_evidence_bundle(bundle_dir)
    assert tampered["verified"] is False
    assert tampered["sha256_mismatches"] == [REQUIRED_ARTIFACTS[0]]

    (bundle_dir / REQUIRED_ARTIFACTS[1]).unlink()
    missing = verify_promotion_evidence_bundle(bundle_dir)
    assert missing["verified"] is False
    assert REQUIRED_ARTIFACTS[1] in missing["missing_artifacts"]


def test_bundle_verify_only_cli_returns_nonzero_for_tampering(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name, "synthetic": True})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )

    import subprocess
    import sys

    ok = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.promotion_evidence_bundle",
            "--bundle-dir",
            str(bundle_dir),
            "--verify-only",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0
    assert '"verified": true' in ok.stdout

    (bundle_dir / REQUIRED_ARTIFACTS[0]).write_text("tampered\n", encoding="utf-8")
    bad = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.promotion_evidence_bundle",
            "--bundle-dir",
            str(bundle_dir),
            "--verify-only",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 1
    assert '"verified": false' in bad.stdout
    assert REQUIRED_ARTIFACTS[0] in bad.stdout


def test_bundle_verify_only_cli_writes_report_for_success_and_failure(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name, "synthetic": True})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    report_path = tmp_path / "reports" / "promotion_bundle_verification.json"

    import subprocess
    import sys

    ok = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.promotion_evidence_bundle",
            "--bundle-dir",
            str(bundle_dir),
            "--verify-only",
            "--verification-report-out",
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0
    ok_report = json.loads(report_path.read_text())
    assert ok_report["verified"] is True
    assert ok_report["checked_artifacts"]

    (bundle_dir / REQUIRED_ARTIFACTS[0]).write_text("tampered\n", encoding="utf-8")
    bad = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.promotion_evidence_bundle",
            "--bundle-dir",
            str(bundle_dir),
            "--verify-only",
            "--verification-report-out",
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 1
    bad_report = json.loads(report_path.read_text())
    assert bad_report["verified"] is False
    assert bad_report["sha256_mismatches"] == [REQUIRED_ARTIFACTS[0]]


def test_bundle_verifier_rejects_malformed_manifest_json(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "promotion_evidence_manifest.json").write_text("{not-json\n", encoding="utf-8")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["manifest_present"] is True
    assert result["schema_valid"] is False
    assert "invalid_manifest_json" in result["manifest_errors"]


def test_bundle_verifier_rejects_manifest_that_is_not_json_object(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "promotion_evidence_manifest.json").write_text("[]\n", encoding="utf-8")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert "manifest_not_object" in result["manifest_errors"]
    assert result["schema_valid"] is False


def test_bundle_verifier_rejects_invalid_manifest_schema_version(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name, "synthetic": True})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = "promotion_evidence_bundle.v0"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["schema_valid"] is False
    assert "invalid_schema_version" in result["manifest_errors"]


def test_bundle_verifier_rejects_required_artifact_missing_checksum_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name, "synthetic": True})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    missing_digest = REQUIRED_ARTIFACTS[0]
    manifest["artifacts"][0].pop("sha256")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["missing_artifact_metadata"] == [missing_digest]
    assert "artifact_metadata_missing" in result["manifest_errors"]


def test_bundle_verifier_rejects_required_artifact_missing_source_path_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name, "synthetic": True})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    missing_source_path = REQUIRED_ARTIFACTS[0]
    manifest["artifacts"][0].pop("source_path")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["missing_artifact_metadata"] == [f"{missing_source_path}:source_path"]
    assert "artifact_metadata_missing" in result["manifest_errors"]
    assert "artifact_source_path_missing" in result["manifest_errors"]


def test_bundle_verifier_rejects_required_artifact_invalid_byte_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name, "synthetic": True})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    invalid_bytes = REQUIRED_ARTIFACTS[0]
    manifest["artifacts"][0]["bytes"] = "not-an-int"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["invalid_artifact_metadata"] == [f"{invalid_bytes}:bytes"]
    assert "artifact_metadata_invalid" in result["manifest_errors"]


def test_bundle_verifier_rejects_duplicate_artifact_manifest_entries(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name, "synthetic": True})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    duplicated = manifest["artifacts"][0]["path"]
    manifest["artifacts"].append(dict(manifest["artifacts"][0]))
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["duplicate_artifact_paths"] == [duplicated]
    assert "duplicate_artifact_path" in result["manifest_errors"]


def test_bundle_verifier_rejects_missing_candidate_id(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name, "synthetic": True})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("candidate_id")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["candidate_id_valid"] is False
    assert "missing_candidate_id" in result["manifest_errors"]


def test_bundle_verifier_rejects_artifact_path_traversal(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name, "synthetic": True})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"][0]["path"] = "../outside.json"
    manifest["required_artifacts"][0] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert "../outside.json" in result["unsafe_artifact_paths"]
    assert "unsafe_artifact_path" in result["manifest_errors"]


def test_bundle_verifier_rejects_required_artifact_missing_manifest_entry(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name, "synthetic": True})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    omitted = REQUIRED_ARTIFACTS[0]
    manifest["artifacts"] = [artifact for artifact in manifest["artifacts"] if artifact["path"] != omitted]
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["unchecked_required_artifacts"] == [omitted]
    assert "required_artifact_missing_manifest_entry" in result["manifest_errors"]


def test_bundle_verifier_rejects_manifest_that_omits_default_required_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name, "synthetic": True})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    omitted = REQUIRED_ARTIFACTS[0]
    manifest["required_artifacts"] = [name for name in manifest["required_artifacts"] if name != omitted]
    manifest["artifacts"] = [artifact for artifact in manifest["artifacts"] if artifact["path"] != omitted]
    (bundle_dir / omitted).unlink()
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["omitted_default_required_artifacts"] == [omitted]
    assert omitted in result["missing_artifacts"]
    assert "default_required_artifact_omitted" in result["manifest_errors"]


def test_bundle_collector_rejects_blank_candidate_id(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name, "synthetic": True})

    with pytest.raises(ValueError, match="candidate_id"):
        collect_promotion_evidence_bundle(source, tmp_path / "bundle", candidate_id="  ")


def test_bundle_collector_rejects_unsafe_required_artifact_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.json"
    _write_json(outside, {"outside": True})

    with pytest.raises(ValueError, match="unsafe required artifact path"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate-1",
            required_artifacts=("../outside.json",),
        )


def test_bundle_collector_fails_closed_when_required_artifact_missing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / "market_microstructure_gate.json", {"artifact": "market_microstructure_gate.json"})

    with pytest.raises(FileNotFoundError, match="passive_order_calibration_summary.json"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate-1",
            evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
        )


def test_collect_rejects_unknown_evidence_source_fields(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})

    with pytest.raises(ValueError, match="unknown evidence_source field: label"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate-1",
            evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1", "label": "legacy-alias"},
        )

def test_collect_rejects_non_string_evidence_source_keys_before_copying(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle = tmp_path / "bundle"

    with pytest.raises(ValueError, match="evidence_source keys must be canonical strings"):
        collect_promotion_evidence_bundle(
            source,
            bundle,
            candidate_id="candidate-1",
            evidence_source={123: "x", "type": "promotion_bundle_export"},  # type: ignore[dict-item]
        )

    assert not bundle.exists()

def test_collect_rejects_padded_evidence_source_keys_before_copying(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle = tmp_path / "bundle"

    with pytest.raises(ValueError, match="evidence_source keys must be canonical strings"):
        collect_promotion_evidence_bundle(
            source,
            bundle,
            candidate_id="candidate-1",
            evidence_source={" type ": "promotion_bundle_export"},
        )

    assert not bundle.exists()

def test_collect_rejects_non_string_evidence_source_type(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})

    with pytest.raises(ValueError, match="evidence_source type must be a string"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate-1",
            evidence_source={"type": 123, "run_id": "bundle-1"},
        )

def test_collect_rejects_non_string_evidence_source_run_id(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})

    with pytest.raises(ValueError, match="evidence_source run_id must be a string"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate-1",
            evidence_source={"type": "promotion_bundle_export", "run_id": 123},
        )

def test_collect_rejects_non_live_grade_evidence_source_type(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})

    with pytest.raises(ValueError, match="evidence_source type must be live-grade"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate-1",
            evidence_source={"type": "synthetic_fixture", "run_id": "bundle-1"},
        )

def test_bundle_collector_rejects_padded_required_artifact_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / " trades.json ", {"artifact": "padded"})

    with pytest.raises(ValueError, match="noncanonical required artifact path"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate-1",
            evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
            required_artifacts=(" trades.json ",),
        )

def test_bundle_collector_rejects_noncanonical_required_artifact_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    nested = source / "nested"
    nested.mkdir()
    _write_json(nested / "artifact.json", {"artifact": "nested/artifact.json"})

    with pytest.raises(ValueError, match="noncanonical required artifact path"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate-1",
            evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
            required_artifacts=("nested//artifact.json",),
        )

def test_collect_requires_explicit_evidence_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})

    with pytest.raises(ValueError, match="evidence_source is required"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate-1",
        )

def test_promotion_bundle_cli_collect_requires_evidence_source_type(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.promotion_evidence_bundle",
            "--source-dir",
            str(source),
            "--bundle-dir",
            str(tmp_path / "bundle"),
            "--candidate-id",
            "candidate-1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "--evidence-source-type" in result.stderr


def test_promotion_bundle_cli_collect_accepts_live_grade_evidence_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle = tmp_path / "bundle"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.promotion_evidence_bundle",
            "--source-dir",
            str(source),
            "--bundle-dir",
            str(bundle),
            "--candidate-id",
            "candidate-1",
            "--evidence-source-type",
            "promotion_bundle_export",
            "--evidence-source-run-id",
            "bundle-1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    manifest = json.loads((bundle / "promotion_evidence_manifest.json").read_text())
    assert manifest["evidence_source"] == {"type": "promotion_bundle_export", "run_id": "bundle-1"}

def test_bundle_verifier_rejects_missing_manifest_decision(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("decision")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert "missing_manifest_decision" in result["manifest_errors"]

def test_bundle_verifier_marks_schema_invalid_for_unknown_manifest_fields(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["legacy_field"] = "stale"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["schema_valid"] is False
    assert "unknown_top_level_field: legacy_field" in result["manifest_errors"]

def test_bundle_verifier_marks_schema_invalid_for_missing_manifest_decision(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("decision")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["schema_valid"] is False
    assert "missing_manifest_decision" in result["manifest_errors"]

def test_bundle_verifier_marks_schema_invalid_for_missing_evidence_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("evidence_source")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["schema_valid"] is False
    assert "evidence_source_missing" in result["manifest_errors"]

def test_bundle_verifier_marks_schema_invalid_for_bad_artifact_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"][0]["bytes"] = "100"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["schema_valid"] is False
    assert "artifact_metadata_invalid" in result["manifest_errors"]

def test_bundle_verifier_marks_schema_invalid_for_required_artifacts_not_list(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["required_artifacts"] = {"path": REQUIRED_ARTIFACTS[0]}
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["schema_valid"] is False
    assert "required_artifacts_not_list" in result["manifest_errors"]

def test_bundle_verifier_marks_schema_invalid_for_required_artifacts_string_container(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["required_artifacts"] = REQUIRED_ARTIFACTS[0]
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["schema_valid"] is False
    assert "required_artifacts_not_list" in result["manifest_errors"]
    assert "required_artifacts_string_container" in result["manifest_errors"]
    assert result["invalid_required_artifacts"] == []

def test_collect_rejects_required_artifacts_mapping_container(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / "trades.json", {"artifact": "trades.json"})
    bundle = tmp_path / "bundle"

    with pytest.raises(ValueError, match="required_artifacts must be a list or tuple"):
        collect_promotion_evidence_bundle(
            source,
            bundle,
            candidate_id="candidate-1",
            evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
            required_artifacts={"trades.json": True},  # type: ignore[arg-type]
        )

    assert not bundle.exists()


def test_bundle_verifier_marks_schema_invalid_for_missing_candidate_id(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("candidate_id")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["schema_valid"] is False
    assert "missing_candidate_id" in result["manifest_errors"]

def test_bundle_verifier_marks_schema_invalid_for_non_string_declared_missing_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["missing_artifacts"] = [123]
    _write_json(manifest_path, manifest)

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["schema_valid"] is False
    assert "missing_artifact_entry_not_string" in result["manifest_errors"]
    assert result["declared_missing_artifacts"] == []
    assert result["invalid_declared_missing_artifacts"] == ["missing_artifacts[1]"]

def test_bundle_verifier_marks_schema_invalid_for_blank_declared_missing_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["missing_artifacts"] = ["   "]
    _write_json(manifest_path, manifest)

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["schema_valid"] is False
    assert "missing_artifact_entry_blank" in result["manifest_errors"]
    assert result["invalid_declared_missing_artifacts"] == ["missing_artifacts[1]"]

def test_collect_rejects_blank_required_artifact_entries(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(ValueError, match="blank required artifact path"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate-1",
            evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
            required_artifacts=("   ",),
        )

def test_collect_rejects_padded_evidence_source_type(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})

    with pytest.raises(ValueError, match="evidence_source type must be canonical"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate-1",
            evidence_source={"type": " promotion_bundle_export ", "run_id": "bundle-1"},
        )

def test_collect_rejects_padded_evidence_source_run_id(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})

    with pytest.raises(ValueError, match="evidence_source run_id must be canonical"):
        collect_promotion_evidence_bundle(
            source,
            tmp_path / "bundle",
            candidate_id="candidate-1",
            evidence_source={"type": "promotion_bundle_export", "run_id": " bundle-1 "},
        )

def test_bundle_verifier_marks_schema_invalid_for_padded_artifact_source_path(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["source_path"] = " " + manifest["artifacts"][0]["source_path"] + " "
    _write_json(manifest_path, manifest)

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["schema_valid"] is False
    assert "artifact_source_path_noncanonical" in result["manifest_errors"]
    assert result["invalid_artifact_metadata"] == [f"{REQUIRED_ARTIFACTS[0]}:source_path"]


def test_bundle_verifier_rejects_artifact_metadata_mapping_subclasses_and_noncanonical_source_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ArtifactMetadata(dict):
        pass

    source = tmp_path / "source"
    source.mkdir()
    for name in REQUIRED_ARTIFACTS:
        _write_json(source / name, {"artifact": name})
    bundle_dir = collect_promotion_evidence_bundle(
        source,
        tmp_path / "bundle",
        candidate_id="candidate-1",
        evidence_source={"type": "promotion_bundle_export", "run_id": "bundle-1"},
    )
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bad_metadata_path = REQUIRED_ARTIFACTS[0]
    bad_source_path = REQUIRED_ARTIFACTS[1]
    manifest["artifacts"][0] = ArtifactMetadata(manifest["artifacts"][0])
    manifest["artifacts"][1]["source_path"] = str(source / "nested" / ".." / bad_source_path)

    def load_manifest_with_mapping_subclass(_text: str) -> dict:
        return manifest

    monkeypatch.setattr(promotion_bundle.json, "loads", load_manifest_with_mapping_subclass)

    result = verify_promotion_evidence_bundle(bundle_dir)

    assert result["verified"] is False
    assert result["schema_valid"] is False
    assert f"artifacts[1]" in result["invalid_artifact_metadata"]
    assert f"{bad_source_path}:source_path" in result["invalid_artifact_metadata"]
    assert "artifact_entry_not_object" in result["manifest_errors"]
    assert "artifact_source_path_noncanonical" in result["manifest_errors"]


def test_bundle_verifier_reports_non_string_metadata_reason_entries() -> None:
    class NonStringReason:
        def __str__(self) -> str:
            return f"{REQUIRED_ARTIFACTS[0]}:sha256"

    reasons = promotion_bundle._promotion_artifact_metadata_reason_keys([NonStringReason()])

    assert "artifact_metadata_reason_entry_not_string" in reasons
    assert "artifact_sha256_invalid_format" not in reasons
