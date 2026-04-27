from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_system.app.backtest.types import ExperimentParams
from trading_system.app.types import EngineCandidate


def _candidate(*, score: float = 0.80) -> EngineCandidate:
    return EngineCandidate(
        engine="trend",
        setup_type="BREAKOUT_CONTINUATION",
        symbol="SOLUSDT",
        side="LONG",
        score=score,
        stop_loss=95.0,
    )


def _label(
    *,
    sentiment_score: float = 0.10,
    event_risk: str = "low",
    fomo_risk: str = "low",
    allow_long: bool = True,
    confidence: float = 0.80,
):
    from trading_system.app.backtest.llm_labels import LlmEventLabel

    return LlmEventLabel(
        timestamp=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        symbol="SOLUSDT",
        sentiment_score=sentiment_score,
        event_risk=event_risk,
        fomo_risk=fomo_risk,
        allow_long=allow_long,
        confidence=confidence,
    )


def _params(**overrides: object) -> ExperimentParams:
    values = {
        "minimum_final_score": 0.75,
        "minimum_label_confidence": 0.5,
    }
    values.update(overrides)
    return ExperimentParams(**values)


def test_apply_llm_filter_rejects_missing_required_label() -> None:
    from trading_system.app.backtest.llm_trend_breakout import apply_llm_trend_breakout_filter

    row = apply_llm_trend_breakout_filter(
        timestamp=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        candidate=_candidate(),
        label=None,
        params=_params(require_llm_label=True),
    )

    assert row.decision == "rejected"
    assert row.final_score == pytest.approx(0.80)
    assert row.reasons == ("missing_llm_label",)


def test_apply_llm_filter_accepts_missing_optional_label_using_technical_score() -> None:
    from trading_system.app.backtest.llm_trend_breakout import apply_llm_trend_breakout_filter

    row = apply_llm_trend_breakout_filter(
        timestamp=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        candidate=_candidate(score=0.78),
        label=None,
        params=_params(require_llm_label=False),
    )

    assert row.decision == "accepted"
    assert row.final_score == pytest.approx(0.78)
    assert row.sentiment_score is None
    assert row.reasons == ("llm_label_not_required",)


def test_apply_llm_filter_rejects_when_label_disallows_long() -> None:
    from trading_system.app.backtest.llm_trend_breakout import apply_llm_trend_breakout_filter

    row = apply_llm_trend_breakout_filter(
        timestamp=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        candidate=_candidate(),
        label=_label(allow_long=False),
        params=_params(),
    )

    assert row.decision == "rejected"
    assert "llm_disallows_long" in row.reasons


def test_apply_llm_filter_rejects_high_event_risk() -> None:
    from trading_system.app.backtest.llm_trend_breakout import apply_llm_trend_breakout_filter

    row = apply_llm_trend_breakout_filter(
        timestamp=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        candidate=_candidate(),
        label=_label(event_risk="high"),
        params=_params(),
    )

    assert row.decision == "rejected"
    assert "high_event_risk" in row.reasons


def test_apply_llm_filter_penalizes_medium_event_risk() -> None:
    from trading_system.app.backtest.llm_trend_breakout import apply_llm_trend_breakout_filter

    row = apply_llm_trend_breakout_filter(
        timestamp=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        candidate=_candidate(score=0.80),
        label=_label(sentiment_score=0.10, event_risk="medium"),
        params=_params(),
    )

    assert row.decision == "accepted"
    assert row.final_score == pytest.approx(0.75)
    assert "medium_event_risk_penalty" in row.reasons


def test_apply_llm_filter_penalizes_high_fomo_risk() -> None:
    from trading_system.app.backtest.llm_trend_breakout import apply_llm_trend_breakout_filter

    row = apply_llm_trend_breakout_filter(
        timestamp=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        candidate=_candidate(score=0.85),
        label=_label(sentiment_score=0.10, fomo_risk="high"),
        params=_params(),
    )

    assert row.decision == "accepted"
    assert row.final_score == pytest.approx(0.80)
    assert "high_fomo_risk_penalty" in row.reasons


def test_apply_llm_filter_rejects_high_fomo_risk_in_strict_mode() -> None:
    from trading_system.app.backtest.llm_trend_breakout import apply_llm_trend_breakout_filter

    row = apply_llm_trend_breakout_filter(
        timestamp=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        candidate=_candidate(score=0.85),
        label=_label(fomo_risk="high"),
        params=_params(reject_high_fomo=True),
    )

    assert row.decision == "rejected"
    assert "high_fomo_risk" in row.reasons


def test_apply_llm_filter_boosts_positive_sentiment_and_emits_final_score() -> None:
    from trading_system.app.backtest.llm_trend_breakout import apply_llm_trend_breakout_filter

    row = apply_llm_trend_breakout_filter(
        timestamp=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        candidate=_candidate(score=0.70),
        label=_label(sentiment_score=0.12),
        params=_params(),
    )

    assert row.decision == "accepted"
    assert row.technical_score == pytest.approx(0.70)
    assert row.sentiment_score == pytest.approx(0.12)
    assert row.final_score == pytest.approx(0.82)


def test_apply_llm_filter_rejects_low_label_confidence() -> None:
    from trading_system.app.backtest.llm_trend_breakout import apply_llm_trend_breakout_filter

    row = apply_llm_trend_breakout_filter(
        timestamp=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        candidate=_candidate(score=0.90),
        label=_label(confidence=0.49),
        params=_params(minimum_label_confidence=0.5),
    )

    assert row.decision == "rejected"
    assert row.final_score == pytest.approx(0.90)
    assert row.reasons == ("label_confidence_below_minimum",)


