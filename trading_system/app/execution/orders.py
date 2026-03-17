from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal

from ..types import BJ, OrderIntent

OrderMode = Literal["paper", "dry-run", "live"]


def side_to_binance(side: str) -> str:
    return "BUY" if side == "LONG" else "SELL"


def build_entry_order_payload(order: OrderIntent) -> dict[str, Any]:
    return {
        "symbol": order.symbol,
        "side": side_to_binance(order.side),
        "type": "MARKET",
        "quantity": order.qty,
        "newClientOrderId": order.intent_id,
    }


def build_stop_order_payload(order: OrderIntent) -> dict[str, Any]:
    close_side = "SELL" if order.side == "LONG" else "BUY"
    return {
        "symbol": order.symbol,
        "side": close_side,
        "type": "STOP_MARKET",
        "stopPrice": order.stop_loss,
        "closePosition": "true",
        "workingType": "MARK_PRICE",
        "newClientOrderId": f"{order.intent_id}-sl",
    }


def build_take_profit_payload(order: OrderIntent) -> dict[str, Any] | None:
    if order.take_profit is None:
        return None
    close_side = "SELL" if order.side == "LONG" else "BUY"
    return {
        "symbol": order.symbol,
        "side": close_side,
        "type": "TAKE_PROFIT_MARKET",
        "stopPrice": order.take_profit,
        "closePosition": "true",
        "workingType": "MARK_PRICE",
        "newClientOrderId": f"{order.intent_id}-tp",
    }


def paper_fill(order: OrderIntent) -> dict[str, Any]:
    return {
        "mode": "paper",
        "ts_bj": datetime.now(BJ).isoformat(),
        "entry_order": build_entry_order_payload(order),
        "stop_order": build_stop_order_payload(order),
        "take_profit_order": build_take_profit_payload(order),
        "intent": asdict(order),
        "result": "FILLED",
    }


def dry_run_fill(order: OrderIntent) -> dict[str, Any]:
    return {
        "mode": "dry-run",
        "ts_bj": datetime.now(BJ).isoformat(),
        "entry_order": build_entry_order_payload(order),
        "stop_order": build_stop_order_payload(order),
        "take_profit_order": build_take_profit_payload(order),
        "intent": asdict(order),
        "result": "PREVIEW_ONLY",
    }
