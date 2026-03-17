from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal

from ..types import BJ, ManagementActionIntent, ManagementActionPreview, OrderIntent

OrderMode = Literal["paper", "dry-run", "live"]


def side_to_binance(side: str) -> str:
    return "BUY" if side == "LONG" else "SELL"


def close_side_to_binance(side: str) -> str:
    return "SELL" if side == "LONG" else "BUY"


def build_entry_order_payload(order: OrderIntent) -> dict[str, Any]:
    return {
        "symbol": order.symbol,
        "side": side_to_binance(order.side),
        "type": "MARKET",
        "quantity": order.qty,
        "newClientOrderId": order.intent_id,
    }


def build_stop_order_payload(order: OrderIntent) -> dict[str, Any]:
    return {
        "symbol": order.symbol,
        "side": close_side_to_binance(order.side),
        "type": "STOP_MARKET",
        "stopPrice": order.stop_loss,
        "closePosition": "true",
        "workingType": "MARK_PRICE",
        "newClientOrderId": f"{order.intent_id}-sl",
    }


def build_take_profit_payload(order: OrderIntent) -> dict[str, Any] | None:
    if order.take_profit is None:
        return None
    return {
        "symbol": order.symbol,
        "side": close_side_to_binance(order.side),
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


def build_management_stop_preview_payload(intent: ManagementActionIntent) -> dict[str, Any]:
    return {
        "symbol": intent.symbol,
        "side": close_side_to_binance(intent.side),
        "type": "STOP_MARKET",
        "stopPrice": intent.stop_loss,
        "closePosition": "true",
        "workingType": "MARK_PRICE",
        "newClientOrderId": f"{intent.intent_id}-sl-update",
    }


def build_management_close_preview_payload(intent: ManagementActionIntent) -> dict[str, Any]:
    return {
        "symbol": intent.symbol,
        "side": close_side_to_binance(intent.side),
        "type": "MARKET",
        "quantity": intent.qty,
        "reduceOnly": "true",
        "newClientOrderId": f"{intent.intent_id}-close",
    }


def build_management_preview(intent: ManagementActionIntent) -> ManagementActionPreview:
    if intent.action == "BREAK_EVEN":
        if intent.stop_loss is None:
            return ManagementActionPreview(
                intent=intent,
                preview_kind="UNSUPPORTED",
                supported=False,
                reason="missing_stop_loss",
            )
        return ManagementActionPreview(
            intent=intent,
            preview_kind="STOP_LOSS_UPDATE",
            payload=build_management_stop_preview_payload(intent),
        )

    if intent.action == "PARTIAL_TAKE_PROFIT":
        if not intent.qty or intent.qty <= 0:
            return ManagementActionPreview(
                intent=intent,
                preview_kind="UNSUPPORTED",
                supported=False,
                reason="missing_reduce_qty",
            )
        return ManagementActionPreview(
            intent=intent,
            preview_kind="REDUCE_ONLY_TP_CLOSE",
            payload=build_management_close_preview_payload(intent),
        )

    if intent.action == "EXIT":
        if not intent.qty or intent.qty <= 0:
            return ManagementActionPreview(
                intent=intent,
                preview_kind="UNSUPPORTED",
                supported=False,
                reason="missing_close_qty",
            )
        return ManagementActionPreview(
            intent=intent,
            preview_kind="CLOSE_POSITION",
            payload=build_management_close_preview_payload(intent),
        )

    return ManagementActionPreview(
        intent=intent,
        preview_kind="UNSUPPORTED",
        supported=False,
        reason="action_not_previewable_in_mvp",
    )


def preview_result(intent: ManagementActionIntent, preview: ManagementActionPreview, mode: OrderMode) -> dict[str, Any]:
    return {
        "mode": mode,
        "ts_bj": datetime.now(BJ).isoformat(),
        "intent": asdict(intent),
        "preview": asdict(preview),
        "result": "PREVIEW_ONLY",
    }
