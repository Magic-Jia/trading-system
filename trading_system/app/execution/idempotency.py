from __future__ import annotations

import hashlib

from ..types import OrderIntent, RuntimeState, TradeSignal


def signal_fingerprint(signal: TradeSignal) -> str:
    raw = "|".join(
        [
            signal.signal_id,
            signal.symbol,
            signal.side,
            f"{signal.entry_price:.8f}",
            f"{signal.stop_loss:.8f}",
            signal.timeframe,
            signal.source,
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def intent_id(signal: TradeSignal) -> str:
    return f"intent-{signal_fingerprint(signal)}"


def already_processed(state: RuntimeState, signal: TradeSignal) -> bool:
    fp = signal_fingerprint(signal)
    return state.last_signal_ids.get(signal.symbol) == fp


def mark_processed(state: RuntimeState, signal: TradeSignal) -> str:
    fp = signal_fingerprint(signal)
    state.last_signal_ids[signal.symbol] = fp
    return fp


def replay_processed_execution(state: RuntimeState, signal: TradeSignal) -> dict[str, str] | None:
    existing_intent_id = intent_id(signal)
    active = state.active_orders.get(existing_intent_id)
    if isinstance(active, dict):
        status = str(active.get("status", "")).upper()
        if status:
            return {"status": status, "intent_id": existing_intent_id}

    position = state.positions.get(signal.symbol)
    if isinstance(position, dict) and position.get("intent_id") == existing_intent_id:
        status = str(position.get("status", "FILLED")).upper()
        return {"status": status, "intent_id": existing_intent_id}

    return None


def bind_active_order(state: RuntimeState, order: OrderIntent) -> None:
    state.active_orders[order.intent_id] = {
        "signal_id": order.signal_id,
        "symbol": order.symbol,
        "side": order.side,
        "qty": order.qty,
        "status": order.status,
    }
