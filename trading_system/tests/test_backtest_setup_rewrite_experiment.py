from __future__ import annotations

import importlib
import math

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


def test_setup_rewrite_experiment_applies_setup_scoped_score_and_cost_coverage_filters() -> None:
    module = importlib.import_module("trading_system.app.backtest.setup_rewrite_experiment")

    params = SetupRewriteParams(
        rules=(
            SetupRewriteRule(
                name="require_setup_min_score",
                setup_types=("RS_REACCELERATION", "RS_PULLBACK"),
                min_score=0.7,
            ),
            SetupRewriteRule(
                name="require_setup_min_cost_coverage_ratio",
                setup_types=("RS_REACCELERATION", "RS_PULLBACK"),
                min_cost_coverage_ratio=1.1,
            ),
        )
    )

    artifact = module.build_setup_rewrite_experiment(
        rows=[
            {
                "symbol": "BTCUSDT",
                "setup_type": "RS_REACCELERATION",
                "score": 0.64,
                "cost_coverage_ratio": 1.5,
                "net_pnl": -3.0,
            },
            {
                "symbol": "ETHUSDT",
                "setup_type": "RS_PULLBACK",
                "score": 0.74,
                "cost_coverage_ratio": 0.95,
                "net_pnl": -2.0,
            },
            {
                "symbol": "SOLUSDT",
                "setup_type": "TREND_PULLBACK",
                "score": 0.4,
                "cost_coverage_ratio": 0.2,
                "net_pnl": 4.0,
            },
            {
                "symbol": "LINKUSDT",
                "setup_type": "RS_PULLBACK",
                "cost_coverage_ratio": 1.3,
                "net_pnl": -1.0,
            },
            {
                "symbol": "ADAUSDT",
                "setup_type": "RS_REACCELERATION",
                "score": 0.8,
                "net_pnl": -1.5,
            },
        ],
        setup_rewrite=params,
    )

    assert artifact["metadata"]["changes_baseline_ledger"] is False
    assert artifact["metadata"]["setup_rewrite"] == {
        "rules": [
            {
                "name": "require_setup_min_score",
                "setup_types": ["RS_REACCELERATION", "RS_PULLBACK"],
                "min_score": 0.7,
            },
            {
                "name": "require_setup_min_cost_coverage_ratio",
                "setup_types": ["RS_REACCELERATION", "RS_PULLBACK"],
                "min_cost_coverage_ratio": 1.1,
            },
        ]
    }
    assert artifact["summary"]["evaluated_count"] == 3
    assert artifact["summary"]["would_keep_count"] == 1
    assert artifact["summary"]["would_filter_count"] == 2
    assert artifact["summary"]["skipped_count"] == 2

    statuses = [(row["symbol"], row["evaluation_status"], row["evaluation_reason"], row["would_keep"]) for row in artifact["evaluation_rows"]]
    assert statuses == [
        ("BTCUSDT", "evaluated", "setup_score_below_minimum", False),
        ("ETHUSDT", "evaluated", "setup_cost_coverage_below_minimum", False),
        ("SOLUSDT", "evaluated", "passed_all_rules", True),
        ("LINKUSDT", "no_evidence", "missing_score", False),
        ("ADAUSDT", "no_evidence", "missing_cost_coverage_ratio", False),
    ]


