from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from ..backtest.promotion import compare_backtest_bundles
from ..types import BJ

CompareBacktestBundlesFn = Callable[..., dict[str, dict[str, Any]]]


def _recorded_at_bj(value: str | None) -> str:
    if value is None:
        return datetime.now(BJ).isoformat()
    if not isinstance(value, str):
        raise ValueError("recorded_at_bj must be a string")
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


def _numeric(value: Any, *, field_name: str, default: float | None = None) -> float:
    if value is None:
        if default is None:
            raise ValueError(f"{field_name} must be numeric")
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc


def _integer(value: Any, *, field_name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _recommendation_ids(recommendations: list[Any]) -> list[str]:
    ids: list[str] = []
    for item in recommendations:
        if not isinstance(item, Mapping):
            raise ValueError("recommendations entries must be objects")
        recommendation_id = item.get("id")
        if recommendation_id is None:
            continue
        if not isinstance(recommendation_id, str) or not recommendation_id:
            raise ValueError("recommendations.id must be a string")
        ids.append(recommendation_id)
    return ids


def _object_section(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object")
    return dict(value)


def _optional_section_str(section: Mapping[str, Any], key: str, *, section_name: str) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{section_name}.{key} must be a string")
    return value


def _bundle_path(value: str | Path | None, *, field_name: str) -> str | Path | None:
    if value is None:
        return None
    if not isinstance(value, (str, Path)):
        raise ValueError(f"{field_name} must be a path string")
    return value


def materialize_env_overrides(
    recommendations_payload: Mapping[str, Any],
    *,
    baseline_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    baseline_snapshot: dict[str, str] = {}
    if baseline_env is not None:
        if not isinstance(baseline_env, Mapping):
            raise ValueError("baseline_env must be an object")
        for key, value in baseline_env.items():
            if not isinstance(key, str):
                raise ValueError("baseline_env keys must be strings")
            if not isinstance(value, str):
                raise ValueError(f"baseline_env.{key} must be a string")
            baseline_snapshot[key] = value
    env_values: dict[str, str] = dict(baseline_snapshot)
    recommendations = recommendations_payload.get("recommendations")
    if not isinstance(recommendations, list):
        return {}

    for recommendation in recommendations:
        if not isinstance(recommendation, Mapping):
            raise ValueError("recommendations entries must be objects")
        overlay_ops = recommendation.get("overlay_ops")
        if overlay_ops is None:
            continue
        if not isinstance(overlay_ops, list):
            raise ValueError("overlay_ops must be a list")
        for raw_op in overlay_ops:
            if not isinstance(raw_op, Mapping):
                raise ValueError("overlay_ops entries must be objects")
            raw_env_name = raw_op.get("env")
            if not isinstance(raw_env_name, str):
                raise ValueError("overlay_ops.env must be a string")
            env_name = raw_env_name.strip()
            if not env_name:
                continue
            raw_op_name = raw_op.get("op", "set")
            if not isinstance(raw_op_name, str):
                raise ValueError("overlay_ops.op must be a string")
            op = raw_op_name.strip().lower()
            if op == "set":
                raw_value = raw_op.get("value", "")
                if not isinstance(raw_value, str):
                    raise ValueError("overlay_ops.value must be a string")
                env_values[env_name] = raw_value
                continue
            if op != "multiply":
                raise ValueError(f"unsupported overlay op: {op}")

            precision = _integer(raw_op.get("precision"), field_name="overlay_ops.precision", default=4)
            factor = _numeric(raw_op.get("factor"), field_name="overlay_ops.factor", default=1.0)
            minimum = _numeric(raw_op.get("minimum"), field_name="overlay_ops.minimum", default=float("-inf"))
            maximum = _numeric(raw_op.get("maximum"), field_name="overlay_ops.maximum", default=float("inf"))
            base_value = _numeric(
                env_values.get(env_name, raw_op.get("default", 0.0)),
                field_name="overlay_ops.base_value",
            )
            proposed = max(minimum, min(maximum, base_value * factor))
            env_values[env_name] = _format_float(round(proposed, precision), precision)

    return {
        key: value
        for key, value in env_values.items()
        if baseline_env is None or baseline_snapshot.get(key) != value or key not in baseline_snapshot
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
    recommendations = recommendations_payload.get("recommendations", [])
    if not isinstance(recommendations, list):
        raise ValueError("recommendations must be a list")

    env_overrides = materialize_env_overrides(
        recommendations_payload,
        baseline_env=baseline_env,
    )
    applied_ids = _recommendation_ids(recommendations)

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

    baseline_bundle = _bundle_path(baseline_bundle, field_name="baseline_bundle")
    variant_bundle = _bundle_path(variant_bundle, field_name="variant_bundle")
    if baseline_bundle is None or variant_bundle is None:
        return payload

    comparison = compare_backtest_bundles_fn(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )
    if not isinstance(comparison, Mapping):
        raise ValueError("comparison result must be an object")
    promotion_gate = _object_section(comparison, "promotion_gate")
    decision_summary = _object_section(comparison, "decision_summary")
    payload["status"] = (
        _optional_section_str(promotion_gate, "decision", section_name="promotion_gate")
        or _optional_section_str(decision_summary, "decision", section_name="decision_summary")
        or "hold"
    )
    payload["decision"] = payload["status"]
    payload["baseline_bundle"] = str(baseline_bundle)
    payload["variant_bundle"] = str(variant_bundle)
    payload["promotion_gate"] = promotion_gate
    payload["decision_summary"] = decision_summary
    payload["summary"] = _optional_section_str(decision_summary, "summary", section_name="decision_summary") or payload["summary"]
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