def test_load_llm_event_labels_keys_by_timestamp_and_uppercase_symbol(fixture_dir: Path) -> None:
    from trading_system.app.backtest.llm_labels import LlmEventLabel, load_llm_event_labels

    labels = load_llm_event_labels(fixture_dir / "backtest" / "llm_labels" / "sample_labels.json")

    key = (datetime(2026, 1, 15, 10, 0, tzinfo=UTC), "SOLUSDT")
    assert labels[key] == LlmEventLabel(
        timestamp=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        symbol="SOLUSDT",
        sentiment_score=0.35,
        event_risk="low",
        fomo_risk="medium",
        allow_long=True,
        confidence=0.72,
        reason="Positive ecosystem news; no major negative event found.",
    )


def test_load_llm_event_labels_defaults_missing_reason_to_empty_string(fixture_dir: Path) -> None:
    from trading_system.app.backtest.llm_labels import load_llm_event_labels

    labels = load_llm_event_labels(fixture_dir / "backtest" / "llm_labels" / "sample_labels.json")

    label = labels[(datetime(2026, 1, 15, 11, 0, tzinfo=UTC), "BTCUSDT")]
    assert label.reason == ""


def test_load_llm_event_labels_rejects_invalid_event_risk(tmp_path: Path) -> None:
    from trading_system.app.backtest.llm_labels import load_llm_event_labels

    path = tmp_path / "labels.json"
    path.write_text(
        """
        {
          "labels": [
            {
              "timestamp": "2026-01-15T10:00:00Z",
              "symbol": "SOLUSDT",
              "sentiment_score": 0.1,
              "event_risk": "extreme",
              "fomo_risk": "low",
              "allow_long": true,
              "confidence": 0.8
            }
          ]
        }
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid event_risk"):
        load_llm_event_labels(path)


def test_load_llm_event_labels_rejects_invalid_fomo_risk(tmp_path: Path) -> None:
    from trading_system.app.backtest.llm_labels import load_llm_event_labels

    path = tmp_path / "labels.json"
    path.write_text(
        """
        {
          "labels": [
            {
              "timestamp": "2026-01-15T10:00:00Z",
              "symbol": "SOLUSDT",
              "sentiment_score": 0.1,
              "event_risk": "low",
              "fomo_risk": "panic",
              "allow_long": true,
              "confidence": 0.8
            }
          ]
        }
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid fomo_risk"):
        load_llm_event_labels(path)

def test_run_llm_trend_breakout_experiment_filters_candidates_and_summarizes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from trading_system.app.backtest.llm_labels import LlmEventLabel
    from trading_system.app.backtest.llm_trend_breakout import run_llm_trend_breakout_experiment
    from trading_system.app.backtest.types import DatasetSnapshotRow

    timestamp = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    rows = [
        DatasetSnapshotRow(
            timestamp=timestamp,
            run_id="run-1",
            market={"symbols": {}},
            derivatives=[],
        )
    ]
    params = _params(
        llm_label_path=str(tmp_path / "labels.json"),
        symbols=("SOLUSDT",),
        allowed_setup_types=("BREAKOUT_CONTINUATION",),
        entry_profile="scout",
    )
    labels = {
        (timestamp, "SOLUSDT"): LlmEventLabel(
            timestamp=timestamp,
            symbol="SOLUSDT",
            sentiment_score=0.10,
            event_risk="low",
            fomo_risk="medium",
            allow_long=True,
            confidence=0.80,
        )
    }

    def fake_generate_trend_candidates(market, *, derivatives, regime, entry_profile):
        assert entry_profile == "scout"
        return [
            _candidate(score=0.80),
            EngineCandidate(
                engine="trend",
                setup_type="PULLBACK",
                symbol="ETHUSDT",
                side="LONG",
                score=0.95,
                stop_loss=90.0,
            ),
        ]

    monkeypatch.setattr(
        "trading_system.app.backtest.llm_trend_breakout.generate_trend_candidates",
        fake_generate_trend_candidates,
    )

    result = run_llm_trend_breakout_experiment(rows, params=params, labels=labels)

    assert result["summary"] == {
        "snapshot_count": 1,
        "technical_candidate_count": 1,
        "accepted_candidate_count": 1,
        "rejected_candidate_count": 0,
        "acceptance_rate": 1.0,
        "rejection_reasons": {},
    }
    assert result["candidate_rows"] == [
        {
            "timestamp": timestamp.isoformat(),
            "symbol": "SOLUSDT",
            "setup_type": "BREAKOUT_CONTINUATION",
            "technical_score": 0.8,
            "sentiment_score": 0.1,
            "final_score": pytest.approx(0.85),
            "decision": "accepted",
            "reasons": ["medium_fomo_risk_penalty"],
            "event_risk": "low",
            "fomo_risk": "medium",
            "label_confidence": 0.8,
        }
    ]


def test_run_llm_trend_breakout_experiment_counts_rejection_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_system.app.backtest.llm_trend_breakout import run_llm_trend_breakout_experiment
    from trading_system.app.backtest.types import DatasetSnapshotRow

    timestamp = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    rows = [DatasetSnapshotRow(timestamp=timestamp, run_id="run-1", market={}, derivatives=[])]

    monkeypatch.setattr(
        "trading_system.app.backtest.llm_trend_breakout.generate_trend_candidates",
        lambda market, *, derivatives, regime, entry_profile: [_candidate(score=0.80)],
    )

    result = run_llm_trend_breakout_experiment(rows, params=_params(require_llm_label=True), labels={})

    assert result["summary"]["technical_candidate_count"] == 1
    assert result["summary"]["accepted_candidate_count"] == 0
    assert result["summary"]["rejected_candidate_count"] == 1
    assert result["summary"]["acceptance_rate"] == 0.0
    assert result["summary"]["rejection_reasons"] == {"missing_llm_label": 1}
    assert result["candidate_rows"][0]["decision"] == "rejected"

