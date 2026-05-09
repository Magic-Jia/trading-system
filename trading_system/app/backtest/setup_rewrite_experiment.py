from __future__ import annotations

from collections import defaultdict
import math
from numbers import Real
from typing import Any, Mapping, Sequence

from .types import SetupRewriteParams, SetupRewriteRule


def serialize_setup_rewrite(params: SetupRewriteParams | None) -> dict[str, Any] | None:
    if params is None:
        return None
    return {"rules": [_serialize_rule(rule) for rule in params.rules]}


def build_setup_rewrite_experiment(
    *,
    rows: Sequence[Mapping[str, Any]],
    setup_rewrite: SetupRewriteParams,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evaluation_rows = [_evaluate_row(index=index, row=row, params=setup_rewrite) for index, row in enumerate(rows, start=1)]
    summary = {
        "total_rows": len(evaluation_rows),
        "total_trades": len(evaluation_rows),
        "evaluated_count": sum(1 for row in evaluation_rows if row["evaluation_status"] == "evaluated"),
        "would_keep_count": sum(1 for row in evaluation_rows if row["would_keep"] is True),
        "would_filter_count": sum(1 for row in evaluation_rows if row["evaluation_status"] == "evaluated" and row["would_keep"] is False),
        "skipped_count": sum(1 for row in evaluation_rows if row["evaluation_status"] != "evaluated"),
        "by_setup": _breakdown(evaluation_rows, key_name="setup_type"),
        "by_symbol": _breakdown(evaluation_rows, key_name="symbol"),
        "by_source_chunk": _breakdown(evaluation_rows, key_name="source_chunk"),
    }
    base_metadata = dict(metadata or {})
    base_metadata.update(
        {
            "artifact_type": "opt_in_offline_diagnostic",
            "changes_baseline_ledger": False,
            "setup_rewrite": serialize_setup_rewrite(setup_rewrite),
        }
    )
    return {
        "metadata": base_metadata,
        "summary": summary,
        "evaluation_rows": evaluation_rows,
    }


def _serialize_rule(rule: SetupRewriteRule) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": rule.name}
    if rule.name == "require_min_score":
        payload["min_score"] = rule.min_score
    elif rule.name == "exclude_setup_types":
        payload["setup_types"] = list(rule.setup_types)
    elif rule.name == "require_setup_min_score":
        payload["setup_types"] = list(rule.setup_types)
        payload["min_score"] = rule.min_score
    elif rule.name == "require_setup_min_cost_coverage_ratio":
        payload["setup_types"] = list(rule.setup_types)
        payload["min_cost_coverage_ratio"] = rule.min_cost_coverage_ratio
    elif rule.name == "require_setup_allowed_symbols":
        payload["setup_types"] = list(rule.setup_types)
        payload["symbols"] = list(rule.symbols)
    return payload


def _evaluate_row(*, index: int, row: Mapping[str, Any], params: SetupRewriteParams) -> dict[str, Any]:
    identity = {
        "row_index": index,
        "symbol": _string_or_none(row.get("symbol"), field_path=f"rows[{index}].symbol"),
        "setup_type": _string_or_none(row.get("setup_type"), field_path=f"rows[{index}].setup_type"),
        "side": _string_or_none(row.get("side"), field_path=f"rows[{index}].side"),
        "entry_timestamp": _string_or_none(row.get("entry_timestamp"), field_path=f"rows[{index}].entry_timestamp"),
        "score": _float_or_none(row.get("score"), field_path=f"rows[{index}].score"),
        "net_pnl": _net_pnl_or_none(row.get("net_pnl"), field_path=f"rows[{index}].net_pnl"),
        "source_chunk": _source_chunk(row, index=index),
    }
    for rule in params.rules:
        status, reason, keep = _evaluate_rule(identity=identity, raw_row=row, rule=rule)
        if status != "evaluated" or not keep:
            return {
                **identity,
                "evaluation_status": status,
                "evaluation_reason": reason,
                "would_keep": False,
            }
    return {
        **identity,
        "evaluation_status": "evaluated",
        "evaluation_reason": "passed_all_rules",
        "would_keep": True,
    }


def _evaluate_rule(
    *,
    identity: Mapping[str, Any],
    raw_row: Mapping[str, Any],
    rule: SetupRewriteRule,
) -> tuple[str, str, bool]:
    if rule.name == "require_min_score":
        score = identity["score"]
        if score is None:
            return "no_evidence", "missing_score", False
        if rule.min_score is None:
            return "no_evidence", "missing_min_score_rule_value", False
        if score < rule.min_score:
            return "evaluated", "score_below_minimum", False
        return "evaluated", "score_meets_minimum", True

    if rule.name == "exclude_setup_types":
        setup_type = identity["setup_type"]
        if setup_type is None:
            return "no_evidence", "missing_setup_type", False
        if setup_type.upper() in rule.setup_types:
            return "evaluated", "excluded_setup_type", False
        return "evaluated", "setup_type_allowed", True

    if rule.name == "require_setup_min_score":
        if not _setup_type_matches(identity=identity, rule=rule):
            return "evaluated", "setup_type_out_of_scope", True
        score = identity["score"]
        if score is None:
            return "no_evidence", "missing_score", False
        if rule.min_score is None:
            return "no_evidence", "missing_min_score_rule_value", False
        if score < rule.min_score:
            return "evaluated", "setup_score_below_minimum", False
        return "evaluated", "setup_score_meets_minimum", True

    if rule.name == "require_setup_min_cost_coverage_ratio":
        if not _setup_type_matches(identity=identity, rule=rule):
            return "evaluated", "setup_type_out_of_scope", True
        cost_coverage_ratio = _float_or_none(
            raw_row.get("cost_coverage_ratio"),
            field_path=f"rows[{identity['row_index']}].cost_coverage_ratio",
        )
        if cost_coverage_ratio is None:
            return "no_evidence", "missing_cost_coverage_ratio", False
        if rule.min_cost_coverage_ratio is None:
            return "no_evidence", "missing_min_cost_coverage_ratio_rule_value", False
        if cost_coverage_ratio < rule.min_cost_coverage_ratio:
            return "evaluated", "setup_cost_coverage_below_minimum", False
        return "evaluated", "setup_cost_coverage_meets_minimum", True

    if rule.name == "require_setup_allowed_symbols":
        if not _setup_type_matches(identity=identity, rule=rule):
            return "evaluated", "setup_type_out_of_scope", True
        symbol = identity["symbol"]
        if symbol is None:
            return "no_evidence", "missing_symbol", False
        if symbol.upper() not in rule.symbols:
            return "evaluated", "setup_symbol_not_allowed", False
        return "evaluated", "setup_symbol_allowed", True

    if rule.name == "require_after_cost_breakeven_evidence":
        cost_coverage_ratio = _float_or_none(
            raw_row.get("cost_coverage_ratio"),
            field_path=f"rows[{identity['row_index']}].cost_coverage_ratio",
        )
        if cost_coverage_ratio is None:
            return "no_evidence", "missing_cost_coverage_ratio", False
        if cost_coverage_ratio < 1.0:
            return "evaluated", "insufficient_after_cost_breakeven_evidence", False
        return "evaluated", "after_cost_breakeven_evidence_present", True

    return "no_evidence", f"unknown_rule:{rule.name}", False


def _setup_type_matches(*, identity: Mapping[str, Any], rule: SetupRewriteRule) -> bool:
    setup_type = identity["setup_type"]
    return setup_type is not None and setup_type.upper() in rule.setup_types


def _breakdown(rows: Sequence[Mapping[str, Any]], *, key_name: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(_empty_bucket)
    for row in rows:
        key = row.get(key_name)
        if key is None or key == "":
            continue
        bucket = buckets[str(key)]
        bucket["total_rows"] += 1
        if row["evaluation_status"] == "evaluated":
            bucket["evaluated_count"] += 1
            if row["would_keep"] is True:
                bucket["would_keep_count"] += 1
            else:
                bucket["would_filter_count"] += 1
        else:
            bucket["skipped_count"] += 1
        net_pnl = row.get("net_pnl")
        if net_pnl is not None:
            bucket["net_pnl"] += float(net_pnl)
    return {key: buckets[key] for key in sorted(buckets)}


def _empty_bucket() -> dict[str, Any]:
    return {
        "total_rows": 0,
        "evaluated_count": 0,
        "would_keep_count": 0,
        "would_filter_count": 0,
        "skipped_count": 0,
        "net_pnl": 0.0,
    }


def _float_or_none(value: Any, *, field_path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field_path} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_path} must be a finite number")
    return result


def _net_pnl_or_none(value: Any, *, field_path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_path} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_path} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field_path} must be a finite number")
    return result


def _string_or_none(value: Any, *, field_path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_path} must be a string")
    text = str(value).strip()
    return text or None


def _source_chunk(row: Mapping[str, Any], *, index: int) -> str | None:
    for key in ("source_chunk", "chunk", "chunk_name"):
        value = _string_or_none(row.get(key), field_path=f"rows[{index}].{key}")
        if value is not None:
            return value
    return None
