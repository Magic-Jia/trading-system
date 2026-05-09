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
    if value is None:
        return datetime.now(BJ).isoformat()
    if not isinstance(value, str):
        raise ValueError("recorded_at_bj must be a string")
    if value:
        return value
    return datetime.now(BJ).isoformat()


def _optional_str(payload: Mapping[str, Any], field_name: str, *, source_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{source_name}.{field_name} must be a string")
    return value


def _optional_list(payload: Mapping[str, Any], field_name: str, *, source_name: str) -> list[Any]:
    value = payload.get(field_name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{source_name}.{field_name} must be a list")
    return value


def _optional_mapping(payload: Mapping[str, Any] | None, *, source_name: str) -> Mapping[str, Any] | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ValueError(f"{source_name} must be an object")
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _float(value: Any, default: float = 0.0, *, field_name: str = "metric") -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    return float(value)


def _int(value: Any, default: int = 0, *, field_name: str = "metric") -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be numeric")
    return value


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
    trade_outcome_count = _int(bucket.get("trade_outcome_count"), field_name=f"by_engine.{engine}.trade_outcome_count")
    unrealized_pnl_total = round(_float(bucket.get("unrealized_pnl_total"), field_name=f"by_engine.{engine}.unrealized_pnl_total"), 4)
    position_not_tracked_count = _int(bucket.get("position_not_tracked_count"), field_name=f"by_engine.{engine}.position_not_tracked_count")
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
    trade_outcome_count = _int(daily_metrics.get("trade_outcome_count"), field_name="daily_metrics.trade_outcome_count")
    unrealized_pnl_total = round(_float(daily_metrics.get("unrealized_pnl_total"), field_name="daily_metrics.unrealized_pnl_total"), 4)
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
            "open_count": _int(daily_metrics.get("open_count"), field_name="daily_metrics.open_count"),
            "position_not_tracked_count": _int(daily_metrics.get("position_not_tracked_count"), field_name="daily_metrics.position_not_tracked_count"),
        },
    }


def generate_recommendations(
    *,
    daily_metrics: Mapping[str, Any],
    health_report: Mapping[str, Any],
    recorded_at_bj: str | None = None,
    previous_recommendations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    previous_payload = _optional_mapping(previous_recommendations, source_name="previous_recommendations")
    metrics_recorded_at_bj = _optional_str(daily_metrics, "recorded_at_bj", source_name="daily_metrics")
    recommendation_recorded_at = _recorded_at_bj(recorded_at_bj)
    trade_outcome_count = _int(daily_metrics.get("trade_outcome_count"), field_name="daily_metrics.trade_outcome_count")
    unrealized_pnl_total = round(_float(daily_metrics.get("unrealized_pnl_total"), field_name="daily_metrics.unrealized_pnl_total"), 4)
    warnings = _optional_list(health_report, "warnings", source_name="health_report")
    suppressed: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []

    health_status = _optional_str(health_report, "status", source_name="health_report") or "missing"
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
        if previous_payload is not None:
            previous_suppressed = _optional_list(previous_payload, "suppressed", source_name="previous_recommendations")
            for item in previous_suppressed:
                if not isinstance(item, Mapping):
                    raise ValueError("previous_recommendations.suppressed entries must be objects")
                if item.get("reason") == "low_sample":
                    previous_low_sample_count = max(previous_low_sample_count, _int(item.get("consecutive_count"), 1, field_name="previous_recommendations.suppressed.consecutive_count"))
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
        if by_engine is not None:
            if not isinstance(by_engine, Mapping):
                raise ValueError("daily_metrics.by_engine must be an object")
            for engine_name, raw_bucket in by_engine.items():
                if not isinstance(engine_name, str):
                    raise ValueError("daily_metrics.by_engine keys must be strings")
                if not isinstance(raw_bucket, Mapping):
                    raise ValueError(f"daily_metrics.by_engine.{engine_name} must be an object")
                engine_trade_outcome_count = _int(raw_bucket.get("trade_outcome_count"), field_name=f"by_engine.{engine_name}.trade_outcome_count")
                engine_unrealized_pnl_total = _float(raw_bucket.get("unrealized_pnl_total"), field_name=f"by_engine.{engine_name}.unrealized_pnl_total")
                if engine_trade_outcome_count < _DEFAULT_MIN_ENGINE_TRADE_OUTCOMES:
                    continue
                if engine_unrealized_pnl_total > _DEFAULT_ENGINE_LOSS_THRESHOLD:
                    continue
                recommendation = _engine_weight_recommendation(
                    engine=engine_name,
                    bucket=raw_bucket,
                    metrics_recorded_at_bj=metrics_recorded_at_bj,
                )
                if recommendation is not None:
                    recommendations.append(recommendation)

    previous_ids = set()
    if previous_payload is not None:
        previous_items = _optional_list(previous_payload, "recommendations", source_name="previous_recommendations")
        for item in previous_items:
            if not isinstance(item, Mapping):
                raise ValueError("previous_recommendations.recommendations entries must be objects")
            previous_id = item.get("id")
            if previous_id is None:
                continue
            if not isinstance(previous_id, str):
                raise ValueError("previous_recommendations.recommendations.id must be a string")
            if previous_id:
                previous_ids.add(previous_id)
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
