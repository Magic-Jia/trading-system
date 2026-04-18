from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Mapping

from .metrics import cost_drag
from .types import BaselineReplayResult, TradeLedgerRow


def render_regime_scorecard(
    *,
    experiment_name: str,
    experiment: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    by_regime = dict(experiment.get("by_regime", {}))
    best_regime = None
    best_return = None
    worst_regime = None
    worst_return = None
    for label, payload in by_regime.items():
        current = float(dict(payload.get("forward_return_by_window", {})).get("3d", 0.0))
        if best_return is None or current > best_return:
            best_regime, best_return = label, current
        if worst_return is None or current < worst_return:
            worst_regime, worst_return = label, current

    regimes_with_samples = len(by_regime)
    promotion_pass = regimes_with_samples >= 2 and (best_return or 0.0) > 0 and (worst_return or 0.0) < 0
    summary = (
        f"{best_regime} leads forward return dispersion while {worst_regime} stays weakest"
        if promotion_pass
        else "regime separation is not yet strong enough for promotion"
    )

    return {
        "metadata": {
            "experiment_name": experiment_name,
            "dataset_root": metadata.get("dataset_root"),
            "baseline_name": metadata.get("baseline_name"),
            "variant_name": metadata.get("variant_name"),
            "sample_period": metadata.get("sample_period"),
        },
        "key_metrics": {
            "snapshot_count": dict(experiment.get("metadata", {})).get("snapshot_count", 0),
            "regimes_covered": regimes_with_samples,
            "best_regime_3d": best_regime,
            "best_regime_3d_return": best_return or 0.0,
            "worst_regime_3d": worst_regime,
            "worst_regime_3d_return": worst_return or 0.0,
        },
        "decision_summary": {
            "decision": "保留" if promotion_pass else "暂缓，等更多样本",
            "summary": summary,
        },
        "promotion_gate": {
            "status": "pass" if promotion_pass else "hold",
            "checks": {
                "has_multiple_regimes": regimes_with_samples >= 2,
                "positive_best_regime": (best_return or 0.0) > 0,
                "negative_worst_regime": (worst_return or 0.0) < 0,
            },
        },
    }


def _trade_breakdown_rows(
    trade_ledger: tuple[TradeLedgerRow, ...], *, key_name: str, key_fn: Callable[[TradeLedgerRow], Any]
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, float | int | str]] = {}
    for row in trade_ledger:
        bucket_key = str(key_fn(row))
        bucket = buckets.setdefault(
            bucket_key,
            {
                key_name: bucket_key,
                "trade_count": 0,
                "gross_pnl": 0.0,
                "net_pnl": 0.0,
                "fees": 0.0,
                "slippage": 0.0,
                "funding": 0.0,
            },
        )
        bucket["trade_count"] = int(bucket["trade_count"]) + 1
        bucket["gross_pnl"] = float(bucket["gross_pnl"]) + row.gross_pnl
        bucket["net_pnl"] = float(bucket["net_pnl"]) + row.net_pnl
        bucket["fees"] = float(bucket["fees"]) + row.fee_paid
        bucket["slippage"] = float(bucket["slippage"]) + row.slippage_paid
        bucket["funding"] = float(bucket["funding"]) + row.funding_paid
    return [buckets[key] for key in sorted(buckets)]


def render_full_market_baseline_report(result: BaselineReplayResult) -> dict[str, Any]:
    reason_counts = Counter(
        reason
        for row in result.rejection_ledger
        for reason in row.reasons
    )

    return {
        "summary": {
            "experiment_name": result.portfolio_summary.experiment_name,
            "total_return": result.portfolio_summary.total_return,
            "max_drawdown": result.portfolio_summary.max_drawdown,
            "sharpe": result.portfolio_summary.sharpe,
            "sortino": result.portfolio_summary.sortino,
            "calmar": result.portfolio_summary.calmar,
            "turnover": result.portfolio_summary.turnover,
            "trade_count": result.portfolio_summary.trade_count,
            "cost_drag": cost_drag(result.gross_period_returns, result.net_period_returns),
            "cost_breakdown": dict(result.cost_breakdown),
        },
        "breakdowns": {
            "by_market": _trade_breakdown_rows(result.trade_ledger, key_name="market_type", key_fn=lambda row: row.market_type),
            "by_year": _trade_breakdown_rows(result.trade_ledger, key_name="year", key_fn=lambda row: row.exit_timestamp.year),
        },
        "audit": {
            "trade_count": len(result.trade_ledger),
            "accepted_count": sum(1 for row in result.trade_ledger if row.status == "accepted"),
            "resized_count": sum(1 for row in result.trade_ledger if row.status == "resized"),
            "rejection_count": len(result.rejection_ledger),
            "rejection_reasons": dict(sorted(reason_counts.items())),
        },
    }
