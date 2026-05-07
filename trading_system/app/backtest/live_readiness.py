from __future__ import annotations

import argparse
import json
import math
import re
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from trading_system.app.backtest.promotion_evidence_bundle import verify_promotion_evidence_bundle


DEPTH_CLASSIFICATIONS = (
    "trade_print_entry_only",
    "has_orderbook_top",
    "has_depth_levels",
    "maker_calibrated_possible",
    "insufficient_for_maker_replay",
)

TRADE_FINANCIAL_FIELDS = ("net_pnl", "gross_pnl", "fee_paid", "slippage_paid", "funding_paid")
TRADE_COST_FIELDS = {"fee_paid", "slippage_paid"}
TRADE_DIMENSION_FIELDS = ("symbol", "side", "setup_type")
TRADE_TIME_FIELDS = ("entry_time", "exit_time")
TRADE_PRICE_FIELDS = ("entry_price", "exit_price")
TRADE_SIZE_FIELDS = ("quantity", "notional")
TRADE_EXIT_REASON_FIELDS = ("simulated_exit_reason", "exit_reason")
VALID_TRADES_ARTIFACT_SCHEMA_VERSION = "trades.v1"
VALID_SUMMARY_ARTIFACT_SCHEMA_VERSION = "backtest_summary.v1"
TRADE_EXECUTION_COST_FIELDS = ("fee_paid", "slippage_paid")
TRADE_ROW_FIELDS = frozenset(
    {
        "depth_levels_consumed",
        "entry_price",
        "entry_time",
        "execution_price_source",
        "exit_fill_quality",
        "exit_price",
        "exit_price_source",
        "exit_reason",
        "exit_time",
        "fee_paid",
        "fill_model",
        "fill_quality",
        "funding_paid",
        "gross_pnl",
        "mae_pct",
        "maker_status",
        "maker_wait_seconds",
        "mfe_pct",
        "net_pnl",
        "notional",
        "quantity",
        "setup_type",
        "side",
        "simulated_exit_ordering",
        "simulated_exit_price",
        "simulated_exit_reason",
        "slippage_paid",
        "symbol",
        "trade_id",
    }
)
VALID_TRADE_SIDES = ("long", "short")
VALID_EXIT_REASONS = ("take_profit", "stop_loss", "stop", "tp", "fixed_horizon")
NOTIONAL_CONSISTENCY_ABS_TOLERANCE = 1e-9
NOTIONAL_CONSISTENCY_REL_TOLERANCE = 1e-6
PNL_CONSISTENCY_ABS_TOLERANCE = 1e-9
PNL_CONSISTENCY_REL_TOLERANCE = 1e-6

EXIT_CLASSIFICATIONS = (
    "fixed_horizon_only",
    "bar_path_stop_or_tp",
    "trade_print_path_available",
    "ambiguous_intrabar_order",
)


def _natural_path_key(path: Path) -> tuple[Any, ...]:
    parts: list[Any] = []
    for token in re.split(r"(\d+)", path.name):
        if not token:
            continue
        parts.append(int(token) if token.isdigit() else token)
    return tuple(parts)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": f"invalid_json: {exc.msg}"}
    return dict(payload) if isinstance(payload, Mapping) else {"_parse_error": "json_payload_not_object"}


def _json_parse_error(payload: Mapping[str, Any]) -> str:
    return str(payload.get("_parse_error") or "")


def _trades_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("trades", payload if isinstance(payload, list) else [])
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _depth_classification(trade: Mapping[str, Any]) -> str:
    fill_model = str(trade.get("fill_model", ""))
    price_source = str(trade.get("execution_price_source", ""))
    if trade.get("depth_levels_consumed") is not None or fill_model == "taker_orderbook_depth":
        return "has_depth_levels"
    if trade.get("maker_status") or trade.get("maker_wait_seconds") is not None or fill_model.startswith("maker_"):
        return "maker_calibrated_possible"
    if price_source in {"best_bid", "best_ask"} or fill_model == "taker_orderbook":
        return "has_orderbook_top"
    if price_source == "trade_print" or fill_model == "taker_trade_print":
        return "trade_print_entry_only"
    return "insufficient_for_maker_replay"


def audit_execution_depth(trades_json: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(trades_json, (str, Path)):
        payload = _load_json(Path(trades_json))
    else:
        payload = dict(trades_json)
    rows = _trades_payload(payload)
    audited: list[dict[str, Any]] = []
    counts = Counter({key: 0 for key in DEPTH_CLASSIFICATIONS})
    for index, trade in enumerate(rows, start=1):
        classification = _depth_classification(trade)
        counts[classification] += 1
        audited.append(
            {
                "index": index,
                "symbol": trade.get("symbol"),
                "side": trade.get("side"),
                "setup_type": trade.get("setup_type"),
                "classification": classification,
                "fill_model": trade.get("fill_model"),
                "execution_price_source": trade.get("execution_price_source"),
            }
        )
    return {
        "schema_version": "execution_depth_readiness_audit.v1",
        "counts": {key: counts[key] for key in DEPTH_CLASSIFICATIONS},
        "trades": audited,
        "caveats": [
            "This is a substitute readiness audit until true historical L2/orderbook replay is available.",
            "trade_print_entry_only confirms entry print evidence but not passive queue position.",
            "maker_calibrated_possible still requires externally captured passive-order calibration provenance.",
        ],
    }


def _dimension_bucket_key(value: Any, field: str) -> str:
    if not isinstance(value, str):
        return "UNKNOWN"
    stripped = value.strip()
    if not stripped:
        return "UNKNOWN"
    if stripped != value:
        return "UNKNOWN"
    if field == "symbol" and not re.fullmatch(r"[A-Z0-9]{3,20}", stripped):
        return "UNKNOWN"
    if field == "setup_type" and not re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*", stripped):
        return "UNKNOWN"
    if field == "side" and stripped not in VALID_TRADE_SIDES:
        return "UNKNOWN"
    return stripped


def _evidence_quality_is_live_grade(value: Any) -> bool:
    return isinstance(value, str) and value in {"evidence_backed", "partial_evidence_backed"}


def _evidence_source_is_live_grade(value: Any) -> bool:
    return isinstance(value, str) and value == "trade_print"


def _evidence_component_invalid(value: Any) -> bool:
    if value is None:
        return False
    if not isinstance(value, str):
        return True
    return value != value.strip().lower()


def _evidence_component_synthetic(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in {"synthetic", "simulated", "unknown"}


def _entry_evidence_live_grade(trade: Mapping[str, Any]) -> bool:
    fill_quality = trade.get("fill_quality")
    execution_source = trade.get("execution_price_source")
    if _evidence_component_invalid(fill_quality) or _evidence_component_invalid(execution_source):
        return False
    if _evidence_component_synthetic(fill_quality) or _evidence_component_synthetic(execution_source):
        return False
    return _evidence_quality_is_live_grade(fill_quality) or _evidence_source_is_live_grade(execution_source)


def _exit_evidence_live_grade(trade: Mapping[str, Any]) -> bool:
    fill_quality = trade.get("exit_fill_quality")
    execution_source = trade.get("exit_price_source")
    if _evidence_component_invalid(fill_quality) or _evidence_component_invalid(execution_source):
        return False
    if _evidence_component_synthetic(fill_quality) or _evidence_component_synthetic(execution_source):
        return False
    return _evidence_quality_is_live_grade(fill_quality) or _evidence_source_is_live_grade(execution_source)


def _execution_trades_for_symbol(market_context: Mapping[str, Any], symbol: str) -> list[Any]:
    symbols = _as_mapping(market_context.get("symbols"))
    payload = _as_mapping(symbols.get(symbol))
    execution = _as_mapping(payload.get("execution"))
    trades = execution.get("trades")
    return trades if isinstance(trades, list) else []


def _exit_classification(trade: Mapping[str, Any], market_context: Mapping[str, Any]) -> str:
    raw_symbol = trade.get("symbol")
    symbol = raw_symbol if isinstance(raw_symbol, str) else ""
    if symbol and _execution_trades_for_symbol(market_context, symbol):
        return "trade_print_path_available"
    raw_simulated_ordering = trade.get("simulated_exit_ordering")
    simulated_ordering = raw_simulated_ordering.lower() if isinstance(raw_simulated_ordering, str) else ""
    if simulated_ordering == "ambiguous_conservative_stop":
        return "ambiguous_intrabar_order"
    raw_simulated_reason = trade.get("simulated_exit_reason")
    simulated_reason = raw_simulated_reason.lower() if isinstance(raw_simulated_reason, str) else ""
    raw_exit_reason = trade.get("exit_reason")
    exit_reason = raw_exit_reason.lower() if isinstance(raw_exit_reason, str) else ""
    if simulated_reason in {"stop_loss", "take_profit", "stop", "tp"} or (
        trade.get("simulated_exit_price") is not None and simulated_reason
    ):
        return "bar_path_stop_or_tp"
    if exit_reason in {"stop_loss", "take_profit", "stop", "tp"}:
        return "ambiguous_intrabar_order"
    if exit_reason == "fixed_horizon" and (trade.get("mfe_pct") is not None or trade.get("mae_pct") is not None):
        return "fixed_horizon_only"
    return "ambiguous_intrabar_order"


def audit_exit_path_replay(
    trades: Iterable[Mapping[str, Any]],
    *,
    market_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context = market_context or {}
    audited: list[dict[str, Any]] = []
    counts = Counter({key: 0 for key in EXIT_CLASSIFICATIONS})
    for index, trade in enumerate(trades, start=1):
        classification = _exit_classification(trade, context)
        counts[classification] += 1
        audited.append(
            {
                "index": index,
                "symbol": trade.get("symbol"),
                "exit_reason": trade.get("exit_reason"),
                "simulated_exit_reason": trade.get("simulated_exit_reason"),
                "simulated_exit_ordering": trade.get("simulated_exit_ordering"),
                "classification": classification,
            }
        )
    return {
        "schema_version": "exit_path_replay_audit.v1",
        "counts": {key: counts[key] for key in EXIT_CLASSIFICATIONS},
        "trades": audited,
        "caveats": [
            "This audit does not invent tick precision where only bar-level MFE/MAE fields exist.",
            "ambiguous_intrabar_order means stop/take-profit ordering cannot be proven from available fields.",
        ],
    }


def _float_value(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _finite_float_value(value: Any) -> tuple[float, bool]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0, False
    if not math.isfinite(parsed):
        return 0.0, False
    return parsed, True


def _trades_artifact_integrity(chunk_dirs: Sequence[Path]) -> dict[str, Any]:
    invalid_artifacts: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        path = chunk_dir / "trades.json"
        if not path.exists():
            invalid_artifacts.append(
                {
                    "chunk": chunk_dir.name,
                    "artifact": "trades.json",
                    "schema_version": None,
                    "error": "missing_artifact",
                }
            )
            continue
        payload = _load_json(path)
        schema_version = payload.get("schema_version")
        parse_error = _json_parse_error(payload)
        if parse_error.startswith("invalid_json"):
            invalid_artifacts.append(
                {
                    "chunk": chunk_dir.name,
                    "artifact": "trades.json",
                    "schema_version": schema_version,
                    "error": "invalid_json",
                }
            )
            continue
        if parse_error == "json_payload_not_object":
            invalid_artifacts.append(
                {
                    "chunk": chunk_dir.name,
                    "artifact": "trades.json",
                    "schema_version": schema_version,
                    "error": "json_payload_not_object",
                }
            )
            continue
        if schema_version != VALID_TRADES_ARTIFACT_SCHEMA_VERSION:
            invalid_artifacts.append(
                {
                    "chunk": chunk_dir.name,
                    "artifact": "trades.json",
                    "schema_version": schema_version,
                    "error": "invalid_or_missing_schema_version",
                }
            )
        rows = payload.get("trades", [])
        unknown_top_level_fields = sorted(set(payload) - {"schema_version", "trades"})
        for field in unknown_top_level_fields:
            invalid_artifacts.append(
                {
                    "chunk": chunk_dir.name,
                    "artifact": "trades.json",
                    "schema_version": schema_version,
                    "field": field,
                    "error": "unknown_top_level_field",
                }
            )
        if not isinstance(rows, list):
            invalid_artifacts.append(
                {
                    "chunk": chunk_dir.name,
                    "artifact": "trades.json",
                    "schema_version": schema_version,
                    "error": "trades_rows_not_list",
                }
            )
            continue
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, Mapping):
                invalid_artifacts.append(
                    {
                        "chunk": chunk_dir.name,
                        "artifact": "trades.json",
                        "schema_version": schema_version,
                        "index": index,
                        "error": "trade_row_not_object",
                    }
                )
                continue
            unknown_trade_fields = sorted(set(row) - TRADE_ROW_FIELDS)
            for field in unknown_trade_fields:
                invalid_artifacts.append(
                    {
                        "chunk": chunk_dir.name,
                        "artifact": "trades.json",
                        "schema_version": schema_version,
                        "index": index,
                        "field": f"trades[{index}].{field}",
                        "error": "unknown_trade_row_field",
                    }
                )
    return {
        "schema_version": "trades_artifact_integrity.v1",
        "valid": not invalid_artifacts,
        "expected_schema_version": VALID_TRADES_ARTIFACT_SCHEMA_VERSION,
        "invalid_artifacts": invalid_artifacts[:100],
        "invalid_artifact_count": len(invalid_artifacts),
    }


def _summary_artifact_integrity(chunk_dirs: Sequence[Path]) -> dict[str, Any]:
    invalid_artifacts: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        path = chunk_dir / "summary.json"
        if not path.exists():
            invalid_artifacts.append(
                {
                    "chunk": chunk_dir.name,
                    "artifact": "summary.json",
                    "error": "missing_artifact",
                }
            )
            continue
        payload = _load_json(path)
        parse_error = _json_parse_error(payload)
        if parse_error.startswith("invalid_json"):
            invalid_artifacts.append(
                {
                    "chunk": chunk_dir.name,
                    "artifact": "summary.json",
                    "error": "invalid_json",
                }
            )
            continue
        elif parse_error == "json_payload_not_object":
            invalid_artifacts.append(
                {
                    "chunk": chunk_dir.name,
                    "artifact": "summary.json",
                    "error": "json_payload_not_object",
                }
            )
            continue
        else:
            schema_version = payload.get("schema_version")
            if schema_version != VALID_SUMMARY_ARTIFACT_SCHEMA_VERSION:
                invalid_artifacts.append(
                    {
                        "chunk": chunk_dir.name,
                        "artifact": "summary.json",
                        "schema_version": schema_version,
                        "expected_schema_version": VALID_SUMMARY_ARTIFACT_SCHEMA_VERSION,
                        "error": "invalid_or_missing_schema_version",
                    }
                )
                continue
            unknown_top_level_fields = sorted(set(payload) - {"schema_version", "summary"})
            for field in unknown_top_level_fields:
                invalid_artifacts.append(
                    {
                        "chunk": chunk_dir.name,
                        "artifact": "summary.json",
                        "schema_version": schema_version,
                        "field": field,
                        "error": "unknown_top_level_field",
                    }
                )
        summary_payload = payload.get("summary")
        if not isinstance(summary_payload, Mapping):
            invalid_artifacts.append(
                {
                    "chunk": chunk_dir.name,
                    "artifact": "summary.json",
                    "field": "summary",
                    "error": "summary_not_object",
                }
            )
            continue
        summary = _as_mapping(summary_payload)
        unknown_summary_fields = sorted(set(summary) - {"trade_count", "cost_breakdown"})
        for field in unknown_summary_fields:
            invalid_artifacts.append(
                {
                    "chunk": chunk_dir.name,
                    "artifact": "summary.json",
                    "field": f"summary.{field}",
                    "error": "unknown_summary_field",
                }
            )
        trades = _trades_payload(_load_json(chunk_dir / "trades.json"))
        trade_count = summary.get("trade_count")
        if trade_count is None:
            invalid_artifacts.append(
                {
                    "chunk": chunk_dir.name,
                    "artifact": "summary.json",
                    "field": "summary.trade_count",
                    "error": "missing_summary_trade_count",
                }
            )
        else:
            parsed_trade_count, valid_trade_count = _strict_summary_int_value(trade_count)
            if not valid_trade_count:
                invalid_artifacts.append(
                    {
                        "chunk": chunk_dir.name,
                        "artifact": "summary.json",
                        "field": "summary.trade_count",
                        "value": trade_count,
                        "error": "invalid_summary_trade_count",
                    }
                )
            elif parsed_trade_count != len(trades):
                invalid_artifacts.append(
                    {
                        "chunk": chunk_dir.name,
                        "artifact": "summary.json",
                        "field": "summary.trade_count",
                        "value": trade_count,
                        "expected": len(trades),
                        "error": "summary_trade_count_mismatch",
                    }
                )
        cost_breakdown_payload = summary.get("cost_breakdown")
        if not isinstance(cost_breakdown_payload, Mapping):
            invalid_artifacts.append(
                {
                    "chunk": chunk_dir.name,
                    "artifact": "summary.json",
                    "field": "summary.cost_breakdown",
                    "error": "cost_breakdown_not_object",
                }
            )
            continue
        cost_breakdown = _as_mapping(cost_breakdown_payload)
        expected_costs = {
            "fees": sum(_strict_float_or_zero(trade.get("fee_paid")) for trade in trades),
            "slippage": sum(_strict_float_or_zero(trade.get("slippage_paid")) for trade in trades),
            "funding": sum(_strict_float_or_zero(trade.get("funding_paid")) for trade in trades),
        }
        for field in expected_costs:
            if field not in cost_breakdown:
                invalid_artifacts.append(
                    {
                        "chunk": chunk_dir.name,
                        "artifact": "summary.json",
                        "field": f"summary.cost_breakdown.{field}",
                        "error": "missing_cost_breakdown_field",
                    }
                )
        for field in cost_breakdown:
            if field not in expected_costs:
                invalid_artifacts.append(
                    {
                        "chunk": chunk_dir.name,
                        "artifact": "summary.json",
                        "field": f"summary.cost_breakdown.{field}",
                        "error": "unknown_cost_breakdown_field",
                    }
                )
        for field, value in cost_breakdown.items():
            parsed, valid = _strict_float_value(value)
            if not valid:
                invalid_artifacts.append(
                    {
                        "chunk": chunk_dir.name,
                        "artifact": "summary.json",
                        "field": f"summary.cost_breakdown.{field}",
                        "value": value,
                        "error": "invalid_cost_breakdown_value",
                    }
                )
                continue
            if field in expected_costs:
                expected = expected_costs[field]
                tolerance = max(PNL_CONSISTENCY_ABS_TOLERANCE, abs(expected) * PNL_CONSISTENCY_REL_TOLERANCE)
                if abs(parsed - expected) > tolerance:
                    invalid_artifacts.append(
                        {
                            "chunk": chunk_dir.name,
                            "artifact": "summary.json",
                            "field": f"summary.cost_breakdown.{field}",
                            "value": value,
                            "expected": expected,
                            "error": "summary_cost_breakdown_mismatch",
                        }
                    )
    return {
        "schema_version": "summary_artifact_integrity.v1",
        "valid": not invalid_artifacts,
        "invalid_artifacts": invalid_artifacts[:100],
        "invalid_artifact_count": len(invalid_artifacts),
    }


def _is_chunk_result_dir(path: Path) -> bool:
    return path.is_dir() and ((path / "trades.json").exists() or re.fullmatch(r"chunk(?:_\d+)?", path.name) is not None)


def _trade_financial_integrity(chunk_dirs: Sequence[Path]) -> dict[str, Any]:
    invalid_fields: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        rows = _trades_payload(_load_json(chunk_dir / "trades.json"))
        for index, trade in enumerate(rows, start=1):
            for field in TRADE_FINANCIAL_FIELDS:
                if field not in trade:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": None,
                            "error": "missing_required_field",
                        }
                    )
                    continue
                value = trade.get(field)
                parsed, valid = _strict_float_value(value)
                if not valid:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "invalid_financial_field",
                        }
                    )
                    continue
                if field in TRADE_COST_FIELDS and parsed < 0.0:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "negative_financial_field",
                        }
                    )
    return {
        "schema_version": "trade_financial_integrity.v1",
        "valid": not invalid_fields,
        "invalid_fields": invalid_fields[:100],
        "invalid_field_count": len(invalid_fields),
    }


