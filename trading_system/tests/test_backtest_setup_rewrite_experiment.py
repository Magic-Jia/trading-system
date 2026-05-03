from __future__ import annotations

import importlib

import pytest

from trading_system.app.backtest.types import SetupRewriteParams, SetupRewriteRule


def _params() -> SetupRewriteParams:
    return SetupRewriteParams(
        rules=(
            SetupRewriteRule(name="require_min_score", min_score=0.7),
            SetupRewriteRule(name="exclude_setup_types", setup_types=("RS_OVERHEAT",)),
            SetupRewriteRule(name="require_after_cost_breakeven_evidence"),
        )
    )


def test_setup_rewrite_experiment_marks_keep_filter_and_no_evidence_rows() -> None:
    module = importlib.import_module("trading_system.app.backtest.setup_rewrite_experiment")

    artifact = module.build_setup_rewrite_experiment(
        rows=[
            {
                "symbol": "BTCUSDT",
                "setup_type": "TREND_PULLBACK",
                "side": "long",
                "entry_timestamp": "2026-03-10T00:00:00Z",
                "score": 0.82,
                "net_pnl": 12.5,
                "source_chunk": "2026-03",
                "cost_coverage_ratio": 1.4,
            },
            {
                "symbol": "ETHUSDT",
                "setup_type": "TREND_PULLBACK",
                "side": "long",
                "entry_timestamp": "2026-03-10T01:00:00Z",
                "score": 0.62,
                "net_pnl": -4.0,
                "source_chunk": "2026-03",
                "cost_coverage_ratio": 1.2,
            },
            {
                "symbol": "SOLUSDT",
                "setup_type": "RS_OVERHEAT",
                "side": "short",
                "entry_timestamp": "2026-03-10T02:00:00Z",
                "score": 0.91,
                "net_pnl": -9.0,
                "source_chunk": "2026-04",
                "cost_coverage_ratio": 1.8,
            },
            {
                "symbol": "LINKUSDT",
                "setup_type": "TREND_PULLBACK",
                "side": "long",
                "entry_timestamp": "2026-03-10T03:00:00Z",
                "score": 0.76,
                "net_pnl": 1.0,
                "source_chunk": "2026-04",
            },
        ],
        setup_rewrite=_params(),
        metadata={"baseline_name": "current_system"},
    )

    assert artifact["metadata"]["artifact_type"] == "opt_in_offline_diagnostic"
    assert artifact["metadata"]["changes_baseline_ledger"] is False
    assert artifact["metadata"]["setup_rewrite"] == {
        "rules": [
            {"name": "require_min_score", "min_score": 0.7},
            {"name": "exclude_setup_types", "setup_types": ["RS_OVERHEAT"]},
            {"name": "require_after_cost_breakeven_evidence"},
        ]
    }
    assert artifact["summary"]["total_rows"] == 4
    assert artifact["summary"]["total_trades"] == 4
    assert artifact["summary"]["evaluated_count"] == 3
    assert artifact["summary"]["would_keep_count"] == 1
    assert artifact["summary"]["would_filter_count"] == 2
    assert artifact["summary"]["skipped_count"] == 1
    assert artifact["summary"]["by_setup"]["TREND_PULLBACK"] == {
        "total_rows": 3,
        "evaluated_count": 2,
        "would_keep_count": 1,
        "would_filter_count": 1,
        "skipped_count": 1,
        "net_pnl": pytest.approx(9.5),
    }
    assert artifact["summary"]["by_symbol"]["BTCUSDT"]["would_keep_count"] == 1
    assert artifact["summary"]["by_source_chunk"]["2026-04"]["skipped_count"] == 1

    statuses = [(row["symbol"], row["evaluation_status"], row["evaluation_reason"], row["would_keep"]) for row in artifact["evaluation_rows"]]
    assert statuses == [
        ("BTCUSDT", "evaluated", "passed_all_rules", True),
        ("ETHUSDT", "evaluated", "score_below_minimum", False),
        ("SOLUSDT", "evaluated", "excluded_setup_type", False),
        ("LINKUSDT", "no_evidence", "missing_cost_coverage_ratio", False),
    ]
