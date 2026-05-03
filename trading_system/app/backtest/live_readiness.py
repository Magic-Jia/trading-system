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


def build_live_readiness_gate_report(
    chunk_results_dir: str | Path,
    *,
    evidence_coverage_threshold: float = 0.95,
    exit_evidence_coverage_threshold: float = 0.95,
    max_exit_path_ambiguity_rate: float = 0.05,
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
    setup_rewrite_diagnostic = _setup_rewrite_diagnostic(chunk_dirs)

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
    exit_path_counts = _as_mapping(exit_path_replay.get("counts"))
    exit_path_ambiguous_count = int(exit_path_counts.get("fixed_horizon_only") or 0) + int(
        exit_path_counts.get("ambiguous_intrabar_order") or 0
    )
    exit_path_ambiguity_rate = exit_path_ambiguous_count / trade_count if trade_count else 0.0

    major_negative = [key for key, bucket in by_setup.items() if bucket["trade_count"] >= 1 and bucket["net_pnl"] < 0.0]
    reasons: list[str] = []
    if net_pnl < 0.0:
        reasons.append("net_pnl_below_zero")
    if evidence_coverage < evidence_coverage_threshold:
        reasons.append("evidence_coverage_below_threshold")
    if exit_evidence_coverage < exit_evidence_coverage_threshold:
        reasons.append("exit_evidence_coverage_below_threshold")
    if exit_path_ambiguity_rate > max_exit_path_ambiguity_rate:
        reasons.append("exit_path_ambiguity_rate_above_threshold")
    if major_negative:
        reasons.append("major_setup_bucket_negative")
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
        "exit_path_replay": {
            "schema_version": exit_path_replay.get("schema_version"),
            "counts": dict(exit_path_counts),
            "ambiguous_count": exit_path_ambiguous_count,
            "ambiguity_rate": exit_path_ambiguity_rate,
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
                "major_setup_buckets_non_negative": not major_negative,
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
    return {
        "schema_version": "trade_postmortem_summary.v1",
        "summary": summary_payload,
        "by_failure_taxonomy": {key: by_failure[key] for key in sorted(by_failure)},
        "by_setup_type": {key: by_setup[key] for key in sorted(by_setup)},
        "by_symbol": {key: by_symbol[key] for key in sorted(by_symbol)},
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


def write_live_readiness_smoke_report(
    input_root: str | Path,
    output_dir: str | Path,
    *,
    evidence_coverage_threshold: float = 0.95,
    exit_evidence_coverage_threshold: float = 0.95,
    max_exit_path_ambiguity_rate: float = 0.05,
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
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = write_live_readiness_smoke_report(
        args.input_root,
        args.output_dir,
        evidence_coverage_threshold=args.evidence_coverage_threshold,
        exit_evidence_coverage_threshold=args.exit_evidence_coverage_threshold,
        max_exit_path_ambiguity_rate=args.max_exit_path_ambiguity_rate,
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
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
