from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping


DEPTH_CLASSIFICATIONS = (
    "trade_print_entry_only",
    "has_orderbook_top",
    "has_depth_levels",
    "maker_calibrated_possible",
    "insufficient_for_maker_replay",
)

EXIT_CLASSIFICATIONS = (
    "fixed_horizon_only",
    "bar_path_stop_or_tp",
    "trade_print_path_available",
    "ambiguous_intrabar_order",
)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(payload) if isinstance(payload, Mapping) else {}


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


def _execution_trades_for_symbol(market_context: Mapping[str, Any], symbol: str) -> list[Any]:
    symbols = _as_mapping(market_context.get("symbols"))
    payload = _as_mapping(symbols.get(symbol))
    execution = _as_mapping(payload.get("execution"))
    trades = execution.get("trades")
    return trades if isinstance(trades, list) else []


def _exit_classification(trade: Mapping[str, Any], market_context: Mapping[str, Any]) -> str:
    symbol = str(trade.get("symbol", ""))
    if _execution_trades_for_symbol(market_context, symbol):
        return "trade_print_path_available"
    simulated_ordering = str(trade.get("simulated_exit_ordering") or "").lower()
    if simulated_ordering == "ambiguous_conservative_stop":
        return "ambiguous_intrabar_order"
    simulated_reason = str(trade.get("simulated_exit_reason") or "").lower()
    exit_reason = str(trade.get("exit_reason") or "").lower()
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
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bucket_add(bucket: dict[str, Any], trade: Mapping[str, Any]) -> None:
    bucket["trade_count"] += 1
    bucket["net_pnl"] += _float_value(trade.get("net_pnl"))
    bucket["gross_pnl"] += _float_value(trade.get("gross_pnl"))
    bucket["fees"] += _float_value(trade.get("fee_paid"))
    bucket["slippage"] += _float_value(trade.get("slippage_paid"))
    bucket["funding"] += _float_value(trade.get("funding_paid"))


def _empty_bucket(name: str, key_name: str) -> dict[str, Any]:
    return {key_name: name, "trade_count": 0, "net_pnl": 0.0, "gross_pnl": 0.0, "fees": 0.0, "slippage": 0.0, "funding": 0.0}


def _add_group(groups: dict[str, dict[str, Any]], key: str, key_name: str, trade: Mapping[str, Any]) -> None:
    bucket = groups.setdefault(key, _empty_bucket(key, key_name))
    _bucket_add(bucket, trade)


def _chunk_report(chunk_dir: Path) -> dict[str, Any]:
    trades_payload = _load_json(chunk_dir / "trades.json")
    summary_payload = _load_json(chunk_dir / "summary.json")
    trades = _trades_payload(trades_payload)
    net_pnl = sum(_float_value(trade.get("net_pnl")) for trade in trades)
    evidence_count = sum(
        1
        for trade in trades
        if str(trade.get("fill_quality", "")).lower() in {"evidence_backed", "partial_evidence_backed"}
        or str(trade.get("execution_price_source", "")).lower() == "trade_print"
    )
    exit_evidence_count = sum(
        1
        for trade in trades
        if str(trade.get("exit_fill_quality", "")).lower() in {"evidence_backed", "partial_evidence_backed"}
        or str(trade.get("exit_price_source", "")).lower() == "trade_print"
    )
    metadata = _as_mapping(trades_payload.get("metadata"))
    period = metadata.get("sample_period")
    return {
        "chunk": chunk_dir.name,
        "path": str(chunk_dir),
        "trade_count": len(trades),
        "net_pnl": net_pnl,
        "gross_pnl": sum(_float_value(trade.get("gross_pnl")) for trade in trades),
        "costs": dict(_as_mapping(_as_mapping(summary_payload.get("summary")).get("cost_breakdown"))),
        "evidence_coverage": evidence_count / len(trades) if trades else 0.0,
        "exit_evidence_coverage": exit_evidence_count / len(trades) if trades else 0.0,
        "regime": metadata.get("regime") or metadata.get("regime_label"),
        "sample_period": period if isinstance(period, Mapping) else {},
    }


def _setup_rewrite_counts(summary: Mapping[str, Any]) -> dict[str, int]:
    return {
        "evaluated_count": int(summary.get("evaluated_count") or 0),
        "would_keep_count": int(summary.get("would_keep_count") or 0),
        "would_filter_count": int(summary.get("would_filter_count") or 0),
        "skipped_count": int(summary.get("skipped_count") or 0),
    }


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
    for chunk_dir in chunk_dirs:
        path = chunk_dir / "setup_rewrite_experiment.json"
        if not path.exists():
            continue
        payload = _load_json(path)
        summary = _as_mapping(payload.get("summary"))
        counts = _setup_rewrite_counts(summary)
        chunks.append({"chunk": chunk_dir.name, "path": str(path), "status": "loaded", "summary": counts})
        for key, value in counts.items():
            totals[key] += value
        for row in payload.get("evaluation_rows", []):
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
    }