def _trade_identity_integrity(chunk_dirs: Sequence[Path]) -> dict[str, Any]:
    missing_trade_ids: list[dict[str, Any]] = []
    invalid_trade_ids: list[dict[str, Any]] = []
    occurrences: dict[str, list[dict[str, Any]]] = {}
    for chunk_dir in chunk_dirs:
        rows = _trades_payload(_load_json(chunk_dir / "trades.json"))
        for index, trade in enumerate(rows, start=1):
            raw_trade_id = trade.get("trade_id")
            location = {"chunk": chunk_dir.name, "index": index}
            if raw_trade_id is None:
                missing_trade_ids.append(location)
                continue
            if not isinstance(raw_trade_id, str):
                invalid_trade_ids.append({**location, "trade_id": raw_trade_id, "error": "trade_id_not_string"})
                continue
            trade_id = raw_trade_id.strip()
            if trade_id != raw_trade_id:
                invalid_trade_ids.append({**location, "trade_id": raw_trade_id, "error": "trade_id_not_canonical"})
                continue
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", trade_id):
                invalid_trade_ids.append({**location, "trade_id": raw_trade_id, "error": "invalid_trade_id"})
                continue
            occurrences.setdefault(trade_id, []).append(location)
    duplicate_trade_ids = [
        {"trade_id": trade_id, "occurrences": locations}
        for trade_id, locations in sorted(occurrences.items())
        if len(locations) > 1
    ]
    return {
        "schema_version": "trade_identity_integrity.v1",
        "valid": not missing_trade_ids and not invalid_trade_ids and not duplicate_trade_ids,
        "missing_trade_ids": missing_trade_ids[:100],
        "missing_trade_id_count": len(missing_trade_ids),
        "invalid_trade_ids": invalid_trade_ids[:100],
        "invalid_trade_id_count": len(invalid_trade_ids),
        "duplicate_trade_ids": duplicate_trade_ids[:100],
        "duplicate_trade_id_count": len(duplicate_trade_ids),
    }


def _trade_dimension_integrity(chunk_dirs: Sequence[Path]) -> dict[str, Any]:
    invalid_fields: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        rows = _trades_payload(_load_json(chunk_dir / "trades.json"))
        for index, trade in enumerate(rows, start=1):
            for field in TRADE_DIMENSION_FIELDS:
                value = trade.get(field)
                if value is None:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "missing_or_blank_dimension",
                        }
                    )
                    continue
                if not isinstance(value, str):
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "dimension_not_string",
                        }
                    )
                    continue
                stripped = value.strip()
                if not stripped:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "missing_or_blank_dimension",
                        }
                    )
                    continue
                if stripped != value:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "dimension_not_canonical",
                        }
                    )
                    continue
                if field == "symbol" and not re.fullmatch(r"[A-Z0-9]{3,20}", stripped):
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "invalid_symbol",
                        }
                    )
                if field == "setup_type" and not re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*", stripped):
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "invalid_setup_type",
                        }
                    )
                if field == "side" and stripped not in VALID_TRADE_SIDES:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "invalid_side",
                        }
                    )
    return {
        "schema_version": "trade_dimension_integrity.v1",
        "valid": not invalid_fields,
        "invalid_fields": invalid_fields[:100],
        "invalid_field_count": len(invalid_fields),
    }


def _parse_trade_time(value: Any) -> tuple[datetime | None, str | None]:
    if not isinstance(value, str):
        return None, "missing_or_invalid_timestamp"
    stripped = value.strip()
    if not stripped:
        return None, "missing_or_invalid_timestamp"
    if stripped != value:
        return None, "timestamp_not_canonical"
    normalized = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None, "missing_or_invalid_timestamp"
    if parsed.tzinfo is None:
        return None, "timestamp_missing_timezone"
    return parsed.astimezone(UTC), None


def _trade_time_integrity(chunk_dirs: Sequence[Path]) -> dict[str, Any]:
    invalid_fields: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        rows = _trades_payload(_load_json(chunk_dir / "trades.json"))
        for index, trade in enumerate(rows, start=1):
            parsed_times: dict[str, datetime] = {}
            for field in TRADE_TIME_FIELDS:
                value = trade.get(field)
                parsed, parse_error = _parse_trade_time(value)
                if parsed is None:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": parse_error or "missing_or_invalid_timestamp",
                        }
                    )
                    continue
                parsed_times[field] = parsed
            entry_time = parsed_times.get("entry_time")
            exit_time = parsed_times.get("exit_time")
            if entry_time is not None and exit_time is not None and exit_time <= entry_time:
                invalid_fields.append(
                    {
                        "chunk": chunk_dir.name,
                        "index": index,
                        "field": "exit_time",
                        "value": trade.get("exit_time"),
                        "entry_time": trade.get("entry_time"),
                        "error": "exit_before_entry" if exit_time < entry_time else "non_positive_duration",
                    }
                )
    return {
        "schema_version": "trade_time_integrity.v1",
        "valid": not invalid_fields,
        "invalid_fields": invalid_fields[:100],
        "invalid_field_count": len(invalid_fields),
    }


def _int_value(value: Any) -> tuple[int, bool]:
    if isinstance(value, bool):
        return 0, False
    if isinstance(value, int):
        return value, True
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value), True
        return 0, False
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0, False
        try:
            parsed = int(stripped, 10)
        except ValueError:
            return 0, False
        return parsed, True
    return 0, False


def _strict_summary_int_value(value: Any) -> tuple[int, bool]:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0, False
    return value, True



def _strict_float_value(value: Any) -> tuple[float, bool]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0, False
    parsed = float(value)
    if not math.isfinite(parsed):
        return 0.0, False
    return parsed, True


def _strict_float_or_zero(value: Any) -> float:
    parsed, valid = _strict_float_value(value)
    return parsed if valid else 0.0


def _trade_price_integrity(chunk_dirs: Sequence[Path]) -> dict[str, Any]:
    invalid_fields: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        rows = _trades_payload(_load_json(chunk_dir / "trades.json"))
        for index, trade in enumerate(rows, start=1):
            for field in TRADE_PRICE_FIELDS:
                if field not in trade:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": None,
                            "error": "missing_price",
                        }
                    )
                    continue
                value = trade.get(field)
                parsed, valid = _strict_float_value(value)
                if not valid:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "invalid_price",
                        }
                    )
                    continue
                if parsed <= 0.0:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "non_positive_price",
                        }
                    )
    return {
        "schema_version": "trade_price_integrity.v1",
        "valid": not invalid_fields,
        "invalid_fields": invalid_fields[:100],
        "invalid_field_count": len(invalid_fields),
    }


def _trade_size_integrity(chunk_dirs: Sequence[Path]) -> dict[str, Any]:
    invalid_fields: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        rows = _trades_payload(_load_json(chunk_dir / "trades.json"))
        for index, trade in enumerate(rows, start=1):
            for field in TRADE_SIZE_FIELDS:
                if field not in trade:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": None,
                            "error": "missing_size",
                        }
                    )
                    continue
                value = trade.get(field)
                parsed, valid = _strict_float_value(value)
                if not valid:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "invalid_size",
                        }
                    )
                    continue
                if parsed <= 0.0:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "non_positive_size",
                        }
                    )
    return {
        "schema_version": "trade_size_integrity.v1",
        "valid": not invalid_fields,
        "invalid_fields": invalid_fields[:100],
        "invalid_field_count": len(invalid_fields),
    }


def _trade_notional_consistency(chunk_dirs: Sequence[Path]) -> dict[str, Any]:
    invalid_fields: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        rows = _trades_payload(_load_json(chunk_dir / "trades.json"))
        for index, trade in enumerate(rows, start=1):
            entry_price, entry_price_valid = _strict_float_value(trade.get("entry_price"))
            quantity, quantity_valid = _strict_float_value(trade.get("quantity"))
            notional, notional_valid = _strict_float_value(trade.get("notional"))
            numeric_fields = (
                ("entry_price", trade.get("entry_price"), entry_price_valid),
                ("quantity", trade.get("quantity"), quantity_valid),
                ("notional", trade.get("notional"), notional_valid),
            )
            invalid_numeric_fields = [item for item in numeric_fields if not item[2]]
            negative_numeric_fields = [
                (field, value, "negative_numeric_field" if parsed < 0.0 else "non_positive_numeric_field")
                for field, value, _valid, parsed in (
                    ("entry_price", trade.get("entry_price"), entry_price_valid, entry_price),
                    ("quantity", trade.get("quantity"), quantity_valid, quantity),
                    ("notional", trade.get("notional"), notional_valid, notional),
                )
                if _valid and parsed <= 0.0
            ]
            if invalid_numeric_fields:
                for field, value, _valid in invalid_numeric_fields:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "invalid_numeric_field",
                        }
                    )
                continue
            if negative_numeric_fields:
                for field, value, error in negative_numeric_fields:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": error,
                        }
                    )
                continue
            expected = entry_price * quantity
            tolerance = max(
                NOTIONAL_CONSISTENCY_ABS_TOLERANCE,
                abs(expected) * NOTIONAL_CONSISTENCY_REL_TOLERANCE,
            )
            if abs(notional - expected) > tolerance:
                invalid_fields.append(
                    {
                        "chunk": chunk_dir.name,
                        "index": index,
                        "field": "notional",
                        "value": notional,
                        "expected": expected,
                        "error": "notional_mismatch",
                    }
                )
    return {
        "schema_version": "trade_notional_consistency.v1",
        "valid": not invalid_fields,
        "invalid_fields": invalid_fields[:100],
        "invalid_field_count": len(invalid_fields),
        "absolute_tolerance": NOTIONAL_CONSISTENCY_ABS_TOLERANCE,
        "relative_tolerance": NOTIONAL_CONSISTENCY_REL_TOLERANCE,
    }


def _trade_cost_sign_integrity(chunk_dirs: Sequence[Path]) -> dict[str, Any]:
    invalid_fields: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        rows = _trades_payload(_load_json(chunk_dir / "trades.json"))
        for index, trade in enumerate(rows, start=1):
            for field in TRADE_EXECUTION_COST_FIELDS:
                if field not in trade:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": None,
                            "error": "missing_execution_cost",
                        }
                    )
                    continue
                value = trade.get(field)
                parsed, valid = _strict_float_value(value)
                if not valid:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "invalid_execution_cost",
                        }
                    )
                    continue
                if parsed < 0.0:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "negative_execution_cost",
                        }
                    )
    return {
        "schema_version": "trade_cost_sign_integrity.v1",
        "valid": not invalid_fields,
        "invalid_fields": invalid_fields[:100],
        "invalid_field_count": len(invalid_fields),
        "nonnegative_cost_fields": list(TRADE_EXECUTION_COST_FIELDS),
        "funding_paid_policy": "signed_funding_allowed",
    }


def _trade_pnl_consistency(chunk_dirs: Sequence[Path]) -> dict[str, Any]:
    invalid_fields: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        rows = _trades_payload(_load_json(chunk_dir / "trades.json"))
        for index, trade in enumerate(rows, start=1):
            net_pnl, net_valid = _strict_float_value(trade.get("net_pnl"))
            gross_pnl, gross_valid = _strict_float_value(trade.get("gross_pnl"))
            fee_paid, fee_valid = _strict_float_value(trade.get("fee_paid"))
            slippage_paid, slippage_valid = _strict_float_value(trade.get("slippage_paid"))
            funding_paid, funding_valid = _strict_float_value(trade.get("funding_paid"))
            numeric_fields = (
                ("net_pnl", trade.get("net_pnl"), net_valid),
                ("gross_pnl", trade.get("gross_pnl"), gross_valid),
                ("fee_paid", trade.get("fee_paid"), fee_valid),
                ("slippage_paid", trade.get("slippage_paid"), slippage_valid),
                ("funding_paid", trade.get("funding_paid"), funding_valid),
            )
            invalid_numeric_fields = [item for item in numeric_fields if not item[2]]
            negative_cost_fields = [
                (field, value)
                for field, value, _valid, parsed in (
                    ("fee_paid", trade.get("fee_paid"), fee_valid, fee_paid),
                    ("slippage_paid", trade.get("slippage_paid"), slippage_valid, slippage_paid),
                )
                if _valid and parsed < 0.0
            ]
            if invalid_numeric_fields:
                for field, value, _valid in invalid_numeric_fields:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "invalid_numeric_field",
                        }
                    )
                continue
            if negative_cost_fields:
                for field, value in negative_cost_fields:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "negative_numeric_field",
                        }
                    )
                continue
            expected = gross_pnl - fee_paid - slippage_paid - funding_paid
            tolerance = max(PNL_CONSISTENCY_ABS_TOLERANCE, abs(expected) * PNL_CONSISTENCY_REL_TOLERANCE)
            if abs(net_pnl - expected) > tolerance:
                invalid_fields.append(
                    {
                        "chunk": chunk_dir.name,
                        "index": index,
                        "field": "net_pnl",
                        "value": net_pnl,
                        "expected": expected,
                        "error": "net_pnl_mismatch",
                    }
                )
    return {
        "schema_version": "trade_pnl_consistency.v1",
        "valid": not invalid_fields,
        "invalid_fields": invalid_fields[:100],
        "invalid_field_count": len(invalid_fields),
        "absolute_tolerance": PNL_CONSISTENCY_ABS_TOLERANCE,
        "relative_tolerance": PNL_CONSISTENCY_REL_TOLERANCE,
    }


def _trade_side_price_pnl_consistency(chunk_dirs: Sequence[Path]) -> dict[str, Any]:
    invalid_fields: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        rows = _trades_payload(_load_json(chunk_dir / "trades.json"))
        for index, trade in enumerate(rows, start=1):
            raw_side = trade.get("side")
            if not isinstance(raw_side, str):
                invalid_fields.append(
                    {
                        "chunk": chunk_dir.name,
                        "index": index,
                        "field": "side",
                        "value": raw_side,
                        "error": "side_not_string",
                    }
                )
                continue
            side = raw_side.strip()
            if not side or side not in VALID_TRADE_SIDES:
                invalid_fields.append(
                    {
                        "chunk": chunk_dir.name,
                        "index": index,
                        "field": "side",
                        "value": raw_side,
                        "error": "invalid_side",
                    }
                )
                continue
            entry_price, entry_valid = _strict_float_value(trade.get("entry_price"))
            exit_price, exit_valid = _strict_float_value(trade.get("exit_price"))
            quantity, quantity_valid = _strict_float_value(trade.get("quantity"))
            gross_pnl, gross_valid = _strict_float_value(trade.get("gross_pnl"))
            numeric_fields = (
                ("entry_price", trade.get("entry_price"), entry_valid),
                ("exit_price", trade.get("exit_price"), exit_valid),
                ("quantity", trade.get("quantity"), quantity_valid),
                ("gross_pnl", trade.get("gross_pnl"), gross_valid),
            )
            invalid_numeric_fields = [item for item in numeric_fields if not item[2]]
            negative_numeric_fields = [
                (field, value, "negative_numeric_field" if parsed < 0.0 else "non_positive_numeric_field")
                for field, value, _valid, parsed in (
                    ("entry_price", trade.get("entry_price"), entry_valid, entry_price),
                    ("exit_price", trade.get("exit_price"), exit_valid, exit_price),
                    ("quantity", trade.get("quantity"), quantity_valid, quantity),
                )
                if _valid and parsed <= 0.0
            ]
            if invalid_numeric_fields:
                for field, value, _valid in invalid_numeric_fields:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "invalid_numeric_field",
                        }
                    )
                continue
            if negative_numeric_fields:
                for field, value, error in negative_numeric_fields:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": error,
                        }
                    )
                continue
            expected = (exit_price - entry_price) * quantity if side == "long" else (entry_price - exit_price) * quantity
            tolerance = max(PNL_CONSISTENCY_ABS_TOLERANCE, abs(expected) * PNL_CONSISTENCY_REL_TOLERANCE)
            if abs(gross_pnl - expected) > tolerance:
                invalid_fields.append(
                    {
                        "chunk": chunk_dir.name,
                        "index": index,
                        "field": "gross_pnl",
                        "value": gross_pnl,
                        "expected": expected,
                        "error": "side_price_pnl_mismatch",
                    }
                )
    return {
        "schema_version": "trade_side_price_pnl_consistency.v1",
        "valid": not invalid_fields,
        "invalid_fields": invalid_fields[:100],
        "invalid_field_count": len(invalid_fields),
        "absolute_tolerance": PNL_CONSISTENCY_ABS_TOLERANCE,
        "relative_tolerance": PNL_CONSISTENCY_REL_TOLERANCE,
    }


