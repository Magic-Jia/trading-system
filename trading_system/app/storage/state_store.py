from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..types import BJ, RuntimeState


@dataclass(slots=True)
class RuntimeStateV2(RuntimeState):
    latest_regime: dict[str, Any] = field(default_factory=dict)
    latest_universes: dict[str, Any] = field(default_factory=dict)
    latest_candidates: list[dict[str, Any]] = field(default_factory=list)
    latest_allocations: list[dict[str, Any]] = field(default_factory=list)
    latest_lifecycle: dict[str, dict[str, Any]] = field(default_factory=dict)


_RUNTIME_STATE_V2_FIELDS = {item.name for item in fields(RuntimeStateV2)}


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> RuntimeStateV2:
        if not self.path.exists():
            return RuntimeStateV2.empty()
        raw = json.loads(self.path.read_text())
        if not isinstance(raw, dict):
            return RuntimeStateV2.empty()

        normalized: dict[str, Any] = {key: value for key, value in raw.items() if key in _RUNTIME_STATE_V2_FIELDS}
        if "updated_at_bj" not in normalized:
            normalized["updated_at_bj"] = datetime.now(BJ).isoformat()
        return RuntimeStateV2(**normalized)

    def save(self, state: RuntimeState) -> None:
        state.updated_at_bj = datetime.now(BJ).isoformat()
        self.path.write_text(json.dumps(state.as_dict(), ensure_ascii=False, indent=2))

    def replace_management_suggestions(self, state: RuntimeState, suggestions: list[dict[str, Any]]) -> None:
        state.management_suggestions = suggestions

    def replace_management_action_previews(self, state: RuntimeState, previews: list[dict[str, Any]]) -> None:
        state.management_action_previews = previews

    def record_signal(self, state: RuntimeState, symbol: str, signal_id: str, cooldown_minutes: int) -> None:
        state.last_signal_ids[symbol] = signal_id
        until = datetime.now(BJ) + timedelta(minutes=cooldown_minutes)
        state.cooldowns[symbol] = until.isoformat()

    def in_cooldown(self, state: RuntimeState, symbol: str) -> bool:
        value = state.cooldowns.get(symbol)
        if not value:
            return False
        try:
            return datetime.fromisoformat(value) > datetime.now(BJ)
        except ValueError:
            return False

    def circuit_breaker_active(self, state: RuntimeState) -> bool:
        value = state.circuit_breaker_until
        if not value:
            return False
        try:
            return datetime.fromisoformat(value) > datetime.now(BJ)
        except ValueError:
            return False

    def set_circuit_breaker(self, state: RuntimeState, minutes: int, reason: str) -> None:
        until = datetime.now(BJ) + timedelta(minutes=minutes)
        state.circuit_breaker_until = until.isoformat()
        state.active_orders["__circuit_breaker__"] = {"reason": reason, "until": until.isoformat()}


def build_state_store(config: AppConfig) -> StateStore:
    return StateStore(config.state_file)
