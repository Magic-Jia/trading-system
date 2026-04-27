from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from ..types import BJ

_DEFAULT_MIN_TRADE_OUTCOMES = 5
_DEFAULT_MIN_ENGINE_TRADE_OUTCOMES = 3
_DEFAULT_TOTAL_LOSS_THRESHOLD = -0.5
_DEFAULT_ENGINE_LOSS_THRESHOLD = -0.25
_DEFAULT_TOTAL_RISK_PCT = 0.03
_ENGINE_WEIGHT_DEFAULTS = {
    "trend": ("TRADING_ALLOCATOR_TREND_BUCKET_WEIGHT", 0.70),
    "rotation": ("TRADING_ALLOCATOR_ROTATION_BUCKET_WEIGHT", 0.30),
    "short": ("TRADING_ALLOCATOR_SHORT_BUCKET_WEIGHT", 0.00),
}


def _recorded_at_bj(value: str | None) -> str:
    if value:
        return value
    return datetime.now(BJ).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _engine_weight_recommendation(
    *,
    engine: str,
    bucket: Mapping[str, Any],
    metrics_recorded_at_bj: str | None,
) -> dict[str, Any] | None:
    env_contract = _ENGINE_WEIGHT_DEFAULTS.get(engine)
    if env_contract is None:
        return None
    env_name, default_value = env_contract
    if default_value <= 0:
        return None
    proposed_value = round(default_value * 0.75, 4)
    trade_outcome_count = _int(bucket.get("trade_outcome_count"))
    unrealized_pnl_total = round(_float(bucket.get("unrealized_pnl_total")), 4)
    position_not_tracked_count = _int(bucket.get("position_not_tracked_count"))
    return {
        "id": f"reduce-{engine}-bucket-weight",
        "kind": "reduce_bucket_weight",
        "severity": "recommendation",
        "target": {
            "scope": "engine",
            "engine": engine,
            "config_key": env_name,
        },
        "overlay_ops": [
            {
                "env": env_name,
                "op": "multiply",
                "factor": 0.75,
                "default": default_value,
                "minimum": 0.0,
                "precision": 4,
            }
        ],
        "proposed_value": proposed_value,
        "rationale": (
            f"{engine} bucket showed {trade_outcome_count} tracked outcomes with "
            f"unrealized_pnl_total={unrealized_pnl_total:.4f}; reduce bucket weight conservatively before disabling it."
        ),
        "evidence_window": {
            "metrics_recorded_at_bj": metrics_recorded_at_bj,
            "trade_outcome_count": trade_outcome_count,
            "position_not_tracked_count": position_not_tracked_count,
        },
        "evidence": {
            "unrealized_pnl_total": unrealized_pnl_total,
            "trade_outcome_count": trade_outcome_count,
            "position_not_tracked_count": position_not_tracked_count,
        },
    }


def _risk_budget_recommendation(
    *,
    daily_metrics: Mapping[str, Any],
    metrics_recorded_at_bj: str | None,
) -> dict[str, Any]:
    trade_outcome_count = _int(daily_metrics.get("trade_outcome_count"))
    unrealized_pnl_total = round(_float(daily_metrics.get("unrealized_pnl_total")), 4)
    proposed_value = round(_DEFAULT_TOTAL_RISK_PCT * 0.8, 4)
    return {
        "id": "lower-total-risk-budget",
        "kind": "lower_risk_budget",
        "severity": "recommendation",
        "target": {
            "scope": "portfolio",
            "config_key": "TRADING_MAX_TOTAL_RISK_PCT",
        },
        "overlay_ops": [
            {
                "env": "TRADING_MAX_TOTAL_RISK_PCT",
                "op": "multiply",
                "factor": 0.8,
                "default": _DEFAULT_TOTAL_RISK_PCT,
                "minimum": 0.005,
                "precision": 4,
            }
        ],
        "proposed_value": proposed_value,
        "rationale": (
            f"Portfolio-level paper outcomes accumulated unrealized_pnl_total={unrealized_pnl_total:.4f} across "
            f"{trade_outcome_count} outcomes; lower max total risk before changing strategy code."
        ),
        "evidence_window": {
            "metrics_recorded_at_bj": metrics_recorded_at_bj,
            "trade_outcome_count": trade_outcome_count,
        },
        "evidence": {
            "unrealized_pnl_total": unrealized_pnl_total,
            "trade_outcome_count": trade_outcome_count,
            "open_count": _int(daily_metrics.get("open_count")),
            "position_not_tracked_count": _int(daily_metrics.get("position_not_tracked_count")),
        },
    }


