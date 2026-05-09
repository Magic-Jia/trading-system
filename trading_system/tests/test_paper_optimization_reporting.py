from __future__ import annotations

import json

from trading_system.app.paper_optimization.reporting import build_optimization_summary


def test_build_optimization_summary_uses_daily_metrics_and_related_artifacts(tmp_path) -> None:
    signal_facts_path = tmp_path / "signal_facts.jsonl"
    trade_outcomes_path = tmp_path / "trade_outcomes.jsonl"
    daily_metrics_path = tmp_path / "daily_metrics.json"
    health_report_path = tmp_path / "health_report.json"
    recommendations_path = tmp_path / "recommendations.json"
    promotion_decision_path = tmp_path / "promotion_decision.json"

    signal_facts_path.write_text('{"signal": 1}\n{"signal": 2}\n', encoding="utf-8")
    trade_outcomes_path.write_text('{"outcome": 1}\n', encoding="utf-8")
    daily_metrics_path.write_text(
        json.dumps(
            {
                "signal_fact_count": 3,
                "trade_outcome_count": 2,
                "recorded_at_bj": "2026-04-24T12:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    health_report_path.write_text(json.dumps({"status": "warn", "warnings": [{"code": "position_not_tracked"}]}), encoding="utf-8")
    recommendations_path.write_text(
        json.dumps({"recorded_at_bj": "2026-04-24T12:05:00+08:00", "recommendations": [{"id": "lower-total-risk-budget"}]}),
        encoding="utf-8",
    )
    promotion_decision_path.write_text(json.dumps({"status": "recommend", "decision": "awaiting_backtest"}), encoding="utf-8")

    summary = build_optimization_summary(
        signal_facts_path=signal_facts_path,
        trade_outcomes_path=trade_outcomes_path,
        daily_metrics_path=daily_metrics_path,
        health_report_path=health_report_path,
        recommendations_path=recommendations_path,
        promotion_decision_path=promotion_decision_path,
    )

    assert summary == {
        "signal_fact_count": 3,
        "trade_outcome_count": 2,
        "last_metrics_at": "2026-04-24T12:00:00+08:00",
        "last_recommendation_at": "2026-04-24T12:05:00+08:00",
        "health_status": "warn",
        "warning_count": 1,
        "recommendation_count": 1,
        "optimization_alert_count": 0,
        "optimization_alerts": [],
        "promotion_status": "recommend",
        "promotion_decision": "awaiting_backtest",
    }


def test_build_optimization_summary_surfaces_consecutive_low_sample_alert(tmp_path) -> None:
    recommendations_path = tmp_path / "recommendations.json"
    recommendations_path.write_text(
        json.dumps(
            {
                "recorded_at_bj": "2026-04-24T12:05:00+08:00",
                "recommendations": [],
                "alerts": [
                    {
                        "code": "consecutive_low_sample",
                        "severity": "warning",
                        "message": "paper optimization has been suppressed by low_sample for 3 consecutive runs",
                        "consecutive_count": 3,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = build_optimization_summary(
        signal_facts_path=tmp_path / "signal_facts.jsonl",
        trade_outcomes_path=tmp_path / "trade_outcomes.jsonl",
        daily_metrics_path=tmp_path / "daily_metrics.json",
        health_report_path=tmp_path / "health_report.json",
        recommendations_path=recommendations_path,
        promotion_decision_path=tmp_path / "promotion_decision.json",
    )

    assert summary["optimization_alert_count"] == 1
    assert summary["optimization_alerts"] == [
        {
            "code": "consecutive_low_sample",
            "severity": "warning",
            "message": "paper optimization has been suppressed by low_sample for 3 consecutive runs",
            "consecutive_count": 3,
        }
    ]


def test_build_optimization_summary_falls_back_to_jsonl_counts_when_metrics_are_missing(tmp_path) -> None:
    signal_facts_path = tmp_path / "signal_facts.jsonl"
    trade_outcomes_path = tmp_path / "trade_outcomes.jsonl"
    signal_facts_path.write_text('{"signal": 1}\n{"signal": 2}\n', encoding="utf-8")
    trade_outcomes_path.write_text('{"outcome": 1}\n', encoding="utf-8")

    summary = build_optimization_summary(
        signal_facts_path=signal_facts_path,
        trade_outcomes_path=trade_outcomes_path,
        daily_metrics_path=tmp_path / "daily_metrics.json",
        health_report_path=tmp_path / "health_report.json",
        recommendations_path=tmp_path / "recommendations.json",
        promotion_decision_path=tmp_path / "promotion_decision.json",
    )

    assert summary == {
        "signal_fact_count": 2,
        "trade_outcome_count": 1,
        "last_metrics_at": None,
        "last_recommendation_at": None,
        "health_status": None,
        "warning_count": 0,
        "recommendation_count": 0,
        "optimization_alert_count": 0,
        "optimization_alerts": [],
        "promotion_status": None,
        "promotion_decision": None,
    }

def test_build_optimization_summary_rejects_boolean_daily_metric_counts(tmp_path) -> None:
    import pytest

    daily_metrics_path = tmp_path / "daily_metrics.json"
    daily_metrics_path.write_text(
        json.dumps({"signal_fact_count": True, "trade_outcome_count": 1}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="daily_metrics.signal_fact_count must be an integer"):
        build_optimization_summary(
            signal_facts_path=tmp_path / "signal_facts.jsonl",
            trade_outcomes_path=tmp_path / "trade_outcomes.jsonl",
            daily_metrics_path=daily_metrics_path,
            health_report_path=tmp_path / "health_report.json",
            recommendations_path=tmp_path / "recommendations.json",
            promotion_decision_path=tmp_path / "promotion_decision.json",
        )

def test_build_optimization_summary_rejects_non_list_health_warnings(tmp_path) -> None:
    import pytest

    health_report_path = tmp_path / "health_report.json"
    health_report_path.write_text(
        json.dumps({"status": "warn", "warnings": "not-a-list"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="health_report.warnings must be a list"):
        build_optimization_summary(
            signal_facts_path=tmp_path / "signal_facts.jsonl",
            trade_outcomes_path=tmp_path / "trade_outcomes.jsonl",
            daily_metrics_path=tmp_path / "daily_metrics.json",
            health_report_path=health_report_path,
            recommendations_path=tmp_path / "recommendations.json",
            promotion_decision_path=tmp_path / "promotion_decision.json",
        )
