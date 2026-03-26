from __future__ import annotations

from typing import Any

from ..portfolio.positions import apply_executed_intent
from ..types import OrderIntent, RuntimeState
from .idempotency import bind_active_order
from .orders import paper_fill
from .paper_ledger import PaperLedger


class PaperExecutor:
    def __init__(self, ledger: PaperLedger):
        self.ledger = ledger

    def execute(self, order: OrderIntent, state: RuntimeState) -> dict[str, Any]:
        order.status = "FILLED"
        result = paper_fill(order)
        bind_active_order(state, order)
        position_update = apply_executed_intent(state, order)
        ledger_event = self.ledger.record_fill(order, result, position_update)

        return {
            **result,
            "ledger_event": {
                "event_type": ledger_event["event_type"],
                "intent_id": ledger_event["intent_id"],
                "recorded_at_bj": ledger_event["recorded_at_bj"],
            },
            "position_update": position_update,
        }