def generate_recommendations(
    *,
    daily_metrics: Mapping[str, Any],
    health_report: Mapping[str, Any],
    recorded_at_bj: str | None = None,
    previous_recommendations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metrics_recorded_at_bj = str(daily_metrics.get("recorded_at_bj") or "") or None
    recommendation_recorded_at = _recorded_at_bj(recorded_at_bj)
    trade_outcome_count = _int(daily_metrics.get("trade_outcome_count"))
    unrealized_pnl_total = round(_float(daily_metrics.get("unrealized_pnl_total")), 4)
    warnings = list(health_report.get("warnings") or []) if isinstance(health_report.get("warnings"), list) else []
    suppressed: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []

    health_status = str(health_report.get("status") or "missing")
    if health_status != "ok":
        suppressed.append(
            {
                "reason": "health_not_ok",
                "message": "health report is not clean enough to emit configuration recommendations",
                "health_status": health_status,
                "warning_count": len(warnings),
            }
        )
    elif trade_outcome_count < _DEFAULT_MIN_TRADE_OUTCOMES:
        previous_low_sample_count = 0
        if isinstance(previous_recommendations, Mapping):
            previous_suppressed = previous_recommendations.get("suppressed")
            if isinstance(previous_suppressed, list):
                for item in previous_suppressed:
                    if isinstance(item, Mapping) and item.get("reason") == "low_sample":
                        previous_low_sample_count = max(previous_low_sample_count, _int(item.get("consecutive_count"), 1))
        consecutive_count = previous_low_sample_count + 1
        suppressed.append(
            {
                "reason": "low_sample",
                "message": (
                    f"trade_outcome_count={trade_outcome_count} is below the minimum sample "
                    f"threshold {_DEFAULT_MIN_TRADE_OUTCOMES}"
                ),
                "minimum_trade_outcome_count": _DEFAULT_MIN_TRADE_OUTCOMES,
                "trade_outcome_count": trade_outcome_count,
                "consecutive_count": consecutive_count,
            }
        )
        if consecutive_count >= 3:
            alerts.append(
                {
                    "code": "consecutive_low_sample",
                    "severity": "warning",
                    "message": f"paper optimization has been suppressed by low_sample for {consecutive_count} consecutive runs",
                    "consecutive_count": consecutive_count,
                    "minimum_trade_outcome_count": _DEFAULT_MIN_TRADE_OUTCOMES,
                    "trade_outcome_count": trade_outcome_count,
                }
            )
    else:
        if unrealized_pnl_total <= _DEFAULT_TOTAL_LOSS_THRESHOLD:
            recommendations.append(
                _risk_budget_recommendation(
                    daily_metrics=daily_metrics,
                    metrics_recorded_at_bj=metrics_recorded_at_bj,
                )
            )

        by_engine = daily_metrics.get("by_engine")
        if isinstance(by_engine, Mapping):
            for engine_name, raw_bucket in by_engine.items():
                if not isinstance(raw_bucket, Mapping):
                    continue
                engine_trade_outcome_count = _int(raw_bucket.get("trade_outcome_count"))
                engine_unrealized_pnl_total = _float(raw_bucket.get("unrealized_pnl_total"))
                if engine_trade_outcome_count < _DEFAULT_MIN_ENGINE_TRADE_OUTCOMES:
                    continue
                if engine_unrealized_pnl_total > _DEFAULT_ENGINE_LOSS_THRESHOLD:
                    continue
                recommendation = _engine_weight_recommendation(
                    engine=str(engine_name),
                    bucket=raw_bucket,
                    metrics_recorded_at_bj=metrics_recorded_at_bj,
                )
                if recommendation is not None:
                    recommendations.append(recommendation)

    previous_ids = set()
    if isinstance(previous_recommendations, Mapping):
        previous_items = previous_recommendations.get("recommendations")
        if isinstance(previous_items, list):
            for item in previous_items:
                if isinstance(item, Mapping) and item.get("id"):
                    previous_ids.add(str(item.get("id")))
    for recommendation in recommendations:
        recommendation["is_repeat"] = recommendation["id"] in previous_ids

    return {
        "recorded_at_bj": recommendation_recorded_at,
        "metrics_recorded_at_bj": metrics_recorded_at_bj,
        "health_status": health_status,
        "warning_count": len(warnings),
        "recommendation_count": len(recommendations),
        "recommendations": recommendations,
        "suppressed": suppressed,
        "alerts": alerts,
    }


def write_recommendations(
    *,
    daily_metrics_path: Path,
    health_report_path: Path,
    recommendations_path: Path,
    previous_recommendations_path: Path | None = None,
    recorded_at_bj: str | None = None,
) -> dict[str, Any]:
    daily_metrics = _read_json(daily_metrics_path)
    health_report = _read_json(health_report_path)
    previous: dict[str, Any] | None = None
    if previous_recommendations_path is not None and previous_recommendations_path.exists():
        previous = _read_json(previous_recommendations_path)

    payload = generate_recommendations(
        daily_metrics=daily_metrics,
        health_report=health_report,
        recorded_at_bj=recorded_at_bj,
        previous_recommendations=previous,
    )
    recommendations_path.parent.mkdir(parents=True, exist_ok=True)
    recommendations_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


__all__ = ["generate_recommendations", "write_recommendations"]