def _runtime_safety_gate(chunk_dirs: Sequence[Path], *, required: bool) -> dict[str, Any]:
    required_checks = (
        "kill_switch_dry_run_met",
        "order_position_reconciliation_met",
        "fail_closed_met",
        "dust_before_scale_met",
        "live_trade_ledger_met",
        "runtime_explainability_met",
        "drift_guard_met",
    )
    artifacts: list[dict[str, Any]] = []
    aggregate_checks = {key: False for key in required_checks}
    for chunk_dir in chunk_dirs:
        path = chunk_dir / "runtime_safety_gate.json"
        if not path.exists():
            continue
        payload = _load_json(path)
        checks = _as_mapping(payload.get("checks"))
        artifacts.append(
            {
                "chunk": chunk_dir.name,
                "path": str(path),
                "schema_version": payload.get("schema_version"),
                "checks": {key: bool(checks.get(key)) for key in required_checks},
                "summary": _as_mapping(payload.get("summary")),
            }
        )
    if artifacts:
        aggregate_checks = {
            key: all(bool(_as_mapping(artifact.get("checks")).get(key)) for artifact in artifacts)
            for key in required_checks
        }
    return {
        "schema_version": "runtime_safety_gate.v1",
        "required": required,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "checks": aggregate_checks,
    }


def _microstructure_gate(chunk_dirs: Sequence[Path], *, required: bool) -> dict[str, Any]:
    required_checks = ("l2_tick_coverage_met", "depth_driven_taker_met")
    artifacts: list[dict[str, Any]] = []
    aggregate_checks = {key: False for key in required_checks}
    for chunk_dir in chunk_dirs:
        path = chunk_dir / "market_microstructure_gate.json"
        if not path.exists():
            continue
        payload = _load_json(path)
        checks = _as_mapping(payload.get("checks"))
        artifacts.append(
            {
                "chunk": chunk_dir.name,
                "path": str(path),
                "schema_version": payload.get("schema_version"),
                "checks": {key: bool(checks.get(key)) for key in required_checks},
                "summary": _as_mapping(payload.get("summary")),
            }
        )
    if artifacts:
        aggregate_checks = {
            key: all(bool(_as_mapping(artifact.get("checks")).get(key)) for artifact in artifacts)
            for key in required_checks
        }
    return {
        "schema_version": "microstructure_gate.v1",
        "required": required,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "checks": aggregate_checks,
    }


def _validation_gate(chunk_dirs: Sequence[Path], *, required: bool) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    required_checks = (
        "oos_non_degraded_met",
        "multi_regime_met",
        "cost_stress_positive_met",
        "forward_contamination_absent_met",
    )
    aggregate_checks = {key: False for key in required_checks}
    for chunk_dir in chunk_dirs:
        path = chunk_dir / "validation_gate.json"
        if not path.exists():
            continue
        payload = _load_json(path)
        checks = _as_mapping(payload.get("checks"))
        artifacts.append(
            {
                "chunk": chunk_dir.name,
                "path": str(path),
                "schema_version": payload.get("schema_version"),
                "checks": {key: bool(checks.get(key)) for key in required_checks},
                "summary": _as_mapping(payload.get("summary")),
            }
        )
    if artifacts:
        aggregate_checks = {
            key: all(bool(_as_mapping(artifact.get("checks")).get(key)) for artifact in artifacts)
            for key in required_checks
        }
    return {
        "schema_version": "validation_gate.v1",
        "required": required,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "checks": aggregate_checks,
    }


def _setup_quality_gate(
    by_setup: Mapping[str, Mapping[str, Any]],
    *,
    min_setup_trade_count: int | None,
    banned_setup_types: Sequence[str] | None,
) -> dict[str, Any]:
    banned = sorted({str(item) for item in (banned_setup_types or []) if str(item)})
    under_sampled = []
    if min_setup_trade_count is not None:
        threshold = max(0, int(min_setup_trade_count))
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
        "checks": {
            "setup_min_sample_met": not under_sampled,
            "banned_setup_types_absent": not present_banned,
        },
    }


