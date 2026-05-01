from __future__ import annotations

import json
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
        "regime": metadata.get("regime") or metadata.get("regime_label"),
        "sample_period": period if isinstance(period, Mapping) else {},
    }


def build_live_readiness_gate_report(
    chunk_results_dir: str | Path,
    *,
    evidence_coverage_threshold: float = 0.95,
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

    major_negative = [key for key, bucket in by_setup.items() if bucket["trade_count"] >= 1 and bucket["net_pnl"] < 0.0]
    reasons: list[str] = []
    if net_pnl < 0.0:
        reasons.append("net_pnl_below_zero")
    if evidence_coverage < evidence_coverage_threshold:
        reasons.append("evidence_coverage_below_threshold")
    if major_negative:
        reasons.append("major_setup_bucket_negative")
    decision = "reject_for_live_promotion" if reasons else "candidate_for_promotion"

    return {
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
                "major_setup_buckets_non_negative": not major_negative,
            },
        },
        "caveats": [
            "Offline readiness gate only; it must not place live or testnet orders.",
            "Chunk aggregation depends on trades.json fields emitted by the backtest bundle.",
        ],
    }


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
        "",
        "## Caveats",
    ]
    lines.extend(f"- {item}" for item in report.get("caveats", []))
    lines.append("")
    return "\n".join(lines)
