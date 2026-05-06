from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from trading_system.app.backtest import engine as backtest_engine
from trading_system.app.backtest.config import load_backtest_config
from trading_system.app.backtest.live_readiness import (
    audit_execution_depth,
    audit_exit_path_replay,
    build_live_readiness_gate_report,
    summarize_trade_postmortem,
    render_live_readiness_markdown,
    write_live_readiness_smoke_report,
)
from trading_system.app.backtest.microstructure_evidence import build_microstructure_gate
from trading_system.app.backtest.promotion_evidence_bundle import (
    collect_promotion_evidence_bundle,
    verify_promotion_evidence_bundle,
)
from trading_system.app.backtest.validation_evidence import build_validation_gate
from trading_system.app.backtest.types import DatasetSnapshotRow
from trading_system.app.execution.calibration import load_calibration_records, summarize_calibration_records
from trading_system.app.runtime.runtime_safety_evidence import build_runtime_safety_gate


def test_calibration_jsonl_summary_groups_passive_order_quality(tmp_path: Path) -> None:
    path = tmp_path / "calibration.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "symbol": "BTCUSDT",
                        "side": "buy",
                        "setup_type": "TREND_PULLBACK",
                        "intended_limit_price": 100.0,
                        "submitted_at": "2026-03-10T00:00:00Z",
                        "first_fill_at": "2026-03-10T00:00:05Z",
                        "last_fill_at": "2026-03-10T00:00:10Z",
                        "requested_qty": 2.0,
                        "requested_notional": 200.0,
                        "filled_qty": 2.0,
                        "filled_notional": 199.8,
                        "status": "filled",
                        "maker_taker": "maker",
                        "fees": 0.02,
                        "slippage_bps": -1.0,
                        "ref_price": 100.0,
                        "latency_ms": 120,
                    }
                ),
                json.dumps(
                    {
                        "symbol": "BTCUSDT",
                        "side": "buy",
                        "setup_type": "TREND_PULLBACK",
                        "intended_limit_price": 99.5,
                        "submitted_at": "2026-03-10T01:00:00Z",
                        "first_fill_at": "2026-03-10T01:00:30Z",
                        "requested_qty": 2.0,
                        "requested_notional": 199.0,
                        "filled_qty": 1.0,
                        "filled_notional": 99.4,
                        "status": "partially_filled",
                        "maker_taker": "maker",
                        "fees": 0.01,
                        "ref_price": 99.6,
                        "cancel_reason": "timeout",
                    }
                ),
                json.dumps(
                    {
                        "symbol": "ETHUSDT",
                        "side": "sell",
                        "setup_type": "FAILED_BOUNCE_SHORT",
                        "intended_limit_price": 2000.0,
                        "submitted_at": "2026-03-10T02:00:00Z",
                        "requested_qty": 1.0,
                        "requested_notional": 2000.0,
                        "filled_qty": 0.0,
                        "filled_notional": 0.0,
                        "status": "expired",
                        "maker_taker": "maker",
                        "ref_price": 1998.0,
                        "expire_reason": "no_touch",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    records = load_calibration_records(path)
    summary = summarize_calibration_records(records)

    assert summary["overall"]["attempt_count"] == 3
    assert summary["overall"]["fill_rate"] == pytest.approx(2 / 3)
    assert summary["overall"]["partial_fill_rate"] == pytest.approx(1 / 3)
    assert summary["overall"]["missed_fill_rate"] == pytest.approx(1 / 3)
    assert summary["overall"]["median_wait_seconds"] == pytest.approx(17.5)
    assert summary["overall"]["p95_wait_seconds"] == pytest.approx(29.0)
    assert summary["overall"]["fee_bps"] > 0.0
    assert summary["by_symbol"]["BTCUSDT"]["attempt_count"] == 2
    assert summary["by_side"]["buy"]["fill_rate"] == 1.0
    assert summary["by_setup_type"]["FAILED_BOUNCE_SHORT"]["missed_fill_rate"] == 1.0


def test_execution_depth_audit_classifies_trade_evidence_caveats() -> None:
    report = audit_execution_depth(
        {
            "trades": [
                {"symbol": "BTCUSDT", "fill_model": "taker_trade_print", "execution_price_source": "trade_print"},
                {"symbol": "ETHUSDT", "execution_price_source": "best_ask", "fill_model": "taker_orderbook"},
                {"symbol": "SOLUSDT", "depth_levels_consumed": 3, "fill_model": "taker_orderbook_depth"},
                {"symbol": "BNBUSDT", "maker_status": "filled", "maker_wait_seconds": 2.5},
                {"symbol": "XRPUSDT"},
            ]
        }
    )

    assert report["counts"] == {
        "trade_print_entry_only": 1,
        "has_orderbook_top": 1,
        "has_depth_levels": 1,
        "maker_calibrated_possible": 1,
        "insufficient_for_maker_replay": 1,
    }
    assert report["trades"][0]["classification"] == "trade_print_entry_only"
    assert any("substitute" in caveat.lower() for caveat in report["caveats"])


def test_exit_path_replay_audit_marks_intrabar_limitations() -> None:
    report = audit_exit_path_replay(
        [
            {"symbol": "BTCUSDT", "exit_reason": "fixed_horizon", "mfe_pct": 0.01, "mae_pct": -0.01},
            {
                "symbol": "ETHUSDT",
                "exit_reason": "fixed_horizon",
                "simulated_exit_reason": "stop_loss",
                "simulated_exit_price": 95.0,
                "mfe_pct": 0.02,
                "mae_pct": -0.03,
            },
            {
                "symbol": "XRPUSDT",
                "exit_reason": "fixed_horizon",
                "simulated_exit_reason": "stop_loss",
                "simulated_exit_ordering": "ambiguous_conservative_stop",
                "simulated_exit_price": 90.0,
                "mfe_pct": 0.03,
                "mae_pct": -0.04,
            },
            {"symbol": "SOLUSDT", "exit_reason": "take_profit", "mfe_pct": 0.04, "mae_pct": -0.03},
            {"symbol": "BNBUSDT", "exit_reason": "fixed_horizon"},
        ],
        market_context={"symbols": {"SOLUSDT": {"execution": {"trades": [{"price": 101.0}]}}}},
    )

    assert report["counts"]["fixed_horizon_only"] == 1
    assert report["counts"]["bar_path_stop_or_tp"] == 1
    assert report["counts"]["trade_print_path_available"] == 1
    assert report["counts"]["ambiguous_intrabar_order"] == 2
    assert any(row["simulated_exit_ordering"] == "ambiguous_conservative_stop" for row in report["trades"])
    assert any("does not invent tick precision" in caveat for caveat in report["caveats"])






def test_live_readiness_smoke_report_consumes_producer_gate_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    chunk = source / "chunk_001"
    chunk.mkdir(parents=True)
    trades = {
        "trades": [
            {
                "trade_id": "t1",
                "symbol": "BTCUSDT",
                "side": "long",
                "setup_type": "BREAKOUT_CONTINUATION",
                "net_pnl": 100.0,
                "gross_pnl": 125.0,
                "fee_paid": 10.0,
                "slippage_paid": 10.0,
                "funding_paid": 5.0,
                "fill_quality": "evidence_backed",
                "execution_price_source": "trade_print",
                "exit_fill_quality": "evidence_backed",
                "exit_price_source": "trade_print",
                "simulated_exit_reason": "take_profit",
            }
        ]
    }
    (chunk / "trades.json").write_text(json.dumps(trades), encoding="utf-8")
    (chunk / "summary.json").write_text(json.dumps({"net_pnl": 100.0}), encoding="utf-8")
    (chunk / "exit_path_replay.json").write_text(json.dumps({"trades": [{"trade_id": "t1"}]}), encoding="utf-8")
    (chunk / "market_microstructure_gate.json").write_text(
        json.dumps(
            build_microstructure_gate(
                {
                    "coverage": {"l2_tick_coverage": 0.995, "required_l2_tick_coverage": 0.99},
                    "depth_driven_fills": [{"depth_sufficient": True, "slippage_bps": 1.5}],
                }
            )
        ),
        encoding="utf-8",
    )
    (chunk / "validation_gate.json").write_text(
        json.dumps(
            build_validation_gate(
                {
                    "oos": {"baseline_net_pnl": 100.0, "oos_net_pnl": 80.0, "min_oos_ratio": 0.5},
                    "regimes": [
                        {"name": "trend", "net_pnl": 40.0},
                        {"name": "chop", "net_pnl": 20.0},
                    ],
                    "cost_stress": {"stressed_net_pnl": 30.0},
                    "forward_contamination": {"detected": False, "audit_complete": True},
                }
            )
        ),
        encoding="utf-8",
    )
    (chunk / "runtime_safety_gate.json").write_text(
        json.dumps(
            build_runtime_safety_gate(
                {
                    "events": [
                        {"event_type": "kill_switch_dry_run", "passed": True},
                        {"event_type": "order_position_reconciliation", "passed": True},
                        {"event_type": "fail_closed", "passed": True},
                        {"event_type": "live_dust_before_scale", "passed": True},
                        {"event_type": "live_trade_ledger", "passed": True},
                        {"event_type": "runtime_explainability", "passed": True},
                        {"event_type": "drift_guard", "passed": True},
                    ]
                }
            )
        ),
        encoding="utf-8",
    )
    (chunk / "passive_order_calibration_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "passive_order_calibration_summary.v1",
                "evidence_source": {"type": "testnet_exchange", "run_id": "passive-calibration-1"},
                "overall": {"attempt_count": 10, "fill_rate": 0.7},
                "provenance": {"source": "testnet_exchange", "real_exchange_records": True},
            }
        ),
        encoding="utf-8",
    )

    report = write_live_readiness_smoke_report(
        source,
        tmp_path / "out",
        require_microstructure_evidence=True,
        require_validation_evidence=True,
        require_runtime_safety_evidence=True,
        require_passive_calibration=True,
        min_passive_calibration_attempts=5,
        min_passive_fill_rate=0.5,
        require_exit_path_replay_rows=True,
        max_setup_trade_share=None,
        max_symbol_trade_share=None,
        max_setup_net_abs_share=None,
        max_symbol_net_abs_share=None,
        max_setup_loss_abs_share=None,
        max_symbol_loss_abs_share=None,
    )

    assert report["microstructure_gate"]["artifact_count"] == 1
    assert report["validation_gate"]["artifact_count"] == 1
    assert report["runtime_safety_gate"]["artifact_count"] == 1
    assert report["passive_calibration"]["chunks"]
    reasons = set(report["promotion_gate"]["reasons"])
    assert "microstructure_evidence_missing" not in reasons
    assert "validation_evidence_missing" not in reasons
    assert "runtime_safety_evidence_missing" not in reasons
    assert "passive_calibration_missing" not in reasons


