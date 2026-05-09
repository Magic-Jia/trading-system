from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

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


def _parse_required_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _parse_risk_level(value: object, *, field_name: str) -> RiskLevel:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be one of: low, medium, high")
    risk = value.strip().lower()
    if risk not in _VALID_RISK_LEVELS:
        short_field_name = field_name.rsplit(".", maxsplit=1)[-1]
        raise ValueError(f"invalid {short_field_name}: {value} ({field_name})")
    return risk  # type: ignore[return-value]


def _parse_finite_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite number")
    return parsed


def _parse_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a bool")
    return value


def _parse_reason(value: object, *, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def load_llm_event_labels(path: str | Path) -> dict[tuple[datetime, str], LlmEventLabel]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("root payload must be an object")
    raw_labels = raw.get("labels", [])
    if not isinstance(raw_labels, list):
        raise ValueError("labels must be a list")

    labels: dict[tuple[datetime, str], LlmEventLabel] = {}
    for index, item in enumerate(raw_labels):
        if not isinstance(item, dict):
            raise ValueError(f"labels[{index}] must be an object")
        timestamp_field = f"labels[{index}].timestamp"
        raw_timestamp = item["timestamp"]
        if not isinstance(raw_timestamp, str):
            raise ValueError(f"{timestamp_field} must be a string")
        timestamp = _parse_timestamp(raw_timestamp, field_name=timestamp_field)
        symbol = _parse_required_string(item["symbol"], field_name=f"labels[{index}].symbol").upper()
        label = LlmEventLabel(
            timestamp=timestamp,
            symbol=symbol,
            sentiment_score=_parse_finite_number(
                item["sentiment_score"], field_name=f"labels[{index}].sentiment_score"
            ),
            event_risk=_parse_risk_level(item["event_risk"], field_name=f"labels[{index}].event_risk"),
            fomo_risk=_parse_risk_level(item["fomo_risk"], field_name=f"labels[{index}].fomo_risk"),
            allow_long=_parse_bool(item["allow_long"], field_name=f"labels[{index}].allow_long"),
            confidence=_parse_finite_number(item["confidence"], field_name=f"labels[{index}].confidence"),
            reason=_parse_reason(item.get("reason", ""), field_name=f"labels[{index}].reason"),
        )
        labels[(timestamp, symbol)] = label
    return labels
