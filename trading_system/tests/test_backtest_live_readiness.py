from __future__ import annotations

import json
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
from trading_system.app.backtest.types import DatasetSnapshotRow
from trading_system.app.execution.calibration import load_calibration_records, summarize_calibration_records


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
    persisted = json.loads((output_dir / "live_readiness_gate.json").read_text(encoding="utf-8"))
    assert persisted["smoke_report"] == report["smoke_report"]
    assert "- setup_rewrite:" in (output_dir / "live_readiness_gate.md").read_text(encoding="utf-8")
    assert (output_dir / "trade_postmortem_summary.json").exists()
    postmortem = json.loads((output_dir / "trade_postmortem_summary.json").read_text(encoding="utf-8"))
    assert postmortem["schema_version"] == "trade_postmortem_summary.v1"
    assert postmortem["by_failure_taxonomy"]["有效盈利_after_cost"]["trades"] == 1


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


class _Universes:
    major_universe = ()
    rotation_universe = ()
    short_universe = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
