from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .models import PaperSignalFact


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_str(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if value == "":
        return None
    return value


def _str_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _float_or_none(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc


def _int_or_none(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc


def _key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        _str_value(row.get("symbol")).upper(),
        _str_value(row.get("engine")).lower(),
        _str_value(row.get("setup_type")).upper(),
    )


def _allocation_index(allocation_rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in allocation_rows:
        if not isinstance(row, Mapping):
            raise ValueError("allocation rows must be objects")
        index[_key(row)] = dict(row)
    return index


def _execution_index(execution_rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_intent_id: dict[str, dict[str, Any]] = {}
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in execution_rows:
        if not isinstance(row, Mapping):
            raise ValueError("execution rows must be objects")
        intent_id = _optional_str(row.get("intent_id"), field_name="execution.intent_id")
        if intent_id:
            by_intent_id[intent_id] = dict(row)
        symbol = _str_value(row.get("symbol")).upper()
        if symbol:
            by_symbol[symbol] = dict(row)
    return by_intent_id, by_symbol


def _allocation_execution(allocation: Mapping[str, Any]) -> Mapping[str, Any]:
    execution = allocation.get("execution")
    if isinstance(execution, Mapping):
        return execution
    return {}


def _validation_allowed(candidate: Mapping[str, Any]) -> bool | None:
    validation = candidate.get("validation")
    if not isinstance(validation, Mapping):
        return None
    allowed = validation.get("allowed")
    if allowed is None:
        return None
    if not isinstance(allowed, bool):
        raise ValueError("validation.allowed must be boolean")
    return allowed


def collect_signal_facts(
    *,
    signal_facts_path: Path,
    candidate_rows: list[dict[str, Any]],
    allocation_rows: list[dict[str, Any]],
    execution_rows: list[dict[str, Any]],
    regime: Mapping[str, Any],
    mode: str,
    runtime_env: str,
) -> dict[str, Any]:
    allocations = _allocation_index(allocation_rows)
    executions_by_intent_id, executions_by_symbol = _execution_index(execution_rows)
    facts: list[PaperSignalFact] = []

    for candidate in candidate_rows:
        if not isinstance(candidate, Mapping):
            raise ValueError("candidate rows must be objects")
        allocation = allocations.get(_key(candidate), {})
        allocation_execution = _allocation_execution(allocation)
        allocation_execution_intent_id = _optional_str(
            allocation_execution.get("intent_id"),
            field_name="allocation.execution.intent_id",
        )
        allocation_intent_id = _optional_str(allocation.get("intent_id"), field_name="allocation.intent_id")
        intent_id = allocation_execution_intent_id if allocation_execution_intent_id is not None else allocation_intent_id
        execution = executions_by_intent_id.get(intent_id or "") or executions_by_symbol.get(_str_value(candidate.get("symbol")).upper()) or {}
        execution_status = execution.get("status") or allocation_execution.get("status")

        facts.append(
            PaperSignalFact(
                fact_type="signal",
                mode=_str_value(mode),
                runtime_env=_str_value(runtime_env),
                regime_label=_str_value(regime.get("label")),
                regime_confidence=_float_or_none(regime.get("confidence"), field_name="regime.confidence"),
                symbol=_str_value(candidate.get("symbol")).upper(),
                side=_str_value(candidate.get("side")),
                engine=_str_value(candidate.get("engine")),
                setup_type=_str_value(candidate.get("setup_type")),
                score=_float_or_none(candidate.get("score"), field_name="candidate.score"),
                stop_loss=_float_or_none(candidate.get("stop_loss"), field_name="candidate.stop_loss"),
                invalidation_source=_str_value(candidate.get("invalidation_source")),
                validation_allowed=_validation_allowed(candidate),
                allocation_status=_str_or_none(allocation.get("status")),
                allocation_rank=_int_or_none(allocation.get("rank"), field_name="allocation.rank"),
                final_risk_budget=_float_or_none(allocation.get("final_risk_budget"), field_name="allocation.final_risk_budget"),
                execution_status=_str_or_none(execution_status),
                intent_id=intent_id if intent_id is not None else _optional_str(execution.get("intent_id"), field_name="execution.intent_id"),
            )
        )

    signal_facts_path.parent.mkdir(parents=True, exist_ok=True)
    with signal_facts_path.open("a", encoding="utf-8") as handle:
        for fact in facts:
            handle.write(json.dumps(fact.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    return {"signal_facts_path": str(signal_facts_path), "appended_count": len(facts)}