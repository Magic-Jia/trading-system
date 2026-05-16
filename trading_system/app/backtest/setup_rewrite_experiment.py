from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import math
from numbers import Integral
from numbers import Real
import re
from typing import Any, Mapping, Sequence

from .types import SetupRewriteParams, SetupRewriteRule

_FALSE_DISCOVERY_CORRECTION_METHODS = frozenset(
    {
        "bonferroni",
        "holm_bonferroni",
        "benjamini_hochberg",
    }
)


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
    _validate_serialized_trade_identity(rows)
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
    promotion_grade, promotion_grade_reasons = _promotion_grade_contract(
        setup_rewrite=setup_rewrite,
        metadata=base_metadata,
        evaluation_rows=evaluation_rows,
    )
    base_metadata.update(
        {
            "artifact_type": "opt_in_offline_diagnostic",
            "changes_baseline_ledger": False,
            "promotion_grade": promotion_grade,
            "promotion_grade_reasons": promotion_grade_reasons,
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


def _promotion_grade_contract(
    *,
    setup_rewrite: SetupRewriteParams,
    metadata: Mapping[str, Any],
    evaluation_rows: Sequence[Mapping[str, Any]],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    correction_scope = _false_discovery_correction_scope(setup_rewrite)
    if correction_scope is not None:
        reasons.extend(
            _false_discovery_correction_reasons(
                raw=metadata.get("false_discovery_correction"),
                expected_identities=correction_scope,
            )
        )
    if any(row["evaluation_status"] != "evaluated" for row in evaluation_rows):
        reasons.append("setup_rewrite_missing_evidence")
    return not reasons, reasons


def _false_discovery_correction_scope(params: SetupRewriteParams) -> tuple[str, ...] | None:
    setup_identities: set[str] = set()
    for rule in params.rules:
        setup_identities.update(rule.setup_types)
    if len(setup_identities) > 1:
        return tuple(sorted(setup_identities))
    if len(params.rules) > 1:
        return tuple(_rule_identity(rule) for rule in params.rules)
    return None


def _rule_identity(rule: SetupRewriteRule) -> str:
    return "|".join(str(part) for part in _serialize_rule(rule).items())


def _false_discovery_correction_reasons(*, raw: Any, expected_identities: Sequence[str]) -> list[str]:
    reason_prefix = "setup_rewrite_false_discovery_correction"
    if raw is None:
        return [f"{reason_prefix}_missing"]
    if not isinstance(raw, Mapping):
        return [f"{reason_prefix}_invalid"]

    expected_fields = {
        "correction_method",
        "family_size",
        "alpha",
        "adjusted_threshold",
        "controls_familywise_error",
        "setup_identities",
    }
    unknown_fields = set(raw) - expected_fields
    if unknown_fields:
        return [f"{reason_prefix}_unknown_field"]

    method = raw.get("correction_method")
    if not isinstance(method, str) or method.strip() != method or method not in _FALSE_DISCOVERY_CORRECTION_METHODS:
        return [f"{reason_prefix}_method_invalid"]

    family_size = raw.get("family_size")
    if isinstance(family_size, bool) or not isinstance(family_size, Integral) or int(family_size) <= 0:
        return [f"{reason_prefix}_family_size_invalid"]
    if int(family_size) != len(expected_identities):
        return [f"{reason_prefix}_family_size_mismatch"]

    alpha = raw.get("alpha")
    if not _is_finite_number(alpha) or not (0.0 < float(alpha) <= 1.0):
        return [f"{reason_prefix}_alpha_invalid"]

    adjusted_threshold = raw.get("adjusted_threshold")
    if not _is_finite_number(adjusted_threshold) or not (0.0 <= float(adjusted_threshold) <= float(alpha)):
        return [f"{reason_prefix}_adjusted_threshold_invalid"]

    controls_familywise_error = raw.get("controls_familywise_error")
    if type(controls_familywise_error) is not bool:
        return [f"{reason_prefix}_controls_familywise_error_invalid"]
    if controls_familywise_error is not True:
        return [f"{reason_prefix}_does_not_control_familywise_error"]

    setup_identities = raw.get("setup_identities")
    if not isinstance(setup_identities, Sequence) or isinstance(setup_identities, (str, bytes)):
        return [f"{reason_prefix}_setup_identities_invalid"]
    parsed_identities: list[str] = []
    for identity in setup_identities:
        if not isinstance(identity, str) or identity.strip() != identity or not identity:
            return [f"{reason_prefix}_setup_identities_invalid"]
        parsed_identities.append(identity)
    if len(parsed_identities) != len(set(parsed_identities)):
        return [f"{reason_prefix}_setup_identities_duplicate"]
    if tuple(sorted(parsed_identities)) != tuple(sorted(expected_identities)):
        return [f"{reason_prefix}_setup_identities_mismatch"]

    return []


def _is_finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, Real) and math.isfinite(float(value))


def _evaluate_row(*, index: int, row: Mapping[str, Any], params: SetupRewriteParams) -> dict[str, Any]:
    identity = {
        "row_index": index,
        **_serialized_trade_identity(row, index=index),
        "symbol": _canonical_symbol_or_none(row.get("symbol"), field_path=f"rows[{index}].symbol"),
        "setup_type": _canonical_setup_type_or_none(row.get("setup_type"), field_path=f"rows[{index}].setup_type"),
        "side": _canonical_side_or_none(row.get("side"), field_path=f"rows[{index}].side"),
        "entry_timestamp": _string_or_none(row.get("entry_timestamp"), field_path=f"rows[{index}].entry_timestamp"),
        "score": _float_or_none(row.get("score"), field_path=f"rows[{index}].score"),
        "net_pnl": _net_pnl_or_none(row.get("net_pnl"), field_path=f"rows[{index}].net_pnl"),
        "source_chunk": _source_chunk(row, index=index),
    }
    _validate_execution_evidence_timestamps(row, index=index)
    _validate_execution_scalars(row, index=index)
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
        if setup_type in rule.setup_types:
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


def _validate_serialized_trade_identity(rows: Sequence[Mapping[str, Any]]) -> None:
    seen_by_field: dict[str, set[str]] = {"trade_id": set(), "fill_id": set()}
    for index, row in enumerate(rows, start=1):
        for field in ("trade_id", "fill_id"):
            identity = _optional_canonical_identifier(row, field=field, index=index)
            if identity is None:
                continue
            if identity in seen_by_field[field]:
                raise ValueError(f"duplicate rows[{index}].{field}: {identity}")
            seen_by_field[field].add(identity)


def _serialized_trade_identity(row: Mapping[str, Any], *, index: int) -> dict[str, str]:
    identity: dict[str, str] = {}
    for field in ("trade_id", "fill_id"):
        value = _optional_canonical_identifier(row, field=field, index=index)
        if value is not None:
            identity[field] = value
    return identity


def _optional_canonical_identifier(row: Mapping[str, Any], *, field: str, index: int) -> str | None:
    if field not in row or row[field] is None:
        return None
    value = row[field]
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"rows[{index}].{field} must be a canonical non-blank string when present")
    return value


def _setup_type_matches(*, identity: Mapping[str, Any], rule: SetupRewriteRule) -> bool:
    setup_type = identity["setup_type"]
    return setup_type is not None and setup_type in rule.setup_types


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


def _float_or_none(value: Any, *, field_path: str, present_suffix: str = "") -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field_path} must be a finite number{present_suffix}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_path} must be a finite number{present_suffix}")
    return result


