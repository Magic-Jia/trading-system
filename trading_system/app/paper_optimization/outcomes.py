from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .models import PaperTradeOutcome


_EXECUTED_STATUSES = {"FILLED", "SENT"}
_NOT_EXECUTED_STATUSES = {"BLOCKED", "FAILED", "SKIPPED"}


def _jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            payload = line.strip()
            if not payload:
                continue
            try:
                raw = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{line_number} must be valid JSON") from exc
            if not isinstance(raw, dict):
                raise ValueError(f"{path.name}:{line_number} must contain a JSON object")
            rows.append(raw)
    return rows


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_str(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value if value else None


def _str_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _required_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a string")
    return value


def _float_or_none(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc


def _mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _signal_facts(signal_facts: list[dict[str, Any]] | None, signal_facts_path: Path | None) -> list[dict[str, Any]]:
    if signal_facts is not None:
        rows: list[dict[str, Any]] = []
        for row in signal_facts:
            if not isinstance(row, Mapping):
                raise ValueError("signal_facts rows must be objects")
            rows.append(dict(row))
        return rows
    return _jsonl(signal_facts_path)


def _ledger_index(paper_ledger_path: Path | None) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in _jsonl(paper_ledger_path):
        intent_id = _optional_str(row.get("intent_id"), field_name="ledger.intent_id")
        if intent_id:
            index[intent_id] = row
    return index


def _position_indexes(runtime_positions: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_intent_id: dict[str, dict[str, Any]] = {}
    by_symbol: dict[str, dict[str, Any]] = {}
    for symbol, raw in runtime_positions.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"runtime_positions.{symbol} must be an object")
        row = dict(raw)
        normalized_symbol = _str_value(row.get("symbol") or symbol).upper()
        if normalized_symbol:
            by_symbol[normalized_symbol] = row
        intent_id = _optional_str(row.get("intent_id"), field_name="position.intent_id")
        if intent_id:
            by_intent_id[intent_id] = row
    return by_intent_id, by_symbol


def _outcome_status(execution_status: str | None, position: Mapping[str, Any]) -> str:
    normalized = _str_value(execution_status).upper()
    if normalized in _NOT_EXECUTED_STATUSES:
        return "NOT_EXECUTED"
    if normalized in _EXECUTED_STATUSES:
        qty = _float_or_none(position.get("qty"), field_name="position.qty")
        if qty is not None and qty > 0:
            return "OPEN"
        return "POSITION_NOT_TRACKED"
    return "UNKNOWN"



def collect_trade_outcomes(
    *,
    trade_outcomes_path: Path,
    runtime_positions: Mapping[str, Mapping[str, Any]],
    signal_facts: list[dict[str, Any]] | None = None,
    signal_facts_path: Path | None = None,
    paper_ledger_path: Path | None = None,
) -> dict[str, Any]:
    facts = _signal_facts(signal_facts, signal_facts_path)
    ledger_by_intent = _ledger_index(paper_ledger_path)
    positions_by_intent, positions_by_symbol = _position_indexes(runtime_positions)

    outcomes: list[PaperTradeOutcome] = []
    open_count = 0
    not_executed_count = 0
    position_not_tracked_count = 0

    for fact in facts:
        if _str_value(fact.get("fact_type")) and _str_value(fact.get("fact_type")) != "signal":
            continue

        intent_id = _optional_str(fact.get("intent_id"), field_name="fact.intent_id")
        symbol = _required_str(fact.get("symbol"), field_name="fact.symbol").upper()
        position = positions_by_intent.get(intent_id or "") or positions_by_symbol.get(symbol) or {}
        ledger_event = ledger_by_intent.get(intent_id or "") or {}
        order = _mapping(ledger_event.get("order"), field_name="ledger.order")
        result = _mapping(ledger_event.get("result"), field_name="ledger.result")
        position_update = _mapping(ledger_event.get("position_update"), field_name="ledger.position_update")

        execution_status = _optional_str(fact.get("execution_status"), field_name="fact.execution_status")
        outcome_status = _outcome_status(execution_status, position)
        if outcome_status == "OPEN":
            open_count += 1
        elif outcome_status == "NOT_EXECUTED":
            not_executed_count += 1
        elif outcome_status == "POSITION_NOT_TRACKED":
            position_not_tracked_count += 1

        unrealized_pnl = _float_or_none(position.get("unrealized_pnl"), field_name="position.unrealized_pnl")
        outcomes.append(
            PaperTradeOutcome(
                fact_type="trade_outcome",
                mode=_required_str(fact.get("mode"), field_name="fact.mode"),
                runtime_env=_required_str(fact.get("runtime_env"), field_name="fact.runtime_env"),
                regime_label=_required_str(fact.get("regime_label"), field_name="fact.regime_label"),
                symbol=symbol,
                side=_required_str(fact.get("side"), field_name="fact.side"),
                engine=_required_str(fact.get("engine"), field_name="fact.engine"),
                setup_type=_required_str(fact.get("setup_type"), field_name="fact.setup_type"),
                intent_id=intent_id,
                signal_id=(
                    _optional_str(position.get("signal_id"), field_name="position.signal_id")
                    or _optional_str(position_update.get("signal_id"), field_name="position_update.signal_id")
                    or _optional_str(order.get("signal_id"), field_name="order.signal_id")
                    or _optional_str(ledger_event.get("signal_id"), field_name="ledger.signal_id")
                ),
                allocation_status=_optional_str(fact.get("allocation_status"), field_name="fact.allocation_status"),
                execution_status=execution_status,
                outcome_status=outcome_status,
                position_status=_optional_str(position.get("status"), field_name="position.status"),
                score=_float_or_none(fact.get("score"), field_name="fact.score"),
                final_risk_budget=_float_or_none(fact.get("final_risk_budget"), field_name="fact.final_risk_budget"),
                filled_qty=(
                    _float_or_none(result.get("filled_qty"), field_name="result.filled_qty")
                    or _float_or_none(order.get("qty"), field_name="order.qty")
                    or _float_or_none(position_update.get("qty"), field_name="position_update.qty")
                ),
                open_qty=_float_or_none(position.get("qty"), field_name="position.qty"),
                entry_price=(
                    _float_or_none(position.get("entry_price"), field_name="position.entry_price")
                    or _float_or_none(position_update.get("entry_price"), field_name="position_update.entry_price")
                    or _float_or_none(order.get("entry_price"), field_name="order.entry_price")
                    or _float_or_none(result.get("avg_price"), field_name="result.avg_price")
                ),
                mark_price=_float_or_none(position.get("mark_price"), field_name="position.mark_price") or _float_or_none(position_update.get("mark_price"), field_name="position_update.mark_price"),
                stop_loss=(
                    _float_or_none(position.get("stop_loss"), field_name="position.stop_loss")
                    or _float_or_none(position_update.get("stop_loss"), field_name="position_update.stop_loss")
                    or _float_or_none(order.get("stop_loss"), field_name="order.stop_loss")
                    or _float_or_none(fact.get("stop_loss"), field_name="fact.stop_loss")
                ),
                take_profit=(
                    _float_or_none(position.get("take_profit"), field_name="position.take_profit")
                    or _float_or_none(position_update.get("take_profit"), field_name="position_update.take_profit")
                    or _float_or_none(order.get("take_profit"), field_name="order.take_profit")
                ),
                unrealized_pnl=unrealized_pnl,
                realized_pnl=None,
                pnl_basis="unrealized" if unrealized_pnl is not None else None,
                opened_at_bj=(
                    _optional_str(position.get("opened_at_bj"), field_name="position.opened_at_bj")
                    or _optional_str(position_update.get("opened_at_bj"), field_name="position_update.opened_at_bj")
                ),
                updated_at_bj=(
                    _optional_str(position.get("updated_at_bj"), field_name="position.updated_at_bj")
                    or _optional_str(position_update.get("updated_at_bj"), field_name="position_update.updated_at_bj")
                ),
                recorded_at_bj=_optional_str(ledger_event.get("recorded_at_bj"), field_name="ledger.recorded_at_bj"),
            )
        )

    trade_outcomes_path.parent.mkdir(parents=True, exist_ok=True)
    with trade_outcomes_path.open("w", encoding="utf-8") as handle:
        for outcome in outcomes:
            handle.write(json.dumps(outcome.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    return {
        "trade_outcomes_path": str(trade_outcomes_path),
        "appended_count": len(outcomes),
        "open_count": open_count,
        "not_executed_count": not_executed_count,
        "position_not_tracked_count": position_not_tracked_count,
    }