def _exit_path_replay_reconciliation(chunk_dirs: Sequence[Path], *, required: bool) -> dict[str, Any]:
    trade_ids: list[str] = []
    path_trade_ids: set[str] = set()
    chunks_missing_artifact: list[str] = []
    for chunk_dir in chunk_dirs:
        trades = _trades_payload(_load_json(chunk_dir / "trades.json"))
        for index, trade in enumerate(trades):
            trade_ids.append(str(trade.get("trade_id") or f"{chunk_dir.name}:{index}"))
        path = chunk_dir / "exit_path_replay.json"
        if not path.exists():
            chunks_missing_artifact.append(chunk_dir.name)
            continue
        payload = _load_json(path)
        for index, row in enumerate(_trades_payload(payload)):
            path_trade_ids.add(str(row.get("trade_id") or f"{chunk_dir.name}:{index}"))
    missing = [trade_id for trade_id in trade_ids if trade_id not in path_trade_ids]
    extra = sorted(path_trade_ids - set(trade_ids))
    matched = not missing and not extra and not chunks_missing_artifact
    return {
        "schema_version": "exit_path_replay_reconciliation.v1",
        "required": required,
        "matched": matched if required else True if not trade_ids else matched,
        "trade_count": len(trade_ids),
        "path_trade_count": len(path_trade_ids),
        "missing_trade_count": len(missing),
        "extra_path_trade_count": len(extra),
        "chunks_missing_artifact": chunks_missing_artifact,
        "missing_trade_ids": missing[:50],
        "extra_path_trade_ids": extra[:50],
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
        overall = _as_mapping(payload.get("overall"))
        provenance = _as_mapping(payload.get("provenance"))
        attempts = int(overall.get("attempt_count") or 0)
        fill_rate = _float_value(overall.get("fill_rate"))
        total_attempts += attempts
        weighted_filled += fill_rate * attempts
        chunk_real = bool(provenance.get("real_exchange_records")) or str(provenance.get("source") or "").lower() in {
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
                "attempt_count": attempts,
                "fill_rate": fill_rate,
                "real_exchange_records": chunk_real,
                "provenance": provenance,
            }
        )
    fill_rate = weighted_filled / total_attempts if total_attempts else 0.0
    attempts_met = total_attempts >= max(0, int(min_attempts))
    fill_rate_met = min_fill_rate is None or fill_rate >= min_fill_rate
    real_records_met = (not required) or real_exchange_records
    return {
        "schema_version": "passive_calibration_live_readiness.v1",
        "required": required,
        "chunks": chunks,
        "attempt_count": total_attempts,
        "min_attempts": min_attempts,
        "fill_rate": fill_rate,
        "min_fill_rate": min_fill_rate,
        "real_exchange_records": real_exchange_records,
        "checks": {
            "passive_calibration_present_met": (not required) or bool(chunks),
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
) -> dict[str, Any]:
    root = Path(chunk_results_dir)
    chunk_dirs = sorted(path for path in root.iterdir() if path.is_dir() and (path / "trades.json").exists())
    all_trades: list[dict[str, Any]] = []
    chunk_performance: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        chunk_performance.append(_chunk_report(chunk_dir))
        all_trades.extend(_trades_payload(_load_json(chunk_dir / "trades.json")))

    by_setup: dict[str, dict[str, Any]] = {}
    by_symbol: dict[str, dict[str, Any]] = {}
    by_side: dict[str, dict[str, Any]] = {}
    for trade in all_trades:
        _add_group(by_setup, str(trade.get("setup_type") or "UNKNOWN"), "setup_type", trade)
        _add_group(by_symbol, str(trade.get("symbol") or "UNKNOWN"), "symbol", trade)
        _add_group(by_side, str(trade.get("side") or "UNKNOWN"), "side", trade)
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

    trade_count = len(all_trades)
    net_pnl = sum(_float_value(trade.get("net_pnl")) for trade in all_trades)
    gross_pnl = sum(_float_value(trade.get("gross_pnl")) for trade in all_trades)
    fees = sum(_float_value(trade.get("fee_paid")) for trade in all_trades)
    slippage = sum(_float_value(trade.get("slippage_paid")) for trade in all_trades)
    funding = sum(_float_value(trade.get("funding_paid")) for trade in all_trades)
    evidence_count = sum(
        1
        for trade in all_trades
        if str(trade.get("fill_quality", "")).lower() in {"evidence_backed", "partial_evidence_backed"}
        or str(trade.get("execution_price_source", "")).lower() == "trade_print"
    )
    evidence_coverage = evidence_count / trade_count if trade_count else 0.0
    exit_evidence_count = sum(
        1
        for trade in all_trades
        if str(trade.get("exit_fill_quality", "")).lower() in {"evidence_backed", "partial_evidence_backed"}
        or str(trade.get("exit_price_source", "")).lower() == "trade_print"
    )
    exit_evidence_coverage = exit_evidence_count / trade_count if trade_count else 0.0
    exit_path_replay = audit_exit_path_replay(all_trades)
    exit_path_reconciliation = _exit_path_replay_reconciliation(chunk_dirs, required=require_exit_path_replay_rows)
    exit_path_counts = _as_mapping(exit_path_replay.get("counts"))
    exit_path_ambiguous_count = int(exit_path_counts.get("fixed_horizon_only") or 0) + int(
        exit_path_counts.get("ambiguous_intrabar_order") or 0
    )
    exit_path_ambiguity_rate = exit_path_ambiguous_count / trade_count if trade_count else 0.0

    major_negative = [key for key, bucket in by_setup.items() if bucket["trade_count"] >= 1 and bucket["net_pnl"] < 0.0]
    total_abs_net = sum(abs(_float_value(trade.get("net_pnl"))) for trade in all_trades)
    total_loss_abs_net = sum(abs(min(_float_value(trade.get("net_pnl")), 0.0)) for trade in all_trades)
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
    if net_pnl < 0.0:
        reasons.append("net_pnl_below_zero")
    if evidence_coverage < evidence_coverage_threshold:
        reasons.append("evidence_coverage_below_threshold")
    if exit_evidence_coverage < exit_evidence_coverage_threshold:
        reasons.append("exit_evidence_coverage_below_threshold")
    if exit_path_ambiguity_rate > max_exit_path_ambiguity_rate:
        reasons.append("exit_path_ambiguity_rate_above_threshold")
    exit_path_replay_rows_met = (not require_exit_path_replay_rows) or bool(exit_path_reconciliation.get("matched"))
    if not exit_path_replay_rows_met:
        reasons.append("exit_path_replay_missing_trades")
    if major_negative:
        reasons.append("major_setup_bucket_negative")
    setup_quality_checks = _as_mapping(setup_quality_gate.get("checks"))
    if not setup_quality_checks.get("setup_min_sample_met", True):
        reasons.append("setup_min_sample_too_low")
    if not setup_quality_checks.get("banned_setup_types_absent", True):
        reasons.append("banned_setup_type_present")
    runtime_safety_checks = _as_mapping(runtime_safety_gate.get("checks"))
    if require_runtime_safety_evidence and int(runtime_safety_gate.get("artifact_count") or 0) == 0:
        reasons.append("runtime_safety_evidence_missing")
    runtime_safety_reason_by_check = {
        "kill_switch_dry_run_met": "kill_switch_dry_run_missing",
        "order_position_reconciliation_met": "order_position_reconciliation_missing",
        "fail_closed_met": "runtime_fail_closed_missing",
        "dust_before_scale_met": "live_dust_before_scale_missing",
        "live_trade_ledger_met": "live_trade_ledger_missing",
        "runtime_explainability_met": "runtime_explainability_missing",
        "drift_guard_met": "drift_guard_missing",
    }
    if require_runtime_safety_evidence:
        for check, reason in runtime_safety_reason_by_check.items():
            if not runtime_safety_checks.get(check, False):
                reasons.append(reason)
    microstructure_checks = _as_mapping(microstructure_gate.get("checks"))
    if require_microstructure_evidence and int(microstructure_gate.get("artifact_count") or 0) == 0:
        reasons.append("microstructure_evidence_missing")
    if require_microstructure_evidence and not microstructure_checks.get("l2_tick_coverage_met", False):
        reasons.append("l2_tick_coverage_below_threshold")
    if require_microstructure_evidence and not microstructure_checks.get("depth_driven_taker_met", False):
        reasons.append("taker_depth_driven_missing")
    validation_checks = _as_mapping(validation_gate.get("checks"))
    if require_validation_evidence and int(validation_gate.get("artifact_count") or 0) == 0:
        reasons.append("validation_evidence_missing")
    if require_validation_evidence and not validation_checks.get("oos_non_degraded_met", False):
        reasons.append("oos_degraded")
    if require_validation_evidence and not validation_checks.get("multi_regime_met", False):
        reasons.append("regime_single_point_survivor")
    if require_validation_evidence and not validation_checks.get("cost_stress_positive_met", False):
        reasons.append("cost_stress_not_positive")
    if require_validation_evidence and not validation_checks.get("forward_contamination_absent_met", False):
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
    if not passive_checks.get("passive_calibration_present_met", True):
        reasons.append("passive_calibration_missing")
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
            "setup_rewrite_has_surviving_candidates": not (
                setup_rewrite_evaluated > 0 and setup_rewrite_would_keep == 0
            ),
            "setup_rewrite_evidence_complete": setup_rewrite_skipped == 0,
        }
        if not setup_rewrite_checks["setup_rewrite_has_surviving_candidates"]:
            reasons.append("setup_rewrite_no_surviving_candidates")
        if not setup_rewrite_checks["setup_rewrite_evidence_complete"]:
            reasons.append("setup_rewrite_missing_evidence")
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
            "loss_trade_count": sum(1 for trade in all_trades if _float_value(trade.get("net_pnl")) < 0.0),
            "win_trade_count": sum(1 for trade in all_trades if _float_value(trade.get("net_pnl")) > 0.0),
            "negative_setup_buckets": sorted(major_negative),
            "negative_symbol_buckets": sorted(key for key, bucket in by_symbol.items() if bucket["net_pnl"] < 0.0),
        },
        "by_setup_type": {key: by_setup[key] for key in sorted(by_setup)},
        "by_symbol": {key: by_symbol[key] for key in sorted(by_symbol)},
        "by_side": {key: by_side[key] for key in sorted(by_side)},
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
            "checks": {
                "net_pnl_non_negative": net_pnl >= 0.0,
                "evidence_coverage_met": evidence_coverage >= evidence_coverage_threshold,
                "exit_evidence_coverage_met": exit_evidence_coverage >= exit_evidence_coverage_threshold,
                "exit_path_ambiguity_rate_met": exit_path_ambiguity_rate <= max_exit_path_ambiguity_rate,
                "exit_path_replay_rows_met": exit_path_replay_rows_met,
                "major_setup_buckets_non_negative": not major_negative,
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
    gross = _float_value(trade.get("gross_pnl"))
    net = _float_value(trade.get("net_pnl"))
    cost = _float_value(trade.get("fee_paid")) + _float_value(trade.get("slippage_paid")) + _float_value(trade.get("funding_paid"))
    bucket["trades"] += 1
    bucket["wins"] += 1 if net > 0.0 else 0
    bucket["gross"] += gross
    bucket["net"] += net
    bucket["cost"] += cost
    bucket["win_rate"] = bucket["wins"] / bucket["trades"] if bucket["trades"] else 0.0


def _postmortem_failure_bucket(trade: Mapping[str, Any]) -> str:
    gross = _float_value(trade.get("gross_pnl"))
    net = _float_value(trade.get("net_pnl"))
    mfe = _float_value(trade.get("mfe_pct"))
    mae = _float_value(trade.get("mae_pct"))
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
        setup_key = str(trade.get("setup_type") or "UNKNOWN")
        _add_postmortem_bucket(by_setup.setdefault(setup_key, _empty_postmortem_bucket()), trade)
        symbol_key = str(trade.get("symbol") or "UNKNOWN")
        _add_postmortem_bucket(by_symbol.setdefault(symbol_key, _empty_postmortem_bucket()), trade)
    summary_payload = {
        **summary,
        "gross_pnl": summary["gross"],
        "net_pnl": summary["net"],
        "cost_total": summary["cost"],
    }
    total_trades = int(summary["trades"])
    total_abs_net = sum(abs(_float_value(trade.get("net_pnl"))) for trade in rows)
    total_loss_abs_net = sum(abs(min(_float_value(trade.get("net_pnl")), 0.0)) for trade in rows)
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
        for artifact_name in ("trades.json", "summary.json", "setup_rewrite_experiment.json"):
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
    )
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
                f"- fail_closed_met: {str(bool(checks.get('fail_closed_met'))).lower()}",
                f"- dust_before_scale_met: {str(bool(checks.get('dust_before_scale_met'))).lower()}",
                f"- live_trade_ledger_met: {str(bool(checks.get('live_trade_ledger_met'))).lower()}",
                f"- runtime_explainability_met: {str(bool(checks.get('runtime_explainability_met'))).lower()}",
                f"- drift_guard_met: {str(bool(checks.get('drift_guard_met'))).lower()}",
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
                f"- multi_regime_met: {str(bool(checks.get('multi_regime_met'))).lower()}",
                f"- cost_stress_positive_met: {str(bool(checks.get('cost_stress_positive_met'))).lower()}",
                f"- forward_contamination_absent_met: {str(bool(checks.get('forward_contamination_absent_met'))).lower()}",
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