def _net_pnl_or_none(value: Any, *, field_path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field_path} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_path} must be a finite number")
    return result


def _validate_execution_evidence_timestamps(row: Mapping[str, Any], *, index: int) -> None:
    entry_timestamp = _optional_timestamp(
        row.get("entry_timestamp"),
        field_path=f"rows[{index}].entry_timestamp",
    )
    evidence_timestamp = _optional_timestamp(
        row.get("evidence_timestamp"),
        field_path=f"rows[{index}].evidence_timestamp",
    )
    first_fill_timestamp = _optional_timestamp(
        row.get("first_fill_timestamp"),
        field_path=f"rows[{index}].first_fill_timestamp",
    )
    last_fill_timestamp = _optional_timestamp(
        row.get("last_fill_timestamp"),
        field_path=f"rows[{index}].last_fill_timestamp",
    )
    if first_fill_timestamp is not None and last_fill_timestamp is not None:
        if first_fill_timestamp > last_fill_timestamp:
            raise ValueError(f"rows[{index}].first_fill_timestamp must be at or before last_fill_timestamp")
        if evidence_timestamp is not None and not (first_fill_timestamp <= evidence_timestamp <= last_fill_timestamp):
            raise ValueError(f"rows[{index}].evidence_timestamp must fall within fill timestamp interval")
    if evidence_timestamp is not None:
        if entry_timestamp is None:
            raise ValueError(f"rows[{index}].evidence_timestamp requires entry_timestamp")
        if evidence_timestamp > entry_timestamp:
            raise ValueError(f"rows[{index}].evidence_timestamp must be at or before entry_timestamp")


def _validate_execution_scalars(row: Mapping[str, Any], *, index: int) -> None:
    for field_name in ("execution_impact_bps", "slippage_bps"):
        if field_name in row and row[field_name] is not None:
            _float_or_none(
                row[field_name],
                field_path=f"rows[{index}].{field_name}",
                present_suffix=" when present",
            )


def _optional_timestamp(value: Any, *, field_path: str) -> datetime | None:
    if value is None:
        return None
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        raise ValueError(f"{field_path} must be an ISO timestamp")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field_path} must include a timezone offset")
    return timestamp


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _string_or_none(value: Any, *, field_path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_path} must be a string")
    text = str(value).strip()
    return text or None


def _canonical_symbol_or_none(value: Any, *, field_path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_path} must be a string")
    if not value.strip():
        return None
    if value != value.strip() or value != value.upper():
        raise ValueError(f"{field_path} must be a canonical uppercase string")
    return value


def _canonical_setup_type_or_none(value: Any, *, field_path: str) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or not re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*", value)
    ):
        raise ValueError(f"{field_path} must be a canonical uppercase setup type when present")
    return value


def _canonical_side_or_none(value: Any, *, field_path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in {"long", "short"}:
        raise ValueError(f"{field_path} must be long or short when present")
    return value


def _source_chunk(row: Mapping[str, Any], *, index: int) -> str | None:
    for key in ("source_chunk", "chunk", "chunk_name"):
        value = _source_identifier_or_none(row.get(key), field_path=f"rows[{index}].{key}")
        if value is not None:
            return value
    return None


def _source_identifier_or_none(value: Any, *, field_path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_path} must be a string")
    if not value.strip():
        return None
    if value != value.strip():
        raise ValueError(f"{field_path} must be canonical")
    return value
