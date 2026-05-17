from __future__ import annotations

import json
from pathlib import Path

from trading_system.app.reporting.promotion_readiness_scorecard_trend import (
    build_promotion_readiness_scorecard_trend_report,
    write_promotion_readiness_scorecard_trend_report,
)


def _scorecard(
    generated_at: str,
    *,
    decision: str = "pass",
    score: float = 92.0,
    blockers: list[dict[str, str]] | None = None,
    scorecard_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "promotion_readiness_scorecard.v1",
        "mode": "simulated_live",
        "generated_at": generated_at,
        "decision": decision,
        "scores": {"promotion_readiness": score},
        "blockers": blockers or [],
    }
    if scorecard_id is not None:
        payload["scorecard_id"] = scorecard_id
    return payload


def test_trend_report_passes_when_scorecards_are_ordered_and_stable() -> None:
    report = build_promotion_readiness_scorecard_trend_report(
        scorecards=[
            _scorecard("2026-05-14T10:00:00Z", score=90.0, scorecard_id="scorecard-1"),
            _scorecard("2026-05-15T10:00:00Z", score=91.0, scorecard_id="scorecard-2"),
            _scorecard("2026-05-16T10:00:00Z", score=92.0, scorecard_id="scorecard-3"),
        ],
        generated_at="2026-05-16T10:05:00Z",
        min_sample_count=3,
    )

    assert report["schema_version"] == "promotion_readiness_scorecard_trend.v1"
    assert report["mode"] == "simulated_live"
    assert report["generated_at"] == "2026-05-16T10:05:00Z"
    assert report["decision"] == "pass"
    assert report["reasons"] == []
    assert report["checks"] == {
        "sample_window_sufficient": True,
        "scorecards_well_formed": True,
        "generated_at_monotonic": True,
        "scorecard_identities_unique": True,
        "score_deterioration_within_threshold": True,
        "repeated_blockers_absent": True,
    }
    assert report["score_trend"]["first"] == 90.0
    assert report["score_trend"]["latest"] == 92.0
    assert report["score_trend"]["delta"] == 2.0
    assert report["scorecards"] == [
        {"generated_at": "2026-05-14T10:00:00Z", "identity": "scorecard-1", "decision": "pass", "score": 90.0},
        {"generated_at": "2026-05-15T10:00:00Z", "identity": "scorecard-2", "decision": "pass", "score": 91.0},
        {"generated_at": "2026-05-16T10:00:00Z", "identity": "scorecard-3", "decision": "pass", "score": 92.0},
    ]


def test_trend_report_holds_for_score_deterioration_and_repeated_blockers() -> None:
    repeated = {
        "component": "cross_source_parity",
        "reason_code": "parity_drift",
        "severity": "hold",
        "detail": "max_parity_drift_bps 4.5 > allowed 2.0",
    }

    report = build_promotion_readiness_scorecard_trend_report(
        scorecards=[
            _scorecard("2026-05-14T10:00:00Z", score=94.0),
            _scorecard("2026-05-15T10:00:00Z", decision="hold", score=87.0, blockers=[repeated]),
            _scorecard("2026-05-16T10:00:00Z", decision="hold", score=80.0, blockers=[repeated]),
        ],
        generated_at="2026-05-16T10:05:00Z",
        min_sample_count=3,
        max_score_deterioration=10.0,
        repeated_blocker_min_count=2,
    )

    assert report["decision"] == "hold"
    assert report["reasons"] == ["score_deterioration", "repeated_blocker"]
    assert report["checks"]["score_deterioration_within_threshold"] is False
    assert report["checks"]["repeated_blockers_absent"] is False
    assert report["score_trend"]["delta"] == -14.0
    assert report["score_trend"]["deteriorated"] is True
    assert report["repeated_blockers"] == [
        {
            "component": "cross_source_parity",
            "reason_code": "parity_drift",
            "count": 2,
            "decisions": ["hold"],
            "first_generated_at": "2026-05-15T10:00:00Z",
            "latest_generated_at": "2026-05-16T10:00:00Z",
        }
    ]


def test_trend_report_rejects_non_monotonic_duplicate_and_malformed_scorecards() -> None:
    report = build_promotion_readiness_scorecard_trend_report(
        scorecards=[
            _scorecard("2026-05-15T10:00:00Z", score=90.0, scorecard_id="dup"),
            _scorecard("2026-05-14T10:00:00Z", score="89.0", scorecard_id="dup"),  # type: ignore[arg-type]
            _scorecard("2026-05-14T10:00:00Z", score=True),  # type: ignore[arg-type]
        ],
        generated_at="2026-05-16T10:05:00Z",
        min_sample_count=2,
    )

    assert report["decision"] == "reject"
    assert report["reasons"] == [
        "malformed_scorecard",
        "timestamp_ordering",
        "duplicate_scorecard",
    ]
    assert report["checks"]["scorecards_well_formed"] is False
    assert report["checks"]["generated_at_monotonic"] is False
    assert report["checks"]["scorecard_identities_unique"] is False
    assert report["duplicate_identities"] == ["2026-05-14T10:00:00Z", "dup"]
    assert report["scorecards"][1]["malformed_inputs"] == ["scores.promotion_readiness_not_finite_number"]
    assert report["scorecards"][2]["malformed_inputs"] == ["scores.promotion_readiness_not_finite_number"]


def test_trend_report_holds_for_insufficient_sample_window() -> None:
    report = build_promotion_readiness_scorecard_trend_report(
        scorecards=[_scorecard("2026-05-16T10:00:00Z", score=92.0)],
        generated_at="2026-05-16T10:05:00Z",
        min_sample_count=2,
    )

    assert report["decision"] == "hold"
    assert report["reasons"] == ["insufficient_sample_window"]
    assert report["checks"]["sample_window_sufficient"] is False
    assert report["sample_window"]["observed_count"] == 1
    assert report["sample_window"]["required_count"] == 2


def test_writes_trend_report_as_machine_readable_json(tmp_path: Path) -> None:
    output_path = tmp_path / "promotion_readiness_scorecard_trend.json"

    payload = write_promotion_readiness_scorecard_trend_report(
        output_path,
        scorecards=[
            _scorecard("2026-05-15T10:00:00Z", score=90.0),
            _scorecard("2026-05-16T10:00:00Z", score=91.0),
        ],
        generated_at="2026-05-16T10:05:00Z",
        min_sample_count=2,
    )

    assert json.loads(output_path.read_text(encoding="utf-8")) == payload
