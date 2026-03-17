from __future__ import annotations

from typing import Any

from ..types import ManagementActionIntent, Side

PROTECTIVE_ORDER_TYPES = {
    "STOP",
    "STOP_MARKET",
    "TAKE_PROFIT",
    "TAKE_PROFIT_MARKET",
    "TRAILING_STOP_MARKET",
}
STOP_ORDER_TYPES = {"STOP", "STOP_MARKET", "TRAILING_STOP_MARKET"}


def _close_side_to_binance(side: Side) -> str:
    return "SELL" if side == "LONG" else "BUY"


def _trueish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() == "true"


def _protective_order_sort_key(order: dict[str, Any]) -> tuple[int, int, str]:
    order_id = order.get("orderId")
    try:
        numeric_order_id = int(order_id)
    except (TypeError, ValueError):
        numeric_order_id = -1
    update_time = order.get("updateTime", order.get("time", 0))
    try:
        numeric_update_time = int(update_time)
    except (TypeError, ValueError):
        numeric_update_time = 0
    client_order_id = str(order.get("clientOrderId", ""))
    return (numeric_update_time, numeric_order_id, client_order_id)


def query_open_protective_orders(symbol: str, open_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for order in open_orders:
        if order.get("symbol") != symbol:
            continue
        order_type = str(order.get("type", order.get("origType", ""))).upper()
        if order_type not in PROTECTIVE_ORDER_TYPES:
            continue
        matches.append(
            {
                "symbol": symbol,
                "orderId": order.get("orderId"),
                "clientOrderId": order.get("clientOrderId"),
                "side": order.get("side"),
                "type": order.get("type", order.get("origType")),
                "status": order.get("status"),
                "stopPrice": order.get("stopPrice"),
                "price": order.get("price"),
                "origQty": order.get("origQty"),
                "executedQty": order.get("executedQty"),
                "reduceOnly": _trueish(order.get("reduceOnly")),
                "closePosition": _trueish(order.get("closePosition")),
                "workingType": order.get("workingType"),
                "updateTime": order.get("updateTime", order.get("time")),
            }
        )
    return sorted(matches, key=_protective_order_sort_key)


def prepare_stop_loss_update_request(
    intent: ManagementActionIntent,
    open_protective_orders: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current_orders = open_protective_orders or []
    cancel_targets = [
        {
            "orderId": order.get("orderId"),
            "clientOrderId": order.get("clientOrderId"),
            "type": order.get("type"),
        }
        for order in current_orders
        if str(order.get("type", "")).upper() in STOP_ORDER_TYPES
    ]
    return {
        "op": "upsert_protective_stop",
        "preview_only": True,
        "symbol": intent.symbol,
        "action": intent.action,
        "cancel_existing_stop_orders": cancel_targets,
        "create_order": {
            "symbol": intent.symbol,
            "side": _close_side_to_binance(intent.side),
            "type": "STOP_MARKET",
            "stopPrice": intent.stop_loss,
            "closePosition": "true",
            "workingType": "MARK_PRICE",
            "newClientOrderId": f"{intent.intent_id}-sl-update",
        },
    }


def prepare_reduce_only_close_request(intent: ManagementActionIntent) -> dict[str, Any]:
    return {
        "op": "reduce_only_close",
        "preview_only": True,
        "symbol": intent.symbol,
        "action": intent.action,
        "create_order": {
            "symbol": intent.symbol,
            "side": _close_side_to_binance(intent.side),
            "type": "MARKET",
            "quantity": intent.qty,
            "reduceOnly": "true",
            "newClientOrderId": f"{intent.intent_id}-close",
        },
    }