def _trade_exit_reason_integrity(chunk_dirs: Sequence[Path]) -> dict[str, Any]:
    invalid_fields: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        rows = _trades_payload(_load_json(chunk_dir / "trades.json"))
        for index, trade in enumerate(rows, start=1):
            reasons: dict[str, str] = {}
            invalid_reason_seen = False
            for field in TRADE_EXIT_REASON_FIELDS:
                value = trade.get(field)
                if value is None:
                    continue
                if not isinstance(value, str):
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "exit_reason_not_string",
                        }
                    )
                    invalid_reason_seen = True
                    continue
                reason = value.strip()
                if not reason:
                    continue
                if reason != value:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "exit_reason_not_canonical",
                        }
                    )
                    invalid_reason_seen = True
                    continue
                reasons[field] = reason
                if reason not in VALID_EXIT_REASONS:
                    invalid_fields.append(
                        {
                            "chunk": chunk_dir.name,
                            "index": index,
                            "field": field,
                            "value": value,
                            "error": "invalid_exit_reason",
                        }
                    )
                    invalid_reason_seen = True
            if not reasons and not invalid_reason_seen:
                invalid_fields.append(
                    {
                        "chunk": chunk_dir.name,
                        "index": index,
                        "field": "exit_reason",
                        "value": None,
                        "error": "missing_exit_reason",
                    }
                )
            elif (
                "exit_reason" in reasons
                and "simulated_exit_reason" in reasons
                and reasons["exit_reason"] != reasons["simulated_exit_reason"]
            ):
                invalid_fields.append(
                    {
                        "chunk": chunk_dir.name,
                        "index": index,
                        "field": "exit_reason",
                        "value": trade.get("exit_reason"),
                        "simulated_exit_reason": trade.get("simulated_exit_reason"),
                        "error": "conflicting_exit_reasons",
                    }
                )
    return {
        "schema_version": "trade_exit_reason_integrity.v1",
        "valid": not invalid_fields,
        "invalid_fields": invalid_fields[:100],
        "invalid_field_count": len(invalid_fields),
    }


def _bucket_add(bucket: dict[str, Any], trade: Mapping[str, Any]) -> None:
    bucket["trade_count"] += 1
    bucket["net_pnl"] += _strict_float_or_zero(trade.get("net_pnl"))
    bucket["gross_pnl"] += _strict_float_or_zero(trade.get("gross_pnl"))
    bucket["fees"] += _strict_float_or_zero(trade.get("fee_paid"))
    bucket["slippage"] += _strict_float_or_zero(trade.get("slippage_paid"))
    bucket["funding"] += _strict_float_or_zero(trade.get("funding_paid"))


def _empty_bucket(name: str, key_name: str) -> dict[str, Any]:
    return {key_name: name, "trade_count": 0, "net_pnl": 0.0, "gross_pnl": 0.0, "fees": 0.0, "slippage": 0.0, "funding": 0.0}


def _add_group(groups: dict[str, dict[str, Any]], key: str, key_name: str, trade: Mapping[str, Any]) -> None:
    bucket = groups.setdefault(key, _empty_bucket(key, key_name))
    _bucket_add(bucket, trade)


def _chunk_report(chunk_dir: Path) -> dict[str, Any]:
    trades_payload = _load_json(chunk_dir / "trades.json")
    summary_payload = _load_json(chunk_dir / "summary.json")
    trades = _trades_payload(trades_payload)
    net_pnl = sum(_strict_float_or_zero(trade.get("net_pnl")) for trade in trades)
    evidence_count = sum(1 for trade in trades if _entry_evidence_live_grade(trade))
    exit_evidence_count = sum(1 for trade in trades if _exit_evidence_live_grade(trade))
    cost_breakdown = _as_mapping(_as_mapping(summary_payload.get("summary")).get("cost_breakdown"))
    normalized_costs = {
        field: _strict_float_or_zero(cost_breakdown.get(field)) for field in ("fees", "slippage", "funding")
    }
    metadata = _as_mapping(trades_payload.get("metadata"))
    period = metadata.get("sample_period")
    return {
        "chunk": chunk_dir.name,
        "path": str(chunk_dir),
        "trade_count": len(trades),
        "net_pnl": net_pnl,
        "gross_pnl": sum(_strict_float_or_zero(trade.get("gross_pnl")) for trade in trades),
        "costs": normalized_costs,
        "evidence_coverage": evidence_count / len(trades) if trades else 0.0,
        "exit_evidence_coverage": exit_evidence_count / len(trades) if trades else 0.0,
        "regime": metadata.get("regime") or metadata.get("regime_label"),
        "sample_period": period if isinstance(period, Mapping) else {},
    }


SETUP_REWRITE_COUNT_FIELDS = ("evaluated_count", "would_keep_count", "would_filter_count", "skipped_count")


def _setup_rewrite_counts(summary: Mapping[str, Any]) -> tuple[dict[str, int], str]:
    counts: dict[str, int] = {}
    parse_error = ""
    if "total_rows" in summary:
        total_rows, total_rows_valid = _strict_summary_int_value(summary.get("total_rows"))
        if not total_rows_valid:
            parse_error = "invalid_numeric_field: summary.total_rows"
    else:
        total_rows = None
    for field in SETUP_REWRITE_COUNT_FIELDS:
        raw_value = summary.get(field, 0)
        parsed, valid = _strict_summary_int_value(raw_value)
        counts[field] = parsed if valid else 0
        if not valid and not parse_error:
            parse_error = f"invalid_numeric_field: summary.{field}"
    if total_rows is not None and not parse_error:
        counted_rows = counts["would_keep_count"] + counts["would_filter_count"] + counts["skipped_count"]
        if counted_rows != total_rows:
            parse_error = "summary_count_mismatch"
    return counts, parse_error


def _setup_rewrite_by_setup_schema_error(summary: Mapping[str, Any]) -> str:
    if "by_setup" not in summary:
        return ""
    by_setup = summary.get("by_setup")
    if not isinstance(by_setup, Mapping):
        return "invalid_field_type: summary.by_setup"
    allowed_bucket_fields = {"total_rows", "evaluated_count", "would_keep_count", "would_filter_count", "skipped_count", "net_pnl"}
    for setup_type, bucket in by_setup.items():
        if not isinstance(setup_type, str) or not setup_type.strip():
            return "invalid_by_setup_key"
        if not isinstance(bucket, Mapping):
            return f"invalid_by_setup_bucket: {setup_type}"
        unknown_fields = sorted(set(bucket) - allowed_bucket_fields)
        if unknown_fields:
            return f"unknown_by_setup_field: {setup_type}." + ", ".join(unknown_fields)
        for field in ("total_rows", "evaluated_count", "would_keep_count", "would_filter_count", "skipped_count"):
            if field in bucket:
                _, valid = _strict_summary_int_value(bucket.get(field))
                if not valid:
                    return f"invalid_numeric_field: summary.by_setup.{setup_type}.{field}"
        if "net_pnl" in bucket:
            _, net_pnl_valid = _strict_float_value(bucket.get("net_pnl"))
            if not net_pnl_valid:
                return f"invalid_numeric_field: summary.by_setup.{setup_type}.net_pnl"
    return ""


