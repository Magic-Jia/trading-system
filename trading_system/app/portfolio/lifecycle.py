from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..types import ManagementActionIntent, ManagementSuggestion, RuntimeState


def _is_open(position: dict[str, Any]) -> bool:
    return position.get("status") in {None, "OPEN", "FILLED", "SENT"}


def _in_favor(side: str, mark_price: float, reference_price: float) -> bool:
    if side == "LONG":
        return mark_price >= reference_price
    return mark_price <= reference_price


def _stop_breached(side: str, mark_price: float, stop_loss: float) -> bool:
    if side == "LONG":
        return mark_price <= stop_loss
    return mark_price >= stop_loss


def _valid_stop(side: str, entry_price: float, stop_loss: float | None) -> bool:
    if stop_loss is None:
        return False
    if side == "LONG":
        return stop_loss < entry_price
    return stop_loss > entry_price


def evaluate_position(position: dict[str, Any]) -> list[dict[str, Any]]:
    if not _is_open(position):
        return []

    symbol = str(position.get("symbol", ""))
    side = str(position.get("side", "LONG"))
    entry_price = float(position.get("entry_price", 0.0) or 0.0)
    mark_price = position.get("mark_price")
    stop_loss = position.get("stop_loss")
    take_profit = position.get("take_profit")

    if entry_price <= 0:
        return []

    suggestions: list[ManagementSuggestion] = []
    if not _valid_stop(side, entry_price, stop_loss):
        suggestions.append(
            ManagementSuggestion(
                symbol=symbol,
                action="ADD_PROTECTIVE_STOP",
                side=side,
                priority="MEDIUM",
                reason="当前持仓缺少有效止损，建议先补保护性止损后再谈加仓或死扛。",
                reference_price=float(mark_price) if mark_price is not None else entry_price,
                meta={"position_source": position.get("source")},
            )
        )
        return [asdict(item) for item in suggestions]

    stop_loss = float(stop_loss)
    risk_unit = abs(entry_price - stop_loss)
    if risk_unit <= 0:
        return []

    if mark_price is None:
        return []
    mark_price = float(mark_price)

    break_even_trigger = entry_price + risk_unit if side == "LONG" else entry_price - risk_unit
    if _in_favor(side, mark_price, break_even_trigger) and stop_loss != entry_price:
        suggestions.append(
            ManagementSuggestion(
                symbol=symbol,
                action="BREAK_EVEN",
                side=side,
                priority="MEDIUM",
                reason="价格已至少走出 1R，允许把止损上提到保本位。",
                suggested_stop_loss=round(entry_price, 8),
                reference_price=round(mark_price, 8),
                meta={"trigger_price": round(break_even_trigger, 8), "risk_unit": round(risk_unit, 8)},
            )
        )

    if take_profit is not None and _in_favor(side, mark_price, float(take_profit)):
        suggestions.append(
            ManagementSuggestion(
                symbol=symbol,
                action="PARTIAL_TAKE_PROFIT",
                side=side,
                priority="MEDIUM",
                qty_fraction=0.5,
                reason="已触及第一目标位，建议先兑现 50% 仓位并保留剩余仓位观察延伸。",
                reference_price=round(mark_price, 8),
                meta={"target_price": round(float(take_profit), 8)},
            )
        )

    if _stop_breached(side, mark_price, stop_loss):
        suggestions.append(
            ManagementSuggestion(
                symbol=symbol,
                action="EXIT",
                side=side,
                priority="HIGH",
                qty_fraction=1.0,
                reason="当前价格已跌破（或升破）止损位，建议按计划退出。",
                reference_price=round(mark_price, 8),
                meta={"stop_loss": round(stop_loss, 8)},
            )
        )

    return [asdict(item) for item in suggestions]


def evaluate_portfolio(state: RuntimeState) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for position in state.positions.values():
        suggestions.extend(evaluate_position(position))
    return suggestions


def _intent_id(symbol: str, action: str) -> str:
    return f"mgmt-{symbol.lower()}-{action.lower()}".replace("_", "-")


def _position_qty(position: dict[str, Any]) -> float:
    return round(float(position.get("qty", 0.0) or 0.0), 8)


def build_management_action_intents(
    state: RuntimeState,
    suggestions: list[dict[str, Any]] | None = None,
) -> list[ManagementActionIntent]:
    rows = suggestions if suggestions is not None else evaluate_portfolio(state)
    intents: list[ManagementActionIntent] = []
    for row in rows:
        symbol = str(row.get("symbol", ""))
        action = str(row.get("action", ""))
        position = state.positions.get(symbol)
        if not symbol or position is None:
            continue

        position_qty = _position_qty(position)
        qty_fraction = float(row.get("qty_fraction") or 0.0)
        qty = None
        stop_loss = None

        if action == "BREAK_EVEN":
            stop_loss = row.get("suggested_stop_loss")
        elif action == "PARTIAL_TAKE_PROFIT":
            qty = round(position_qty * qty_fraction, 8)
        elif action == "EXIT":
            qty = position_qty

        intents.append(
            ManagementActionIntent(
                intent_id=_intent_id(symbol, action),
                symbol=symbol,
                action=action,
                side=str(row.get("side", position.get("side", "LONG"))),
                position_qty=position_qty,
                qty=qty,
                stop_loss=stop_loss,
                reference_price=row.get("reference_price"),
                meta={
                    "reason": row.get("reason"),
                    "priority": row.get("priority"),
                    "qty_fraction": row.get("qty_fraction"),
                    "position_stop_loss": position.get("stop_loss"),
                    "position_take_profit": position.get("take_profit"),
                    **dict(row.get("meta") or {}),
                },
            )
        )
    return intents
