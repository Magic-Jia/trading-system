from __future__ import annotations

import json

from trading_system.app.paper_optimization.recommendations import generate_recommendations, write_recommendations


def test_generate_recommendations_suppresses_when_health_report_is_not_ok() -> None:
    payload = generate_recommendations(
        daily_metrics={"recorded_at_bj": "2026-04-24T12:00:00+08:00", "trade_outcome_count": 12, "unrealized_pnl_total": -1.2},
        health_report={"status": "warn", "warnings": [{"code": "position_not_tracked", "count": 2}]},
        recorded_at_bj="2026-04-24T12:05:00+08:00",
    )

    assert payload["recommendation_count"] == 0
    assert payload["recommendations"] == []
    assert payload["suppressed"] == [
        {
            "reason": "health_not_ok",
            "message": "health report is not clean enough to emit configuration recommendations",
            "health_status": "warn",
            "warning_count": 1,
        }
    ]


def test_generate_recommendations_suppresses_when_sample_is_too_small() -> None:
    payload = generate_recommendations(
        daily_metrics={"recorded_at_bj": "2026-04-24T12:00:00+08:00", "trade_outcome_count": 3, "unrealized_pnl_total": -2.0},
        health_report={"status": "ok", "warnings": []},
        recorded_at_bj="2026-04-24T12:05:00+08:00",
    )

    assert payload["recommendation_count"] == 0
    assert payload["recommendations"] == []
    assert payload["alerts"] == []
    assert payload["suppressed"] == [
        {
            "reason": "low_sample",
            "message": "trade_outcome_count=3 is below the minimum sample threshold 5",
            "minimum_trade_outcome_count": 5,
            "trade_outcome_count": 3,
            "consecutive_count": 1,
        }
    ]


def test_generate_recommendations_warns_when_low_sample_repeats() -> None:
    payload = generate_recommendations(
        daily_metrics={"recorded_at_bj": "2026-04-24T12:00:00+08:00", "trade_outcome_count": 0, "unrealized_pnl_total": 0.0},
        health_report={"status": "ok", "warnings": []},
        previous_recommendations={
            "suppressed": [
                {
                    "reason": "low_sample",
                    "minimum_trade_outcome_count": 5,
                    "trade_outcome_count": 0,
                    "consecutive_count": 2,
                }
            ]
        },
        recorded_at_bj="2026-04-24T12:05:00+08:00",
    )

    assert payload["suppressed"][0]["reason"] == "low_sample"
    assert payload["suppressed"][0]["consecutive_count"] == 3
    assert payload["alerts"] == [
        {
            "code": "consecutive_low_sample",
            "severity": "warning",
            "message": "paper optimization has been suppressed by low_sample for 3 consecutive runs",
            "consecutive_count": 3,
            "minimum_trade_outcome_count": 5,
            "trade_outcome_count": 0,
        }
    ]


def test_generate_recommendations_emits_portfolio_and_engine_actions_for_supported_losses() -> None:
    payload = generate_recommendations(
        daily_metrics={
            "recorded_at_bj": "2026-04-24T12:00:00+08:00",
            "trade_outcome_count": 8,
            "unrealized_pnl_total": -0.8,
            "open_count": 1,
            "position_not_tracked_count": 0,
            "by_engine": {
                "trend": {
                    "trade_outcome_count": 4,
                    "unrealized_pnl_total": -0.35,
                    "position_not_tracked_count": 0,
                },
                "rotation": {
                    "trade_outcome_count": 3,
                    "unrealized_pnl_total": -0.28,
                    "position_not_tracked_count": 1,
                },
                "short": {
                    "trade_outcome_count": 3,
                    "unrealized_pnl_total": -0.40,
                    "position_not_tracked_count": 0,
                },
            },
        },
        health_report={"status": "ok", "warnings": []},
        previous_recommendations={"recommendations": [{"id": "lower-total-risk-budget"}]},
        recorded_at_bj="2026-04-24T12:05:00+08:00",
    )

    assert payload["recommendation_count"] == 3
    assert [item["id"] for item in payload["recommendations"]] == [
        "lower-total-risk-budget",
        "reduce-trend-bucket-weight",
        "reduce-rotation-bucket-weight",
    ]
    assert payload["recommendations"][0]["is_repeat"] is True
    assert payload["recommendations"][1]["target"]["config_key"] == "TRADING_ALLOCATOR_TREND_BUCKET_WEIGHT"
    assert payload["recommendations"][1]["proposed_value"] == 0.525
    assert payload["recommendations"][2]["target"]["config_key"] == "TRADING_ALLOCATOR_ROTATION_BUCKET_WEIGHT"
    assert payload["recommendations"][2]["proposed_value"] == 0.225
    assert payload["suppressed"] == []


