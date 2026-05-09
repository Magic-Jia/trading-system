from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from ..backtest.promotion import compare_backtest_bundles
from ..types import BJ

CompareBacktestBundlesFn = Callable[..., dict[str, dict[str, Any]]]


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


def _format_float(value: float, precision: int) -> str:
    rendered = f"{value:.{precision}f}"
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def materialize_env_overrides(
    recommendations_payload: Mapping[str, Any],
    *,
    baseline_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env_values: dict[str, str] = dict(baseline_env or {})
    recommendations = recommendations_payload.get("recommendations")
    if not isinstance(recommendations, list):
        return {}

    for recommendation in recommendations:
        if not isinstance(recommendation, Mapping):
            continue
        overlay_ops = recommendation.get("overlay_ops")
        if not isinstance(overlay_ops, list):
            continue
        for raw_op in overlay_ops:
            if not isinstance(raw_op, Mapping):
                raise ValueError("overlay_ops entries must be objects")
            env_name = str(raw_op.get("env") or "").strip()
            if not env_name:
                continue
            op = str(raw_op.get("op") or "set").strip().lower()
            if op == "set":
                env_values[env_name] = str(raw_op.get("value") or "")
                continue
            if op != "multiply":
                raise ValueError(f"unsupported overlay op: {op}")

            precision = int(raw_op.get("precision", 4))
            factor = float(raw_op.get("factor", 1.0))
            minimum = float(raw_op.get("minimum", float("-inf")))
            maximum = float(raw_op.get("maximum", float("inf")))
            base_value = float(env_values.get(env_name, raw_op.get("default", 0.0)))
            proposed = max(minimum, min(maximum, base_value * factor))
            env_values[env_name] = _format_float(round(proposed, precision), precision)

    return {
        key: value
        for key, value in env_values.items()
        if baseline_env is None or baseline_env.get(key) != value or key not in baseline_env
    }


def build_promotion_decision(
    *,
    recommendations_payload: Mapping[str, Any],
    baseline_bundle: str | Path | None = None,
    variant_bundle: str | Path | None = None,
    baseline_env: Mapping[str, str] | None = None,
    compare_backtest_bundles_fn: CompareBacktestBundlesFn = compare_backtest_bundles,
    recorded_at_bj: str | None = None,
) -> dict[str, Any]:
    recommendations = recommendations_payload.get("recommendations")
    if not isinstance(recommendations, list):
        recommendations = []

    env_overrides = materialize_env_overrides(
        recommendations_payload,
        baseline_env=baseline_env,
    )
    applied_ids = [
        str(item.get("id"))
        for item in recommendations
        if isinstance(item, Mapping) and item.get("id")
    ]

    payload: dict[str, Any] = {
        "recorded_at_bj": _recorded_at_bj(recorded_at_bj),
        "recommendation_count": len(applied_ids),
        "applied_recommendation_ids": applied_ids,
        "variant": {
            "name": "paper_optimization_candidate",
            "env_overrides": env_overrides,
        },
    }

    if not applied_ids:
        payload["status"] = "observe"
        payload["decision"] = "observe"
        payload["summary"] = "no active recommendations to validate"
        return payload

    payload["status"] = "recommend"
    payload["decision"] = "awaiting_backtest"
    payload["summary"] = "recommendations translated into a candidate env overlay; awaiting validation bundles"

    if baseline_bundle is None or variant_bundle is None:
        return payload

    comparison = compare_backtest_bundles_fn(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )
    promotion_gate = dict(comparison.get("promotion_gate", {}))
    decision_summary = dict(comparison.get("decision_summary", {}))
    payload["status"] = str(promotion_gate.get("decision") or decision_summary.get("decision") or "hold")
    payload["decision"] = payload["status"]
    payload["baseline_bundle"] = str(baseline_bundle)
    payload["variant_bundle"] = str(variant_bundle)
    payload["promotion_gate"] = promotion_gate
    payload["decision_summary"] = decision_summary
    payload["summary"] = str(decision_summary.get("summary") or payload["summary"])
    return payload


def write_promotion_decision(
    *,
    recommendations_path: Path,
    promotion_decision_path: Path,
    baseline_bundle: str | Path | None = None,
    variant_bundle: str | Path | None = None,
    baseline_env: Mapping[str, str] | None = None,
    compare_backtest_bundles_fn: CompareBacktestBundlesFn = compare_backtest_bundles,
    recorded_at_bj: str | None = None,
) -> dict[str, Any]:
    recommendations_payload = _read_json(recommendations_path)
    payload = build_promotion_decision(
        recommendations_payload=recommendations_payload,
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
        baseline_env=baseline_env,
        compare_backtest_bundles_fn=compare_backtest_bundles_fn,
        recorded_at_bj=recorded_at_bj,
    )
    promotion_decision_path.parent.mkdir(parents=True, exist_ok=True)
    promotion_decision_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


__all__ = ["build_promotion_decision", "materialize_env_overrides", "write_promotion_decision"]
