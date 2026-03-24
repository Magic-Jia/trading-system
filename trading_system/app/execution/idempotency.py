from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..portfolio.positions import apply_executed_intent
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


def _load_logged_order(intent_id: str, execution_log_path: Path) -> OrderIntent | None:
    if not execution_log_path.exists():
        return None

    matched_order: OrderIntent | None = None
    with execution_log_path.open(encoding="utf-8") as handle:
        for line in handle:
            payload = line.strip()
            if not payload:
                continue
            try:
                raw = json.loads(payload)
            except json.JSONDecodeError:
                continue
            order = raw.get("order")
            if not isinstance(order, dict) or order.get("intent_id") != intent_id:
                continue
            try:
                matched_order = OrderIntent(**order)
            except TypeError:
                continue

    return matched_order


def replay_processed_execution(
    state: RuntimeState,
    signal: TradeSignal,
    execution_log_path: Path | None = None,
) -> dict[str, str] | None:
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

    if execution_log_path is None:
        return None

    logged_order = _load_logged_order(existing_intent_id, execution_log_path)
    if logged_order is None:
        return None

    bind_active_order(state, logged_order)
    apply_executed_intent(state, logged_order)
    return {"status": logged_order.status.upper(), "intent_id": existing_intent_id}


def bind_active_order(state: RuntimeState, order: OrderIntent) -> None:
    state.active_orders[order.intent_id] = {
        "signal_id": order.signal_id,
        "symbol": order.symbol,
        "side": order.side,
        "qty": order.qty,
        "status": order.status,
    }