def test_write_recommendations_persists_json_payload(tmp_path) -> None:
    daily_metrics_path = tmp_path / "daily_metrics.json"
    health_report_path = tmp_path / "health_report.json"
    previous_path = tmp_path / "previous.json"
    output_path = tmp_path / "recommendations.json"
    daily_metrics_path.write_text(
        json.dumps({"recorded_at_bj": "2026-04-24T12:00:00+08:00", "trade_outcome_count": 7, "unrealized_pnl_total": -0.7, "by_engine": {}}),
        encoding="utf-8",
    )
    health_report_path.write_text(json.dumps({"status": "ok", "warnings": []}), encoding="utf-8")
    previous_path.write_text(json.dumps({"recommendations": [{"id": "lower-total-risk-budget"}]}), encoding="utf-8")

    payload = write_recommendations(
        daily_metrics_path=daily_metrics_path,
        health_report_path=health_report_path,
        recommendations_path=output_path,
        previous_recommendations_path=previous_path,
        recorded_at_bj="2026-04-24T12:05:00+08:00",
    )

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written == payload
    assert written["recorded_at_bj"] == "2026-04-24T12:05:00+08:00"
    assert written["recommendation_count"] == 1
    assert written["recommendations"][0]["id"] == "lower-total-risk-budget"
    assert written["recommendations"][0]["is_repeat"] is True

def test_generate_recommendations_rejects_boolean_trade_outcome_count() -> None:
    import pytest

    with pytest.raises(ValueError, match="daily_metrics.trade_outcome_count must be numeric"):
        generate_recommendations(
            daily_metrics={
                "recorded_at_bj": "2026-04-24T12:00:00+08:00",
                "trade_outcome_count": True,
                "unrealized_pnl_total": -1.0,
            },
            health_report={"status": "ok", "warnings": []},
            recorded_at_bj="2026-04-24T12:05:00+08:00",
        )


def test_generate_recommendations_rejects_invalid_numeric_strings() -> None:
    import pytest

    with pytest.raises(ValueError, match="daily_metrics.unrealized_pnl_total must be numeric"):
        generate_recommendations(
            daily_metrics={
                "recorded_at_bj": "2026-04-24T12:00:00+08:00",
                "trade_outcome_count": 8,
                "unrealized_pnl_total": "not-a-number",
            },
            health_report={"status": "ok", "warnings": []},
            recorded_at_bj="2026-04-24T12:05:00+08:00",
        )

def test_generate_recommendations_rejects_invalid_engine_bucket_numeric_strings() -> None:
    import pytest

    with pytest.raises(ValueError, match="by_engine.trend.trade_outcome_count must be numeric"):
        generate_recommendations(
            daily_metrics={
                "recorded_at_bj": "2026-04-24T12:00:00+08:00",
                "trade_outcome_count": 8,
                "unrealized_pnl_total": -0.8,
                "by_engine": {
                    "trend": {
                        "trade_outcome_count": "many",
                        "unrealized_pnl_total": -0.35,
                    }
                },
            },
            health_report={"status": "ok", "warnings": []},
            recorded_at_bj="2026-04-24T12:05:00+08:00",
        )