def test_setup_rewrite_experiment_applies_setup_scoped_allowed_symbols_filter() -> None:
    module = importlib.import_module("trading_system.app.backtest.setup_rewrite_experiment")

    artifact = module.build_setup_rewrite_experiment(
        rows=[
            {"symbol": "BTCUSDT", "setup_type": "RS_PULLBACK", "score": 0.8},
            {"symbol": "DOGEUSDT", "setup_type": "RS_PULLBACK", "score": 0.8},
            {"setup_type": "RS_PULLBACK", "score": 0.8},
            {"symbol": "DOGEUSDT", "setup_type": "TREND_PULLBACK", "score": 0.2},
        ],
        setup_rewrite=SetupRewriteParams(
            rules=(
                SetupRewriteRule(
                    name="require_setup_allowed_symbols",
                    setup_types=("RS_PULLBACK",),
                    symbols=("BTCUSDT",),
                ),
            )
        ),
    )

    statuses = [(row["symbol"], row["evaluation_status"], row["evaluation_reason"], row["would_keep"]) for row in artifact["evaluation_rows"]]
    assert statuses == [
        ("BTCUSDT", "evaluated", "passed_all_rules", True),
        ("DOGEUSDT", "evaluated", "setup_symbol_not_allowed", False),
        (None, "no_evidence", "missing_symbol", False),
        ("DOGEUSDT", "evaluated", "passed_all_rules", True),
    ]


def test_setup_rewrite_experiment_rejects_numeric_symbol_with_field_path() -> None:
    module = importlib.import_module("trading_system.app.backtest.setup_rewrite_experiment")

    with pytest.raises(ValueError, match=r"rows\[1\]\.symbol must be a string"):
        module.build_setup_rewrite_experiment(
            rows=[
                {
                    "symbol": 123,
                    "setup_type": "RS_PULLBACK",
                    "score": 0.8,
                }
            ],
            setup_rewrite=SetupRewriteParams(
                rules=(
                    SetupRewriteRule(
                        name="require_setup_allowed_symbols",
                        setup_types=("RS_PULLBACK",),
                        symbols=("BTCUSDT",),
                    ),
                )
            ),
        )


def test_setup_rewrite_experiment_rejects_string_score_with_field_path() -> None:
    module = importlib.import_module("trading_system.app.backtest.setup_rewrite_experiment")

    with pytest.raises(ValueError, match=r"rows\[1\]\.score"):
        module.build_setup_rewrite_experiment(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "setup_type": "TREND_PULLBACK",
                    "score": "0.82",
                    "net_pnl": 12.5,
                    "cost_coverage_ratio": 1.4,
                }
            ],
            setup_rewrite=_params(),
        )


@pytest.mark.parametrize("net_pnl", [True, math.nan, math.inf])
def test_setup_rewrite_experiment_rejects_invalid_net_pnl_with_field_path(net_pnl: object) -> None:
    module = importlib.import_module("trading_system.app.backtest.setup_rewrite_experiment")

    with pytest.raises(ValueError, match=r"rows\[1\]\.net_pnl"):
        module.build_setup_rewrite_experiment(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "setup_type": "TREND_PULLBACK",
                    "score": 0.82,
                    "net_pnl": net_pnl,
                    "cost_coverage_ratio": 1.4,
                }
            ],
            setup_rewrite=_params(),
        )


def test_setup_rewrite_experiment_rejects_string_net_pnl_with_field_path() -> None:
    module = importlib.import_module("trading_system.app.backtest.setup_rewrite_experiment")

    with pytest.raises(ValueError, match=r"rows\[1\]\.net_pnl must be a finite number"):
        module.build_setup_rewrite_experiment(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "setup_type": "TREND_PULLBACK",
                    "score": 0.82,
                    "net_pnl": "12.5",
                    "cost_coverage_ratio": 1.4,
                }
            ],
            setup_rewrite=_params(),
        )


@pytest.mark.parametrize("field_name", ["source_chunk", "chunk", "chunk_name"])
def test_setup_rewrite_experiment_rejects_padded_source_identifier_with_field_path(field_name: str) -> None:
    module = importlib.import_module("trading_system.app.backtest.setup_rewrite_experiment")

    with pytest.raises(ValueError, match=rf"rows\[1\]\.{field_name} must be canonical"):
        module.build_setup_rewrite_experiment(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "setup_type": "TREND_PULLBACK",
                    "score": 0.82,
                    "net_pnl": 12.5,
                    "cost_coverage_ratio": 1.4,
                    field_name: " 2026-03 ",
                }
            ],
            setup_rewrite=_params(),
        )