def _add_setup_rewrite_bucket(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key in ("total_rows", "evaluated_count", "would_keep_count", "would_filter_count", "skipped_count"):
        target[key] += int(source.get(key) or 0)
    target["net_pnl"] += _float_value(source.get("net_pnl"))


def _empty_setup_rewrite_bucket() -> dict[str, Any]:
    return {
        "total_rows": 0,
        "evaluated_count": 0,
        "would_keep_count": 0,
        "would_filter_count": 0,
        "skipped_count": 0,
        "net_pnl": 0.0,
    }


def _setup_rewrite_diagnostic(chunk_dirs: Iterable[Path]) -> dict[str, Any] | None:
    chunks: list[dict[str, Any]] = []
    totals = {"evaluated_count": 0, "would_keep_count": 0, "would_filter_count": 0, "skipped_count": 0}
    reasons: Counter[str] = Counter()
    by_setup: dict[str, dict[str, Any]] = {}
    artifact_schema_valid = True
    for chunk_dir in chunk_dirs:
        path = chunk_dir / "setup_rewrite_experiment.json"
        if not path.exists():
            continue
        payload = _load_json(path)
        parse_error = _json_parse_error(payload)
        summary_payload = payload.get("summary")
        summary_object_valid = isinstance(summary_payload, Mapping)
        summary = _as_mapping(summary_payload)
        if not parse_error and not summary_object_valid:
            parse_error = "invalid_field_type: summary"
        counts, count_parse_error = _setup_rewrite_counts(summary)
        parse_error = parse_error or count_parse_error
        unknown_summary_fields = sorted(set(summary) - (set(SETUP_REWRITE_COUNT_FIELDS) | {"total_rows", "by_setup"}))
        if not parse_error and unknown_summary_fields:
            parse_error = "unknown_summary_field: " + ", ".join(unknown_summary_fields)
        by_setup_schema_error = _setup_rewrite_by_setup_schema_error(summary)
        if not parse_error and by_setup_schema_error:
            parse_error = by_setup_schema_error
        unknown_top_level_fields = sorted(set(payload) - {"summary", "evaluation_rows"})
        if not parse_error and unknown_top_level_fields:
            parse_error = "unknown_top_level_field: " + ", ".join(unknown_top_level_fields)
        evaluation_rows = payload.get("evaluation_rows")
        if not parse_error and evaluation_rows is None:
            parse_error = "missing_required_field: evaluation_rows"
        if evaluation_rows is None:
            evaluation_rows = []
        if not parse_error and not isinstance(evaluation_rows, list):
            parse_error = "invalid_field_type: evaluation_rows"
        if not parse_error:
            row_count = len(evaluation_rows)
            if "total_rows" in summary:
                expected_count, _ = _strict_summary_int_value(summary.get("total_rows"))
            else:
                expected_count = row_count
            if expected_count != row_count:
                parse_error = "row_count_mismatch: evaluation_rows"
        if not parse_error:
            allowed_row_fields = {"symbol", "setup_type", "evaluation_status", "evaluation_reason", "would_keep", "net_pnl"}
            allowed_evaluation_statuses = {"evaluated", "no_evidence"}
            for row_index, row in enumerate(evaluation_rows, start=1):
                if not isinstance(row, Mapping):
                    parse_error = f"invalid_row_type: evaluation_rows[{row_index}]"
                    break
                unknown_row_fields = sorted(set(row) - allowed_row_fields)
                if unknown_row_fields:
                    parse_error = f"unknown_evaluation_row_field: evaluation_rows[{row_index}]." + ", ".join(unknown_row_fields)
                    break
                for field in ("symbol", "setup_type", "evaluation_status", "evaluation_reason"):
                    if field in row and not isinstance(row.get(field), str):
                        parse_error = f"invalid_field_type: evaluation_rows[{row_index}].{field}"
                        break
                if parse_error:
                    break
                evaluation_status = row.get("evaluation_status")
                if evaluation_status is not None and evaluation_status not in allowed_evaluation_statuses:
                    parse_error = f"unknown_evaluation_status: evaluation_rows[{row_index}]"
                    break
                if evaluation_status == "evaluated" and "would_keep" not in row:
                    parse_error = f"missing_required_field: evaluation_rows[{row_index}].would_keep"
                    break
                if evaluation_status == "no_evidence" and row.get("would_keep") is True:
                    parse_error = f"inconsistent_evaluation_row: evaluation_rows[{row_index}]"
                    break
                if "would_keep" in row and not isinstance(row.get("would_keep"), bool):
                    parse_error = f"invalid_field_type: evaluation_rows[{row_index}].would_keep"
                    break
                if "net_pnl" in row:
                    _, net_pnl_valid = _strict_float_value(row.get("net_pnl"))
                    if not net_pnl_valid:
                        parse_error = f"invalid_numeric_field: evaluation_rows[{row_index}].net_pnl"
                        break
        if not parse_error and "would_keep_count" in summary:
            observed_keep_count = sum(1 for row in evaluation_rows if isinstance(row, Mapping) and row.get("would_keep") is True)
            if observed_keep_count != counts.get("would_keep_count", 0):
                parse_error = "would_keep_count_mismatch"
        chunk = {
            "chunk": chunk_dir.name,
            "path": str(path),
            "status": "invalid" if parse_error else "loaded",
            "summary": counts,
        }
        if parse_error:
            artifact_schema_valid = False
            chunk["parse_error"] = parse_error
        chunks.append(chunk)
        if parse_error:
            continue
        for key, value in counts.items():
            totals[key] += value
        for row in evaluation_rows:
            row_mapping = _as_mapping(row)
            reason = row_mapping.get("evaluation_reason")
            if reason:
                reasons[str(reason)] += 1
        for setup_type, bucket in _as_mapping(summary.get("by_setup")).items():
            setup_bucket = by_setup.setdefault(str(setup_type), _empty_setup_rewrite_bucket())
            _add_setup_rewrite_bucket(setup_bucket, _as_mapping(bucket))
    if not chunks:
        return None
    keep_rate = totals["would_keep_count"] / totals["evaluated_count"] if totals["evaluated_count"] else 0.0
    return {
        "schema_version": "setup_rewrite_live_readiness_diagnostic.v1",
        "chunks": chunks,
        "totals": {**totals, "keep_rate": keep_rate},
        "reasons": dict(sorted(reasons.items())),
        "by_setup": {key: by_setup[key] for key in sorted(by_setup)},
        "checks": {"setup_rewrite_artifact_schema_valid": artifact_schema_valid},
    }


def _artifact_schema_valid(payload: Mapping[str, Any], expected_schema_version: str) -> bool:
    return payload.get("schema_version") == expected_schema_version


def _artifact_top_level_schema_error(payload: Mapping[str, Any], allowed_fields: set[str]) -> str:
    unknown_fields = sorted(set(payload) - allowed_fields)
    if unknown_fields:
        return "unknown_top_level_field: " + ", ".join(unknown_fields)
    return ""


def _artifact_provenance_present(payload: Mapping[str, Any]) -> bool:
    source = _as_mapping(payload.get("evidence_source"))
    source_type = source.get("type")
    if not isinstance(source_type, str):
        return False
    if source_type != source_type.strip():
        return False
    normalized_source_type = source_type.strip().lower()
    return normalized_source_type in {
        "live_exchange",
        "testnet_exchange",
        "exchange_export",
        "real_exchange_records",
        "historical_l2_tick_archive",
        "exchange_l2_capture",
        "trade_print_path_replay",
        "walk_forward_oos_report",
        "paper_runtime_logs",
    }


def _artifact_provenance_schema_error(payload: Mapping[str, Any]) -> str:
    raw_source = payload.get("evidence_source")
    if raw_source is not None and not isinstance(raw_source, Mapping):
        return "evidence_source_not_object"
    source = _as_mapping(raw_source)
    unknown_fields = sorted(set(source) - {"type", "run_id", "exported_at"})
    if unknown_fields:
        return "unknown_evidence_source_field: " + ", ".join(unknown_fields)
    source_type = source.get("type")
    if source_type is None:
        return ""
    if not isinstance(source_type, str):
        return "evidence_source_type_not_string"
    if not source_type.strip():
        return "evidence_source_type_blank"
    if source_type != source_type.strip():
        return "evidence_source_type_noncanonical"
    for optional_field in ("run_id", "exported_at"):
        optional_value = source.get(optional_field)
        if optional_value is not None and not isinstance(optional_value, str):
            return f"evidence_source_{optional_field}_not_string"
        if isinstance(optional_value, str) and not optional_value.strip():
            return f"evidence_source_{optional_field}_blank"
        if isinstance(optional_value, str) and optional_value != optional_value.strip():
            return f"evidence_source_{optional_field}_noncanonical"
    return ""


def _legacy_provenance_schema_error(payload: Mapping[str, Any]) -> str:
    raw_legacy = payload.get("provenance")
    if raw_legacy is not None and not isinstance(raw_legacy, Mapping):
        return "provenance_not_object"
    legacy = _as_mapping(raw_legacy)
    unknown_fields = sorted(set(legacy) - {"source", "real_exchange_records"})
    if unknown_fields:
        return "unknown_provenance_field: " + ", ".join(unknown_fields)
    source = legacy.get("source")
    if source is not None and not isinstance(source, str):
        return "provenance_source_not_string"
    if isinstance(source, str) and not source.strip():
        return "provenance_source_blank"
    if isinstance(source, str) and source != source.strip():
        return "provenance_source_noncanonical"
    real_records = legacy.get("real_exchange_records")
    if real_records is not None and not isinstance(real_records, bool):
        return "provenance_real_exchange_records_not_bool"
    return ""

def _strict_check_bool(value: Any) -> bool:
    return value is True



def _runtime_safety_gate(chunk_dirs: Sequence[Path], *, required: bool) -> dict[str, Any]:
    required_checks = (
        "kill_switch_dry_run_met",
        "order_position_reconciliation_met",
        "runtime_fail_closed_met",
        "live_dust_before_scale_met",
        "live_trade_ledger_met",
        "runtime_explainability_met",
        "drift_guard_met",
    )
    artifacts: list[dict[str, Any]] = []
    aggregate_checks = {key: False for key in required_checks}
    schema_valid = False
    provenance_present = False
    for chunk_dir in chunk_dirs:
        path = chunk_dir / "runtime_safety_gate.json"
        if not path.exists():
            continue
        payload = _load_json(path)
        parse_error = _json_parse_error(payload)
        checks_payload = payload.get("checks")
        evidence_source_payload = payload.get("evidence_source")
        summary_payload = payload.get("summary")
        summary_object_valid = summary_payload is None or isinstance(summary_payload, Mapping)
        checks_object_valid = isinstance(checks_payload, Mapping)
        evidence_source_object_valid = isinstance(evidence_source_payload, Mapping)
        evidence_source_schema_error = _artifact_provenance_schema_error(payload)
        top_level_schema_error = _artifact_top_level_schema_error(
            payload, {"schema_version", "evidence_source", "checks", "summary", "reasons"}
        )
        checks = _as_mapping(checks_payload)
        summary = _as_mapping(summary_payload)
        summary_schema_error = ""
        event_count = summary.get("event_count")
        if event_count is not None and (isinstance(event_count, bool) or not isinstance(event_count, int)):
            summary_schema_error = "summary_event_count_not_int"
        counts_by_type = summary.get("counts_by_type")
        if not summary_schema_error and counts_by_type is not None:
            if not isinstance(counts_by_type, Mapping):
                summary_schema_error = "summary_counts_by_type_not_object"
            else:
                for event_type, count in counts_by_type.items():
                    if not isinstance(event_type, str) or not event_type.strip() or event_type != event_type.strip():
                        summary_schema_error = "summary_counts_by_type_key_invalid"
                        break
                    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                        summary_schema_error = "summary_counts_by_type_count_invalid"
                        break
        unknown_check_fields = sorted(set(checks) - set(required_checks))
        chunk_schema_valid = (
            (not parse_error)
            and _artifact_schema_valid(payload, "runtime_safety_gate_input.v1")
            and checks_object_valid
            and evidence_source_object_valid
            and not evidence_source_schema_error
            and not top_level_schema_error
            and summary_object_valid
            and not summary_schema_error
            and not unknown_check_fields
        )
        chunk_provenance_present = (not parse_error) and _artifact_provenance_present(payload)
        parse_error_message = str(parse_error or "")
        if not parse_error:
            if not checks_object_valid:
                parse_error_message = "checks_not_object"
            elif not evidence_source_object_valid:
                parse_error_message = "evidence_source_not_object"
            elif evidence_source_schema_error:
                parse_error_message = evidence_source_schema_error
            elif top_level_schema_error:
                parse_error_message = top_level_schema_error
            elif not summary_object_valid:
                parse_error_message = "summary_not_object"
            elif summary_schema_error:
                parse_error_message = summary_schema_error
            elif unknown_check_fields:
                parse_error_message = "unknown_check_field: " + ", ".join(unknown_check_fields)
        artifacts.append(
            {
                "chunk": chunk_dir.name,
                "path": str(path),
                "parse_error": parse_error_message,
                "schema_version": payload.get("schema_version"),
                "schema_valid": chunk_schema_valid,
                "provenance_present": chunk_provenance_present,
                "evidence_source": _as_mapping(payload.get("evidence_source")),
                "checks": {key: _strict_check_bool(checks.get(key)) for key in required_checks},
                "summary": _as_mapping(payload.get("summary")),
            }
        )
    if artifacts:
        schema_valid = all(bool(artifact.get("schema_valid")) for artifact in artifacts)
        provenance_present = all(bool(artifact.get("provenance_present")) for artifact in artifacts)
        aggregate_checks = {
            key: all(bool(_as_mapping(artifact.get("checks")).get(key)) for artifact in artifacts)
            for key in required_checks
        }
    return {
        "schema_version": "runtime_safety_gate.v1",
        "required": required,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "checks": {
            **aggregate_checks,
            "runtime_safety_artifact_schema_valid": schema_valid if artifacts else not required,
            "runtime_safety_artifact_provenance_present": provenance_present if artifacts else not required,
        },
    }


def _microstructure_gate(chunk_dirs: Sequence[Path], *, required: bool) -> dict[str, Any]:
    required_checks = ("l2_tick_coverage_met", "depth_driven_taker_met")
    artifacts: list[dict[str, Any]] = []
    aggregate_checks = {key: False for key in required_checks}
    schema_valid = False
    provenance_present = False
    for chunk_dir in chunk_dirs:
        path = chunk_dir / "market_microstructure_gate.json"
        if not path.exists():
            continue
        payload = _load_json(path)
        parse_error = _json_parse_error(payload)
        checks_payload = payload.get("checks")
        evidence_source_payload = payload.get("evidence_source")
        summary_payload = payload.get("summary")
        summary_object_valid = summary_payload is None or isinstance(summary_payload, Mapping)
        checks_object_valid = isinstance(checks_payload, Mapping)
        evidence_source_object_valid = isinstance(evidence_source_payload, Mapping)
        evidence_source_schema_error = _artifact_provenance_schema_error(payload)
        top_level_schema_error = _artifact_top_level_schema_error(
            payload, {"schema_version", "evidence_source", "checks", "summary", "coverage", "depth_driven_taker", "reasons"}
        )
        checks = _as_mapping(checks_payload)
        unknown_check_fields = sorted(set(checks) - set(required_checks))
        summary = _as_mapping(summary_payload)
        summary_schema_error = ""
        min_l2_tick_coverage = summary.get("min_l2_tick_coverage")
        if min_l2_tick_coverage is not None:
            _, min_coverage_valid = _strict_float_value(min_l2_tick_coverage)
            if not min_coverage_valid:
                summary_schema_error = "summary_min_l2_tick_coverage_not_number"
        taker_fill_model = summary.get("taker_fill_model")
        if not summary_schema_error and taker_fill_model is not None:
            if not isinstance(taker_fill_model, str):
                summary_schema_error = "summary_taker_fill_model_not_string"
            elif not taker_fill_model.strip():
                summary_schema_error = "summary_taker_fill_model_blank"
            elif taker_fill_model != taker_fill_model.strip():
                summary_schema_error = "summary_taker_fill_model_noncanonical"
        chunk_schema_valid = (
            (not parse_error)
            and _artifact_schema_valid(payload, "market_microstructure_gate_input.v1")
            and checks_object_valid
            and evidence_source_object_valid
            and not evidence_source_schema_error
            and not top_level_schema_error
            and summary_object_valid
            and not summary_schema_error
            and not unknown_check_fields
        )
        chunk_provenance_present = (not parse_error) and _artifact_provenance_present(payload)
        parse_error_message = str(parse_error or "")
        if not parse_error:
            if not checks_object_valid:
                parse_error_message = "checks_not_object"
            elif not evidence_source_object_valid:
                parse_error_message = "evidence_source_not_object"
            elif evidence_source_schema_error:
                parse_error_message = evidence_source_schema_error
            elif top_level_schema_error:
                parse_error_message = top_level_schema_error
            elif not summary_object_valid:
                parse_error_message = "summary_not_object"
            elif summary_schema_error:
                parse_error_message = summary_schema_error
            elif unknown_check_fields:
                parse_error_message = "unknown_check_field: " + ", ".join(unknown_check_fields)
        artifacts.append(
            {
                "chunk": chunk_dir.name,
                "path": str(path),
                "parse_error": parse_error_message,
                "schema_version": payload.get("schema_version"),
                "schema_valid": chunk_schema_valid,
                "provenance_present": chunk_provenance_present,
                "evidence_source": _as_mapping(payload.get("evidence_source")),
                "checks": {key: _strict_check_bool(checks.get(key)) for key in required_checks},
                "summary": _as_mapping(payload.get("summary")),
            }
        )
    if artifacts:
        schema_valid = all(bool(artifact.get("schema_valid")) for artifact in artifacts)
        provenance_present = all(bool(artifact.get("provenance_present")) for artifact in artifacts)
        aggregate_checks = {
            key: all(bool(_as_mapping(artifact.get("checks")).get(key)) for artifact in artifacts)
            for key in required_checks
        }
    return {
        "schema_version": "microstructure_gate.v1",
        "required": required,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "checks": {
            **aggregate_checks,
            "microstructure_artifact_schema_valid": schema_valid if artifacts else not required,
            "microstructure_artifact_provenance_present": provenance_present if artifacts else not required,
        },
    }


def _validation_gate(chunk_dirs: Sequence[Path], *, required: bool) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    required_checks = (
        "oos_non_degraded_met",
        "multi_regime_resilience_met",
        "cost_stress_positive_met",
        "forward_contamination_absent_met",
    )
    aggregate_checks = {key: False for key in required_checks}
    schema_valid = False
    provenance_present = False
    for chunk_dir in chunk_dirs:
        path = chunk_dir / "validation_gate.json"
        if not path.exists():
            continue
        payload = _load_json(path)
        parse_error = _json_parse_error(payload)
        checks_payload = payload.get("checks")
        evidence_source_payload = payload.get("evidence_source")
        summary_payload = payload.get("summary")
        summary_object_valid = summary_payload is None or isinstance(summary_payload, Mapping)
        checks_object_valid = isinstance(checks_payload, Mapping)
        evidence_source_object_valid = isinstance(evidence_source_payload, Mapping)
        evidence_source_schema_error = _artifact_provenance_schema_error(payload)
        top_level_schema_error = _artifact_top_level_schema_error(
            payload, {"schema_version", "evidence_source", "checks", "summary", "reasons"}
        )
        checks = _as_mapping(checks_payload)
        summary = _as_mapping(summary_payload)
        summary_schema_error = ""
        for numeric_field in (
            "oos_degradation_fraction",
            "baseline_net_pnl",
            "oos_net_pnl",
            "stressed_net_pnl",
        ):
            numeric_value = summary.get(numeric_field)
            if numeric_value is not None:
                _, numeric_valid = _strict_float_value(numeric_value)
                if not numeric_valid:
                    summary_schema_error = f"summary_{numeric_field}_not_number"
                    break
        summary_audit_id = summary.get("forward_contamination_audit_id")
        if not summary_schema_error and summary_audit_id is not None:
            if not isinstance(summary_audit_id, str):
                summary_schema_error = "summary_forward_contamination_audit_id_not_string"
            elif not summary_audit_id.strip():
                summary_schema_error = "summary_forward_contamination_audit_id_blank"
            elif summary_audit_id != summary_audit_id.strip():
                summary_schema_error = "summary_forward_contamination_audit_id_noncanonical"
        unknown_check_fields = sorted(set(checks) - set(required_checks))
        chunk_schema_valid = (
            (not parse_error)
            and _artifact_schema_valid(payload, "validation_gate_input.v1")
            and checks_object_valid
            and evidence_source_object_valid
            and not evidence_source_schema_error
            and not top_level_schema_error
            and summary_object_valid
            and not summary_schema_error
            and not unknown_check_fields
        )
        chunk_provenance_present = (not parse_error) and _artifact_provenance_present(payload)
        parse_error_message = str(parse_error or "")
        if not parse_error:
            if not checks_object_valid:
                parse_error_message = "checks_not_object"
            elif not evidence_source_object_valid:
                parse_error_message = "evidence_source_not_object"
            elif evidence_source_schema_error:
                parse_error_message = evidence_source_schema_error
            elif top_level_schema_error:
                parse_error_message = top_level_schema_error
            elif not summary_object_valid:
                parse_error_message = "summary_not_object"
            elif summary_schema_error:
                parse_error_message = summary_schema_error
            elif unknown_check_fields:
                parse_error_message = "unknown_check_field: " + ", ".join(unknown_check_fields)
        artifacts.append(
            {
                "chunk": chunk_dir.name,
                "path": str(path),
                "parse_error": parse_error_message,
                "schema_version": payload.get("schema_version"),
                "schema_valid": chunk_schema_valid,
                "provenance_present": chunk_provenance_present,
                "evidence_source": _as_mapping(payload.get("evidence_source")),
                "checks": {key: _strict_check_bool(checks.get(key)) for key in required_checks},
                "summary": _as_mapping(payload.get("summary")),
            }
        )
    if artifacts:
        schema_valid = all(bool(artifact.get("schema_valid")) for artifact in artifacts)
        provenance_present = all(bool(artifact.get("provenance_present")) for artifact in artifacts)
        aggregate_checks = {
            key: all(bool(_as_mapping(artifact.get("checks")).get(key)) for artifact in artifacts)
            for key in required_checks
        }
    return {
        "schema_version": "validation_gate.v1",
        "required": required,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "checks": {
            **aggregate_checks,
            "validation_artifact_schema_valid": schema_valid if artifacts else not required,
            "validation_artifact_provenance_present": provenance_present if artifacts else not required,
        },
    }


def _setup_quality_gate(
    by_setup: Mapping[str, Mapping[str, Any]],
    *,
    min_setup_trade_count: int | None,
    banned_setup_types: Sequence[str] | None,
) -> dict[str, Any]:
    banned_values: set[str] = set()
    invalid_config: list[dict[str, Any]] = []
    for index, item in enumerate(banned_setup_types or []):
        if not isinstance(item, str):
            invalid_config.append({"field": f"banned_setup_types[{index}]", "value": item, "error": "invalid_setup_type"})
            continue
        if not item.strip():
            invalid_config.append({"field": f"banned_setup_types[{index}]", "value": item, "error": "blank_setup_type"})
            continue
        if item != item.strip():
            invalid_config.append({"field": f"banned_setup_types[{index}]", "value": item, "error": "setup_type_not_canonical"})
            continue
        banned_values.add(item)
    banned = sorted(banned_values)
    under_sampled = []
    if min_setup_trade_count is not None:
        if isinstance(min_setup_trade_count, bool) or not isinstance(min_setup_trade_count, int):
            invalid_config.append(
                {"field": "min_setup_trade_count", "value": min_setup_trade_count, "error": "invalid_threshold"}
            )
        elif min_setup_trade_count < 0:
            invalid_config.append(
                {"field": "min_setup_trade_count", "value": min_setup_trade_count, "error": "negative_threshold"}
            )
        else:
            threshold = min_setup_trade_count
            under_sampled = sorted(
                key for key, bucket in by_setup.items() if int(bucket.get("trade_count") or 0) < threshold
            )
    present_banned = sorted(key for key in banned if key in by_setup)
    return {
        "schema_version": "setup_quality_gate.v1",
        "min_setup_trade_count": min_setup_trade_count,
        "banned_setup_types": banned,
        "under_sampled_setup_types": under_sampled,
        "banned_setup_types_present": present_banned,
        "invalid_config": invalid_config,
        "checks": {
            "setup_min_sample_met": not under_sampled and not invalid_config,
            "banned_setup_types_absent": not present_banned,
        },
    }


def _exit_path_replay_reconciliation(chunk_dirs: Sequence[Path], *, required: bool) -> dict[str, Any]:
    trade_ids: list[str] = []
    path_trade_ids: set[str] = set()
    path_trade_id_counts: Counter[str] = Counter()
    chunks_missing_artifact: list[str] = []
    artifacts: list[dict[str, Any]] = []
    invalid_source_trade_ids: list[dict[str, Any]] = []
    source_trade_id_counts: Counter[str] = Counter()
    for chunk_dir in chunk_dirs:
        trades = _trades_payload(_load_json(chunk_dir / "trades.json"))
        for index, trade in enumerate(trades, start=1):
            trade_id_raw = trade.get("trade_id")
            if trade_id_raw is None:
                invalid_source_trade_ids.append(
                    {"chunk": chunk_dir.name, "index": index, "trade_id": trade_id_raw, "error": "missing_trade_id"}
                )
                continue
            if not isinstance(trade_id_raw, str):
                invalid_source_trade_ids.append(
                    {"chunk": chunk_dir.name, "index": index, "trade_id": trade_id_raw, "error": "trade_id_not_string"}
                )
                continue
            trade_id = trade_id_raw.strip()
            if not trade_id:
                invalid_source_trade_ids.append(
                    {"chunk": chunk_dir.name, "index": index, "trade_id": trade_id_raw, "error": "missing_trade_id"}
                )
                continue
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", trade_id):
                invalid_source_trade_ids.append(
                    {"chunk": chunk_dir.name, "index": index, "trade_id": trade_id_raw, "error": "invalid_trade_id"}
                )
                continue
            source_trade_id_counts[trade_id] += 1
            trade_ids.append(trade_id)
        path = chunk_dir / "exit_path_replay.json"
        if not path.exists():
            chunks_missing_artifact.append(chunk_dir.name)
            continue
        payload = _load_json(path)
        parse_error = _json_parse_error(payload)
        rows_payload = payload.get("trades", [])
        provenance_schema_error = _artifact_provenance_schema_error(payload)
        unknown_top_level_fields = sorted(set(payload) - {"schema_version", "evidence_source", "trades"})
        if not parse_error and unknown_top_level_fields:
            parse_error = "unknown_top_level_field: " + ", ".join(unknown_top_level_fields)
        if not parse_error and provenance_schema_error:
            parse_error = provenance_schema_error
        if not parse_error and not isinstance(rows_payload, list):
            parse_error = "trades_rows_not_list"
        if not parse_error:
            for row_index, row in enumerate(rows_payload, start=1):
                if not isinstance(row, Mapping):
                    parse_error = f"trade_row_not_object: trades[{row_index}]"
                    break
                unknown_row_fields = sorted(set(row) - {"trade_id"})
                if unknown_row_fields:
                    parse_error = f"unknown_trade_row_field: trades[{row_index}]." + ", ".join(unknown_row_fields)
                    break
                trade_id = row.get("trade_id")
                if trade_id is None:
                    parse_error = f"trade_id_missing_or_blank: trades[{row_index}]"
                    break
                if not isinstance(trade_id, str):
                    parse_error = f"trade_id_not_string: trades[{row_index}]"
                    break
                if not trade_id.strip():
                    parse_error = f"trade_id_missing_or_blank: trades[{row_index}]"
                    break
                if trade_id != trade_id.strip():
                    parse_error = f"trade_id_not_canonical: trades[{row_index}]"
                    break
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", trade_id):
                    parse_error = f"invalid_trade_id: trades[{row_index}]"
                    break
        chunk_schema_valid = (not parse_error) and _artifact_schema_valid(payload, "exit_path_replay.v1")
        chunk_provenance_present = (not parse_error) and _artifact_provenance_present(payload)
        artifacts.append(
            {
                "chunk": chunk_dir.name,
                "path": str(path),
                "parse_error": parse_error,
                "schema_version": payload.get("schema_version"),
                "schema_valid": chunk_schema_valid,
                "provenance_present": chunk_provenance_present,
                "evidence_source": _as_mapping(payload.get("evidence_source")),
            }
        )
        for row in _trades_payload(payload):
            path_trade_id = row.get("trade_id")
            if not isinstance(path_trade_id, str) or not path_trade_id.strip():
                continue
            if path_trade_id != path_trade_id.strip():
                continue
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", path_trade_id):
                continue
            path_trade_ids.add(path_trade_id)
            path_trade_id_counts[path_trade_id] += 1
    duplicate_path_trade_ids = sorted(trade_id for trade_id, count in path_trade_id_counts.items() if count > 1)
    duplicate_source_trade_ids = sorted(trade_id for trade_id, count in source_trade_id_counts.items() if count > 1)
    missing = [trade_id for trade_id in trade_ids if trade_id not in path_trade_ids]
    extra = sorted(path_trade_ids - set(trade_ids))
    schema_valid = bool(artifacts) and all(bool(artifact.get("schema_valid")) for artifact in artifacts)
    provenance_present = bool(artifacts) and all(bool(artifact.get("provenance_present")) for artifact in artifacts)
    matched = (
        not missing
        and not extra
        and not duplicate_path_trade_ids
        and not duplicate_source_trade_ids
        and not invalid_source_trade_ids
        and not chunks_missing_artifact
        and (schema_valid if artifacts else not required)
        and (provenance_present if artifacts else not required)
    )
    return {
        "schema_version": "exit_path_replay_reconciliation.v1",
        "required": required,
        "matched": matched if required else True if not trade_ids and not invalid_source_trade_ids else matched,
        "schema_valid": schema_valid,
        "provenance_present": provenance_present,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "trade_count": len(trade_ids),
        "path_trade_count": len(path_trade_ids),
        "duplicate_path_trade_count": len(duplicate_path_trade_ids),
        "duplicate_source_trade_count": len(duplicate_source_trade_ids),
        "invalid_source_trade_id_count": len(invalid_source_trade_ids),
        "missing_trade_count": len(missing),
        "extra_path_trade_count": len(extra),
        "chunks_missing_artifact": chunks_missing_artifact,
        "missing_trade_ids": missing[:50],
        "extra_path_trade_ids": extra[:50],
        "duplicate_path_trade_ids": duplicate_path_trade_ids[:50],
        "duplicate_source_trade_ids": duplicate_source_trade_ids[:50],
        "invalid_source_trade_ids": invalid_source_trade_ids[:50],
    }


def _passive_calibration_diagnostic(
    chunk_dirs: Sequence[Path],
    *,
    required: bool,
    min_attempts: int,
    min_fill_rate: float | None,
) -> dict[str, Any]:
    chunks: list[dict[str, Any]] = []
    total_attempts = 0
    weighted_filled = 0.0
    real_exchange_records = False
    for chunk_dir in chunk_dirs:
        path = chunk_dir / "passive_order_calibration_summary.json"
        if not path.exists():
            continue
        payload = _load_json(path)
        parse_error = _json_parse_error(payload)
        overall_payload = payload.get("overall")
        overall_object_valid = isinstance(overall_payload, Mapping)
        overall = _as_mapping(overall_payload)
        evidence_source = _as_mapping(payload.get("evidence_source"))
        legacy_provenance = _as_mapping(payload.get("provenance"))
        provenance = evidence_source or legacy_provenance
        unknown_top_level_fields = sorted(set(payload) - {"schema_version", "evidence_source", "provenance", "overall"})
        schema_error = _artifact_provenance_schema_error(payload) or _legacy_provenance_schema_error(payload)
        if unknown_top_level_fields:
            schema_error = "unknown_top_level_field: " + ", ".join(unknown_top_level_fields)
        schema_valid = (
            (not parse_error)
            and _artifact_schema_valid(payload, "passive_order_calibration_summary.v1")
            and not schema_error
        )
        provenance_present = (not parse_error) and _artifact_provenance_present(payload)
        attempts, attempts_valid = _strict_summary_int_value(overall.get("attempt_count", 0))
        fill_rate, fill_rate_valid = _strict_float_value(overall.get("fill_rate", 0.0))
        numeric_error = schema_error or ""
        unknown_overall_fields = sorted(set(overall) - {"attempt_count", "fill_rate"})
        if not overall_object_valid:
            numeric_error = "overall_not_object"
        elif unknown_overall_fields:
            numeric_error = "unknown_overall_field: " + ", ".join(unknown_overall_fields)
        elif not attempts_valid or attempts < 0:
            numeric_error = "invalid_numeric_field: attempt_count"
        elif not fill_rate_valid or fill_rate < 0.0 or fill_rate > 1.0:
            numeric_error = "invalid_numeric_field: fill_rate"
        if numeric_error:
            parse_error = parse_error or numeric_error
            schema_valid = False
            provenance_present = False
        chunk_valid_for_aggregation = schema_valid and provenance_present
        if chunk_valid_for_aggregation:
            total_attempts += attempts
            weighted_filled += fill_rate * attempts
        chunk_real = False
        if chunk_valid_for_aggregation:
            chunk_real = bool(legacy_provenance.get("real_exchange_records")) or str(
                provenance.get("type") or provenance.get("source") or ""
            ).lower() in {
                "live_exchange",
                "testnet_exchange",
                "exchange_export",
                "real_exchange_records",
            }
        real_exchange_records = real_exchange_records or chunk_real
        chunks.append(
            {
                "chunk": chunk_dir.name,
                "path": str(path),
                "parse_error": parse_error,
                "attempt_count": attempts,
                "fill_rate": fill_rate,
                "real_exchange_records": chunk_real,
                "schema_valid": schema_valid,
                "provenance_present": provenance_present,
                "evidence_source": evidence_source,
                "provenance": provenance,
            }
        )
    fill_rate = weighted_filled / total_attempts if total_attempts else 0.0
    invalid_config: list[dict[str, Any]] = []
    if isinstance(min_attempts, bool) or not isinstance(min_attempts, int):
        invalid_config.append({"field": "min_passive_calibration_attempts", "value": min_attempts, "error": "invalid_threshold"})
    elif min_attempts < 0:
        invalid_config.append({"field": "min_passive_calibration_attempts", "value": min_attempts, "error": "negative_threshold"})
    if min_fill_rate is not None:
        if isinstance(min_fill_rate, bool) or not isinstance(min_fill_rate, (int, float)) or not math.isfinite(float(min_fill_rate)):
            invalid_config.append({"field": "min_passive_fill_rate", "value": min_fill_rate, "error": "invalid_threshold"})
        elif min_fill_rate < 0.0 or min_fill_rate > 1.0:
            invalid_config.append({"field": "min_passive_fill_rate", "value": min_fill_rate, "error": "out_of_range_threshold"})
    attempts_met = (not invalid_config) and total_attempts >= min_attempts
    fill_rate_met = (not invalid_config) and (min_fill_rate is None or fill_rate >= min_fill_rate)
    real_records_met = (not required) or real_exchange_records
    schema_valid = (not chunks) or all(bool(chunk.get("schema_valid")) for chunk in chunks)
    provenance_present = (not chunks) or all(bool(chunk.get("provenance_present")) for chunk in chunks)
    return {
        "schema_version": "passive_calibration_live_readiness.v1",
        "required": required,
        "chunks": chunks,
        "attempt_count": total_attempts,
        "min_attempts": min_attempts,
        "fill_rate": fill_rate,
        "min_fill_rate": min_fill_rate,
        "invalid_config": invalid_config,
        "real_exchange_records": real_exchange_records,
        "checks": {
            "passive_calibration_present_met": (not required) or bool(chunks),
            "passive_calibration_artifact_schema_valid": schema_valid,
            "passive_calibration_artifact_provenance_present": provenance_present,
            "passive_calibration_real_records_met": real_records_met,
            "passive_calibration_attempts_met": attempts_met,
            "passive_calibration_fill_rate_met": fill_rate_met,
        },
    }


def _dominance_from_gate_buckets(
    buckets: Mapping[str, Mapping[str, Any]],
    *,
    total_trades: int,
    total_abs_net: float,
    total_loss_abs_net: float = 0.0,
    net_key: str = "net_pnl",
    trade_count_key: str = "trade_count",
    sort_by: str = "trades",
) -> dict[str, Any] | None:
    if not buckets or total_trades <= 0:
        return None
    if sort_by == "loss_abs":
        sort_key = lambda item: (
            abs(min(_float_value(item[1].get(net_key)), 0.0)),
            int(item[1].get(trade_count_key, 0)),
            item[0],
        )
    elif sort_by == "net_abs":
        sort_key = lambda item: (abs(_float_value(item[1].get(net_key))), int(item[1].get(trade_count_key, 0)), item[0])
    else:
        sort_key = lambda item: (int(item[1].get(trade_count_key, 0)), abs(_float_value(item[1].get(net_key))), item[0])
    key, bucket = max(buckets.items(), key=sort_key)
    trades = int(bucket.get(trade_count_key, 0))
    net = _float_value(bucket.get(net_key))
    loss_abs = abs(min(net, 0.0))
    return {
        "key": key,
        "trades": trades,
        "trade_share": trades / total_trades if total_trades else 0.0,
        "net": net,
        "net_abs_share": abs(net) / total_abs_net if total_abs_net > 0.0 else 0.0,
        "loss_abs_share": loss_abs / total_loss_abs_net if total_loss_abs_net > 0.0 else 0.0,
    }


def build_live_readiness_gate_report(
    chunk_results_dir: str | Path,
    *,
    evidence_coverage_threshold: float = 0.95,
    exit_evidence_coverage_threshold: float = 0.95,
    max_exit_path_ambiguity_rate: float = 0.05,
    max_setup_trade_share: float | None = None,
    max_symbol_trade_share: float | None = None,
    max_setup_net_abs_share: float | None = None,
    max_symbol_net_abs_share: float | None = None,
    max_setup_loss_abs_share: float | None = None,
    max_symbol_loss_abs_share: float | None = None,
    require_passive_calibration: bool = False,
    min_passive_calibration_attempts: int = 0,
    min_passive_fill_rate: float | None = None,
    require_exit_path_replay_rows: bool = False,
    min_setup_trade_count: int | None = None,
    banned_setup_types: Sequence[str] | None = None,
    require_validation_evidence: bool = False,
    require_microstructure_evidence: bool = False,
    require_runtime_safety_evidence: bool = False,
    require_promotion_bundle_integrity: bool = False,
) -> dict[str, Any]:
    root = Path(chunk_results_dir)
    promotion_bundle_integrity = {
        "schema_version": "promotion_evidence_bundle_verification.v1",
        "required": require_promotion_bundle_integrity,
        "verified": True,
        "manifest_present": False,
        "missing_artifacts": [],
        "sha256_mismatches": [],
        "byte_size_mismatches": [],
        "checked_artifacts": [],
    }
    manifest_candidate = root / "promotion_evidence_manifest.json"
    if require_promotion_bundle_integrity or manifest_candidate.exists():
        promotion_bundle_integrity = {
            "required": require_promotion_bundle_integrity,
            **verify_promotion_evidence_bundle(root),
        }
    chunk_dirs = sorted((path for path in root.iterdir() if _is_chunk_result_dir(path)), key=_natural_path_key)
    policy_invalid_config: list[dict[str, Any]] = []
    for field, value in (
        ("evidence_coverage_threshold", evidence_coverage_threshold),
        ("exit_evidence_coverage_threshold", exit_evidence_coverage_threshold),
        ("max_exit_path_ambiguity_rate", max_exit_path_ambiguity_rate),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            policy_invalid_config.append({"field": field, "value": value, "error": "invalid_threshold"})
            continue
        if 0.0 <= float(value) <= 1.0:
            continue
        policy_invalid_config.append({"field": field, "value": value, "error": "out_of_range_threshold"})
    for field, value in (
        ("max_setup_trade_share", max_setup_trade_share),
        ("max_symbol_trade_share", max_symbol_trade_share),
        ("max_setup_net_abs_share", max_setup_net_abs_share),
        ("max_symbol_net_abs_share", max_symbol_net_abs_share),
        ("max_setup_loss_abs_share", max_setup_loss_abs_share),
        ("max_symbol_loss_abs_share", max_symbol_loss_abs_share),
    ):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            policy_invalid_config.append({"field": field, "value": value, "error": "invalid_threshold"})
            continue
        if 0.0 <= float(value) <= 1.0:
            continue
        policy_invalid_config.append({"field": field, "value": value, "error": "out_of_range_threshold"})
    all_trades: list[dict[str, Any]] = []
    chunk_performance: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        chunk_performance.append(_chunk_report(chunk_dir))
        all_trades.extend(_trades_payload(_load_json(chunk_dir / "trades.json")))

    by_setup: dict[str, dict[str, Any]] = {}
    by_symbol: dict[str, dict[str, Any]] = {}
    by_side: dict[str, dict[str, Any]] = {}
    for trade in all_trades:
        _add_group(by_setup, _dimension_bucket_key(trade.get("setup_type"), "setup_type"), "setup_type", trade)
        _add_group(by_symbol, _dimension_bucket_key(trade.get("symbol"), "symbol"), "symbol", trade)
        _add_group(by_side, _dimension_bucket_key(trade.get("side"), "side"), "side", trade)
    setup_quality_gate = _setup_quality_gate(
        by_setup,
        min_setup_trade_count=min_setup_trade_count,
        banned_setup_types=banned_setup_types,
    )
    runtime_safety_gate = _runtime_safety_gate(chunk_dirs, required=require_runtime_safety_evidence)
    microstructure_gate = _microstructure_gate(chunk_dirs, required=require_microstructure_evidence)
    validation_gate = _validation_gate(chunk_dirs, required=require_validation_evidence)
    setup_rewrite_diagnostic = _setup_rewrite_diagnostic(chunk_dirs)
    passive_calibration = _passive_calibration_diagnostic(
        chunk_dirs,
        required=require_passive_calibration,
        min_attempts=min_passive_calibration_attempts,
        min_fill_rate=min_passive_fill_rate,
    )

    trade_financial_integrity = _trade_financial_integrity(chunk_dirs)
    trades_artifact_integrity = _trades_artifact_integrity(chunk_dirs)
    summary_artifact_integrity = _summary_artifact_integrity(chunk_dirs)
    trade_identity_integrity = _trade_identity_integrity(chunk_dirs)
    trade_dimension_integrity = _trade_dimension_integrity(chunk_dirs)
    trade_time_integrity = _trade_time_integrity(chunk_dirs)
    trade_price_integrity = _trade_price_integrity(chunk_dirs)
    trade_size_integrity = _trade_size_integrity(chunk_dirs)
    trade_notional_consistency = _trade_notional_consistency(chunk_dirs)
    trade_cost_sign_integrity = _trade_cost_sign_integrity(chunk_dirs)
    trade_pnl_consistency = _trade_pnl_consistency(chunk_dirs)
    trade_side_price_pnl_consistency = _trade_side_price_pnl_consistency(chunk_dirs)
    trade_exit_reason_integrity = _trade_exit_reason_integrity(chunk_dirs)

    trade_count = len(all_trades)
    net_pnl = sum(_strict_float_or_zero(trade.get("net_pnl")) for trade in all_trades)
    gross_pnl = sum(_strict_float_or_zero(trade.get("gross_pnl")) for trade in all_trades)
    fees = sum(_strict_float_or_zero(trade.get("fee_paid")) for trade in all_trades)
    slippage = sum(_strict_float_or_zero(trade.get("slippage_paid")) for trade in all_trades)
    funding = sum(_strict_float_or_zero(trade.get("funding_paid")) for trade in all_trades)
    evidence_count = sum(1 for trade in all_trades if _entry_evidence_live_grade(trade))
    evidence_coverage = evidence_count / trade_count if trade_count else 0.0
    exit_evidence_count = sum(1 for trade in all_trades if _exit_evidence_live_grade(trade))
    exit_evidence_coverage = exit_evidence_count / trade_count if trade_count else 0.0
    exit_path_replay = audit_exit_path_replay(all_trades)
    exit_path_reconciliation = _exit_path_replay_reconciliation(chunk_dirs, required=require_exit_path_replay_rows)
    exit_path_counts = _as_mapping(exit_path_replay.get("counts"))
    exit_path_ambiguous_count = int(exit_path_counts.get("fixed_horizon_only") or 0) + int(
        exit_path_counts.get("ambiguous_intrabar_order") or 0
    )
    exit_path_ambiguity_rate = exit_path_ambiguous_count / trade_count if trade_count else 0.0

    major_negative = [key for key, bucket in by_setup.items() if bucket["trade_count"] >= 1 and bucket["net_pnl"] < 0.0]
    total_abs_net = sum(abs(_strict_float_or_zero(trade.get("net_pnl"))) for trade in all_trades)
    total_loss_abs_net = sum(abs(min(_strict_float_or_zero(trade.get("net_pnl")), 0.0)) for trade in all_trades)
    concentration = {
        "max_setup_trade_share": max_setup_trade_share,
        "max_symbol_trade_share": max_symbol_trade_share,
        "max_setup_net_abs_share": max_setup_net_abs_share,
        "max_symbol_net_abs_share": max_symbol_net_abs_share,
        "max_setup_loss_abs_share": max_setup_loss_abs_share,
        "max_symbol_loss_abs_share": max_symbol_loss_abs_share,
        "top_setup_by_trades": _dominance_from_gate_buckets(
            by_setup,
            total_trades=trade_count,
            total_abs_net=total_abs_net,
            total_loss_abs_net=total_loss_abs_net,
        ),
        "top_symbol_by_trades": _dominance_from_gate_buckets(
            by_symbol,
            total_trades=trade_count,
            total_abs_net=total_abs_net,
            total_loss_abs_net=total_loss_abs_net,
        ),
        "top_setup_by_net_abs": _dominance_from_gate_buckets(
            by_setup,
            total_trades=trade_count,
            total_abs_net=total_abs_net,
            total_loss_abs_net=total_loss_abs_net,
            sort_by="net_abs",
        ),
        "top_symbol_by_net_abs": _dominance_from_gate_buckets(
            by_symbol,
            total_trades=trade_count,
            total_abs_net=total_abs_net,
            total_loss_abs_net=total_loss_abs_net,
            sort_by="net_abs",
        ),
        "top_setup_by_loss_abs": _dominance_from_gate_buckets(
            by_setup,
            total_trades=trade_count,
            total_abs_net=total_abs_net,
            total_loss_abs_net=total_loss_abs_net,
            sort_by="loss_abs",
        ),
        "top_symbol_by_loss_abs": _dominance_from_gate_buckets(
            by_symbol,
            total_trades=trade_count,
            total_abs_net=total_abs_net,
            total_loss_abs_net=total_loss_abs_net,
            sort_by="loss_abs",
        ),
    }
    top_setup = _as_mapping(concentration.get("top_setup_by_trades"))
    top_symbol = _as_mapping(concentration.get("top_symbol_by_trades"))
    top_setup_net_abs = _as_mapping(concentration.get("top_setup_by_net_abs"))
    top_symbol_net_abs = _as_mapping(concentration.get("top_symbol_by_net_abs"))
    top_setup_loss_abs = _as_mapping(concentration.get("top_setup_by_loss_abs"))
    top_symbol_loss_abs = _as_mapping(concentration.get("top_symbol_by_loss_abs"))
    setup_concentration_met = max_setup_trade_share is None or _float_value(top_setup.get("trade_share")) <= max_setup_trade_share
    symbol_concentration_met = max_symbol_trade_share is None or _float_value(top_symbol.get("trade_share")) <= max_symbol_trade_share
    setup_net_abs_concentration_met = max_setup_net_abs_share is None or _float_value(top_setup_net_abs.get("net_abs_share")) <= max_setup_net_abs_share
    symbol_net_abs_concentration_met = max_symbol_net_abs_share is None or _float_value(top_symbol_net_abs.get("net_abs_share")) <= max_symbol_net_abs_share
    setup_loss_abs_concentration_met = max_setup_loss_abs_share is None or _float_value(top_setup_loss_abs.get("loss_abs_share")) <= max_setup_loss_abs_share
    symbol_loss_abs_concentration_met = max_symbol_loss_abs_share is None or _float_value(top_symbol_loss_abs.get("loss_abs_share")) <= max_symbol_loss_abs_share
    reasons: list[str] = []
    promotion_bundle_integrity_enforced = require_promotion_bundle_integrity or bool(
        promotion_bundle_integrity.get("manifest_present")
    )
    if promotion_bundle_integrity_enforced and not bool(promotion_bundle_integrity.get("verified")):
        reasons.append("promotion_bundle_integrity_failed")
    if policy_invalid_config:
        reasons.append("live_readiness_policy_config_invalid")
    if net_pnl < 0.0:
        reasons.append("net_pnl_below_zero")
    if not bool(trade_financial_integrity.get("valid")):
        reasons.append("trade_financial_metric_invalid")
    if not bool(trades_artifact_integrity.get("valid")):
        reasons.append("trades_artifact_schema_invalid")
    if not bool(summary_artifact_integrity.get("valid")):
        reasons.append("summary_artifact_schema_invalid")
    if not bool(trade_identity_integrity.get("valid")):
        reasons.append("trade_identity_invalid")
    if not bool(trade_dimension_integrity.get("valid")):
        reasons.append("trade_dimension_invalid")
    if not bool(trade_time_integrity.get("valid")):
        reasons.append("trade_time_invalid")
    if not bool(trade_price_integrity.get("valid")):
        reasons.append("trade_price_invalid")
    if not bool(trade_size_integrity.get("valid")):
        reasons.append("trade_size_invalid")
    if not bool(trade_notional_consistency.get("valid")):
        reasons.append("trade_notional_inconsistent")
    if not bool(trade_cost_sign_integrity.get("valid")):
        reasons.append("trade_cost_sign_invalid")
    if not bool(trade_pnl_consistency.get("valid")):
        reasons.append("trade_pnl_inconsistent")
    if not bool(trade_side_price_pnl_consistency.get("valid")):
        reasons.append("trade_side_price_pnl_inconsistent")
    if not bool(trade_exit_reason_integrity.get("valid")):
        reasons.append("trade_exit_reason_invalid")
    if evidence_coverage < evidence_coverage_threshold:
        reasons.append("evidence_coverage_below_threshold")
    if exit_evidence_coverage < exit_evidence_coverage_threshold:
        reasons.append("exit_evidence_coverage_below_threshold")
    if exit_path_ambiguity_rate > max_exit_path_ambiguity_rate:
        reasons.append("exit_path_ambiguity_rate_above_threshold")
    exit_path_replay_rows_met = bool(exit_path_reconciliation.get("matched")) if exit_path_reconciliation.get("artifacts") else not require_exit_path_replay_rows
    if not exit_path_replay_rows_met:
        reasons.append("exit_path_replay_missing_trades")
    if int(exit_path_reconciliation.get("duplicate_path_trade_count") or 0) > 0:
        reasons.append("exit_path_replay_duplicate_trades")
    if int(exit_path_reconciliation.get("duplicate_source_trade_count") or 0) > 0:
        reasons.append("exit_path_replay_source_trade_id_duplicate")
    if int(exit_path_reconciliation.get("invalid_source_trade_id_count") or 0) > 0:
        reasons.append("exit_path_replay_source_trade_id_invalid")
    if int(exit_path_reconciliation.get("artifact_count") or len(exit_path_reconciliation.get("artifacts") or [])) > 0 and not bool(exit_path_reconciliation.get("schema_valid")):
        reasons.append("exit_path_replay_artifact_schema_invalid")
    if int(exit_path_reconciliation.get("artifact_count") or len(exit_path_reconciliation.get("artifacts") or [])) > 0 and not bool(exit_path_reconciliation.get("provenance_present")):
        reasons.append("exit_path_replay_artifact_provenance_missing")
    if major_negative:
        reasons.append("major_setup_bucket_negative")
    setup_quality_checks = _as_mapping(setup_quality_gate.get("checks"))
    if setup_quality_gate.get("invalid_config"):
        reasons.append("setup_quality_config_invalid")
    if not setup_quality_checks.get("setup_min_sample_met", True):
        reasons.append("setup_min_sample_too_low")
    if not setup_quality_checks.get("banned_setup_types_absent", True):
        reasons.append("banned_setup_type_present")
    runtime_safety_checks = _as_mapping(runtime_safety_gate.get("checks"))
    runtime_safety_artifact_present = int(runtime_safety_gate.get("artifact_count") or 0) > 0
    if require_runtime_safety_evidence and not runtime_safety_artifact_present:
        reasons.append("runtime_safety_evidence_missing")
    if runtime_safety_artifact_present and not runtime_safety_checks.get("runtime_safety_artifact_schema_valid", False):
        reasons.append("runtime_safety_artifact_schema_invalid")
    if runtime_safety_artifact_present and not runtime_safety_checks.get("runtime_safety_artifact_provenance_present", False):
        reasons.append("runtime_safety_artifact_provenance_missing")
    runtime_safety_reason_by_check = {
        "kill_switch_dry_run_met": "kill_switch_dry_run_missing",
        "order_position_reconciliation_met": "order_position_reconciliation_missing",
        "runtime_fail_closed_met": "runtime_fail_closed_missing",
        "live_dust_before_scale_met": "live_dust_before_scale_missing",
        "live_trade_ledger_met": "live_trade_ledger_missing",
        "runtime_explainability_met": "runtime_explainability_missing",
        "drift_guard_met": "drift_guard_missing",
    }
    if require_runtime_safety_evidence or runtime_safety_artifact_present:
        for check, reason in runtime_safety_reason_by_check.items():
            if not runtime_safety_checks.get(check, False):
                reasons.append(reason)
    microstructure_checks = _as_mapping(microstructure_gate.get("checks"))
    microstructure_artifact_present = int(microstructure_gate.get("artifact_count") or 0) > 0
    if require_microstructure_evidence and not microstructure_artifact_present:
        reasons.append("microstructure_evidence_missing")
    if microstructure_artifact_present and not microstructure_checks.get("microstructure_artifact_schema_valid", False):
        reasons.append("microstructure_artifact_schema_invalid")
    if microstructure_artifact_present and not microstructure_checks.get("microstructure_artifact_provenance_present", False):
        reasons.append("microstructure_artifact_provenance_missing")
    if (require_microstructure_evidence or microstructure_artifact_present) and not microstructure_checks.get("l2_tick_coverage_met", False):
        reasons.append("l2_tick_coverage_below_threshold")
    if (require_microstructure_evidence or microstructure_artifact_present) and not microstructure_checks.get("depth_driven_taker_met", False):
        reasons.append("taker_depth_driven_missing")
    validation_checks = _as_mapping(validation_gate.get("checks"))
    validation_artifact_present = int(validation_gate.get("artifact_count") or 0) > 0
    if require_validation_evidence and not validation_artifact_present:
        reasons.append("validation_evidence_missing")
    if validation_artifact_present and not validation_checks.get("validation_artifact_schema_valid", False):
        reasons.append("validation_artifact_schema_invalid")
    if validation_artifact_present and not validation_checks.get("validation_artifact_provenance_present", False):
        reasons.append("validation_artifact_provenance_missing")
    if (require_validation_evidence or validation_artifact_present) and not validation_checks.get("oos_non_degraded_met", False):
        reasons.append("oos_degraded")
    if (require_validation_evidence or validation_artifact_present) and not validation_checks.get("multi_regime_resilience_met", False):
        reasons.append("regime_single_point_survivor")
    if (require_validation_evidence or validation_artifact_present) and not validation_checks.get("cost_stress_positive_met", False):
        reasons.append("cost_stress_not_positive")
    if (require_validation_evidence or validation_artifact_present) and not validation_checks.get("forward_contamination_absent_met", False):
        reasons.append("forward_contamination_unproven")

    if not setup_concentration_met:
        reasons.append("setup_concentration_too_high")
    if not symbol_concentration_met:
        reasons.append("symbol_concentration_too_high")
    if not setup_net_abs_concentration_met:
        reasons.append("setup_net_abs_concentration_too_high")
    if not symbol_net_abs_concentration_met:
        reasons.append("symbol_net_abs_concentration_too_high")
    if not setup_loss_abs_concentration_met:
        reasons.append("setup_loss_abs_concentration_too_high")
    if not symbol_loss_abs_concentration_met:
        reasons.append("symbol_loss_abs_concentration_too_high")
    passive_checks = _as_mapping(passive_calibration.get("checks"))
    if passive_calibration.get("invalid_config"):
        reasons.append("passive_calibration_config_invalid")
    if not passive_checks.get("passive_calibration_present_met", True):
        reasons.append("passive_calibration_missing")
    passive_artifact_count = len(passive_calibration.get("chunks") or [])
    if passive_artifact_count > 0 and not passive_checks.get("passive_calibration_artifact_schema_valid", False):
        reasons.append("passive_calibration_artifact_schema_invalid")
    if passive_artifact_count > 0 and not passive_checks.get("passive_calibration_artifact_provenance_present", False):
        reasons.append("passive_calibration_artifact_provenance_missing")
    if not passive_checks.get("passive_calibration_real_records_met", True):
        reasons.append("passive_calibration_missing_real_records")
    if not passive_checks.get("passive_calibration_attempts_met", True):
        reasons.append("passive_calibration_insufficient_attempts")
    if not passive_checks.get("passive_calibration_fill_rate_met", True):
        reasons.append("passive_calibration_fill_rate_below_threshold")
    setup_rewrite_checks: dict[str, bool] = {}
    if setup_rewrite_diagnostic is not None:
        setup_rewrite_totals = _as_mapping(setup_rewrite_diagnostic.get("totals"))
        setup_rewrite_evaluated = int(setup_rewrite_totals.get("evaluated_count") or 0)
        setup_rewrite_would_keep = int(setup_rewrite_totals.get("would_keep_count") or 0)
        setup_rewrite_skipped = int(setup_rewrite_totals.get("skipped_count") or 0)
        setup_rewrite_checks = {
            **_as_mapping(setup_rewrite_diagnostic.get("checks")),
            "setup_rewrite_has_surviving_candidates": not (
                setup_rewrite_evaluated > 0 and setup_rewrite_would_keep == 0
            ),
            "setup_rewrite_evidence_complete": setup_rewrite_skipped == 0,
        }
        if not setup_rewrite_checks.get("setup_rewrite_artifact_schema_valid", True):
            reasons.append("setup_rewrite_artifact_schema_invalid")
        if not setup_rewrite_checks["setup_rewrite_has_surviving_candidates"]:
            reasons.append("setup_rewrite_no_surviving_candidates")
        if not setup_rewrite_checks["setup_rewrite_evidence_complete"]:
            reasons.append("setup_rewrite_missing_evidence")
    trade_integrity_checks = {
        "trade_financial_integrity_valid": bool(trade_financial_integrity.get("valid")),
        "trades_artifact_integrity_valid": bool(trades_artifact_integrity.get("valid")),
        "summary_artifact_integrity_valid": bool(summary_artifact_integrity.get("valid")),
        "trade_identity_integrity_valid": bool(trade_identity_integrity.get("valid")),
        "trade_dimension_integrity_valid": bool(trade_dimension_integrity.get("valid")),
        "trade_time_integrity_valid": bool(trade_time_integrity.get("valid")),
        "trade_price_integrity_valid": bool(trade_price_integrity.get("valid")),
        "trade_size_integrity_valid": bool(trade_size_integrity.get("valid")),
        "trade_notional_consistency_valid": bool(trade_notional_consistency.get("valid")),
        "trade_cost_sign_integrity_valid": bool(trade_cost_sign_integrity.get("valid")),
        "trade_pnl_consistency_valid": bool(trade_pnl_consistency.get("valid")),
        "trade_side_price_pnl_consistency_valid": bool(trade_side_price_pnl_consistency.get("valid")),
        "trade_exit_reason_integrity_valid": bool(trade_exit_reason_integrity.get("valid")),
    }
    decision = "reject_for_live_promotion" if reasons else "candidate_for_promotion"

    report = {
        "schema_version": "live_readiness_gate_report.v1",
        "chunk_results_dir": str(root),
        "chunk_performance": chunk_performance,
        "totals": {
            "trade_count": trade_count,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "costs": {"fees": fees, "slippage": slippage, "funding": funding, "total": fees + slippage + funding},
            "evidence_coverage": evidence_coverage,
            "evidence_coverage_threshold": evidence_coverage_threshold,
            "exit_evidence_coverage": exit_evidence_coverage,
            "exit_evidence_coverage_threshold": exit_evidence_coverage_threshold,
            "exit_path_ambiguity_rate": exit_path_ambiguity_rate,
            "max_exit_path_ambiguity_rate": max_exit_path_ambiguity_rate,
        },
        "failure_taxonomy": {
            "loss_trade_count": sum(1 for trade in all_trades if _strict_float_or_zero(trade.get("net_pnl")) < 0.0),
            "win_trade_count": sum(1 for trade in all_trades if _strict_float_or_zero(trade.get("net_pnl")) > 0.0),
            "negative_setup_buckets": sorted(major_negative),
            "negative_symbol_buckets": sorted(key for key, bucket in by_symbol.items() if bucket["net_pnl"] < 0.0),
        },
        "by_setup_type": {key: by_setup[key] for key in sorted(by_setup)},
        "by_symbol": {key: by_symbol[key] for key in sorted(by_symbol)},
        "by_side": {key: by_side[key] for key in sorted(by_side)},
        "trade_financial_integrity": trade_financial_integrity,
        "trades_artifact_integrity": trades_artifact_integrity,
        "summary_artifact_integrity": summary_artifact_integrity,
        "trade_identity_integrity": trade_identity_integrity,
        "trade_dimension_integrity": trade_dimension_integrity,
        "trade_time_integrity": trade_time_integrity,
        "trade_price_integrity": trade_price_integrity,
        "trade_size_integrity": trade_size_integrity,
        "trade_notional_consistency": trade_notional_consistency,
        "trade_cost_sign_integrity": trade_cost_sign_integrity,
        "trade_pnl_consistency": trade_pnl_consistency,
        "trade_side_price_pnl_consistency": trade_side_price_pnl_consistency,
        "trade_exit_reason_integrity": trade_exit_reason_integrity,
        "promotion_bundle_integrity": promotion_bundle_integrity,
        "setup_quality_gate": setup_quality_gate,
        "runtime_safety_gate": runtime_safety_gate,
        "microstructure_gate": microstructure_gate,
        "validation_gate": validation_gate,
        "concentration": concentration,
        "passive_calibration": passive_calibration,
        "exit_path_replay": {
            "schema_version": exit_path_replay.get("schema_version"),
            "counts": dict(exit_path_counts),
            "ambiguous_count": exit_path_ambiguous_count,
            "ambiguity_rate": exit_path_ambiguity_rate,
            "reconciliation": exit_path_reconciliation,
        },
        "cost_sensitivity": {
            "net_pnl_before_costs": net_pnl + fees + slippage + funding,
            "net_pnl_if_costs_double": net_pnl - fees - slippage - funding,
            "cost_to_gross_abs_ratio": (fees + slippage + funding) / abs(gross_pnl) if gross_pnl else None,
        },
        "promotion_gate": {
            "decision": decision,
            "reasons": reasons,
            "invalid_config": policy_invalid_config,
            "checks": {
                "net_pnl_non_negative": net_pnl >= 0.0,
                "live_readiness_policy_config_valid": not policy_invalid_config,
                "evidence_coverage_met": evidence_coverage >= evidence_coverage_threshold,
                "exit_evidence_coverage_met": exit_evidence_coverage >= exit_evidence_coverage_threshold,
                "exit_path_ambiguity_rate_met": exit_path_ambiguity_rate <= max_exit_path_ambiguity_rate,
                "exit_path_replay_rows_met": exit_path_replay_rows_met,
                "major_setup_buckets_non_negative": not major_negative,
                "promotion_bundle_integrity_verified": (not promotion_bundle_integrity_enforced)
                or bool(promotion_bundle_integrity.get("verified")),
                **trade_integrity_checks,
                **setup_quality_checks,
                **runtime_safety_checks,
                **microstructure_checks,
                **validation_checks,
                "setup_concentration_met": setup_concentration_met,
                "symbol_concentration_met": symbol_concentration_met,
                "setup_net_abs_concentration_met": setup_net_abs_concentration_met,
                "symbol_net_abs_concentration_met": symbol_net_abs_concentration_met,
                "setup_loss_abs_concentration_met": setup_loss_abs_concentration_met,
                "symbol_loss_abs_concentration_met": symbol_loss_abs_concentration_met,
                **passive_checks,
                **setup_rewrite_checks,
            },
        },
        "caveats": [
            "Offline readiness gate only; it must not place live or testnet orders.",
            "Chunk aggregation depends on trades.json fields emitted by the backtest bundle.",
        ],
    }
    if setup_rewrite_diagnostic is not None:
        report["setup_rewrite_diagnostic"] = setup_rewrite_diagnostic
    return report


def _empty_postmortem_bucket() -> dict[str, Any]:
    return {"trades": 0, "wins": 0, "win_rate": 0.0, "gross": 0.0, "net": 0.0, "cost": 0.0}


def _add_postmortem_bucket(bucket: dict[str, Any], trade: Mapping[str, Any]) -> None:
    gross = _strict_float_or_zero(trade.get("gross_pnl"))
    net = _strict_float_or_zero(trade.get("net_pnl"))
    cost = (
        _strict_float_or_zero(trade.get("fee_paid"))
        + _strict_float_or_zero(trade.get("slippage_paid"))
        + _strict_float_or_zero(trade.get("funding_paid"))
    )
    bucket["trades"] += 1
    bucket["wins"] += 1 if net > 0.0 else 0
    bucket["gross"] += gross
    bucket["net"] += net
    bucket["cost"] += cost
    bucket["win_rate"] = bucket["wins"] / bucket["trades"] if bucket["trades"] else 0.0


def _postmortem_failure_bucket(trade: Mapping[str, Any]) -> str:
    gross = _strict_float_or_zero(trade.get("gross_pnl"))
    net = _strict_float_or_zero(trade.get("net_pnl"))
    mfe = _strict_float_or_zero(trade.get("mfe_pct"))
    mae = _strict_float_or_zero(trade.get("mae_pct"))
    if net > 0.0:
        return "有效盈利_after_cost"
    if gross > 0.0 and net <= 0.0:
        return "盈利被成本翻负"
    if mfe <= 0.0:
        return "入场后无有效顺向空间"
    if mae > mfe:
        return "MAE压过MFE_方向/时机错误"
    return "净亏损_需逐单复核"


def _postmortem_dominance_bucket(
    buckets: Mapping[str, Mapping[str, Any]],
    *,
    total_trades: int,
    total_abs_net: float,
    total_loss_abs_net: float = 0.0,
    sort_by: str = "trades",
) -> dict[str, Any] | None:
    if not buckets or total_trades <= 0:
        return None
    if sort_by == "loss_abs":
        sort_key = lambda item: (abs(min(_float_value(item[1].get("net")), 0.0)), int(item[1].get("trades", 0)), item[0])
    elif sort_by == "net_abs":
        sort_key = lambda item: (abs(_float_value(item[1].get("net"))), int(item[1].get("trades", 0)), item[0])
    else:
        sort_key = lambda item: (int(item[1].get("trades", 0)), abs(_float_value(item[1].get("net"))), item[0])
    key, bucket = max(buckets.items(), key=sort_key)
    trades = int(bucket.get("trades", 0))
    net = _float_value(bucket.get("net"))
    loss_abs = abs(min(net, 0.0))
    return {
        "key": key,
        "trades": trades,
        "trade_share": trades / total_trades if total_trades else 0.0,
        "net": net,
        "net_abs_share": abs(net) / total_abs_net if total_abs_net > 0.0 else 0.0,
        "loss_abs_share": loss_abs / total_loss_abs_net if total_loss_abs_net > 0.0 else 0.0,
    }


def summarize_trade_postmortem(trades: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(trade) for trade in trades]
    by_failure: dict[str, dict[str, Any]] = {}
    by_setup: dict[str, dict[str, Any]] = {}
    by_symbol: dict[str, dict[str, Any]] = {}
    summary = _empty_postmortem_bucket()
    for trade in rows:
        _add_postmortem_bucket(summary, trade)
        failure_key = _postmortem_failure_bucket(trade)
        _add_postmortem_bucket(by_failure.setdefault(failure_key, _empty_postmortem_bucket()), trade)
        setup_key = _dimension_bucket_key(trade.get("setup_type"), "setup_type")
        _add_postmortem_bucket(by_setup.setdefault(setup_key, _empty_postmortem_bucket()), trade)
        symbol_key = _dimension_bucket_key(trade.get("symbol"), "symbol")
        _add_postmortem_bucket(by_symbol.setdefault(symbol_key, _empty_postmortem_bucket()), trade)
    summary_payload = {
        **summary,
        "gross_pnl": summary["gross"],
        "net_pnl": summary["net"],
        "cost_total": summary["cost"],
    }
    total_trades = int(summary["trades"])
    total_abs_net = sum(abs(_strict_float_or_zero(trade.get("net_pnl"))) for trade in rows)
    total_loss_abs_net = sum(abs(min(_strict_float_or_zero(trade.get("net_pnl")), 0.0)) for trade in rows)
    return {
        "schema_version": "trade_postmortem_summary.v1",
        "summary": summary_payload,
        "by_failure_taxonomy": {key: by_failure[key] for key in sorted(by_failure)},
        "by_setup_type": {key: by_setup[key] for key in sorted(by_setup)},
        "by_symbol": {key: by_symbol[key] for key in sorted(by_symbol)},
        "dominance": {
            "top_setup_by_trades": _postmortem_dominance_bucket(
                by_setup,
                total_trades=total_trades,
                total_abs_net=total_abs_net,
                total_loss_abs_net=total_loss_abs_net,
            ),
            "top_symbol_by_trades": _postmortem_dominance_bucket(
                by_symbol,
                total_trades=total_trades,
                total_abs_net=total_abs_net,
                total_loss_abs_net=total_loss_abs_net,
            ),
            "top_setup_by_net_abs": _postmortem_dominance_bucket(
                by_setup,
                total_trades=total_trades,
                total_abs_net=total_abs_net,
                total_loss_abs_net=total_loss_abs_net,
                sort_by="net_abs",
            ),
            "top_symbol_by_net_abs": _postmortem_dominance_bucket(
                by_symbol,
                total_trades=total_trades,
                total_abs_net=total_abs_net,
                total_loss_abs_net=total_loss_abs_net,
                sort_by="net_abs",
            ),
            "top_setup_by_loss_abs": _postmortem_dominance_bucket(
                by_setup,
                total_trades=total_trades,
                total_abs_net=total_abs_net,
                total_loss_abs_net=total_loss_abs_net,
                sort_by="loss_abs",
            ),
            "top_symbol_by_loss_abs": _postmortem_dominance_bucket(
                by_symbol,
                total_trades=total_trades,
                total_abs_net=total_abs_net,
                total_loss_abs_net=total_loss_abs_net,
                sort_by="loss_abs",
            ),
        },
    }


def _discovered_bundle_dirs(input_root: Path) -> list[Path]:
    if (input_root / "trades.json").exists():
        return [input_root]
    bundle_dirs: list[Path] = []
    for child in sorted(path for path in input_root.iterdir() if path.is_dir()):
        if (child / "trades.json").exists():
            bundle_dirs.append(child)
            continue
        nested = [path for path in sorted(child.iterdir()) if path.is_dir() and (path / "trades.json").exists()]
        bundle_dirs.extend(nested)
    return bundle_dirs


def _normalized_chunk_name(index: int, bundle_dir: Path, input_root: Path) -> str:
    if bundle_dir == input_root:
        return "chunk_001"
    if bundle_dir.parent == input_root:
        return bundle_dir.name
    return bundle_dir.parent.name


def _postmortem_reconciliation(report: Mapping[str, Any], postmortem_summary: Mapping[str, Any]) -> dict[str, Any]:
    totals = _as_mapping(report.get("totals"))
    summary = _as_mapping(postmortem_summary.get("summary"))
    gate_trade_count = int(totals.get("trade_count") or 0)
    postmortem_trade_count = int(summary.get("trades") or 0)
    gate_net_pnl = _float_value(totals.get("net_pnl"))
    postmortem_net_pnl = _float_value(summary.get("net_pnl", summary.get("net")))
    trade_count_delta = postmortem_trade_count - gate_trade_count
    net_pnl_delta = postmortem_net_pnl - gate_net_pnl
    return {
        "schema_version": "live_readiness_postmortem_reconciliation.v1",
        "gate_trade_count": gate_trade_count,
        "postmortem_trade_count": postmortem_trade_count,
        "trade_count_delta": trade_count_delta,
        "gate_net_pnl": gate_net_pnl,
        "postmortem_net_pnl": postmortem_net_pnl,
        "net_pnl_delta": net_pnl_delta,
        "matched": trade_count_delta == 0 and abs(net_pnl_delta) <= 1e-6,
    }


def write_live_readiness_smoke_report(
    input_root: str | Path,
    output_dir: str | Path,
    *,
    evidence_coverage_threshold: float = 0.95,
    exit_evidence_coverage_threshold: float = 0.95,
    max_exit_path_ambiguity_rate: float = 0.05,
    max_setup_trade_share: float | None = 0.45,
    max_symbol_trade_share: float | None = 0.70,
    max_setup_net_abs_share: float | None = 0.60,
    max_symbol_net_abs_share: float | None = 0.60,
    max_setup_loss_abs_share: float | None = 0.60,
    max_symbol_loss_abs_share: float | None = 0.60,
    require_passive_calibration: bool = False,
    min_passive_calibration_attempts: int = 0,
    min_passive_fill_rate: float | None = None,
    require_exit_path_replay_rows: bool = False,
    min_setup_trade_count: int | None = None,
    banned_setup_types: Sequence[str] | None = None,
    require_validation_evidence: bool = False,
    require_microstructure_evidence: bool = False,
    require_runtime_safety_evidence: bool = False,
    require_promotion_bundle_integrity: bool = False,
) -> dict[str, Any]:
    source_root = Path(input_root)
    target = Path(output_dir)
    normalized_root = target / "normalized_chunks"
    if normalized_root.exists():
        shutil.rmtree(normalized_root)
    normalized_root.mkdir(parents=True, exist_ok=True)
    bundle_dirs = _discovered_bundle_dirs(source_root)
    if not bundle_dirs:
        raise FileNotFoundError(f"no trades.json bundle found under {source_root}")

    seen_names: Counter[str] = Counter()
    normalized_chunks: list[dict[str, str]] = []
    for index, bundle_dir in enumerate(bundle_dirs, start=1):
        base_name = _normalized_chunk_name(index, bundle_dir, source_root)
        seen_names[base_name] += 1
        chunk_name = base_name if seen_names[base_name] == 1 else f"{base_name}_{seen_names[base_name]:02d}"
        chunk_dir = normalized_root / chunk_name
        chunk_dir.mkdir(parents=True, exist_ok=True)
        for artifact_name in (
            "trades.json",
            "summary.json",
            "setup_rewrite_experiment.json",
            "exit_path_replay.json",
            "passive_order_calibration_summary.json",
            "market_microstructure_gate.json",
            "validation_gate.json",
            "runtime_safety_gate.json",
        ):
            source = bundle_dir / artifact_name
            if source.exists():
                shutil.copy2(source, chunk_dir / artifact_name)
        normalized_chunks.append({"chunk": chunk_name, "source_dir": str(bundle_dir), "normalized_dir": str(chunk_dir)})

    report = build_live_readiness_gate_report(
        normalized_root,
        evidence_coverage_threshold=evidence_coverage_threshold,
        exit_evidence_coverage_threshold=exit_evidence_coverage_threshold,
        max_exit_path_ambiguity_rate=max_exit_path_ambiguity_rate,
        max_setup_trade_share=max_setup_trade_share,
        max_symbol_trade_share=max_symbol_trade_share,
        max_setup_net_abs_share=max_setup_net_abs_share,
        max_symbol_net_abs_share=max_symbol_net_abs_share,
        max_setup_loss_abs_share=max_setup_loss_abs_share,
        max_symbol_loss_abs_share=max_symbol_loss_abs_share,
        require_passive_calibration=require_passive_calibration,
        min_passive_calibration_attempts=min_passive_calibration_attempts,
        min_passive_fill_rate=min_passive_fill_rate,
        require_exit_path_replay_rows=require_exit_path_replay_rows,
        min_setup_trade_count=min_setup_trade_count,
        banned_setup_types=banned_setup_types,
        require_validation_evidence=require_validation_evidence,
        require_microstructure_evidence=require_microstructure_evidence,
        require_runtime_safety_evidence=require_runtime_safety_evidence,
        require_promotion_bundle_integrity=False,
    )
    if require_promotion_bundle_integrity or (source_root / "promotion_evidence_manifest.json").exists():
        source_integrity = {
            "required": require_promotion_bundle_integrity,
            **verify_promotion_evidence_bundle(source_root),
        }
        report["promotion_bundle_integrity"] = source_integrity
        gate = _as_mapping(report.get("promotion_gate"))
        reasons = list(gate.get("reasons", []))
        checks = dict(_as_mapping(gate.get("checks")))
        checks["promotion_bundle_integrity_verified"] = bool(source_integrity.get("verified"))
        if not bool(source_integrity.get("verified")):
            reasons.append("promotion_bundle_integrity_failed")
        report["promotion_gate"] = {
            **dict(gate),
            "decision": "reject_for_live_promotion" if reasons else "candidate_for_promotion",
            "reasons": reasons,
            "checks": checks,
        }
    report["smoke_report"] = {
        "schema_version": "live_readiness_smoke_report.v1",
        "source_root": str(source_root),
        "normalized_input_dir": str(normalized_root),
        "chunks": normalized_chunks,
    }
    target.mkdir(parents=True, exist_ok=True)
    postmortem_summary = summarize_trade_postmortem(
        trade
        for chunk_dir in sorted(path for path in normalized_root.iterdir() if path.is_dir())
        for trade in _trades_payload(_load_json(chunk_dir / "trades.json"))
    )
    report["trade_postmortem_summary"] = postmortem_summary
    report["postmortem_reconciliation"] = _postmortem_reconciliation(report, postmortem_summary)
    (target / "live_readiness_gate.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (target / "live_readiness_gate.md").write_text(render_live_readiness_markdown(report), encoding="utf-8")
    (target / "trade_postmortem_summary.json").write_text(
        json.dumps(postmortem_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def render_live_readiness_markdown(report: Mapping[str, Any]) -> str:
    def _parse_error_summary(artifacts: Any) -> str:
        entries = []
        if isinstance(artifacts, Sequence) and not isinstance(artifacts, (str, bytes)):
            for artifact in artifacts:
                mapped = _as_mapping(artifact)
                parse_error = str(mapped.get("parse_error") or "")
                if parse_error:
                    entries.append(f"{mapped.get('chunk') or mapped.get('path') or 'artifact'}={parse_error}")
        return ", ".join(entries) or "none"

    gate = _as_mapping(report.get("promotion_gate"))
    totals = _as_mapping(report.get("totals"))
    lines = [
        "# Live Readiness Gate",
        "",
        f"- decision: {gate.get('decision')}",
        f"- reasons: {', '.join(gate.get('reasons', [])) if gate.get('reasons') else 'none'}",
        f"- trades: {totals.get('trade_count', 0)}",
        f"- net_pnl: {float(totals.get('net_pnl') or 0.0):.2f}",
        f"- evidence_coverage: {float(totals.get('evidence_coverage') or 0.0):.2%}",
        f"- exit_evidence_coverage: {float(totals.get('exit_evidence_coverage') or 0.0):.2%}",
        f"- exit_path_ambiguity_rate: {float(totals.get('exit_path_ambiguity_rate') or 0.0):.2%}",
    ]
    setup_rewrite = _as_mapping(report.get("setup_rewrite_diagnostic"))
    if setup_rewrite:
        setup_totals = _as_mapping(setup_rewrite.get("totals"))
        lines.append(
            "- setup_rewrite: "
            f"evaluated={int(setup_totals.get('evaluated_count') or 0)}, "
            f"would_keep={int(setup_totals.get('would_keep_count') or 0)}, "
            f"skipped={int(setup_totals.get('skipped_count') or 0)}, "
            f"keep_rate={float(setup_totals.get('keep_rate') or 0.0):.2%}"
        )
    postmortem = _as_mapping(report.get("trade_postmortem_summary"))
    if postmortem:
        postmortem_summary = _as_mapping(postmortem.get("summary"))
        lines.extend(
            [
                "",
                "## Trade Postmortem Summary",
                f"- schema_version: {postmortem.get('schema_version')}",
                f"- trades: {int(postmortem_summary.get('trades') or 0)}",
                f"- net_pnl: {float(postmortem_summary.get('net_pnl') or postmortem_summary.get('net') or 0.0):.2f}",
                f"- cost_total: {float(postmortem_summary.get('cost_total') or postmortem_summary.get('cost') or 0.0):.2f}",
                "",
                "### Failure Taxonomy",
            ]
        )
        reconciliation = _as_mapping(report.get("postmortem_reconciliation"))
        if reconciliation:
            lines.extend(
                [
                    "",
                    "## Postmortem Reconciliation",
                    f"- schema_version: {reconciliation.get('schema_version')}",
                    f"- matched: {str(bool(reconciliation.get('matched'))).lower()}",
                    f"- gate_trade_count: {int(reconciliation.get('gate_trade_count') or 0)}",
                    f"- postmortem_trade_count: {int(reconciliation.get('postmortem_trade_count') or 0)}",
                    f"- trade_count_delta: {int(reconciliation.get('trade_count_delta') or 0)}",
                    f"- gate_net_pnl: {float(reconciliation.get('gate_net_pnl') or 0.0):.2f}",
                    f"- postmortem_net_pnl: {float(reconciliation.get('postmortem_net_pnl') or 0.0):.2f}",
                    f"- net_pnl_delta: {float(reconciliation.get('net_pnl_delta') or 0.0):.2f}",
                    "",
                    "### Failure Taxonomy",
                ]
            )
        failure_buckets = _as_mapping(postmortem.get("by_failure_taxonomy"))
        for key, bucket in sorted(failure_buckets.items()):
            mapped_bucket = _as_mapping(bucket)
            lines.append(
                f"- {key}: trades={int(mapped_bucket.get('trades') or 0)}, "
                f"net={float(mapped_bucket.get('net') or 0.0):.2f}, "
                f"win_rate={float(mapped_bucket.get('win_rate') or 0.0):.2%}"
            )
        setup_buckets = _as_mapping(postmortem.get("by_setup_type"))
        if setup_buckets:
            lines.extend(["", "### Setup Type Summary"])
            for key, bucket in sorted(setup_buckets.items()):
                mapped_bucket = _as_mapping(bucket)
                lines.append(
                    f"- {key}: trades={int(mapped_bucket.get('trades') or 0)}, "
                    f"net={float(mapped_bucket.get('net') or 0.0):.2f}, "
                    f"win_rate={float(mapped_bucket.get('win_rate') or 0.0):.2%}"
                )
        symbol_buckets = _as_mapping(postmortem.get("by_symbol"))
        if symbol_buckets:
            lines.extend(["", "### Symbol Summary"])
            for key, bucket in sorted(symbol_buckets.items()):
                mapped_bucket = _as_mapping(bucket)
                lines.append(
                    f"- {key}: trades={int(mapped_bucket.get('trades') or 0)}, "
                    f"net={float(mapped_bucket.get('net') or 0.0):.2f}, "
                    f"win_rate={float(mapped_bucket.get('win_rate') or 0.0):.2%}"
                )
    setup_quality = _as_mapping(report.get("setup_quality_gate"))
    promotion_bundle_integrity = _as_mapping(report.get("promotion_bundle_integrity"))
    if promotion_bundle_integrity:
        lines.extend(
            [
                "",
                "## Promotion Bundle Integrity",
                f"- schema_version: {promotion_bundle_integrity.get('schema_version')}",
                f"- required: {str(bool(promotion_bundle_integrity.get('required'))).lower()}",
                f"- verified: {str(bool(promotion_bundle_integrity.get('verified'))).lower()}",
                f"- manifest_present: {str(bool(promotion_bundle_integrity.get('manifest_present'))).lower()}",
                "- manifest_errors: "
                + (", ".join(str(item) for item in promotion_bundle_integrity.get("manifest_errors", [])) or "none"),
                "- missing_artifacts: "
                + (", ".join(str(item) for item in promotion_bundle_integrity.get("missing_artifacts", [])) or "none"),
                "- sha256_mismatches: "
                + (", ".join(str(item) for item in promotion_bundle_integrity.get("sha256_mismatches", [])) or "none"),
                "- byte_size_mismatches: "
                + (", ".join(str(item) for item in promotion_bundle_integrity.get("byte_size_mismatches", [])) or "none"),
                "- missing_artifact_metadata: "
                + (", ".join(str(item) for item in promotion_bundle_integrity.get("missing_artifact_metadata", [])) or "none"),
                "- invalid_artifact_metadata: "
                + (", ".join(str(item) for item in promotion_bundle_integrity.get("invalid_artifact_metadata", [])) or "none"),
                "- duplicate_artifact_paths: "
                + (", ".join(str(item) for item in promotion_bundle_integrity.get("duplicate_artifact_paths", [])) or "none"),
            ]
        )
    if setup_quality:
        lines.extend(
            [
                "",
                "## Setup Quality Gate",
                f"- schema_version: {setup_quality.get('schema_version')}",
                f"- min_setup_trade_count: {setup_quality.get('min_setup_trade_count') if setup_quality.get('min_setup_trade_count') is not None else 'disabled'}",
                "- under_sampled_setup_types: "
                + (", ".join(str(item) for item in setup_quality.get("under_sampled_setup_types", [])) or "none"),
                "- banned_setup_types_present: "
                + (", ".join(str(item) for item in setup_quality.get("banned_setup_types_present", [])) or "none"),
            ]
        )
    runtime_safety = _as_mapping(report.get("runtime_safety_gate"))
    if runtime_safety:
        checks = _as_mapping(runtime_safety.get("checks"))
        lines.extend(
            [
                "",
                "## Runtime Safety Gate",
                f"- schema_version: {runtime_safety.get('schema_version')}",
                f"- required: {str(bool(runtime_safety.get('required'))).lower()}",
                f"- artifact_count: {int(runtime_safety.get('artifact_count') or 0)}",
                f"- kill_switch_dry_run_met: {str(bool(checks.get('kill_switch_dry_run_met'))).lower()}",
                f"- order_position_reconciliation_met: {str(bool(checks.get('order_position_reconciliation_met'))).lower()}",
                f"- runtime_fail_closed_met: {str(bool(checks.get('runtime_fail_closed_met'))).lower()}",
                f"- live_dust_before_scale_met: {str(bool(checks.get('live_dust_before_scale_met'))).lower()}",
                f"- live_trade_ledger_met: {str(bool(checks.get('live_trade_ledger_met'))).lower()}",
                f"- runtime_explainability_met: {str(bool(checks.get('runtime_explainability_met'))).lower()}",
                f"- drift_guard_met: {str(bool(checks.get('drift_guard_met'))).lower()}",
                f"- runtime_safety_artifact_parse_errors: {_parse_error_summary(runtime_safety.get('artifacts'))}",
            ]
        )
    microstructure = _as_mapping(report.get("microstructure_gate"))
    if microstructure:
        checks = _as_mapping(microstructure.get("checks"))
        lines.extend(
            [
                "",
                "## Microstructure Gate",
                f"- schema_version: {microstructure.get('schema_version')}",
                f"- required: {str(bool(microstructure.get('required'))).lower()}",
                f"- artifact_count: {int(microstructure.get('artifact_count') or 0)}",
                f"- l2_tick_coverage_met: {str(bool(checks.get('l2_tick_coverage_met'))).lower()}",
                f"- depth_driven_taker_met: {str(bool(checks.get('depth_driven_taker_met'))).lower()}",
                f"- microstructure_artifact_parse_errors: {_parse_error_summary(microstructure.get('artifacts'))}",
            ]
        )
    validation = _as_mapping(report.get("validation_gate"))
    if validation:
        checks = _as_mapping(validation.get("checks"))
        lines.extend(
            [
                "",
                "## Validation Gate",
                f"- schema_version: {validation.get('schema_version')}",
                f"- required: {str(bool(validation.get('required'))).lower()}",
                f"- artifact_count: {int(validation.get('artifact_count') or 0)}",
                f"- oos_non_degraded_met: {str(bool(checks.get('oos_non_degraded_met'))).lower()}",
                f"- multi_regime_resilience_met: {str(bool(checks.get('multi_regime_resilience_met'))).lower()}",
                f"- cost_stress_positive_met: {str(bool(checks.get('cost_stress_positive_met'))).lower()}",
                f"- forward_contamination_absent_met: {str(bool(checks.get('forward_contamination_absent_met'))).lower()}",
                f"- validation_artifact_parse_errors: {_parse_error_summary(validation.get('artifacts'))}",
            ]
        )
    exit_path_replay = _as_mapping(report.get("exit_path_replay"))
    exit_reconciliation = _as_mapping(exit_path_replay.get("reconciliation"))
    if exit_reconciliation:
        lines.extend(
            [
                "",
                "## Exit Path Replay Reconciliation",
                f"- schema_version: {exit_reconciliation.get('schema_version')}",
                f"- required: {str(bool(exit_reconciliation.get('required'))).lower()}",
                f"- matched: {str(bool(exit_reconciliation.get('matched'))).lower()}",
                f"- trade_count: {int(exit_reconciliation.get('trade_count') or 0)}",
                f"- path_trade_count: {int(exit_reconciliation.get('path_trade_count') or 0)}",
                f"- missing_trade_count: {int(exit_reconciliation.get('missing_trade_count') or 0)}",
                f"- extra_path_trade_count: {int(exit_reconciliation.get('extra_path_trade_count') or 0)}",
                f"- exit_path_replay_artifact_parse_errors: {_parse_error_summary(exit_reconciliation.get('artifacts'))}",
            ]
        )
        missing_ids = exit_reconciliation.get("missing_trade_ids") or []
        if missing_ids:
            lines.append("- missing_trade_ids: " + ", ".join(str(item) for item in missing_ids[:10]))
    passive_calibration = _as_mapping(report.get("passive_calibration"))
    if passive_calibration:
        lines.extend(
            [
                "",
                "## Passive Order Calibration Gate",
                f"- schema_version: {passive_calibration.get('schema_version')}",
                f"- required: {str(bool(passive_calibration.get('required'))).lower()}",
                f"- real_exchange_records: {str(bool(passive_calibration.get('real_exchange_records'))).lower()}",
                f"- attempt_count: {int(passive_calibration.get('attempt_count') or 0)}",
                f"- min_attempts: {int(passive_calibration.get('min_attempts') or 0)}",
                f"- fill_rate: {float(passive_calibration.get('fill_rate') or 0.0):.2%}",
                "- min_fill_rate: "
                + (
                    f"{float(passive_calibration.get('min_fill_rate')):.2%}"
                    if passive_calibration.get("min_fill_rate") is not None
                    else "disabled"
                ),
                f"- passive_calibration_artifact_parse_errors: {_parse_error_summary(passive_calibration.get('chunks'))}",
            ]
        )
    concentration = _as_mapping(report.get("concentration"))
    if concentration:
        lines.extend(["", "## Concentration Gate"])
        max_setup_share = concentration.get("max_setup_trade_share")
        max_symbol_share = concentration.get("max_symbol_trade_share")
        max_setup_net_abs_share = concentration.get("max_setup_net_abs_share")
        max_symbol_net_abs_share = concentration.get("max_symbol_net_abs_share")
        max_setup_loss_abs_share = concentration.get("max_setup_loss_abs_share")
        max_symbol_loss_abs_share = concentration.get("max_symbol_loss_abs_share")
        lines.append(
            "- max_setup_trade_share: "
            + (f"{float(max_setup_share):.2%}" if max_setup_share is not None else "disabled")
        )
        lines.append(
            "- max_symbol_trade_share: "
            + (f"{float(max_symbol_share):.2%}" if max_symbol_share is not None else "disabled")
        )
        lines.append(
            "- max_setup_net_abs_share: "
            + (f"{float(max_setup_net_abs_share):.2%}" if max_setup_net_abs_share is not None else "disabled")
        )
        lines.append(
            "- max_symbol_net_abs_share: "
            + (f"{float(max_symbol_net_abs_share):.2%}" if max_symbol_net_abs_share is not None else "disabled")
        )
        lines.append(
            "- max_setup_loss_abs_share: "
            + (f"{float(max_setup_loss_abs_share):.2%}" if max_setup_loss_abs_share is not None else "disabled")
        )
        lines.append(
            "- max_symbol_loss_abs_share: "
            + (f"{float(max_symbol_loss_abs_share):.2%}" if max_symbol_loss_abs_share is not None else "disabled")
        )
        for label, threshold, share_key in (
            ("top_setup_by_trades", max_setup_share, "trade_share"),
            ("top_symbol_by_trades", max_symbol_share, "trade_share"),
            ("top_setup_by_net_abs", max_setup_net_abs_share, "net_abs_share"),
            ("top_symbol_by_net_abs", max_symbol_net_abs_share, "net_abs_share"),
            ("top_setup_by_loss_abs", max_setup_loss_abs_share, "loss_abs_share"),
            ("top_symbol_by_loss_abs", max_symbol_loss_abs_share, "loss_abs_share"),
        ):
            bucket = _as_mapping(concentration.get(label))
            if not bucket:
                continue
            share = float(bucket.get(share_key) or 0.0)
            status = "disabled" if threshold is None else ("breach" if share > float(threshold) else "ok")
            threshold_text = "disabled" if threshold is None else f"{float(threshold):.2%}"
            lines.append(
                f"- {label}: {bucket.get('key')}, "
                f"trades={int(bucket.get('trades') or 0)}, "
                f"{share_key}={share:.2%}, "
                f"threshold={threshold_text}, "
                f"status={status}"
            )
    lines.extend(["", "## Caveats"])
    lines.extend(f"- {item}" for item in report.get("caveats", []))
    lines.append("")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write an offline live-readiness gate smoke report from backtest artifacts.")
    parser.add_argument("--input-root", required=True, type=Path, help="Normalized chunk root or full-market bundle root")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory for live_readiness_gate.json/.md")
    parser.add_argument("--evidence-coverage-threshold", type=float, default=0.95)
    parser.add_argument("--exit-evidence-coverage-threshold", type=float, default=0.95)
    parser.add_argument("--max-exit-path-ambiguity-rate", type=float, default=0.05)
    parser.add_argument("--max-setup-trade-share", type=float, default=0.45)
    parser.add_argument("--max-symbol-trade-share", type=float, default=0.70)
    parser.add_argument("--max-setup-net-abs-share", type=float, default=0.60)
    parser.add_argument("--max-symbol-net-abs-share", type=float, default=0.60)
    parser.add_argument("--max-setup-loss-abs-share", type=float, default=0.60)
    parser.add_argument("--max-symbol-loss-abs-share", type=float, default=0.60)
    parser.add_argument("--require-passive-calibration", action="store_true")
    parser.add_argument("--min-passive-calibration-attempts", type=int, default=0)
    parser.add_argument("--min-passive-fill-rate", type=float, default=None)
    parser.add_argument("--require-exit-path-replay-rows", action="store_true")
    parser.add_argument("--min-setup-trade-count", type=int, default=None)
    parser.add_argument("--banned-setup-type", action="append", default=[])
    parser.add_argument("--require-validation-evidence", action="store_true")
    parser.add_argument("--require-microstructure-evidence", action="store_true")
    parser.add_argument("--require-runtime-safety-evidence", action="store_true")
    parser.add_argument("--require-promotion-bundle-integrity", action="store_true")
    return parser.parse_args()


def _stdout_concentration_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    concentration = _as_mapping(report.get("concentration"))

    def _bucket(name: str) -> dict[str, Any]:
        bucket = _as_mapping(concentration.get(name))
        return {
            "key": bucket.get("key"),
            "trades": int(bucket.get("trades") or 0),
            "trade_share": float(bucket.get("trade_share") or 0.0),
            "net_abs_share": float(bucket.get("net_abs_share") or 0.0),
            "loss_abs_share": float(bucket.get("loss_abs_share") or 0.0),
        }

    return {
        "max_setup_trade_share": concentration.get("max_setup_trade_share"),
        "max_symbol_trade_share": concentration.get("max_symbol_trade_share"),
        "max_setup_net_abs_share": concentration.get("max_setup_net_abs_share"),
        "max_symbol_net_abs_share": concentration.get("max_symbol_net_abs_share"),
        "max_setup_loss_abs_share": concentration.get("max_setup_loss_abs_share"),
        "max_symbol_loss_abs_share": concentration.get("max_symbol_loss_abs_share"),
        "top_setup_by_trades": _bucket("top_setup_by_trades"),
        "top_symbol_by_trades": _bucket("top_symbol_by_trades"),
        "top_setup_by_net_abs": _bucket("top_setup_by_net_abs"),
        "top_symbol_by_net_abs": _bucket("top_symbol_by_net_abs"),
        "top_setup_by_loss_abs": _bucket("top_setup_by_loss_abs"),
        "top_symbol_by_loss_abs": _bucket("top_symbol_by_loss_abs"),
    }


def _stdout_reconciliation_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    reconciliation = _as_mapping(report.get("postmortem_reconciliation"))
    return {
        "matched": bool(reconciliation.get("matched")),
        "trade_count_delta": int(reconciliation.get("trade_count_delta") or 0),
        "net_pnl_delta": float(reconciliation.get("net_pnl_delta") or 0.0),
    }


def main() -> int:
    args = _parse_args()
    report = write_live_readiness_smoke_report(
        args.input_root,
        args.output_dir,
        evidence_coverage_threshold=args.evidence_coverage_threshold,
        exit_evidence_coverage_threshold=args.exit_evidence_coverage_threshold,
        max_exit_path_ambiguity_rate=args.max_exit_path_ambiguity_rate,
        max_setup_trade_share=args.max_setup_trade_share,
        max_symbol_trade_share=args.max_symbol_trade_share,
        max_setup_net_abs_share=args.max_setup_net_abs_share,
        max_symbol_net_abs_share=args.max_symbol_net_abs_share,
        max_setup_loss_abs_share=args.max_setup_loss_abs_share,
        max_symbol_loss_abs_share=args.max_symbol_loss_abs_share,
        require_passive_calibration=args.require_passive_calibration,
        min_passive_calibration_attempts=args.min_passive_calibration_attempts,
        min_passive_fill_rate=args.min_passive_fill_rate,
        require_exit_path_replay_rows=args.require_exit_path_replay_rows,
        min_setup_trade_count=args.min_setup_trade_count,
        banned_setup_types=args.banned_setup_type,
        require_validation_evidence=args.require_validation_evidence,
        require_microstructure_evidence=args.require_microstructure_evidence,
        require_runtime_safety_evidence=args.require_runtime_safety_evidence,
        require_promotion_bundle_integrity=args.require_promotion_bundle_integrity,
    )
    gate = _as_mapping(report.get("promotion_gate"))
    totals = _as_mapping(report.get("totals"))
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "decision": gate.get("decision"),
                "reasons": gate.get("reasons", []),
                "trade_count": totals.get("trade_count", 0),
                "net_pnl": totals.get("net_pnl", 0.0),
                "postmortem_reconciliation": _stdout_reconciliation_summary(report),
                "concentration": _stdout_concentration_summary(report),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
