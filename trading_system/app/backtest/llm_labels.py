from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

RiskLevel = Literal["low", "medium", "high"]
_VALID_RISK_LEVELS = {"low", "medium", "high"}


@dataclass(frozen=True, slots=True)
class LlmEventLabel:
    timestamp: datetime
    symbol: str
    sentiment_score: float
    event_risk: RiskLevel
    fomo_risk: RiskLevel
    allow_long: bool
    confidence: float
    reason: str = ""


def _parse_timestamp(value: str, *, field_name: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid timestamp for {field_name}: {value}") from exc


def _parse_risk_level(value: Any, *, field_name: str) -> RiskLevel:
    risk = str(value).strip().lower()
    if risk not in _VALID_RISK_LEVELS:
        raise ValueError(f"invalid {field_name}: {value}")
    return risk  # type: ignore[return-value]


def load_llm_event_labels(path: str | Path) -> dict[tuple[datetime, str], LlmEventLabel]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_labels = raw.get("labels", [])
    if not isinstance(raw_labels, list):
        raise ValueError("labels must be a list")

    labels: dict[tuple[datetime, str], LlmEventLabel] = {}
    for index, item in enumerate(raw_labels):
        if not isinstance(item, dict):
            raise ValueError(f"labels[{index}] must be an object")
        timestamp = _parse_timestamp(str(item["timestamp"]), field_name=f"labels[{index}].timestamp")
        symbol = str(item["symbol"]).strip().upper()
        label = LlmEventLabel(
            timestamp=timestamp,
            symbol=symbol,
            sentiment_score=float(item["sentiment_score"]),
            event_risk=_parse_risk_level(item["event_risk"], field_name="event_risk"),
            fomo_risk=_parse_risk_level(item["fomo_risk"], field_name="fomo_risk"),
            allow_long=bool(item["allow_long"]),
            confidence=float(item["confidence"]),
            reason=str(item.get("reason", "")),
        )
        labels[(timestamp, symbol)] = label
    return labels