def test_live_readiness_smoke_report_rejects_tampered_promotion_bundle(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    trade = {
        "trade_id": "t1",
        "symbol": "BTCUSDT",
        "side": "long",
        "setup_type": "BREAKOUT_CONTINUATION",
        "net_pnl": 100.0,
        "gross_pnl": 125.0,
        "fee_paid": 10.0,
        "slippage_paid": 10.0,
        "funding_paid": 5.0,
        "fill_quality": "evidence_backed",
        "execution_price_source": "trade_print",
        "exit_fill_quality": "evidence_backed",
        "exit_price_source": "trade_print",
        "simulated_exit_reason": "take_profit",
    }
    (source / "trades.json").write_text(json.dumps({"trades": [trade]}), encoding="utf-8")
    (source / "exit_path_replay.json").write_text(json.dumps({"trades": [{"trade_id": "t1"}]}), encoding="utf-8")
    (source / "market_microstructure_gate.json").write_text(
        json.dumps({"checks": {"l2_tick_coverage_met": True, "depth_driven_taker_met": True}}),
        encoding="utf-8",
    )
    (source / "validation_gate.json").write_text(
        json.dumps(
            {
                "checks": {
                    "oos_non_degraded_met": True,
                    "multi_regime_met": True,
                    "cost_stress_positive_met": True,
                    "forward_contamination_absent_met": True,
                }
            }
        ),
        encoding="utf-8",
    )
    (source / "runtime_safety_gate.json").write_text(
        json.dumps(
            {
                "checks": {
                    "kill_switch_dry_run_met": True,
                    "order_position_reconciliation_met": True,
                    "fail_closed_met": True,
                    "dust_before_scale_met": True,
                    "live_trade_ledger_met": True,
                    "runtime_explainability_met": True,
                    "drift_guard_met": True,
                }
            }
        ),
        encoding="utf-8",
    )
    (source / "passive_order_calibration_summary.json").write_text(
        json.dumps(
            {
                "overall": {"attempt_count": 10, "fill_rate": 0.8},
                "provenance": {"source": "testnet_exchange", "real_exchange_records": True},
            }
        ),
        encoding="utf-8",
    )
    bundle_dir = collect_promotion_evidence_bundle(source, tmp_path / "bundle", candidate_id="candidate-1")
    (bundle_dir / "trades.json").write_text(json.dumps({"trades": [{**trade, "net_pnl": 101.0}]}), encoding="utf-8")

    report = write_live_readiness_smoke_report(
        bundle_dir,
        tmp_path / "out",
        require_promotion_bundle_integrity=True,
        require_microstructure_evidence=True,
        require_validation_evidence=True,
        require_runtime_safety_evidence=True,
        require_passive_calibration=True,
        require_exit_path_replay_rows=True,
        max_setup_trade_share=None,
        max_symbol_trade_share=None,
        max_setup_net_abs_share=None,
        max_symbol_net_abs_share=None,
        max_setup_loss_abs_share=None,
        max_symbol_loss_abs_share=None,
    )

    assert report["promotion_bundle_integrity"]["verified"] is False
    assert "trades.json" in report["promotion_bundle_integrity"]["sha256_mismatches"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"
    markdown = render_live_readiness_markdown(report)
    assert "## Promotion Bundle Integrity" in markdown
    assert "verified: false" in markdown
    assert "- sha256_mismatches: trades.json" in markdown


def test_promotion_bundle_verification_error_reports_keep_stable_audit_fields(tmp_path: Path) -> None:
    expected_fields = {
        "declared_missing_artifacts",
        "invalid_declared_missing_artifacts",
        "unsafe_declared_missing_artifacts",
        "noncanonical_declared_missing_artifacts",
        "missing_artifacts",
        "unchecked_required_artifacts",
        "invalid_required_artifacts",
        "unsafe_required_artifacts",
        "noncanonical_required_artifacts",
        "duplicate_required_artifacts",
        "omitted_default_required_artifacts",
        "missing_artifact_metadata",
        "invalid_artifact_metadata",
        "duplicate_artifact_paths",
        "unsafe_artifact_paths",
        "noncanonical_artifact_paths",
        "sha256_mismatches",
        "byte_size_mismatches",
        "checked_artifacts",
    }

    missing_report = verify_promotion_evidence_bundle(tmp_path / "missing_bundle")
    malformed_dir = tmp_path / "malformed_bundle"
    malformed_dir.mkdir()
    (malformed_dir / "promotion_evidence_manifest.json").write_text('{"schema_version": ', encoding="utf-8")
    malformed_report = verify_promotion_evidence_bundle(malformed_dir)

    for report in (missing_report, malformed_report):
        assert report["verified"] is False
        assert expected_fields <= report.keys()
        for field in expected_fields:
            assert report[field] == []


def test_live_readiness_gate_rejects_malformed_present_promotion_manifest(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (tmp_path / "promotion_evidence_manifest.json").write_text('{"schema_version": ', encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["promotion_bundle_integrity"]["manifest_present"] is True
    assert report["promotion_bundle_integrity"]["verified"] is False
    assert report["promotion_bundle_integrity"]["manifest_errors"] == ["invalid_manifest_json"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_required_artifacts_not_list(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "required_artifacts": "trades.json",
                "artifacts": [],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "required_artifacts_not_list" in integrity["manifest_errors"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_non_object_artifact_row(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "required_artifacts": [
                    "trades.json",
                    "exit_path_replay.json",
                    "market_microstructure_gate.json",
                    "passive_order_calibration_summary.json",
                    "validation_gate.json",
                    "runtime_safety_gate.json",
                ],
                "artifacts": ["trades.json"],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "artifact_entry_not_object" in integrity["manifest_errors"]
    assert integrity["invalid_artifact_metadata"] == ["artifacts[1]"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_artifact_missing_path(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "required_artifacts": [
                    "trades.json",
                    "exit_path_replay.json",
                    "market_microstructure_gate.json",
                    "passive_order_calibration_summary.json",
                    "validation_gate.json",
                    "runtime_safety_gate.json",
                ],
                "artifacts": [{"sha256": "deadbeef", "bytes": 10}],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "artifact_path_missing" in integrity["manifest_errors"]
    assert integrity["missing_artifact_metadata"] == ["artifacts[1].path"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_artifact_path_non_string(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [{"path": 123, "sha256": "deadbeef", "bytes": 10}],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "artifact_path_not_string" in integrity["manifest_errors"]
    assert integrity["invalid_artifact_metadata"] == ["artifacts[1].path"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_artifact_path_noncanonical(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "required_artifacts": ["chunk_001//trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001//trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "artifact_path_noncanonical" in integrity["manifest_errors"]
    assert integrity["noncanonical_artifact_paths"] == ["chunk_001//trades.json"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_artifact_sha256_bad_format(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": "not-a-sha256",
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "artifact_sha256_invalid_format" in integrity["manifest_errors"]
    assert integrity["invalid_artifact_metadata"] == ["chunk_001/trades.json:sha256"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_source_path_non_string(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "source_path": 123,
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "artifact_source_path_not_string" in integrity["manifest_errors"]
    assert integrity["invalid_artifact_metadata"] == ["chunk_001/trades.json:source_path"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_source_path_blank(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "source_path": "",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "artifact_source_path_blank" in integrity["manifest_errors"]
    assert integrity["invalid_artifact_metadata"] == ["chunk_001/trades.json:source_path"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_artifact_bytes_string(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    trades_bytes = trades_path.stat().st_size
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": str(trades_bytes),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "artifact_metadata_invalid" in integrity["manifest_errors"]
    assert integrity["invalid_artifact_metadata"] == ["chunk_001/trades.json:bytes"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_noncanonical_candidate_id(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "../candidate-1",
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["candidate_id_valid"] is False
    assert "invalid_candidate_id" in integrity["manifest_errors"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_evidence_source_not_object(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "evidence_source": "live-ish",
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "evidence_source_not_object" in integrity["manifest_errors"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_evidence_source_type_not_string(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "evidence_source": {"type": 123},
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "evidence_source_type_not_string" in integrity["manifest_errors"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_evidence_source_type_blank(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "evidence_source": {"type": ""},
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "evidence_source_type_blank" in integrity["manifest_errors"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_decision_not_complete(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "decision": "bundle_incomplete",
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "invalid_manifest_decision" in integrity["manifest_errors"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_declared_missing_artifacts(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "decision": "bundle_complete",
                "missing_artifacts": ["exit_path_replay.json"],
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "manifest_declares_missing_artifacts" in integrity["manifest_errors"]
    assert integrity["declared_missing_artifacts"] == ["exit_path_replay.json"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_missing_artifacts_not_list(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "decision": "bundle_complete",
                "missing_artifacts": None,
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "missing_artifacts_not_list" in integrity["manifest_errors"]
    assert integrity["declared_missing_artifacts"] == []
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_missing_artifact_non_string(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "decision": "bundle_complete",
                "missing_artifacts": [123],
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "missing_artifact_entry_not_string" in integrity["manifest_errors"]
    assert integrity["invalid_declared_missing_artifacts"] == ["missing_artifacts[1]"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_missing_artifact_blank(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "decision": "bundle_complete",
                "missing_artifacts": [""],
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "missing_artifact_entry_blank" in integrity["manifest_errors"]
    assert integrity["invalid_declared_missing_artifacts"] == ["missing_artifacts[1]"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_missing_artifact_unsafe_path(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "decision": "bundle_complete",
                "missing_artifacts": ["../evil.json"],
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "missing_artifact_path_unsafe" in integrity["manifest_errors"]
    assert integrity["unsafe_declared_missing_artifacts"] == ["../evil.json"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_missing_artifact_noncanonical_path(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "decision": "bundle_complete",
                "missing_artifacts": ["chunk_001//trades.json"],
                "required_artifacts": ["chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "missing_artifact_path_noncanonical" in integrity["manifest_errors"]
    assert integrity["noncanonical_declared_missing_artifacts"] == ["chunk_001//trades.json"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_required_artifact_non_string(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "required_artifacts": ["chunk_001/trades.json", 123],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "required_artifact_entry_not_string" in integrity["manifest_errors"]
    assert integrity["invalid_required_artifacts"] == ["required_artifacts[2]"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_required_artifact_blank(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "required_artifacts": ["chunk_001/trades.json", ""],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "required_artifact_entry_blank" in integrity["manifest_errors"]
    assert integrity["invalid_required_artifacts"] == ["required_artifacts[2]"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_required_artifact_noncanonical(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "required_artifacts": ["chunk_001/trades.json", "chunk_001//trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "required_artifact_path_noncanonical" in integrity["manifest_errors"]
    assert integrity["noncanonical_required_artifacts"] == ["chunk_001//trades.json"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_required_artifact_unsafe_path(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "required_artifacts": ["chunk_001/trades.json", "../evil.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "required_artifact_path_unsafe" in integrity["manifest_errors"]
    assert integrity["unsafe_required_artifacts"] == ["../evil.json"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_promotion_manifest_duplicate_required_artifacts(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    trades_path = chunk / "trades.json"
    (tmp_path / "promotion_evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "promotion_evidence_bundle.v1",
                "candidate_id": "candidate-1",
                "required_artifacts": ["chunk_001/trades.json", "chunk_001/trades.json"],
                "artifacts": [
                    {
                        "path": "chunk_001/trades.json",
                        "sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
                        "bytes": trades_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["promotion_bundle_integrity"]
    assert integrity["verified"] is False
    assert "duplicate_required_artifact" in integrity["manifest_errors"]
    assert integrity["duplicate_required_artifacts"] == ["chunk_001/trades.json"]
    assert "promotion_bundle_integrity_failed" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["promotion_bundle_integrity_verified"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_markdown_shows_bundle_manifest_and_metadata_errors(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    trade = {
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
    (source / "trades.json").write_text(json.dumps({"trades": [trade]}), encoding="utf-8")
    (source / "exit_path_replay.json").write_text(
        json.dumps(
            {
                "schema_version": "exit_path_replay.v1",
                "evidence_source": {"type": "trade_print_path_replay", "run_id": "exit-path-1"},
                "trades": [{"trade_id": "t1"}],
            }
        ),
        encoding="utf-8",
    )
    (source / "market_microstructure_gate.json").write_text(
        json.dumps(
            {
                "schema_version": "market_microstructure_gate_input.v1",
                "evidence_source": {"type": "historical_l2_tick_archive", "run_id": "microstructure-1"},
                "checks": {"l2_tick_coverage_met": True, "depth_driven_taker_met": True},
                "summary": {"min_l2_tick_coverage": 0.995},
            }
        ),
        encoding="utf-8",
    )
    (source / "passive_order_calibration_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "passive_order_calibration_summary.v1",
                "evidence_source": {"type": "testnet_exchange", "run_id": "passive-calibration-1"},
                "overall": {"attempt_count": 10, "fill_rate": 0.8},
            }
        ),
        encoding="utf-8",
    )
    (source / "validation_gate.json").write_text(
        json.dumps(
            {
                "schema_version": "validation_gate_input.v1",
                "evidence_source": {"type": "walk_forward_oos_report", "run_id": "validation-1"},
                "checks": {
                    "oos_non_degraded_met": True,
                    "multi_regime_met": True,
                    "cost_stress_positive_met": True,
                    "forward_contamination_absent_met": True,
                },
            }
        ),
        encoding="utf-8",
    )
    (source / "runtime_safety_gate.json").write_text(
        json.dumps(
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
                },
            }
        ),
        encoding="utf-8",
    )
    bundle_dir = collect_promotion_evidence_bundle(source, tmp_path / "bundle", candidate_id="candidate-1")
    manifest_path = bundle_dir / "promotion_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"][0].pop("sha256")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    report = write_live_readiness_smoke_report(
        bundle_dir,
        tmp_path / "out",
        require_promotion_bundle_integrity=True,
        max_setup_trade_share=None,
        max_symbol_trade_share=None,
        max_setup_net_abs_share=None,
        max_symbol_net_abs_share=None,
        max_setup_loss_abs_share=None,
        max_symbol_loss_abs_share=None,
    )

    markdown = render_live_readiness_markdown(report)
    assert "- manifest_errors: artifact_metadata_missing" in markdown
    assert "- missing_artifact_metadata: trades.json" in markdown


def test_live_readiness_gate_report_rejects_invalid_passive_calibration_schema_and_provenance(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 1.5,
                        "gross_pnl": 1.5,
                        "fee_paid": 0.0,
                        "slippage_paid": 0.0,
                        "funding_paid": 0.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "take_profit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (chunk / "passive_order_calibration_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "unexpected_passive_calibration.v0",
                "evidence_source": {"type": "unknown_offline_records"},
                "overall": {"attempt_count": 20, "fill_rate": 0.9},
                "provenance": {"source": "testnet_exchange", "real_exchange_records": True},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(
        tmp_path,
        require_passive_calibration=True,
        min_passive_calibration_attempts=5,
        min_passive_fill_rate=0.5,
    )

    passive = report["passive_calibration"]
    reasons = set(report["promotion_gate"]["reasons"])
    assert passive["checks"]["passive_calibration_artifact_schema_valid"] is False
    assert passive["checks"]["passive_calibration_artifact_provenance_present"] is False
    assert "passive_calibration_artifact_schema_invalid" in reasons
    assert "passive_calibration_artifact_provenance_missing" in reasons
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_malformed_required_runtime_safety_artifact(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 1.5,
                        "gross_pnl": 1.5,
                        "fee_paid": 0.0,
                        "slippage_paid": 0.0,
                        "funding_paid": 0.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "take_profit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (chunk / "runtime_safety_gate.json").write_text("{not-json\n", encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path, require_runtime_safety_evidence=True)

    runtime = report["runtime_safety_gate"]
    assert runtime["checks"]["runtime_safety_artifact_schema_valid"] is False
    assert runtime["artifacts"][0]["parse_error"]
    assert "runtime_safety_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    markdown = render_live_readiness_markdown(report)
    assert "runtime_safety_artifact_parse_errors: chunk_001=invalid_json" in markdown
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_invalid_passive_calibration_numeric_fields(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 1.5,
                        "gross_pnl": 1.5,
                        "fee_paid": 0.0,
                        "slippage_paid": 0.0,
                        "funding_paid": 0.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "take_profit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (chunk / "passive_order_calibration_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "passive_order_calibration_summary.v1",
                "evidence_source": {"type": "exchange_export", "run_id": "calibration-1"},
                "overall": {"attempt_count": "not-an-int", "fill_rate": "not-a-float"},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path, require_passive_calibration=True)

    passive = report["passive_calibration"]
    assert passive["checks"]["passive_calibration_artifact_schema_valid"] is False
    assert passive["chunks"][0]["parse_error"] == "invalid_numeric_field: attempt_count"
    assert "passive_calibration_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_non_finite_passive_calibration_fill_rate(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 1.5,
                        "gross_pnl": 1.5,
                        "fee_paid": 0.0,
                        "slippage_paid": 0.0,
                        "funding_paid": 0.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "take_profit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (chunk / "passive_order_calibration_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "passive_order_calibration_summary.v1",
                "evidence_source": {"type": "exchange_export", "run_id": "calibration-1"},
                "overall": {"attempt_count": 10, "fill_rate": "NaN"},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path, require_passive_calibration=True)

    passive = report["passive_calibration"]
    assert passive["checks"]["passive_calibration_artifact_schema_valid"] is False
    assert passive["chunks"][0]["parse_error"] == "invalid_numeric_field: fill_rate"
    assert "passive_calibration_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def _write_profitable_trade_chunk(chunk: Path) -> None:
    chunk.mkdir(parents=True, exist_ok=True)
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "schema_version": "trades.v1",
                "trades": [
                    {
                        "trade_id": "trade-001",
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "entry_time": "2026-03-10T00:00:00Z",
                        "exit_time": "2026-03-10T00:05:00Z",
                        "entry_price": 100.0,
                        "exit_price": 101.5,
                        "quantity": 1.0,
                        "notional": 100.0,
                        "net_pnl": 1.5,
                        "gross_pnl": 1.5,
                        "fee_paid": 0.0,
                        "slippage_paid": 0.0,
                        "funding_paid": 0.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "take_profit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (chunk / "summary.json").write_text(
        json.dumps(
            {
                "schema_version": "backtest_summary.v1",
                "summary": {
                    "trade_count": 1,
                    "cost_breakdown": {"fees": 0.0, "slippage": 0.0, "funding": 0.0},
                },
            }
        ),
        encoding="utf-8",
    )


def test_profitable_trade_fixture_is_live_readiness_candidate(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)

    report = build_live_readiness_gate_report(tmp_path)

    assert report["promotion_gate"]["decision"] == "candidate_for_promotion"
    assert report["promotion_gate"]["reasons"] == []


def test_live_readiness_gate_checks_expose_trade_integrity_results(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)

    report = build_live_readiness_gate_report(tmp_path)

    checks = report["promotion_gate"]["checks"]
    assert checks["trade_financial_integrity_valid"] is True
    assert checks["trades_artifact_integrity_valid"] is True
    assert checks["summary_artifact_integrity_valid"] is True
    assert checks["trade_identity_integrity_valid"] is True
    assert checks["trade_dimension_integrity_valid"] is True
    assert checks["trade_time_integrity_valid"] is True
    assert checks["trade_price_integrity_valid"] is True
    assert checks["trade_size_integrity_valid"] is True
    assert checks["trade_notional_consistency_valid"] is True
    assert checks["trade_cost_sign_integrity_valid"] is True
    assert checks["trade_pnl_consistency_valid"] is True
    assert checks["trade_side_price_pnl_consistency_valid"] is True
    assert checks["trade_exit_reason_integrity_valid"] is True

    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    del payload["trades"][0]["fee_paid"]
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    rejected_report = build_live_readiness_gate_report(tmp_path)

    rejected_checks = rejected_report["promotion_gate"]["checks"]
    assert rejected_checks["trade_financial_integrity_valid"] is False
    assert rejected_checks["trade_pnl_consistency_valid"] is True
    assert "trade_financial_metric_invalid" in rejected_report["promotion_gate"]["reasons"]
    assert rejected_report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_present_runtime_safety_artifact_with_bad_schema(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "runtime_safety_gate.json").write_text(
        json.dumps(
            {
                "schema_version": "runtime_safety_gate_input.v0",
                "evidence_source": {"type": "testnet_exchange"},
                "checks": {
                    "kill_switch_dry_run_met": True,
                    "order_position_reconciliation_met": True,
                    "fail_closed_met": True,
                    "dust_before_scale_met": True,
                    "live_trade_ledger_met": True,
                    "runtime_explainability_met": True,
                    "drift_guard_met": True,
                },
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    assert report["runtime_safety_gate"]["checks"]["runtime_safety_artifact_schema_valid"] is False
    assert "runtime_safety_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["runtime_safety_artifact_schema_valid"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_present_exit_path_replay_artifact_with_bad_rows(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "exit_path_replay.json").write_text(
        json.dumps(
            {
                "schema_version": "exit_path_replay.v1",
                "evidence_source": {"type": "testnet_exchange"},
                "trades": {"trade_id": "trade-001"},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    reconciliation = report["exit_path_replay"]["reconciliation"]
    assert reconciliation["schema_valid"] is False
    assert reconciliation["artifacts"][0]["parse_error"] == "trades_rows_not_list"
    assert "exit_path_replay_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["exit_path_replay_rows_met"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_present_exit_path_replay_artifact_with_non_object_row(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "exit_path_replay.json").write_text(
        json.dumps(
            {
                "schema_version": "exit_path_replay.v1",
                "evidence_source": {"type": "testnet_exchange"},
                "trades": ["trade-001"],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    reconciliation = report["exit_path_replay"]["reconciliation"]
    assert reconciliation["schema_valid"] is False
    assert reconciliation["artifacts"][0]["parse_error"] == "trade_row_not_object: trades[1]"
    assert "exit_path_replay_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["exit_path_replay_rows_met"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_exit_path_replay_blank_trade_id(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "exit_path_replay.json").write_text(
        json.dumps(
            {
                "schema_version": "exit_path_replay.v1",
                "evidence_source": {"type": "testnet_exchange"},
                "trades": [{"trade_id": "   "}],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    reconciliation = report["exit_path_replay"]["reconciliation"]
    assert reconciliation["schema_valid"] is False
    assert reconciliation["artifacts"][0]["parse_error"] == "trade_id_missing_or_blank: trades[1]"
    assert "exit_path_replay_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["exit_path_replay_rows_met"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_exit_path_replay_invalid_trade_id(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "exit_path_replay.json").write_text(
        json.dumps(
            {
                "schema_version": "exit_path_replay.v1",
                "evidence_source": {"type": "testnet_exchange"},
                "trades": [{"trade_id": "../trade 001"}],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    reconciliation = report["exit_path_replay"]["reconciliation"]
    assert reconciliation["schema_valid"] is False
    assert reconciliation["artifacts"][0]["parse_error"] == "invalid_trade_id: trades[1]"
    assert "exit_path_replay_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["exit_path_replay_rows_met"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_exit_path_replay_duplicate_trade_ids(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "exit_path_replay.json").write_text(
        json.dumps(
            {
                "schema_version": "exit_path_replay.v1",
                "evidence_source": {"type": "testnet_exchange"},
                "trades": [{"trade_id": "trade-001"}, {"trade_id": "trade-001"}],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    reconciliation = report["exit_path_replay"]["reconciliation"]
    assert reconciliation["matched"] is False
    assert reconciliation["duplicate_path_trade_count"] == 1
    assert reconciliation["duplicate_path_trade_ids"] == ["trade-001"]
    assert "exit_path_replay_duplicate_trades" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["exit_path_replay_rows_met"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_exit_path_reconciliation_reports_source_trade_id_invalid(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["trade_id"] = "trade 001"
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")
    (chunk / "exit_path_replay.json").write_text(
        json.dumps(
            {
                "schema_version": "exit_path_replay.v1",
                "evidence_source": {"type": "testnet_exchange"},
                "trades": [{"trade_id": "trade 001"}],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    reconciliation = report["exit_path_replay"]["reconciliation"]
    assert reconciliation["matched"] is False
    assert reconciliation["invalid_source_trade_id_count"] == 1
    assert reconciliation["invalid_source_trade_ids"] == [
        {"chunk": "chunk_001", "index": 1, "trade_id": "trade 001", "error": "invalid_trade_id"}
    ]
    assert "exit_path_replay_source_trade_id_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_exit_path_reconciliation_reports_source_trade_id_duplicates(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    first = payload["trades"][0]
    payload["trades"].append({**first, "net_pnl": 2.0, "gross_pnl": 2.0})
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")
    (chunk / "summary.json").write_text(
        json.dumps(
            {
                "schema_version": "backtest_summary.v1",
                "summary": {"trade_count": 2, "cost_breakdown": {"fees": 0.0, "slippage": 0.0, "funding": 0.0}},
            }
        ),
        encoding="utf-8",
    )
    (chunk / "exit_path_replay.json").write_text(
        json.dumps(
            {
                "schema_version": "exit_path_replay.v1",
                "evidence_source": {"type": "testnet_exchange"},
                "trades": [{"trade_id": "trade-001"}],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    reconciliation = report["exit_path_replay"]["reconciliation"]
    assert reconciliation["matched"] is False
    assert reconciliation["duplicate_source_trade_count"] == 1
    assert reconciliation["duplicate_source_trade_ids"] == ["trade-001"]
    assert "exit_path_replay_source_trade_id_duplicate" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_runtime_safety_string_false_checks(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    runtime_payload = {
        "schema_version": "runtime_safety_gate_input.v1",
        "evidence_source": {"type": "exchange_export", "run_id": "runtime-1"},
        "checks": {
            "kill_switch_dry_run_met": "false",
            "order_position_reconciliation_met": True,
            "fail_closed_met": True,
            "dust_before_scale_met": True,
            "live_trade_ledger_met": True,
            "runtime_explainability_met": True,
            "drift_guard_met": True,
        },
    }
    (chunk / "runtime_safety_gate.json").write_text(json.dumps(runtime_payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path, require_runtime_safety_evidence=True)

    runtime_gate = report["runtime_safety_gate"]
    assert runtime_gate["artifacts"][0]["checks"]["kill_switch_dry_run_met"] is False
    assert runtime_gate["checks"]["kill_switch_dry_run_met"] is False
    assert "kill_switch_dry_run_missing" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"



def test_live_readiness_gate_rejects_summary_artifact_missing_cost_breakdown_fields(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    summary_payload = json.loads((chunk / "summary.json").read_text(encoding="utf-8"))
    summary_payload["summary"]["cost_breakdown"].pop("fees")
    (chunk / "summary.json").write_text(json.dumps(summary_payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["summary_artifact_integrity"]
    assert integrity["valid"] is False
    assert any(
        item.get("error") == "missing_cost_breakdown_field" and item.get("field") == "summary.cost_breakdown.fees"
        for item in integrity["invalid_artifacts"]
    )
    assert "summary_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"



def test_live_readiness_gate_rejects_summary_artifact_missing_trade_count(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    summary_payload = json.loads((chunk / "summary.json").read_text(encoding="utf-8"))
    summary_payload["summary"].pop("trade_count")
    (chunk / "summary.json").write_text(json.dumps(summary_payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["summary_artifact_integrity"]
    assert integrity["valid"] is False
    assert any(
        item.get("error") == "missing_summary_trade_count" and item.get("field") == "summary.trade_count"
        for item in integrity["invalid_artifacts"]
    )
    assert "summary_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"



def test_live_readiness_gate_rejects_non_string_trade_ids(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["trade_id"] = 12345
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    identity = report["trade_identity_integrity"]
    assert identity["valid"] is False
    assert identity["invalid_trade_ids"] == [
        {"chunk": "chunk_001", "index": 1, "trade_id": 12345, "error": "trade_id_not_string"}
    ]
    assert "trade_identity_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"



def test_live_readiness_gate_rejects_string_trade_prices(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["entry_price"] = "100.0"
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    price_integrity = report["trade_price_integrity"]
    assert price_integrity["valid"] is False
    assert any(
        item.get("error") == "invalid_price" and item.get("field") == "entry_price" and item.get("value") == "100.0"
        for item in price_integrity["invalid_fields"]
    )
    assert "trade_price_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"



def test_live_readiness_gate_rejects_string_trade_financial_fields(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["net_pnl"] = "1.5"
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    financial = report["trade_financial_integrity"]
    assert financial["valid"] is False
    assert any(
        item.get("field") == "net_pnl" and item.get("value") == "1.5" and item.get("error") == "invalid_financial_field"
        for item in financial["invalid_fields"]
    )
    assert "trade_financial_metric_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"



def test_live_readiness_gate_rejects_string_summary_cost_breakdown_values(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    summary_payload = json.loads((chunk / "summary.json").read_text(encoding="utf-8"))
    summary_payload["summary"]["cost_breakdown"]["fees"] = "0.0"
    (chunk / "summary.json").write_text(json.dumps(summary_payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["summary_artifact_integrity"]
    assert integrity["valid"] is False
    assert any(
        item.get("error") == "invalid_cost_breakdown_value"
        and item.get("field") == "summary.cost_breakdown.fees"
        and item.get("value") == "0.0"
        for item in integrity["invalid_artifacts"]
    )
    assert "summary_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"



def test_live_readiness_gate_rejects_string_summary_trade_count(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    summary_payload = json.loads((chunk / "summary.json").read_text(encoding="utf-8"))
    summary_payload["summary"]["trade_count"] = "1"
    (chunk / "summary.json").write_text(json.dumps(summary_payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["summary_artifact_integrity"]
    assert integrity["valid"] is False
    assert any(
        item.get("error") == "invalid_summary_trade_count"
        and item.get("field") == "summary.trade_count"
        and item.get("value") == "1"
        for item in integrity["invalid_artifacts"]
    )
    assert "summary_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"



def test_live_readiness_gate_rejects_unknown_summary_cost_breakdown_fields(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    summary_payload = json.loads((chunk / "summary.json").read_text(encoding="utf-8"))
    summary_payload["summary"]["cost_breakdown"]["rebates"] = 0.0
    (chunk / "summary.json").write_text(json.dumps(summary_payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    integrity = report["summary_artifact_integrity"]
    assert integrity["valid"] is False
    assert any(
        item.get("error") == "unknown_cost_breakdown_field"
        and item.get("field") == "summary.cost_breakdown.rebates"
        for item in integrity["invalid_artifacts"]
    )
    assert "summary_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"



def test_live_readiness_gate_rejects_non_string_exit_reasons(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["simulated_exit_reason"] = 123
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    exit_integrity = report["trade_exit_reason_integrity"]
    assert exit_integrity["valid"] is False
    assert any(
        item.get("error") == "exit_reason_not_string"
        and item.get("field") == "simulated_exit_reason"
        and item.get("value") == 123
        for item in exit_integrity["invalid_fields"]
    )
    assert "trade_exit_reason_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"



def test_live_readiness_exit_path_reconciliation_reports_non_string_source_trade_ids(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["trade_id"] = 12345
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    reconciliation = report["exit_path_replay"]["reconciliation"]
    assert reconciliation["matched"] is False
    assert reconciliation["invalid_source_trade_ids"] == [
        {"chunk": "chunk_001", "index": 1, "trade_id": 12345, "error": "trade_id_not_string"}
    ]
    assert "exit_path_replay_source_trade_id_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"



def test_live_readiness_gate_rejects_runtime_safety_unknown_check_fields(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    runtime_payload = {
        "schema_version": "runtime_safety_gate_input.v1",
        "evidence_source": {"type": "exchange_export", "run_id": "runtime-unknown-check"},
        "checks": {
            "kill_switch_dry_run_met": True,
            "order_position_reconciliation_met": True,
            "fail_closed_met": True,
            "dust_before_scale_met": True,
            "live_trade_ledger_met": True,
            "runtime_explainability_met": True,
            "drift_guard_met": True,
            "unreviewed_manual_override_met": True,
        },
    }
    (chunk / "runtime_safety_gate.json").write_text(json.dumps(runtime_payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path, require_runtime_safety_evidence=True)

    runtime_gate = report["runtime_safety_gate"]
    assert runtime_gate["checks"]["runtime_safety_artifact_schema_valid"] is False
    assert runtime_gate["artifacts"][0]["parse_error"] == "unknown_check_field: unreviewed_manual_override_met"
    assert "runtime_safety_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"



def test_live_readiness_gate_rejects_microstructure_unknown_check_fields(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    microstructure_payload = {
        "schema_version": "market_microstructure_gate_input.v1",
        "evidence_source": {"type": "exchange_export", "run_id": "microstructure-unknown-check"},
        "checks": {
            "l2_tick_coverage_met": True,
            "depth_driven_taker_met": True,
            "unreviewed_liquidity_proxy_met": True,
        },
    }
    (chunk / "market_microstructure_gate.json").write_text(json.dumps(microstructure_payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path, require_microstructure_evidence=True)

    microstructure_gate = report["microstructure_gate"]
    assert microstructure_gate["checks"]["microstructure_artifact_schema_valid"] is False
    assert microstructure_gate["artifacts"][0]["parse_error"] == "unknown_check_field: unreviewed_liquidity_proxy_met"
    assert "microstructure_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"



def test_live_readiness_gate_sorts_chunk_names_naturally(tmp_path: Path) -> None:
    for name in ("chunk_10", "chunk_2", "chunk_1"):
        chunk = tmp_path / name
        _write_profitable_trade_chunk(chunk)
        payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
        trade = payload["trades"][0]
        suffix = int(name.split("_")[1])
        trade["trade_id"] = f"trade-{suffix:03d}"
        trade["entry_time"] = f"2026-03-10T00:{suffix:02d}:00Z"
        trade["exit_time"] = f"2026-03-10T00:{suffix + 1:02d}:00Z"
        (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert [row["chunk"] for row in report["chunk_performance"]] == ["chunk_1", "chunk_2", "chunk_10"]


def test_live_readiness_gate_rejects_invalid_json_trades_artifact(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir(parents=True)
    (chunk / "trades.json").write_text('{"schema_version": "trades.v1", "trades": [', encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trades_artifact_integrity"]["valid"] is False
    assert report["trades_artifact_integrity"]["invalid_artifacts"] == [
        {
            "chunk": "chunk_001",
            "artifact": "trades.json",
            "schema_version": None,
            "error": "invalid_or_missing_schema_version",
        },
        {
            "chunk": "chunk_001",
            "artifact": "trades.json",
            "schema_version": None,
            "error": "invalid_json",
        },
    ]
    assert "trades_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_reports_non_object_trades_artifact_payload(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir(parents=True)
    (chunk / "trades.json").write_text(json.dumps([{"trade_id": "not-an-object-payload"}]), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trades_artifact_integrity"]["valid"] is False
    assert report["trades_artifact_integrity"]["invalid_artifacts"] == [
        {
            "chunk": "chunk_001",
            "artifact": "trades.json",
            "schema_version": None,
            "error": "invalid_or_missing_schema_version",
        },
        {
            "chunk": "chunk_001",
            "artifact": "trades.json",
            "schema_version": None,
            "error": "json_payload_not_object",
        },
    ]
    assert "trades_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_missing_summary_artifact_for_trade_chunk(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "summary.json").unlink()

    report = build_live_readiness_gate_report(tmp_path)

    assert report["summary_artifact_integrity"]["valid"] is False
    assert report["summary_artifact_integrity"]["invalid_artifacts"] == [
        {"chunk": "chunk_001", "artifact": "summary.json", "error": "missing_artifact"}
    ]
    assert "summary_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["summary_artifact_integrity_valid"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_summary_artifact_with_bad_schema_version(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "summary.json").write_text(
        json.dumps({"schema_version": "backtest_summary.v0", "summary": {"trade_count": 1}}),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    assert report["summary_artifact_integrity"]["valid"] is False
    assert report["summary_artifact_integrity"]["invalid_artifacts"] == [
        {
            "chunk": "chunk_001",
            "artifact": "summary.json",
            "schema_version": "backtest_summary.v0",
            "expected_schema_version": "backtest_summary.v1",
            "error": "invalid_or_missing_schema_version",
        }
    ]
    assert "summary_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["summary_artifact_integrity_valid"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_invalid_json_summary_artifact(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "summary.json").write_text('{"summary": {"cost_breakdown": ', encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["summary_artifact_integrity"]["valid"] is False
    assert report["summary_artifact_integrity"]["invalid_artifacts"] == [
        {
            "chunk": "chunk_001",
            "artifact": "summary.json",
            "error": "invalid_json",
        }
    ]
    assert "summary_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_non_finite_summary_cost_breakdown(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "summary.json").write_text(
        json.dumps(
            {
                "schema_version": "backtest_summary.v1",
                "summary": {"trade_count": 1, "cost_breakdown": {"fees": "NaN", "slippage": 0.0, "funding": 0.0}},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    assert report["summary_artifact_integrity"]["valid"] is False
    assert report["summary_artifact_integrity"]["invalid_artifacts"] == [
        {
            "chunk": "chunk_001",
            "artifact": "summary.json",
            "field": "summary.cost_breakdown.fees",
            "value": "NaN",
            "error": "invalid_cost_breakdown_value",
        }
    ]
    assert "summary_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_summary_totals_inconsistent_with_trades_ledger(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "summary.json").write_text(
        json.dumps(
            {
                "schema_version": "backtest_summary.v1",
                "summary": {"trade_count": 2, "cost_breakdown": {"fees": 1.0, "slippage": 0.0, "funding": 0.0}},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    assert report["summary_artifact_integrity"]["valid"] is False
    assert report["summary_artifact_integrity"]["invalid_artifacts"] == [
        {
            "chunk": "chunk_001",
            "artifact": "summary.json",
            "field": "summary.trade_count",
            "value": 2,
            "expected": 1,
            "error": "summary_trade_count_mismatch",
        },
        {
            "chunk": "chunk_001",
            "artifact": "summary.json",
            "field": "summary.cost_breakdown.fees",
            "value": 1.0,
            "expected": 0.0,
            "error": "summary_cost_breakdown_mismatch",
        },
    ]
    assert "summary_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_missing_trades_artifact_in_chunk_dir(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir(parents=True)
    (chunk / "summary.json").write_text(json.dumps({"schema_version": "backtest_summary.v1"}), encoding="utf-8")

    report = build_live_readiness_gate_report(
        tmp_path,
        evidence_coverage_threshold=0.0,
        exit_evidence_coverage_threshold=0.0,
        max_exit_path_ambiguity_rate=1.0,
    )

    assert report["trades_artifact_integrity"]["valid"] is False
    assert report["trades_artifact_integrity"]["invalid_artifacts"] == [
        {
            "chunk": "chunk_001",
            "artifact": "trades.json",
            "schema_version": None,
            "error": "missing_artifact",
        }
    ]
    assert "trades_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_trades_payload_that_is_not_a_list(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"] = {"trade_id": "not-a-list"}
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trades_artifact_integrity"]["valid"] is False
    assert report["trades_artifact_integrity"]["invalid_artifacts"] == [
        {
            "chunk": "chunk_001",
            "artifact": "trades.json",
            "schema_version": "trades.v1",
            "error": "trades_rows_not_list",
        }
    ]
    assert "trades_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_non_object_trade_rows(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"] = [payload["trades"][0], "not-an-object"]
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trades_artifact_integrity"]["valid"] is False
    assert report["trades_artifact_integrity"]["invalid_artifacts"] == [
        {
            "chunk": "chunk_001",
            "artifact": "trades.json",
            "schema_version": "trades.v1",
            "index": 2,
            "error": "trade_row_not_object",
        }
    ]
    assert "trades_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_missing_trades_schema_version(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    del payload["schema_version"]
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trades_artifact_integrity"]["valid"] is False
    assert report["trades_artifact_integrity"]["invalid_artifacts"] == [
        {
            "chunk": "chunk_001",
            "artifact": "trades.json",
            "schema_version": None,
            "error": "invalid_or_missing_schema_version",
        }
    ]
    assert "trades_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_invalid_trades_schema_version(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["schema_version"] = "trades.v0"
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trades_artifact_integrity"]["valid"] is False
    assert report["trades_artifact_integrity"]["invalid_artifacts"] == [
        {
            "chunk": "chunk_001",
            "artifact": "trades.json",
            "schema_version": "trades.v0",
            "error": "invalid_or_missing_schema_version",
        }
    ]
    assert "trades_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_non_integral_passive_calibration_attempts(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "passive_order_calibration_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "passive_order_calibration_summary.v1",
                "evidence_source": {"type": "exchange_export", "run_id": "calibration-1"},
                "overall": {"attempt_count": 1.5, "fill_rate": 0.5},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path, require_passive_calibration=True)

    passive = report["passive_calibration"]
    assert passive["checks"]["passive_calibration_artifact_schema_valid"] is False
    assert passive["chunks"][0]["parse_error"] == "invalid_numeric_field: attempt_count"
    assert passive["chunks"][0]["attempt_count"] == 0
    assert report["passive_calibration"]["attempt_count"] == 0
    assert "passive_calibration_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_missing_non_finite_and_non_positive_trade_sizes(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    trade = payload["trades"][0]
    del trade["quantity"]
    trade["notional"] = "NaN"
    payload["trades"].append({**trade, "trade_id": "trade-002", "quantity": -1.0, "notional": 0.0})
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_size_integrity"]["valid"] is False
    assert report["trade_size_integrity"]["invalid_fields"] == [
        {"chunk": "chunk_001", "index": 1, "field": "quantity", "value": None, "error": "missing_size"},
        {"chunk": "chunk_001", "index": 1, "field": "notional", "value": "NaN", "error": "invalid_size"},
        {"chunk": "chunk_001", "index": 2, "field": "quantity", "value": -1.0, "error": "non_positive_size"},
        {"chunk": "chunk_001", "index": 2, "field": "notional", "value": 0.0, "error": "non_positive_size"},
    ]
    assert "trade_size_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_trade_pnl_mismatch(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["net_pnl"] = 999.0
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_pnl_consistency"]["valid"] is False
    assert report["trade_pnl_consistency"]["invalid_fields"] == [
        {
            "chunk": "chunk_001",
            "index": 1,
            "field": "net_pnl",
            "value": 999.0,
            "expected": 1.5,
            "error": "net_pnl_mismatch",
        }
    ]
    assert "trade_pnl_inconsistent" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_side_price_gross_pnl_mismatch(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["gross_pnl"] = -999.0
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_side_price_pnl_consistency"]["valid"] is False
    assert report["trade_side_price_pnl_consistency"]["invalid_fields"] == [
        {
            "chunk": "chunk_001",
            "index": 1,
            "field": "gross_pnl",
            "value": -999.0,
            "expected": 1.5,
            "error": "side_price_pnl_mismatch",
        }
    ]
    assert "trade_side_price_pnl_inconsistent" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_blank_and_noncanonical_trade_dimensions(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    base_trade = payload["trades"][0]
    missing_symbol = {**base_trade, "trade_id": "trade-missing-symbol", "symbol": "   "}
    non_string_symbol = {**base_trade, "trade_id": "trade-non-string-symbol", "symbol": 12345}
    invalid_symbol = {**base_trade, "trade_id": "trade-invalid-symbol", "symbol": "btc/usdt"}
    invalid_setup = {**base_trade, "trade_id": "trade-invalid-setup", "setup_type": "breakout continuation"}
    invalid_side = {**base_trade, "trade_id": "trade-invalid-side", "side": "hold"}
    payload["trades"] = [missing_symbol, non_string_symbol, invalid_symbol, invalid_setup, invalid_side]
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_dimension_integrity"]["valid"] is False
    assert report["trade_dimension_integrity"]["invalid_fields"] == [
        {"chunk": "chunk_001", "index": 1, "field": "symbol", "value": "   ", "error": "missing_or_blank_dimension"},
        {"chunk": "chunk_001", "index": 2, "field": "symbol", "value": 12345, "error": "dimension_not_string"},
        {"chunk": "chunk_001", "index": 3, "field": "symbol", "value": "btc/usdt", "error": "invalid_symbol"},
        {
            "chunk": "chunk_001",
            "index": 4,
            "field": "setup_type",
            "value": "breakout continuation",
            "error": "invalid_setup_type",
        },
        {"chunk": "chunk_001", "index": 5, "field": "side", "value": "hold", "error": "invalid_side"},
    ]
    assert "trade_dimension_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_invalid_missing_and_non_positive_trade_times(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    base_trade = payload["trades"][0]
    missing_entry = {**base_trade, "trade_id": "trade-missing-entry-time"}
    missing_entry.pop("entry_time", None)
    invalid_exit = {**base_trade, "trade_id": "trade-invalid-exit-time", "exit_time": "not-a-time"}
    exit_before_entry = {
        **base_trade,
        "trade_id": "trade-exit-before-entry",
        "entry_time": "2026-01-01T00:10:00Z",
        "exit_time": "2026-01-01T00:00:00Z",
    }
    zero_duration = {
        **base_trade,
        "trade_id": "trade-zero-duration",
        "entry_time": "2026-01-01T00:00:00Z",
        "exit_time": "2026-01-01T00:00:00Z",
    }
    payload["trades"] = [missing_entry, invalid_exit, exit_before_entry, zero_duration]
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_time_integrity"]["valid"] is False
    assert report["trade_time_integrity"]["invalid_fields"] == [
        {
            "chunk": "chunk_001",
            "index": 1,
            "field": "entry_time",
            "value": None,
            "error": "missing_or_invalid_timestamp",
        },
        {
            "chunk": "chunk_001",
            "index": 2,
            "field": "exit_time",
            "value": "not-a-time",
            "error": "missing_or_invalid_timestamp",
        },
        {
            "chunk": "chunk_001",
            "index": 3,
            "field": "exit_time",
            "value": "2026-01-01T00:00:00Z",
            "entry_time": "2026-01-01T00:10:00Z",
            "error": "exit_before_entry",
        },
        {
            "chunk": "chunk_001",
            "index": 4,
            "field": "exit_time",
            "value": "2026-01-01T00:00:00Z",
            "entry_time": "2026-01-01T00:00:00Z",
            "error": "non_positive_duration",
        },
    ]
    assert "trade_time_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_missing_invalid_and_conflicting_exit_reasons(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    base_trade = payload["trades"][0]
    missing_reason = {**base_trade, "trade_id": "trade-missing-exit-reason"}
    missing_reason.pop("exit_reason", None)
    missing_reason.pop("simulated_exit_reason", None)
    invalid_reason = {**base_trade, "trade_id": "trade-invalid-exit-reason", "exit_reason": "moonshot"}
    invalid_reason.pop("simulated_exit_reason", None)
    conflicting_reason = {
        **base_trade,
        "trade_id": "trade-conflicting-exit-reason",
        "exit_reason": "take_profit",
        "simulated_exit_reason": "stop_loss",
    }
    payload["trades"] = [missing_reason, invalid_reason, conflicting_reason]
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_exit_reason_integrity"]["valid"] is False
    assert report["trade_exit_reason_integrity"]["invalid_fields"] == [
        {"chunk": "chunk_001", "index": 1, "field": "exit_reason", "value": None, "error": "missing_exit_reason"},
        {"chunk": "chunk_001", "index": 2, "field": "exit_reason", "value": "moonshot", "error": "invalid_exit_reason"},
        {
            "chunk": "chunk_001",
            "index": 3,
            "field": "exit_reason",
            "value": "take_profit",
            "simulated_exit_reason": "stop_loss",
            "error": "conflicting_exit_reasons",
        },
    ]
    assert "trade_exit_reason_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_non_finite_trade_financial_metrics(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["net_pnl"] = "NaN"
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_financial_integrity"]["valid"] is False
    assert report["trade_financial_integrity"]["invalid_fields"] == [
        {"chunk": "chunk_001", "index": 1, "field": "net_pnl", "value": "NaN", "error": "invalid_financial_field"}
    ]
    assert "trade_financial_metric_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_missing_required_trade_financial_metrics(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    del payload["trades"][0]["fee_paid"]
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_financial_integrity"]["valid"] is False
    assert report["trade_financial_integrity"]["invalid_fields"] == [
        {"chunk": "chunk_001", "index": 1, "field": "fee_paid", "value": None, "error": "missing_required_field"}
    ]
    assert "trade_financial_metric_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_duplicate_trade_ids(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    first = payload["trades"][0]
    first["trade_id"] = "duplicate-id"
    payload["trades"].append({**first, "net_pnl": 50.0, "gross_pnl": 60.0})
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_identity_integrity"]["valid"] is False
    assert report["trade_identity_integrity"]["duplicate_trade_ids"] == [
        {"trade_id": "duplicate-id", "occurrences": [{"chunk": "chunk_001", "index": 1}, {"chunk": "chunk_001", "index": 2}]}
    ]
    assert "trade_identity_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_malformed_trade_ids(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["trade_id"] = "trade 001"
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_identity_integrity"]["valid"] is False
    assert report["trade_identity_integrity"]["invalid_trade_ids"] == [
        {
            "chunk": "chunk_001",
            "index": 1,
            "trade_id": "trade 001",
            "error": "invalid_trade_id",
        }
    ]
    assert report["trade_identity_integrity"]["invalid_trade_id_count"] == 1
    assert "trade_identity_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_missing_trade_dimension_fields(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["symbol"] = "   "
    del payload["trades"][0]["setup_type"]
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_dimension_integrity"]["valid"] is False
    assert report["trade_dimension_integrity"]["invalid_fields"] == [
        {"chunk": "chunk_001", "index": 1, "field": "symbol", "value": "   ", "error": "missing_or_blank_dimension"},
        {"chunk": "chunk_001", "index": 1, "field": "setup_type", "value": None, "error": "missing_or_blank_dimension"},
    ]
    assert "trade_dimension_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_noncanonical_trade_symbol(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["symbol"] = "btcusdt"
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_dimension_integrity"]["valid"] is False
    assert report["trade_dimension_integrity"]["invalid_fields"] == [
        {
            "chunk": "chunk_001",
            "index": 1,
            "field": "symbol",
            "value": "btcusdt",
            "error": "invalid_symbol",
        }
    ]
    assert "trade_dimension_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_noncanonical_setup_type(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["setup_type"] = "breakout continuation"
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_dimension_integrity"]["valid"] is False
    assert report["trade_dimension_integrity"]["invalid_fields"] == [
        {
            "chunk": "chunk_001",
            "index": 1,
            "field": "setup_type",
            "value": "breakout continuation",
            "error": "invalid_setup_type",
        }
    ]
    assert "trade_dimension_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_invalid_trade_side(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["side"] = "hold"
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_dimension_integrity"]["valid"] is False
    assert report["trade_dimension_integrity"]["invalid_fields"] == [
        {"chunk": "chunk_001", "index": 1, "field": "side", "value": "hold", "error": "invalid_side"}
    ]
    assert "trade_dimension_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_invalid_trade_timestamps(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["entry_time"] = "2026-03-10T00:10:00Z"
    payload["trades"][0]["exit_time"] = "2026-03-10T00:05:00Z"
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_time_integrity"]["valid"] is False
    assert report["trade_time_integrity"]["invalid_fields"] == [
        {
            "chunk": "chunk_001",
            "index": 1,
            "field": "exit_time",
            "value": "2026-03-10T00:05:00Z",
            "entry_time": "2026-03-10T00:10:00Z",
            "error": "exit_before_entry",
        }
    ]
    assert "trade_time_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_zero_duration_trades(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["exit_time"] = "2026-03-10T00:00:00Z"
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_time_integrity"]["valid"] is False
    assert report["trade_time_integrity"]["invalid_fields"] == [
        {
            "chunk": "chunk_001",
            "index": 1,
            "field": "exit_time",
            "value": "2026-03-10T00:00:00Z",
            "entry_time": "2026-03-10T00:00:00Z",
            "error": "non_positive_duration",
        }
    ]
    assert "trade_time_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_missing_non_finite_and_non_positive_trade_prices(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    trade = payload["trades"][0]
    del trade["entry_price"]
    trade["exit_price"] = "NaN"
    payload["trades"].append({**trade, "trade_id": "trade-002", "entry_price": -1.0, "exit_price": 0.0})
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_price_integrity"]["valid"] is False
    assert report["trade_price_integrity"]["invalid_fields"] == [
        {"chunk": "chunk_001", "index": 1, "field": "entry_price", "value": None, "error": "missing_price"},
        {"chunk": "chunk_001", "index": 1, "field": "exit_price", "value": "NaN", "error": "invalid_price"},
        {"chunk": "chunk_001", "index": 2, "field": "entry_price", "value": -1.0, "error": "non_positive_price"},
        {"chunk": "chunk_001", "index": 2, "field": "exit_price", "value": 0.0, "error": "non_positive_price"},
    ]
    assert "trade_price_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_invalid_trade_size_fields(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["quantity"] = -1.0
    del payload["trades"][0]["notional"]
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_size_integrity"]["valid"] is False
    assert report["trade_size_integrity"]["invalid_fields"] == [
        {"chunk": "chunk_001", "index": 1, "field": "quantity", "value": -1.0, "error": "non_positive_size"},
        {"chunk": "chunk_001", "index": 1, "field": "notional", "value": None, "error": "missing_size"},
    ]
    assert "trade_size_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_invalid_exit_reason(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["simulated_exit_reason"] = "moonshot"
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_exit_reason_integrity"]["valid"] is False
    assert report["trade_exit_reason_integrity"]["invalid_fields"] == [
        {
            "chunk": "chunk_001",
            "index": 1,
            "field": "simulated_exit_reason",
            "value": "moonshot",
            "error": "invalid_exit_reason",
        }
    ]
    assert "trade_exit_reason_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_conflicting_exit_reasons(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    trade = payload["trades"][0]
    trade["exit_reason"] = "fixed_horizon"
    trade["simulated_exit_reason"] = "take_profit"
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_exit_reason_integrity"]["valid"] is False
    assert report["trade_exit_reason_integrity"]["invalid_fields"] == [
        {
            "chunk": "chunk_001",
            "index": 1,
            "field": "exit_reason",
            "value": "fixed_horizon",
            "simulated_exit_reason": "take_profit",
            "error": "conflicting_exit_reasons",
        }
    ]
    assert "trade_exit_reason_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_inconsistent_trade_notional(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["entry_price"] = 100.0
    payload["trades"][0]["quantity"] = 2.0
    payload["trades"][0]["notional"] = 100.0
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_notional_consistency"]["valid"] is False
    assert report["trade_notional_consistency"]["invalid_fields"] == [
        {
            "chunk": "chunk_001",
            "index": 1,
            "field": "notional",
            "value": 100.0,
            "expected": 200.0,
            "error": "notional_mismatch",
        }
    ]
    assert "trade_notional_inconsistent" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"



def test_live_readiness_gate_rejects_missing_execution_cost_fields(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    del payload["trades"][0]["fee_paid"]
    del payload["trades"][0]["slippage_paid"]
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_cost_sign_integrity"]["valid"] is False
    assert report["trade_cost_sign_integrity"]["invalid_fields"] == [
        {"chunk": "chunk_001", "index": 1, "field": "fee_paid", "value": None, "error": "missing_execution_cost"},
        {"chunk": "chunk_001", "index": 1, "field": "slippage_paid", "value": None, "error": "missing_execution_cost"},
    ]
    assert "trade_cost_sign_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_non_finite_execution_cost_fields(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["fee_paid"] = "NaN"
    payload["trades"][0]["slippage_paid"] = "Infinity"
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_cost_sign_integrity"]["valid"] is False
    assert report["trade_cost_sign_integrity"]["invalid_fields"] == [
        {"chunk": "chunk_001", "index": 1, "field": "fee_paid", "value": "NaN", "error": "invalid_execution_cost"},
        {"chunk": "chunk_001", "index": 1, "field": "slippage_paid", "value": "Infinity", "error": "invalid_execution_cost"},
    ]
    assert "trade_cost_sign_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_negative_execution_costs(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    trade = payload["trades"][0]
    trade["fee_paid"] = -0.01
    trade["net_pnl"] = 1.51
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_cost_sign_integrity"]["valid"] is False
    assert report["trade_cost_sign_integrity"]["invalid_fields"] == [
        {"chunk": "chunk_001", "index": 1, "field": "fee_paid", "value": -0.01, "error": "negative_execution_cost"}
    ]
    assert "trade_cost_sign_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_inconsistent_trade_pnl_arithmetic(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["gross_pnl"] = 10.0
    payload["trades"][0]["fee_paid"] = 1.0
    payload["trades"][0]["slippage_paid"] = 2.0
    payload["trades"][0]["funding_paid"] = 3.0
    payload["trades"][0]["net_pnl"] = 10.0
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_pnl_consistency"]["valid"] is False
    assert report["trade_pnl_consistency"]["invalid_fields"] == [
        {
            "chunk": "chunk_001",
            "index": 1,
            "field": "net_pnl",
            "value": 10.0,
            "expected": 4.0,
            "error": "net_pnl_mismatch",
        }
    ]
    assert "trade_pnl_inconsistent" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_side_price_pnl_mismatch(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    payload = json.loads((chunk / "trades.json").read_text(encoding="utf-8"))
    payload["trades"][0]["side"] = "long"
    payload["trades"][0]["entry_price"] = 100.0
    payload["trades"][0]["exit_price"] = 99.0
    payload["trades"][0]["quantity"] = 1.0
    payload["trades"][0]["notional"] = 100.0
    payload["trades"][0]["gross_pnl"] = 1.0
    payload["trades"][0]["fee_paid"] = 0.0
    payload["trades"][0]["slippage_paid"] = 0.0
    payload["trades"][0]["funding_paid"] = 0.0
    payload["trades"][0]["net_pnl"] = 1.0
    (chunk / "trades.json").write_text(json.dumps(payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_side_price_pnl_consistency"]["valid"] is False
    assert report["trade_side_price_pnl_consistency"]["invalid_fields"] == [
        {
            "chunk": "chunk_001",
            "index": 1,
            "field": "gross_pnl",
            "value": 1.0,
            "expected": -1.0,
            "error": "side_price_pnl_mismatch",
        }
    ]
    assert "trade_side_price_pnl_inconsistent" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_duplicate_trade_ids_across_chunks(tmp_path: Path) -> None:
    first_chunk = tmp_path / "chunk_001"
    second_chunk = tmp_path / "chunk_002"
    _write_profitable_trade_chunk(first_chunk)
    _write_profitable_trade_chunk(second_chunk)

    first_payload = json.loads((first_chunk / "trades.json").read_text(encoding="utf-8"))
    second_payload = json.loads((second_chunk / "trades.json").read_text(encoding="utf-8"))
    second_payload["trades"][0]["trade_id"] = first_payload["trades"][0]["trade_id"]
    second_payload["trades"][0]["entry_time"] = "2026-03-10T00:10:00Z"
    second_payload["trades"][0]["exit_time"] = "2026-03-10T00:15:00Z"
    (second_chunk / "trades.json").write_text(json.dumps(second_payload), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    assert report["trade_identity_integrity"]["valid"] is False
    assert report["trade_identity_integrity"]["duplicate_trade_id_count"] == 1
    assert report["trade_identity_integrity"]["duplicate_trade_ids"] == [
        {
            "trade_id": first_payload["trades"][0]["trade_id"],
            "occurrences": [
                {"chunk": "chunk_001", "index": 1},
                {"chunk": "chunk_002", "index": 1},
            ],
        }
    ]
    assert "trade_identity_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_negative_passive_calibration_attempts(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 1.5,
                        "gross_pnl": 1.5,
                        "fee_paid": 0.0,
                        "slippage_paid": 0.0,
                        "funding_paid": 0.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "take_profit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (chunk / "passive_order_calibration_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "passive_order_calibration_summary.v1",
                "evidence_source": {"type": "exchange_export", "run_id": "calibration-1"},
                "overall": {"attempt_count": -1, "fill_rate": 0.5},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path, require_passive_calibration=True)

    passive = report["passive_calibration"]
    assert passive["checks"]["passive_calibration_artifact_schema_valid"] is False
    assert passive["chunks"][0]["parse_error"] == "invalid_numeric_field: attempt_count"
    assert "passive_calibration_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_out_of_range_passive_calibration_fill_rate(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 1.5,
                        "gross_pnl": 1.5,
                        "fee_paid": 0.0,
                        "slippage_paid": 0.0,
                        "funding_paid": 0.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "take_profit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (chunk / "passive_order_calibration_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "passive_order_calibration_summary.v1",
                "evidence_source": {"type": "exchange_export", "run_id": "calibration-1"},
                "overall": {"attempt_count": 10, "fill_rate": 1.5},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path, require_passive_calibration=True)

    passive = report["passive_calibration"]
    assert passive["checks"]["passive_calibration_artifact_schema_valid"] is False
    assert passive["chunks"][0]["parse_error"] == "invalid_numeric_field: fill_rate"
    assert "passive_calibration_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_excludes_invalid_passive_calibration_chunks_from_aggregates(tmp_path: Path) -> None:
    invalid_chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(invalid_chunk)
    (invalid_chunk / "passive_order_calibration_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "passive_order_calibration_summary.v1",
                "evidence_source": {"type": "exchange_export", "run_id": "calibration-bad"},
                "overall": {"attempt_count": 1000, "fill_rate": 1.5},
            }
        ),
        encoding="utf-8",
    )
    valid_chunk = tmp_path / "chunk_002"
    _write_profitable_trade_chunk(valid_chunk)
    (valid_chunk / "passive_order_calibration_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "passive_order_calibration_summary.v1",
                "evidence_source": {"type": "exchange_export", "run_id": "calibration-good"},
                "overall": {"attempt_count": 10, "fill_rate": 0.4},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(
        tmp_path,
        require_passive_calibration=True,
        min_passive_calibration_attempts=1,
        min_passive_fill_rate=0.5,
    )

    passive = report["passive_calibration"]
    assert passive["chunks"][0]["parse_error"] == "invalid_numeric_field: fill_rate"
    assert passive["attempt_count"] == 10
    assert passive["fill_rate"] == 0.4
    assert passive["checks"]["passive_calibration_fill_rate_met"] is False
    assert "passive_calibration_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert "passive_calibration_fill_rate_below_threshold" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_report_rejects_missing_runtime_safety_evidence(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 1.5,
                        "gross_pnl": 1.5,
                        "fee_paid": 0.0,
                        "slippage_paid": 0.0,
                        "funding_paid": 0.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "take_profit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path, require_runtime_safety_evidence=True)

    gate = report["runtime_safety_gate"]
    assert gate["schema_version"] == "runtime_safety_gate.v1"
    assert gate["required"] is True
    assert gate["artifact_count"] == 0
    for key in (
        "kill_switch_dry_run_met",
        "order_position_reconciliation_met",
        "fail_closed_met",
        "dust_before_scale_met",
        "live_trade_ledger_met",
        "runtime_explainability_met",
        "drift_guard_met",
    ):
        assert gate["checks"][key] is False
    reasons = report["promotion_gate"]["reasons"]
    assert "runtime_safety_evidence_missing" in reasons
    assert "kill_switch_dry_run_missing" in reasons
    assert "order_position_reconciliation_missing" in reasons
    assert "runtime_fail_closed_missing" in reasons
    assert "live_dust_before_scale_missing" in reasons
    assert "live_trade_ledger_missing" in reasons
    assert "runtime_explainability_missing" in reasons
    assert "drift_guard_missing" in reasons
    markdown = render_live_readiness_markdown(report)
    assert "## Runtime Safety Gate" in markdown
    assert "kill_switch_dry_run_met: false" in markdown
    assert "drift_guard_met: false" in markdown


def test_live_readiness_gate_report_accepts_runtime_safety_evidence_artifact(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 1.5,
                        "gross_pnl": 1.5,
                        "fee_paid": 0.0,
                        "slippage_paid": 0.0,
                        "funding_paid": 0.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "take_profit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (chunk / "runtime_safety_gate.json").write_text(
        json.dumps(
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
                },
                "summary": {"ledger_rows": 1, "max_drift_bps": 3.0},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path, require_runtime_safety_evidence=True)

    gate = report["runtime_safety_gate"]
    assert gate["artifact_count"] == 1
    assert all(gate["checks"].values())
    assert "runtime_safety_evidence_missing" not in report["promotion_gate"]["reasons"]
    assert "drift_guard_missing" not in report["promotion_gate"]["reasons"]

def test_live_readiness_gate_report_rejects_missing_microstructure_evidence(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 1.5,
                        "gross_pnl": 1.5,
                        "fee_paid": 0.0,
                        "slippage_paid": 0.0,
                        "funding_paid": 0.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "take_profit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path, require_microstructure_evidence=True)

    gate = report["microstructure_gate"]
    assert gate["schema_version"] == "microstructure_gate.v1"
    assert gate["required"] is True
    assert gate["artifact_count"] == 0
    assert gate["checks"]["l2_tick_coverage_met"] is False
    assert gate["checks"]["depth_driven_taker_met"] is False
    assert "microstructure_evidence_missing" in report["promotion_gate"]["reasons"]
    assert "l2_tick_coverage_below_threshold" in report["promotion_gate"]["reasons"]
    assert "taker_depth_driven_missing" in report["promotion_gate"]["reasons"]
    markdown = render_live_readiness_markdown(report)
    assert "## Microstructure Gate" in markdown
    assert "l2_tick_coverage_met: false" in markdown
    assert "depth_driven_taker_met: false" in markdown


def test_live_readiness_gate_report_rejects_invalid_producer_artifact_schema_and_provenance(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 1.5,
                        "gross_pnl": 1.5,
                        "fee_paid": 0.0,
                        "slippage_paid": 0.0,
                        "funding_paid": 0.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "take_profit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (chunk / "market_microstructure_gate.json").write_text(
        json.dumps(
            {
                "schema_version": "unexpected_microstructure.v0",
                "checks": {"l2_tick_coverage_met": True, "depth_driven_taker_met": True},
                "summary": {"min_l2_tick_coverage": 0.995},
            }
        ),
        encoding="utf-8",
    )
    (chunk / "validation_gate.json").write_text(
        json.dumps(
            {
                "schema_version": "unexpected_validation.v0",
                "checks": {
                    "oos_non_degraded_met": True,
                    "multi_regime_met": True,
                    "cost_stress_positive_met": True,
                    "forward_contamination_absent_met": True,
                },
                "summary": {"oos_net_pnl": 50.0},
            }
        ),
        encoding="utf-8",
    )
    (chunk / "runtime_safety_gate.json").write_text(
        json.dumps(
            {
                "schema_version": "unexpected_runtime.v0",
                "checks": {
                    "kill_switch_dry_run_met": True,
                    "order_position_reconciliation_met": True,
                    "fail_closed_met": True,
                    "dust_before_scale_met": True,
                    "live_trade_ledger_met": True,
                    "runtime_explainability_met": True,
                    "drift_guard_met": True,
                },
                "summary": {"ledger_rows": 1},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(
        tmp_path,
        require_microstructure_evidence=True,
        require_validation_evidence=True,
        require_runtime_safety_evidence=True,
    )

    reasons = set(report["promotion_gate"]["reasons"])
    assert "microstructure_artifact_schema_invalid" in reasons
    assert "validation_artifact_schema_invalid" in reasons
    assert "runtime_safety_artifact_schema_invalid" in reasons
    assert "microstructure_artifact_provenance_missing" in reasons
    assert "validation_artifact_provenance_missing" in reasons
    assert "runtime_safety_artifact_provenance_missing" in reasons
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"
    assert report["microstructure_gate"]["checks"]["microstructure_artifact_schema_valid"] is False
    assert report["validation_gate"]["checks"]["validation_artifact_provenance_present"] is False
    assert report["runtime_safety_gate"]["checks"]["runtime_safety_artifact_schema_valid"] is False


def test_live_readiness_gate_report_accepts_microstructure_evidence_artifact(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 1.5,
                        "gross_pnl": 1.5,
                        "fee_paid": 0.0,
                        "slippage_paid": 0.0,
                        "funding_paid": 0.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "take_profit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (chunk / "market_microstructure_gate.json").write_text(
        json.dumps(
            {
                "schema_version": "market_microstructure_gate_input.v1",
                "checks": {"l2_tick_coverage_met": True, "depth_driven_taker_met": True},
                "summary": {"min_l2_tick_coverage": 0.995, "taker_fill_model": "orderbook_depth"},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path, require_microstructure_evidence=True)

    gate = report["microstructure_gate"]
    assert gate["artifact_count"] == 1
    assert gate["checks"]["l2_tick_coverage_met"] is True
    assert gate["checks"]["depth_driven_taker_met"] is True
    assert "microstructure_evidence_missing" not in report["promotion_gate"]["reasons"]
    assert "l2_tick_coverage_below_threshold" not in report["promotion_gate"]["reasons"]
    assert "taker_depth_driven_missing" not in report["promotion_gate"]["reasons"]

def test_live_readiness_gate_report_rejects_missing_validation_evidence(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 1.5,
                        "gross_pnl": 1.5,
                        "fee_paid": 0.0,
                        "slippage_paid": 0.0,
                        "funding_paid": 0.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "take_profit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path, require_validation_evidence=True)

    validation = report["validation_gate"]
    assert validation["schema_version"] == "validation_gate.v1"
    assert validation["required"] is True
    assert validation["artifact_count"] == 0
    assert validation["checks"]["oos_non_degraded_met"] is False
    assert validation["checks"]["multi_regime_met"] is False
    assert validation["checks"]["cost_stress_positive_met"] is False
    assert validation["checks"]["forward_contamination_absent_met"] is False
    assert "validation_evidence_missing" in report["promotion_gate"]["reasons"]
    assert "oos_degraded" in report["promotion_gate"]["reasons"]
    assert "regime_single_point_survivor" in report["promotion_gate"]["reasons"]
    assert "cost_stress_not_positive" in report["promotion_gate"]["reasons"]
    assert "forward_contamination_unproven" in report["promotion_gate"]["reasons"]
    markdown = render_live_readiness_markdown(report)
    assert "## Validation Gate" in markdown
    assert "schema_version: validation_gate.v1" in markdown
    assert "artifact_count: 0" in markdown


def test_live_readiness_gate_report_accepts_validation_evidence_artifact(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 1.5,
                        "gross_pnl": 1.5,
                        "fee_paid": 0.0,
                        "slippage_paid": 0.0,
                        "funding_paid": 0.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "take_profit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (chunk / "validation_gate.json").write_text(
        json.dumps(
            {
                "schema_version": "validation_gate_input.v1",
                "evidence_source": {"type": "walk_forward_oos_report", "run_id": "validation-1"},
                "checks": {
                    "oos_non_degraded_met": True,
                    "multi_regime_met": True,
                    "cost_stress_positive_met": True,
                    "forward_contamination_absent_met": True,
                },
                "summary": {"oos_net_pnl": 50.0, "worst_regime_net_pnl": 10.0, "double_cost_net_pnl": 25.0},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path, require_validation_evidence=True)

    validation = report["validation_gate"]
    assert validation["artifact_count"] == 1
    assert all(validation["checks"].values())
    assert "validation_evidence_missing" not in report["promotion_gate"]["reasons"]
    assert "oos_degraded" not in report["promotion_gate"]["reasons"]
    assert "regime_single_point_survivor" not in report["promotion_gate"]["reasons"]
    assert "cost_stress_not_positive" not in report["promotion_gate"]["reasons"]
    assert "forward_contamination_unproven" not in report["promotion_gate"]["reasons"]


def test_live_readiness_gate_report_rejects_under_sampled_and_banned_setups(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    trades = []
    for index in range(3):
        trades.append(
            {
                "symbol": "BTCUSDT",
                "side": "long",
                "setup_type": "RS_REACCELERATION",
                "net_pnl": 25.0,
                "gross_pnl": 30.0,
                "fee_paid": 2.0,
                "slippage_paid": 3.0,
                "fill_quality": "evidence_backed",
                "execution_price_source": "trade_print",
                "exit_fill_quality": "evidence_backed",
                "exit_price_source": "trade_print",
                "simulated_exit_reason": "take_profit",
            }
        )
    (chunk / "trades.json").write_text(json.dumps({"trades": trades}), encoding="utf-8")

    report = build_live_readiness_gate_report(
        tmp_path,
        min_setup_trade_count=5,
        banned_setup_types=["RS_REACCELERATION"],
    )

    setup_gate = report["setup_quality_gate"]
    assert setup_gate["schema_version"] == "setup_quality_gate.v1"
    assert setup_gate["min_setup_trade_count"] == 5
    assert setup_gate["under_sampled_setup_types"] == ["RS_REACCELERATION"]
    assert setup_gate["banned_setup_types_present"] == ["RS_REACCELERATION"]
    assert report["promotion_gate"]["checks"]["setup_min_sample_met"] is False
    assert report["promotion_gate"]["checks"]["banned_setup_types_absent"] is False
    assert "setup_min_sample_too_low" in report["promotion_gate"]["reasons"]
    assert "banned_setup_type_present" in report["promotion_gate"]["reasons"]
    markdown = render_live_readiness_markdown(report)
    assert "## Setup Quality Gate" in markdown
    assert "min_setup_trade_count: 5" in markdown
    assert "under_sampled_setup_types: RS_REACCELERATION" in markdown
    assert "banned_setup_types_present: RS_REACCELERATION" in markdown


def test_live_readiness_gate_report_rejects_concentrated_setup_and_symbol_buckets(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    trades = []
    for index in range(10):
        trades.append(
            {
                "symbol": "SOLUSDT" if index < 8 else "ETHUSDT",
                "side": "long",
                "setup_type": "RS_REACCELERATION" if index < 5 else "BREAKOUT_CONTINUATION",
                "net_pnl": 10.0,
                "gross_pnl": 12.0,
                "fee_paid": 1.0,
                "slippage_paid": 1.0,
                "fill_quality": "evidence_backed",
                "execution_price_source": "trade_print",
                "exit_fill_quality": "evidence_backed",
                "exit_price_source": "trade_print",
                "simulated_exit_reason": "stop_loss",
                "simulated_exit_price": 95.0,
            }
        )
    (chunk / "trades.json").write_text(json.dumps({"trades": trades}), encoding="utf-8")

    report = build_live_readiness_gate_report(
        tmp_path,
        max_setup_trade_share=0.45,
        max_symbol_trade_share=0.70,
    )

    assert report["concentration"]["top_setup_by_trades"]["key"] == "RS_REACCELERATION"
    assert report["concentration"]["top_setup_by_trades"]["trade_share"] == pytest.approx(0.5)
    assert report["concentration"]["top_symbol_by_trades"]["key"] == "SOLUSDT"
    assert report["concentration"]["top_symbol_by_trades"]["trade_share"] == pytest.approx(0.8)
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"
    assert "setup_concentration_too_high" in report["promotion_gate"]["reasons"]
    assert "symbol_concentration_too_high" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["setup_concentration_met"] is False
    assert report["promotion_gate"]["checks"]["symbol_concentration_met"] is False



def test_live_readiness_gate_report_rejects_missing_exit_path_rows(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    trades = [
        {
            "trade_id": "t1",
            "symbol": "BTCUSDT",
            "side": "long",
            "setup_type": "BREAKOUT_CONTINUATION",
            "net_pnl": 50.0,
            "gross_pnl": 60.0,
            "fee_paid": 5.0,
            "slippage_paid": 5.0,
            "fill_quality": "evidence_backed",
            "execution_price_source": "trade_print",
            "exit_fill_quality": "evidence_backed",
            "exit_price_source": "trade_print",
            "simulated_exit_reason": "fixed_horizon",
        },
        {
            "trade_id": "t2",
            "symbol": "ETHUSDT",
            "side": "long",
            "setup_type": "BREAKOUT_CONTINUATION",
            "net_pnl": 50.0,
            "gross_pnl": 60.0,
            "fee_paid": 5.0,
            "slippage_paid": 5.0,
            "fill_quality": "evidence_backed",
            "execution_price_source": "trade_print",
            "exit_fill_quality": "evidence_backed",
            "exit_price_source": "trade_print",
            "simulated_exit_reason": "fixed_horizon",
        },
    ]
    (chunk / "trades.json").write_text(json.dumps({"trades": trades}), encoding="utf-8")
    (chunk / "exit_path_replay.json").write_text(
        json.dumps({"trades": [{"trade_id": "t1", "path_classification": "trade_print_path_available"}]}),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path, require_exit_path_replay_rows=True)

    reconciliation = report["exit_path_replay"]["reconciliation"]
    assert reconciliation["schema_version"] == "exit_path_replay_reconciliation.v1"
    assert reconciliation["matched"] is False
    assert reconciliation["trade_count"] == 2
    assert reconciliation["path_trade_count"] == 1
    assert reconciliation["missing_trade_ids"] == ["t2"]
    assert report["promotion_gate"]["checks"]["exit_path_replay_rows_met"] is False
    assert "exit_path_replay_missing_trades" in report["promotion_gate"]["reasons"]
    markdown = render_live_readiness_markdown(report)
    assert "## Exit Path Replay Reconciliation" in markdown
    assert "schema_version: exit_path_replay_reconciliation.v1" in markdown
    assert "matched: false" in markdown
    assert "missing_trade_ids: t2" in markdown


def test_live_readiness_gate_report_rejects_exit_path_replay_invalid_schema_and_provenance(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    trades = [
        {
            "trade_id": "t1",
            "symbol": "BTCUSDT",
            "side": "long",
            "setup_type": "BREAKOUT_CONTINUATION",
            "net_pnl": 100.0,
            "gross_pnl": 120.0,
            "fee_paid": 5.0,
            "slippage_paid": 5.0,
            "fill_quality": "evidence_backed",
            "execution_price_source": "trade_print",
            "exit_fill_quality": "evidence_backed",
            "exit_price_source": "trade_print",
            "simulated_exit_reason": "take_profit",
        }
    ]
    (chunk / "trades.json").write_text(json.dumps({"trades": trades}), encoding="utf-8")
    (chunk / "exit_path_replay.json").write_text(
        json.dumps(
            {
                "schema_version": "unexpected_exit_path_replay.v0",
                "evidence_source": {"type": "synthetic_fixture"},
                "trades": [{"trade_id": "t1", "path_classification": "trade_print_path_available"}],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path, require_exit_path_replay_rows=True)

    reconciliation = report["exit_path_replay"]["reconciliation"]
    reasons = set(report["promotion_gate"]["reasons"])
    assert reconciliation["matched"] is False
    assert reconciliation["schema_valid"] is False
    assert reconciliation["provenance_present"] is False
    assert report["promotion_gate"]["checks"]["exit_path_replay_rows_met"] is False
    assert "exit_path_replay_artifact_schema_invalid" in reasons
    assert "exit_path_replay_artifact_provenance_missing" in reasons



def test_live_readiness_gate_report_rejects_when_exit_path_ambiguity_rate_exceeds_threshold(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 100.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "exit_reason": "fixed_horizon",
                        "mfe_pct": 0.01,
                        "mae_pct": -0.01,
                    },
                    {
                        "symbol": "ETHUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 50.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "exit_reason": "take_profit",
                        "mfe_pct": 0.04,
                        "mae_pct": -0.03,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(
        tmp_path,
        evidence_coverage_threshold=0.95,
        exit_evidence_coverage_threshold=0.95,
        max_exit_path_ambiguity_rate=0.25,
    )

    assert report["exit_path_replay"]["counts"]["fixed_horizon_only"] == 1
    assert report["exit_path_replay"]["counts"]["ambiguous_intrabar_order"] == 1
    assert report["totals"]["exit_path_ambiguity_rate"] == pytest.approx(1.0)
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"
    assert "exit_path_ambiguity_rate_above_threshold" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["exit_path_ambiguity_rate_met"] is False


def test_live_readiness_gate_report_rejects_when_exit_evidence_coverage_is_below_threshold(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 100.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "approximate",
                        "exit_price_source": "ohlcv_close",
                    },
                    {
                        "symbol": "ETHUSDT",
                        "side": "long",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "net_pnl": 50.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(
        tmp_path,
        evidence_coverage_threshold=0.95,
        exit_evidence_coverage_threshold=0.95,
    )

    assert report["totals"]["evidence_coverage"] == pytest.approx(1.0)
    assert report["totals"]["exit_evidence_coverage"] == pytest.approx(0.5)
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"
    assert "exit_evidence_coverage_below_threshold" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["exit_evidence_coverage_met"] is False


def test_live_readiness_gate_report_gates_optional_setup_rewrite_diagnostic(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "TREND_PULLBACK",
                        "net_pnl": 100.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "stop_loss",
                        "simulated_exit_price": 95.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (chunk / "setup_rewrite_experiment.json").write_text(
        json.dumps(
            {
                "summary": {
                    "total_rows": 2,
                    "evaluated_count": 1,
                    "would_keep_count": 0,
                    "would_filter_count": 1,
                    "skipped_count": 1,
                    "by_setup": {
                        "TREND_PULLBACK": {
                            "total_rows": 2,
                            "evaluated_count": 1,
                            "would_keep_count": 0,
                            "would_filter_count": 1,
                            "skipped_count": 1,
                            "net_pnl": 90.0,
                        }
                    },
                },
                "evaluation_rows": [
                    {
                        "symbol": "BTCUSDT",
                        "setup_type": "TREND_PULLBACK",
                        "evaluation_status": "evaluated",
                        "evaluation_reason": "score_below_minimum",
                        "would_keep": False,
                        "net_pnl": 100.0,
                    },
                    {
                        "symbol": "ETHUSDT",
                        "setup_type": "TREND_PULLBACK",
                        "evaluation_status": "no_evidence",
                        "evaluation_reason": "missing_score",
                        "would_keep": False,
                        "net_pnl": -10.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    diagnostic = report["setup_rewrite_diagnostic"]
    assert diagnostic["schema_version"] == "setup_rewrite_live_readiness_diagnostic.v1"
    assert diagnostic["chunks"] == [
        {
            "chunk": "chunk_001",
            "path": str(chunk / "setup_rewrite_experiment.json"),
            "status": "loaded",
            "summary": {
                "evaluated_count": 1,
                "would_keep_count": 0,
                "would_filter_count": 1,
                "skipped_count": 1,
            },
        }
    ]
    assert diagnostic["totals"] == {
        "evaluated_count": 1,
        "would_keep_count": 0,
        "would_filter_count": 1,
        "skipped_count": 1,
        "keep_rate": 0.0,
    }
    assert diagnostic["reasons"] == {"missing_score": 1, "score_below_minimum": 1}
    assert diagnostic["by_setup"]["TREND_PULLBACK"] == {
        "total_rows": 2,
        "evaluated_count": 1,
        "would_keep_count": 0,
        "would_filter_count": 1,
        "skipped_count": 1,
        "net_pnl": pytest.approx(90.0),
    }
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"
    assert "setup_rewrite_no_surviving_candidates" in report["promotion_gate"]["reasons"]
    assert "setup_rewrite_missing_evidence" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["setup_rewrite_has_surviving_candidates"] is False
    assert report["promotion_gate"]["checks"]["setup_rewrite_evidence_complete"] is False
    assert "- setup_rewrite:" in render_live_readiness_markdown(report)


def test_live_readiness_gate_rejects_non_object_setup_rewrite_artifact(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "setup_rewrite_experiment.json").write_text(json.dumps([]), encoding="utf-8")

    report = build_live_readiness_gate_report(tmp_path)

    diagnostic = report["setup_rewrite_diagnostic"]
    assert diagnostic["checks"]["setup_rewrite_artifact_schema_valid"] is False
    assert diagnostic["chunks"] == [
        {
            "chunk": "chunk_001",
            "path": str(chunk / "setup_rewrite_experiment.json"),
            "status": "invalid",
            "parse_error": "json_payload_not_object",
            "summary": {
                "evaluated_count": 0,
                "would_keep_count": 0,
                "would_filter_count": 0,
                "skipped_count": 0,
            },
        }
    ]
    assert "setup_rewrite_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["setup_rewrite_artifact_schema_valid"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_invalid_setup_rewrite_summary_counts(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "setup_rewrite_experiment.json").write_text(
        json.dumps(
            {
                "summary": {
                    "evaluated_count": "many",
                    "would_keep_count": 1,
                    "would_filter_count": 0,
                    "skipped_count": 0,
                }
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    diagnostic = report["setup_rewrite_diagnostic"]
    assert diagnostic["checks"]["setup_rewrite_artifact_schema_valid"] is False
    assert diagnostic["chunks"] == [
        {
            "chunk": "chunk_001",
            "path": str(chunk / "setup_rewrite_experiment.json"),
            "status": "invalid",
            "parse_error": "invalid_numeric_field: summary.evaluated_count",
            "summary": {
                "evaluated_count": 0,
                "would_keep_count": 1,
                "would_filter_count": 0,
                "skipped_count": 0,
            },
        }
    ]
    assert "setup_rewrite_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["setup_rewrite_artifact_schema_valid"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_rejects_invalid_setup_rewrite_evaluation_rows(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    _write_profitable_trade_chunk(chunk)
    (chunk / "setup_rewrite_experiment.json").write_text(
        json.dumps(
            {
                "summary": {
                    "evaluated_count": 1,
                    "would_keep_count": 1,
                    "would_filter_count": 0,
                    "skipped_count": 0,
                },
                "evaluation_rows": {"row": "not-a-list"},
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path)

    diagnostic = report["setup_rewrite_diagnostic"]
    assert diagnostic["checks"]["setup_rewrite_artifact_schema_valid"] is False
    assert diagnostic["chunks"] == [
        {
            "chunk": "chunk_001",
            "path": str(chunk / "setup_rewrite_experiment.json"),
            "status": "invalid",
            "parse_error": "invalid_field_type: evaluation_rows",
            "summary": {
                "evaluated_count": 1,
                "would_keep_count": 1,
                "would_filter_count": 0,
                "skipped_count": 0,
            },
        }
    ]
    assert "setup_rewrite_artifact_schema_invalid" in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["setup_rewrite_artifact_schema_valid"] is False
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"


def test_live_readiness_gate_report_rejects_negative_chunks_and_setup_buckets(tmp_path: Path) -> None:
    first = tmp_path / "chunk_001"
    second = tmp_path / "chunk_002"
    first.mkdir()
    second.mkdir()
    (first / "summary.json").write_text(
        json.dumps({"summary": {"trade_count": 2, "cost_breakdown": {"fees": 10.0, "slippage": 5.0}}}),
        encoding="utf-8",
    )
    (first / "trades.json").write_text(
        json.dumps(
            {
                "metadata": {"sample_period": {"start": "2026-03-01T00:00:00Z", "end": "2026-03-10T00:00:00Z"}},
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "TREND_PULLBACK",
                        "net_pnl": 100.0,
                        "fee_paid": 2.0,
                        "slippage_paid": 1.0,
                        "fill_quality": "evidence_backed",
                    },
                    {
                        "symbol": "ETHUSDT",
                        "side": "short",
                        "setup_type": "FAILED_BOUNCE_SHORT",
                        "net_pnl": -250.0,
                        "fee_paid": 3.0,
                        "slippage_paid": 2.0,
                        "fill_quality": "approximate",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (second / "trades.json").write_text(
        json.dumps(
            {
                "metadata": {"regime": "RISK_OFF"},
                "trades": [
                    {
                        "symbol": "SOLUSDT",
                        "side": "short",
                        "setup_type": "BREAKDOWN_SHORT",
                        "net_pnl": -50.0,
                        "fee_paid": 1.0,
                        "slippage_paid": 1.0,
                        "fill_quality": "evidence_backed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(tmp_path, evidence_coverage_threshold=0.75)

    assert report["totals"]["trade_count"] == 3
    assert report["totals"]["net_pnl"] == pytest.approx(-200.0)
    assert report["totals"]["evidence_coverage"] == pytest.approx(2 / 3)
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"
    assert "net_pnl_below_zero" in report["promotion_gate"]["reasons"]
    assert "evidence_coverage_below_threshold" in report["promotion_gate"]["reasons"]
    assert "major_setup_bucket_negative" in report["promotion_gate"]["reasons"]
    assert report["failure_taxonomy"]["loss_trade_count"] == 2
    assert report["by_setup_type"]["FAILED_BOUNCE_SHORT"]["net_pnl"] == pytest.approx(-250.0)


def test_live_readiness_gate_report_rejects_net_abs_concentration(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    trades = []
    for index, (symbol, setup_type, net_pnl) in enumerate(
        [
            ("BTCUSDT", "SETUP_A", -900.0),
            ("ETHUSDT", "SETUP_B", 25.0),
            ("SOLUSDT", "SETUP_C", 25.0),
            ("BNBUSDT", "SETUP_D", 25.0),
            ("XRPUSDT", "SETUP_E", 25.0),
        ]
    ):
        trades.append(
            {
                "symbol": symbol,
                "side": "long",
                "setup_type": setup_type,
                "net_pnl": net_pnl,
                "fill_quality": "evidence_backed",
                "execution_price_source": "trade_print",
                "exit_fill_quality": "evidence_backed",
                "exit_price_source": "trade_print",
                "trade_id": f"trade-{index}",
            }
        )
    (chunk / "trades.json").write_text(json.dumps({"trades": trades}), encoding="utf-8")

    report = build_live_readiness_gate_report(
        tmp_path,
        max_setup_trade_share=0.45,
        max_symbol_trade_share=0.70,
        max_setup_net_abs_share=0.60,
        max_symbol_net_abs_share=0.60,
    )

    reasons = report["promotion_gate"]["reasons"]
    checks = report["promotion_gate"]["checks"]
    concentration = report["concentration"]
    assert "setup_concentration_too_high" not in reasons
    assert "symbol_concentration_too_high" not in reasons
    assert "setup_net_abs_concentration_too_high" in reasons
    assert "symbol_net_abs_concentration_too_high" in reasons
    assert checks["setup_net_abs_concentration_met"] is False
    assert checks["symbol_net_abs_concentration_met"] is False
    assert concentration["max_setup_net_abs_share"] == 0.60
    assert concentration["max_symbol_net_abs_share"] == 0.60
    assert concentration["top_setup_by_net_abs"]["key"] == "SETUP_A"
    assert concentration["top_setup_by_net_abs"]["net_abs_share"] == pytest.approx(0.9)
    assert concentration["top_symbol_by_net_abs"]["key"] == "BTCUSDT"
    assert concentration["top_symbol_by_net_abs"]["net_abs_share"] == pytest.approx(0.9)


def test_live_readiness_gate_report_rejects_loss_abs_concentration(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk_001"
    chunk.mkdir()
    trades = []
    for index, (symbol, setup_type, net_pnl) in enumerate(
        [
            ("BTCUSDT", "SETUP_A", -300.0),
            ("ETHUSDT", "SETUP_B", -50.0),
            ("SOLUSDT", "SETUP_C", -50.0),
            ("BNBUSDT", "SETUP_D", 600.0),
        ]
    ):
        trades.append(
            {
                "symbol": symbol,
                "side": "long",
                "setup_type": setup_type,
                "net_pnl": net_pnl,
                "fill_quality": "evidence_backed",
                "execution_price_source": "trade_print",
                "exit_fill_quality": "evidence_backed",
                "exit_price_source": "trade_print",
                "trade_id": f"trade-{index}",
            }
        )
    (chunk / "trades.json").write_text(json.dumps({"trades": trades}), encoding="utf-8")

    report = build_live_readiness_gate_report(
        tmp_path,
        max_setup_trade_share=0.50,
        max_symbol_trade_share=0.50,
        max_setup_net_abs_share=0.70,
        max_symbol_net_abs_share=0.70,
        max_setup_loss_abs_share=0.60,
        max_symbol_loss_abs_share=0.60,
    )

    reasons = report["promotion_gate"]["reasons"]
    checks = report["promotion_gate"]["checks"]
    concentration = report["concentration"]
    assert "setup_concentration_too_high" not in reasons
    assert "symbol_concentration_too_high" not in reasons
    assert "setup_net_abs_concentration_too_high" not in reasons
    assert "symbol_net_abs_concentration_too_high" not in reasons
    assert "setup_loss_abs_concentration_too_high" in reasons
    assert "symbol_loss_abs_concentration_too_high" in reasons
    assert checks["setup_loss_abs_concentration_met"] is False
    assert checks["symbol_loss_abs_concentration_met"] is False
    assert concentration["max_setup_loss_abs_share"] == 0.60
    assert concentration["max_symbol_loss_abs_share"] == 0.60
    assert concentration["top_setup_by_loss_abs"]["key"] == "SETUP_A"
    assert concentration["top_setup_by_loss_abs"]["loss_abs_share"] == pytest.approx(0.75)
    assert concentration["top_symbol_by_loss_abs"]["key"] == "BTCUSDT"
    assert concentration["top_symbol_by_loss_abs"]["loss_abs_share"] == pytest.approx(0.75)


def test_quarantined_short_setup_types_exclude_only_when_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "dataset_root": str(tmp_path),
                "experiment_kind": "full_market_baseline",
                "sample_windows": [{"name": "all", "start": "2026-03-01T00:00:00Z", "end": "2026-03-02T00:00:00Z"}],
                "costs": {"fee_bps": {"spot": 4.0, "futures": 4.0}, "slippage_tiers": {"top": 1.0}, "funding_mode": "historical_series"},
                "baseline_name": "current",
                "variant_name": "quarantine",
                "universe": {"listing_age_days": 1, "min_quote_volume_usdt_24h": {"top": 1.0}},
                "capital": {"model": "shared_pool", "initial_equity": 100000.0, "risk_per_trade": 0.01, "max_open_risk": 0.03},
                "experiment_params": {
                    "quarantined_short_setup_types": ["breakdown_short", "FAILED_BOUNCE_SHORT"]
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_backtest_config(config_path)
    assert config.experiment_params is not None
    assert config.experiment_params.quarantined_short_setup_types == ("BREAKDOWN_SHORT", "FAILED_BOUNCE_SHORT")

    row = DatasetSnapshotRow(
        timestamp=datetime(2026, 3, 1, tzinfo=UTC),
        run_id="row-1",
        market={"symbols": {"BTCUSDT": {}, "ETHUSDT": {}, "SOLUSDT": {}}},
        derivatives=[],
    )

    monkeypatch.setattr(backtest_engine, "build_universes", lambda market, derivatives: _Universes())
    monkeypatch.setattr(backtest_engine, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        backtest_engine,
        "generate_short_candidates",
        lambda *args, **kwargs: [
            {"symbol": "BTCUSDT", "engine": "short", "setup_type": "BREAKDOWN_SHORT", "score": 0.9},
            {"symbol": "ETHUSDT", "engine": "short", "setup_type": "FAILED_BOUNCE_SHORT", "score": 0.8},
            {"symbol": "SOLUSDT", "engine": "short", "setup_type": "CLEAN_SHORT", "score": 0.7},
        ],
    )

    default_candidates = backtest_engine._raw_full_market_candidates(row)
    quarantined_candidates = backtest_engine._raw_full_market_candidates(
        row,
        quarantined_short_setup_types=frozenset(config.experiment_params.quarantined_short_setup_types),
    )

    assert [candidate["setup_type"] for candidate in default_candidates] == [
        "BREAKDOWN_SHORT",
        "FAILED_BOUNCE_SHORT",
        "CLEAN_SHORT",
    ]
    assert [candidate["setup_type"] for candidate in quarantined_candidates] == ["CLEAN_SHORT"]


def test_quarantined_setup_types_exclude_any_setup_bucket_when_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "dataset_root": str(tmp_path),
                "experiment_kind": "full_market_baseline",
                "sample_windows": [{"name": "all", "start": "2026-03-01T00:00:00Z", "end": "2026-03-02T00:00:00Z"}],
                "costs": {"fee_bps": {"spot": 4.0, "futures": 4.0}, "slippage_tiers": {"top": 1.0}, "funding_mode": "historical_series"},
                "baseline_name": "current",
                "variant_name": "quarantine",
                "universe": {"listing_age_days": 1, "min_quote_volume_usdt_24h": {"top": 1.0}},
                "capital": {"model": "shared_pool", "initial_equity": 100000.0, "risk_per_trade": 0.01, "max_open_risk": 0.03},
                "experiment_params": {
                    "quarantined_setup_types": ["rs_pullback", "RS_REACCELERATION", "FAILED_BOUNCE_SHORT"]
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_backtest_config(config_path)
    assert config.experiment_params is not None
    assert config.experiment_params.quarantined_setup_types == (
        "RS_PULLBACK",
        "RS_REACCELERATION",
        "FAILED_BOUNCE_SHORT",
    )

    row = DatasetSnapshotRow(
        timestamp=datetime(2026, 3, 1, tzinfo=UTC),
        run_id="row-1",
        market={"symbols": {"BTCUSDT": {}, "ETHUSDT": {}, "SOLUSDT": {}, "BNBUSDT": {}}},
        derivatives=[],
    )

    monkeypatch.setattr(backtest_engine, "build_universes", lambda market, derivatives: _Universes())
    monkeypatch.setattr(
        backtest_engine,
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            {"symbol": "BTCUSDT", "engine": "trend", "setup_type": "RS_PULLBACK", "score": 0.95},
            {"symbol": "ETHUSDT", "engine": "trend", "setup_type": "TREND_CONTINUATION", "score": 0.9},
        ],
    )
    monkeypatch.setattr(
        backtest_engine,
        "generate_rotation_candidates",
        lambda *args, **kwargs: [
            {"symbol": "SOLUSDT", "engine": "rotation", "setup_type": "RS_REACCELERATION", "score": 0.85}
        ],
    )
    monkeypatch.setattr(
        backtest_engine,
        "generate_short_candidates",
        lambda *args, **kwargs: [
            {"symbol": "BNBUSDT", "engine": "short", "setup_type": "FAILED_BOUNCE_SHORT", "score": 0.8}
        ],
    )

    default_candidates = backtest_engine._raw_full_market_candidates(row)
    quarantined_candidates = backtest_engine._raw_full_market_candidates(
        row,
        quarantined_setup_types=frozenset(config.experiment_params.quarantined_setup_types),
    )

    assert [candidate["setup_type"] for candidate in default_candidates] == [
        "RS_PULLBACK",
        "TREND_CONTINUATION",
        "RS_REACCELERATION",
        "FAILED_BOUNCE_SHORT",
    ]
    assert [candidate["setup_type"] for candidate in quarantined_candidates] == ["TREND_CONTINUATION"]


def test_live_readiness_smoke_report_materializes_nested_full_market_bundle(tmp_path: Path) -> None:
    input_root = tmp_path / "results"
    bundle = input_root / "chunk_001_20260301_20260302" / "full_market_baseline__current_policy__smoke"
    bundle.mkdir(parents=True)
    (bundle / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "TREND_PULLBACK",
                        "net_pnl": 100.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "simulated_exit_reason": "stop_loss",
                        "simulated_exit_price": 95.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (bundle / "summary.json").write_text(json.dumps({"summary": {"cost_breakdown": {"fees": 1.0}}}), encoding="utf-8")
    (bundle / "setup_rewrite_experiment.json").write_text(
        json.dumps(
            {
                "summary": {
                    "evaluated_count": 1,
                    "would_keep_count": 0,
                    "would_filter_count": 1,
                    "skipped_count": 1,
                    "by_setup": {
                        "TREND_PULLBACK": {
                            "total_rows": 1,
                            "evaluated_count": 1,
                            "would_keep_count": 0,
                            "would_filter_count": 1,
                            "skipped_count": 1,
                            "net_pnl": 100.0,
                        }
                    },
                },
                "evaluation_rows": [
                    {"setup_type": "TREND_PULLBACK", "evaluation_reason": "score_below_minimum", "would_keep": False}
                ],
            }
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "smoke"
    report = write_live_readiness_smoke_report(input_root, output_dir)

    normalized_chunk = output_dir / "normalized_chunks" / "chunk_001_20260301_20260302"
    assert (normalized_chunk / "trades.json").exists()
    assert (normalized_chunk / "summary.json").exists()
    assert (normalized_chunk / "setup_rewrite_experiment.json").exists()
    assert report["smoke_report"]["source_root"] == str(input_root)
    assert report["smoke_report"]["normalized_input_dir"] == str(output_dir / "normalized_chunks")
    assert report["smoke_report"]["chunks"] == [
        {"chunk": "chunk_001_20260301_20260302", "source_dir": str(bundle), "normalized_dir": str(normalized_chunk)}
    ]
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"
    assert "setup_rewrite_no_surviving_candidates" in report["promotion_gate"]["reasons"]
    assert "setup_rewrite_missing_evidence" in report["promotion_gate"]["reasons"]
    assert "setup_concentration_too_high" in report["promotion_gate"]["reasons"]
    assert "symbol_concentration_too_high" in report["promotion_gate"]["reasons"]
    assert "setup_net_abs_concentration_too_high" in report["promotion_gate"]["reasons"]
    assert "symbol_net_abs_concentration_too_high" in report["promotion_gate"]["reasons"]
    assert "setup_loss_abs_concentration_too_high" not in report["promotion_gate"]["reasons"]
    assert "symbol_loss_abs_concentration_too_high" not in report["promotion_gate"]["reasons"]
    assert report["promotion_gate"]["checks"]["setup_concentration_met"] is False
    assert report["promotion_gate"]["checks"]["symbol_concentration_met"] is False
    assert report["promotion_gate"]["checks"]["setup_net_abs_concentration_met"] is False
    assert report["promotion_gate"]["checks"]["symbol_net_abs_concentration_met"] is False
    assert report["promotion_gate"]["checks"]["setup_loss_abs_concentration_met"] is True
    assert report["promotion_gate"]["checks"]["symbol_loss_abs_concentration_met"] is True
    persisted = json.loads((output_dir / "live_readiness_gate.json").read_text(encoding="utf-8"))
    assert persisted["smoke_report"] == report["smoke_report"]
    markdown = (output_dir / "live_readiness_gate.md").read_text(encoding="utf-8")
    assert "- setup_rewrite:" in markdown
    assert "## Trade Postmortem Summary" in markdown
    assert "- schema_version: trade_postmortem_summary.v1" in markdown
    assert "- 有效盈利_after_cost: trades=1" in markdown
    assert "### Setup Type Summary" in markdown
    assert "- TREND_PULLBACK: trades=1" in markdown
    assert "### Symbol Summary" in markdown
    assert "- BTCUSDT: trades=1" in markdown
    assert "## Concentration Gate" in markdown
    assert "- max_setup_trade_share: 45.00%" in markdown
    assert "- max_symbol_trade_share: 70.00%" in markdown
    assert "- max_setup_net_abs_share: 60.00%" in markdown
    assert "- max_symbol_net_abs_share: 60.00%" in markdown
    assert "- max_setup_loss_abs_share: 60.00%" in markdown
    assert "- max_symbol_loss_abs_share: 60.00%" in markdown
    assert "- top_setup_by_trades: TREND_PULLBACK, trades=1, trade_share=100.00%, threshold=45.00%, status=breach" in markdown
    assert "- top_symbol_by_trades: BTCUSDT, trades=1, trade_share=100.00%, threshold=70.00%, status=breach" in markdown
    assert "- top_setup_by_net_abs: TREND_PULLBACK, trades=1, net_abs_share=100.00%, threshold=60.00%, status=breach" in markdown
    assert "- top_symbol_by_net_abs: BTCUSDT, trades=1, net_abs_share=100.00%, threshold=60.00%, status=breach" in markdown
    assert "- top_setup_by_loss_abs: TREND_PULLBACK, trades=1, loss_abs_share=0.00%, threshold=60.00%, status=ok" in markdown
    assert "- top_symbol_by_loss_abs: BTCUSDT, trades=1, loss_abs_share=0.00%, threshold=60.00%, status=ok" in markdown
    assert (output_dir / "trade_postmortem_summary.json").exists()
    postmortem = json.loads((output_dir / "trade_postmortem_summary.json").read_text(encoding="utf-8"))
    assert postmortem["schema_version"] == "trade_postmortem_summary.v1"
    assert postmortem["by_failure_taxonomy"]["有效盈利_after_cost"]["trades"] == 1
    assert report["postmortem_reconciliation"] == {
        "schema_version": "live_readiness_postmortem_reconciliation.v1",
        "gate_trade_count": 1,
        "postmortem_trade_count": 1,
        "trade_count_delta": 0,
        "gate_net_pnl": pytest.approx(100.0),
        "postmortem_net_pnl": pytest.approx(100.0),
        "net_pnl_delta": pytest.approx(0.0),
        "matched": True,
    }
    assert persisted["postmortem_reconciliation"]["matched"] is True
    assert "## Postmortem Reconciliation" in markdown
    assert "- matched: true" in markdown
    assert "- trade_count_delta: 0" in markdown
    assert "- net_pnl_delta: 0.00" in markdown


def test_live_readiness_cli_stdout_includes_concentration_summary(tmp_path: Path) -> None:
    input_root = tmp_path / "results"
    bundle = input_root / "chunk_001_20260301_20260302" / "full_market_baseline__current_policy__smoke"
    bundle.mkdir(parents=True)
    (bundle / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "TREND_PULLBACK",
                        "net_pnl": 100.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.live_readiness",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(tmp_path / "smoke"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    stdout = json.loads(completed.stdout)
    assert stdout["postmortem_reconciliation"] == {
        "matched": True,
        "trade_count_delta": 0,
        "net_pnl_delta": 0.0,
    }
    assert stdout["concentration"] == {
        "max_setup_trade_share": 0.45,
        "max_symbol_trade_share": 0.70,
        "max_setup_net_abs_share": 0.60,
        "max_symbol_net_abs_share": 0.60,
        "max_setup_loss_abs_share": 0.60,
        "max_symbol_loss_abs_share": 0.60,
        "top_setup_by_trades": {
            "key": "TREND_PULLBACK",
            "trades": 1,
            "trade_share": 1.0,
            "net_abs_share": 1.0,
            "loss_abs_share": 0.0,
        },
        "top_symbol_by_trades": {
            "key": "BTCUSDT",
            "trades": 1,
            "trade_share": 1.0,
            "net_abs_share": 1.0,
            "loss_abs_share": 0.0,
        },
        "top_setup_by_net_abs": {
            "key": "TREND_PULLBACK",
            "trades": 1,
            "trade_share": 1.0,
            "net_abs_share": 1.0,
            "loss_abs_share": 0.0,
        },
        "top_symbol_by_net_abs": {
            "key": "BTCUSDT",
            "trades": 1,
            "trade_share": 1.0,
            "net_abs_share": 1.0,
            "loss_abs_share": 0.0,
        },
        "top_setup_by_loss_abs": {
            "key": "TREND_PULLBACK",
            "trades": 1,
            "trade_share": 1.0,
            "net_abs_share": 1.0,
            "loss_abs_share": 0.0,
        },
        "top_symbol_by_loss_abs": {
            "key": "BTCUSDT",
            "trades": 1,
            "trade_share": 1.0,
            "net_abs_share": 1.0,
            "loss_abs_share": 0.0,
        },
    }



def test_live_readiness_gate_report_rejects_missing_real_passive_calibration_for_maker_assumption(
    tmp_path: Path,
) -> None:
    chunk = tmp_path / "chunk_00"
    chunk.mkdir()
    (chunk / "trades.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "long",
                        "setup_type": "TREND_PULLBACK",
                        "gross_pnl": 200.0,
                        "net_pnl": 150.0,
                        "fee_paid": 10.0,
                        "slippage_paid": 5.0,
                        "fill_quality": "evidence_backed",
                        "execution_price_source": "trade_print",
                        "exit_fill_quality": "evidence_backed",
                        "exit_price_source": "trade_print",
                        "exit_path_classification": "trade_print_path_available",
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (chunk / "passive_order_calibration_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "passive_order_calibration_summary.v1",
                "provenance": {"source": "synthetic_fixture", "real_exchange_records": False},
                "overall": {"attempt_count": 20, "fill_rate": 0.8, "partial_fill_rate": 0.1, "missed_fill_rate": 0.2},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    report = build_live_readiness_gate_report(
        tmp_path,
        require_passive_calibration=True,
        min_passive_calibration_attempts=10,
        min_passive_fill_rate=0.5,
    )

    assert report["passive_calibration"]["schema_version"] == "passive_calibration_live_readiness.v1"
    assert report["passive_calibration"]["real_exchange_records"] is False
    assert report["promotion_gate"]["checks"]["passive_calibration_real_records_met"] is False
    assert "passive_calibration_missing_real_records" in report["promotion_gate"]["reasons"]


def test_trade_postmortem_summary_buckets_failure_taxonomy_and_setups() -> None:
    report = summarize_trade_postmortem(
        [
            {
                "symbol": "SOLUSDT",
                "setup_type": "RS_REACCELERATION",
                "gross_pnl": 500.0,
                "net_pnl": 420.0,
                "fee_paid": 50.0,
                "slippage_paid": 30.0,
                "mfe_pct": 0.012,
                "mae_pct": 0.0,
            },
            {
                "symbol": "ETHUSDT",
                "setup_type": "BREAKOUT_CONTINUATION",
                "gross_pnl": 20.0,
                "net_pnl": -25.0,
                "fee_paid": 30.0,
                "slippage_paid": 15.0,
                "mfe_pct": 0.001,
                "mae_pct": 0.0,
            },
            {
                "symbol": "BTCUSDT",
                "setup_type": "TREND_PULLBACK",
                "gross_pnl": -100.0,
                "net_pnl": -140.0,
                "fee_paid": 20.0,
                "slippage_paid": 20.0,
                "mfe_pct": 0.0,
                "mae_pct": 0.006,
            },
            {
                "symbol": "BNBUSDT",
                "setup_type": "TREND_PULLBACK",
                "gross_pnl": -80.0,
                "net_pnl": -110.0,
                "fee_paid": 10.0,
                "slippage_paid": 20.0,
                "mfe_pct": 0.002,
                "mae_pct": 0.007,
            },
            {
                "symbol": "XRPUSDT",
                "setup_type": "PULLBACK_CONTINUATION",
                "gross_pnl": -30.0,
                "net_pnl": -60.0,
                "fee_paid": 10.0,
                "slippage_paid": 20.0,
                "mfe_pct": 0.004,
                "mae_pct": 0.001,
            },
        ]
    )

    assert report["schema_version"] == "trade_postmortem_summary.v1"
    assert report["summary"]["trades"] == 5
    assert report["summary"]["gross_pnl"] == pytest.approx(310.0)
    assert report["summary"]["net_pnl"] == pytest.approx(85.0)
    assert report["summary"]["cost_total"] == pytest.approx(225.0)
    assert report["by_failure_taxonomy"]["有效盈利_after_cost"]["trades"] == 1
    assert report["by_failure_taxonomy"]["盈利被成本翻负"]["trades"] == 1
    assert report["by_failure_taxonomy"]["入场后无有效顺向空间"]["trades"] == 1
    assert report["by_failure_taxonomy"]["MAE压过MFE_方向/时机错误"]["trades"] == 1
    assert report["by_failure_taxonomy"]["净亏损_需逐单复核"]["trades"] == 1
    assert report["by_setup_type"]["TREND_PULLBACK"]["trades"] == 2
    assert report["by_setup_type"]["TREND_PULLBACK"]["net"] == pytest.approx(-250.0)
    assert report["by_symbol"]["SOLUSDT"]["win_rate"] == 1.0
    assert report["dominance"]["top_setup_by_trades"] == {
        "key": "TREND_PULLBACK",
        "trades": 2,
        "trade_share": pytest.approx(0.4),
        "net": pytest.approx(-250.0),
        "net_abs_share": pytest.approx(250.0 / 755.0),
        "loss_abs_share": pytest.approx(250.0 / 335.0),
    }
    assert report["dominance"]["top_symbol_by_trades"] == {
        "key": "SOLUSDT",
        "trades": 1,
        "trade_share": pytest.approx(0.2),
        "net": pytest.approx(420.0),
        "net_abs_share": pytest.approx(420.0 / 755.0),
        "loss_abs_share": pytest.approx(0.0),
    }
    assert report["dominance"]["top_setup_by_net_abs"] == {
        "key": "RS_REACCELERATION",
        "trades": 1,
        "trade_share": pytest.approx(0.2),
        "net": pytest.approx(420.0),
        "net_abs_share": pytest.approx(420.0 / 755.0),
        "loss_abs_share": pytest.approx(0.0),
    }
    assert report["dominance"]["top_symbol_by_net_abs"] == {
        "key": "SOLUSDT",
        "trades": 1,
        "trade_share": pytest.approx(0.2),
        "net": pytest.approx(420.0),
        "net_abs_share": pytest.approx(420.0 / 755.0),
        "loss_abs_share": pytest.approx(0.0),
    }
    assert report["dominance"]["top_setup_by_loss_abs"] == {
        "key": "TREND_PULLBACK",
        "trades": 2,
        "trade_share": pytest.approx(0.4),
        "net": pytest.approx(-250.0),
        "net_abs_share": pytest.approx(250.0 / 755.0),
        "loss_abs_share": pytest.approx(250.0 / 335.0),
    }
    assert report["dominance"]["top_symbol_by_loss_abs"] == {
        "key": "BTCUSDT",
        "trades": 1,
        "trade_share": pytest.approx(0.2),
        "net": pytest.approx(-140.0),
        "net_abs_share": pytest.approx(140.0 / 755.0),
        "loss_abs_share": pytest.approx(140.0 / 335.0),
    }


class _Universes:
    major_universe = ()
    rotation_universe = ()
    short_universe = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
