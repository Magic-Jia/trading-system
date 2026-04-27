from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable, Literal

from trading_system.app.signals.trend_engine import generate_trend_candidates
from trading_system.app.types import EngineCandidate

from .llm_labels import LlmEventLabel, load_llm_event_labels
from .types import ExperimentParams

Decision = Literal["accepted", "rejected"]

_MEDIUM_EVENT_RISK_PENALTY = 0.15
_MEDIUM_FOMO_RISK_PENALTY = 0.05
_HIGH_FOMO_RISK_PENALTY = 0.15


@dataclass(frozen=True, slots=True)
class LlmTrendBreakoutCandidateRow:
    timestamp: datetime
    symbol: str
    setup_type: str
    technical_score: float
    sentiment_score: float | None
    final_score: float
    decision: Decision
    reasons: tuple[str, ...]
    event_risk: str | None = None
    fomo_risk: str | None = None
    label_confidence: float | None = None


def _row(
    *,
    timestamp: datetime,
    candidate: EngineCandidate,
    sentiment_score: float | None,
    final_score: float,
    decision: Decision,
    reasons: tuple[str, ...],
    label: LlmEventLabel | None,
) -> LlmTrendBreakoutCandidateRow:
    return LlmTrendBreakoutCandidateRow(
        timestamp=timestamp,
        symbol=candidate.symbol,
        setup_type=candidate.setup_type,
        technical_score=float(candidate.score),
        sentiment_score=sentiment_score,
        final_score=final_score,
        decision=decision,
        reasons=reasons,
        event_risk=label.event_risk if label is not None else None,
        fomo_risk=label.fomo_risk if label is not None else None,
        label_confidence=label.confidence if label is not None else None,
    )


def apply_llm_trend_breakout_filter(
    *,
    timestamp: datetime,
    candidate: EngineCandidate,
    label: LlmEventLabel | None,
    params: ExperimentParams,
) -> LlmTrendBreakoutCandidateRow:
    technical_score = float(candidate.score)
    if label is None:
        if params.require_llm_label:
            return _row(
                timestamp=timestamp,
                candidate=candidate,
                sentiment_score=None,
                final_score=technical_score,
                decision="rejected",
                reasons=("missing_llm_label",),
                label=None,
            )
        decision: Decision = "accepted" if technical_score >= params.minimum_final_score else "rejected"
        reasons = ("llm_label_not_required",) if decision == "accepted" else ("final_score_below_minimum",)
        return _row(
            timestamp=timestamp,
            candidate=candidate,
            sentiment_score=None,
            final_score=technical_score,
            decision=decision,
            reasons=reasons,
            label=None,
        )

    if label.confidence < params.minimum_label_confidence:
        return _row(
            timestamp=timestamp,
            candidate=candidate,
            sentiment_score=None,
            final_score=technical_score,
            decision="rejected",
            reasons=("label_confidence_below_minimum",),
            label=label,
        )
    if not label.allow_long:
        return _row(
            timestamp=timestamp,
            candidate=candidate,
            sentiment_score=label.sentiment_score,
            final_score=technical_score + label.sentiment_score,
            decision="rejected",
            reasons=("llm_disallows_long",),
            label=label,
        )
    if label.event_risk == "high":
        return _row(
            timestamp=timestamp,
            candidate=candidate,
            sentiment_score=label.sentiment_score,
            final_score=technical_score + label.sentiment_score,
            decision="rejected",
            reasons=("high_event_risk",),
            label=label,
        )
    if label.fomo_risk == "high" and params.reject_high_fomo:
        return _row(
            timestamp=timestamp,
            candidate=candidate,
            sentiment_score=label.sentiment_score,
            final_score=technical_score + label.sentiment_score,
            decision="rejected",
            reasons=("high_fomo_risk",),
            label=label,
        )

    final_score = technical_score + label.sentiment_score
    reasons: list[str] = []
    if label.event_risk == "medium":
        final_score -= _MEDIUM_EVENT_RISK_PENALTY
        reasons.append("medium_event_risk_penalty")
    if label.fomo_risk == "medium":
        final_score -= _MEDIUM_FOMO_RISK_PENALTY
        reasons.append("medium_fomo_risk_penalty")
    elif label.fomo_risk == "high":
        final_score -= _HIGH_FOMO_RISK_PENALTY
        reasons.append("high_fomo_risk_penalty")

    decision = "accepted" if final_score >= params.minimum_final_score else "rejected"
    if decision == "rejected":
        reasons.append("final_score_below_minimum")
    if not reasons:
        reasons.append("llm_filter_passed")

    return _row(
        timestamp=timestamp,
        candidate=candidate,
        sentiment_score=label.sentiment_score,
        final_score=final_score,
        decision=decision,
        reasons=tuple(reasons),
        label=label,
    )


def _serialize_candidate_row(row: LlmTrendBreakoutCandidateRow) -> dict[str, Any]:
    payload = asdict(row)
    payload["timestamp"] = row.timestamp.isoformat()
    payload["reasons"] = list(row.reasons)
    return payload


def _candidate_allowed(candidate: EngineCandidate, params: ExperimentParams) -> bool:
    if params.symbols and candidate.symbol.upper() not in params.symbols:
        return False
    if params.allowed_setup_types and candidate.setup_type.upper() not in params.allowed_setup_types:
        return False
    return True


def run_llm_trend_breakout_experiment(
    rows: Iterable[Any],
    *,
    params: ExperimentParams,
    labels: dict[tuple[datetime, str], LlmEventLabel] | None = None,
) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=lambda row: (row.timestamp, row.run_id))
    label_map = labels
    if label_map is None:
        if params.llm_label_path is None:
            label_map = {}
        else:
            label_map = load_llm_event_labels(params.llm_label_path)

    candidate_rows: list[dict[str, Any]] = []
    rejection_reasons: dict[str, int] = {}
    for row in ordered_rows:
        technical_candidates = [
            candidate
            for candidate in generate_trend_candidates(
                row.market,
                derivatives=row.derivatives,
                regime=None,
                entry_profile=params.entry_profile,
            )
            if _candidate_allowed(candidate, params)
        ]
        for candidate in technical_candidates:
            label = label_map.get((row.timestamp, candidate.symbol.upper()))
            scored = apply_llm_trend_breakout_filter(
                timestamp=row.timestamp,
                candidate=candidate,
                label=label,
                params=params,
            )
            candidate_rows.append(_serialize_candidate_row(scored))
            if scored.decision == "rejected":
                for reason in scored.reasons:
                    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    technical_candidate_count = len(candidate_rows)
    accepted_candidate_count = sum(1 for item in candidate_rows if item["decision"] == "accepted")
    rejected_candidate_count = technical_candidate_count - accepted_candidate_count
    acceptance_rate = accepted_candidate_count / technical_candidate_count if technical_candidate_count else 0.0
    return {
        "summary": {
            "snapshot_count": len(ordered_rows),
            "technical_candidate_count": technical_candidate_count,
            "accepted_candidate_count": accepted_candidate_count,
            "rejected_candidate_count": rejected_candidate_count,
            "acceptance_rate": acceptance_rate,
            "rejection_reasons": dict(sorted(rejection_reasons.items())),
        },
        "candidate_rows": candidate_rows,
    }
