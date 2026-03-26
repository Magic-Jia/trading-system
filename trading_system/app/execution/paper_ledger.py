from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from ..types import BJ, OrderIntent


class PaperLedger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_fill(
        self,
        order: OrderIntent,
        result: dict[str, Any],
        position_update: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "event_type": "paper_fill",
            "recorded_at_bj": datetime.now(BJ).isoformat(),
            "intent_id": order.intent_id,
            "signal_id": order.signal_id,
            "symbol": order.symbol,
            "order": asdict(order),
            "result": result,
            "position_update": position_update,
            "replay_result": {
                "status": str(order.status).upper(),
                "intent_id": order.intent_id,
            },
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def load_replay_result(self, intent_id: str) -> dict[str, str] | None:
        event = self.load_event(intent_id)
        if event is None:
            return None

        replay_result = event.get("replay_result")
        if not isinstance(replay_result, dict):
            return None

        status = replay_result.get("status")
        stored_intent_id = replay_result.get("intent_id")
        if not isinstance(status, str) or not isinstance(stored_intent_id, str):
            return None

        return {
            "status": status,
            "intent_id": stored_intent_id,
        }

    def load_event(self, intent_id: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None

        matched_event: dict[str, Any] | None = None
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                payload = line.strip()
                if not payload:
                    continue
                try:
                    raw = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                if raw.get("intent_id") != intent_id:
                    continue
                matched_event = raw
        return matched_event
