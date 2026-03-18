from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .lifecycle_v2 import advance_lifecycle_transition
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


def _suggest_protective_stop(side: str, entry_price: float, mark_price: float | None) -> float:
    # MVP fallback: anchor the stop 2% beyond entry, but if price is already through
    # that level, place it 0.5% beyond the current mark instead to avoid previewing an
    # already-breached stop. This stays deterministic and keeps the stop on the loss side.
    if side == "LONG":
        fallback = entry_price * 0.98
        if mark_price is None or mark_price <= 0:
            return round(fallback, 8)
        return round(min(fallback, mark_price * 0.995), 8)

    fallback = entry_price * 1.02
    if mark_price is None or mark_price <= 0:
        return round(fallback, 8)
    return round(max(fallback, mark_price * 1.005), 8)


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
        reference_price = float(mark_price) if mark_price is not None else entry_price
        suggested_stop_loss = _suggest_protective_stop(side, entry_price, mark_price)
        suggestions.append(
            ManagementSuggestion(
                symbol=symbol,
                action="ADD_PROTECTIVE_STOP",
                side=side,
                priority="MEDIUM",
                reason="当前持仓缺少有效止损，建议先补保护性止损后再谈加仓或死扛。",
                suggested_stop_loss=suggested_stop_loss,
                reference_price=reference_price,
                meta={
                    "position_source": position.get("source"),
                    "heuristic": "entry_2pct_or_mark_0.5pct_buffer",
                    "entry_price": round(entry_price, 8),
                    "mark_price": round(reference_price, 8),
                },
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


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _r_multiple(position: dict[str, Any]) -> float:
    side = str(position.get("side", "LONG"))
    entry = _float(position.get("entry_price"))
    mark = _float(position.get("mark_price"))
    stop = position.get("stop_loss")
    stop_loss = _float(stop) if stop is not None else 0.0
    if entry <= 0 or mark <= 0 or not _valid_stop(side, entry, stop_loss):
        return 0.0

    risk_unit = abs(entry - stop_loss)
    if risk_unit <= 0:
        return 0.0
    if side == "LONG":
        return (mark - entry) / risk_unit
    return (entry - mark) / risk_unit


def advance_lifecycle_positions(state: RuntimeState, lifecycle_config: Any) -> dict[str, dict[str, Any]]:
    latest = dict(getattr(state, "latest_lifecycle", {}) or {})
    updates: dict[str, dict[str, Any]] = {}
    protect_trigger = _float(getattr(lifecycle_config, "protect_r_multiple", 1.2)) or 1.2

    for symbol, position in state.positions.items():
        side = str(position.get("side", "LONG"))
        mark = _float(position.get("mark_price"))
        stop = position.get("stop_loss")
        stop_loss = _float(stop) if stop is not None else 0.0
        take_profit = position.get("take_profit")
        r_multiple = _r_multiple(position)
        current_state = str((latest.get(symbol) or {}).get("state", "INIT"))

        stop_hit = False
        if mark > 0 and stop is not None and _valid_stop(side, _float(position.get("entry_price")), stop_loss):
            stop_hit = _stop_breached(side, mark, stop_loss)

        target_hit = False
        if mark > 0 and take_profit is not None:
            target_hit = _in_favor(side, mark, _float(take_profit))

        next_state, reason_codes = advance_lifecycle_transition(
            current_state,
            {
                "r_multiple": r_multiple,
                "confirmed": position.get("status") in {"OPEN", "FILLED", "SENT"},
                "payload_ready": _float(position.get("qty")) > 0,
                "trend_mature": r_multiple >= protect_trigger,
                "stop_hit": stop_hit,
                "target_hit": target_hit,
            },
            config=lifecycle_config,
        )
        updates[symbol] = {
            "state": next_state.value,
            "reason_codes": reason_codes,
            "r_multiple": round(r_multiple, 6),
        }

    return updates


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

        if action in {"BREAK_EVEN", "ADD_PROTECTIVE_STOP"}:
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
