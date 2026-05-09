from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from ..backtest import cli as backtest_cli
from ..backtest.dataset import load_historical_dataset
from ..backtest.promotion import compare_backtest_bundles
from .promotion import build_promotion_decision, materialize_env_overrides

VALIDATION_DATASET_ROOT_ENV = "TRADING_OPTIMIZATION_VALIDATION_DATASET_ROOT"
DEFAULT_BASELINE_NAME = "paper_optimization_policy"
BASELINE_VARIANT_NAME = "paper_opt_baseline"
CANDIDATE_VARIANT_NAME = "paper_opt_candidate"
SUPPORTED_RUNTIME_ENV_KEYS = (
    "TRADING_MAX_TOTAL_RISK_PCT",
    "TRADING_ALLOCATOR_TREND_BUCKET_WEIGHT",
    "TRADING_ALLOCATOR_ROTATION_BUCKET_WEIGHT",
    "TRADING_ALLOCATOR_SHORT_BUCKET_WEIGHT",
    "TRADING_DISABLED_ENGINES",
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _optional_manifest_str(manifest: Mapping[str, Any], field_name: str) -> str:
    value = manifest.get(field_name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"import_manifest.{field_name} must be a string")
    return value.strip()


def _baseline_env_snapshot(baseline_env: Mapping[str, str] | None = None) -> dict[str, str]:
    if baseline_env is not None:
        snapshot: dict[str, str] = {}
        for key, value in baseline_env.items():
            if key not in SUPPORTED_RUNTIME_ENV_KEYS:
                continue
            if not isinstance(value, str):
                raise ValueError(f"baseline_env.{key} must be a string")
            snapshot[key] = value
        return snapshot
    snapshot: dict[str, str] = {}
    for key in SUPPORTED_RUNTIME_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None and value.strip():
            snapshot[key] = value.strip()
    return snapshot


def _repo_root(repo_root: Path | None = None) -> Path:
    return repo_root if repo_root is not None else Path(__file__).resolve().parents[2]


def resolve_validation_dataset_root(
    *,
    dataset_root: str | Path | None = None,
    repo_root: Path | None = None,
) -> Path | None:
    if dataset_root is not None:
        if not isinstance(dataset_root, (str, Path)):
            raise ValueError("dataset_root must be a path string")
        return Path(dataset_root)

    explicit_env = os.environ.get(VALIDATION_DATASET_ROOT_ENV)
    if explicit_env:
        return Path(explicit_env)

    imported_roots_dir = _repo_root(repo_root) / "data" / "imported-datasets"
    if not imported_roots_dir.exists():
        return None

    candidates: list[tuple[str, str, Path]] = []
    for manifest_path in sorted(imported_roots_dir.glob("*/import_manifest.json")):
        manifest = _read_json(manifest_path)
        dataset_path = manifest_path.parent
        end_timestamp = _optional_manifest_str(manifest, "end_timestamp")
        start_timestamp = _optional_manifest_str(manifest, "start_timestamp")
        candidates.append((end_timestamp, start_timestamp, dataset_path))

    if not candidates:
        return None

    return sorted(candidates, key=lambda item: (item[0], item[1], item[2].name))[-1][2]


def _dataset_time_bounds(dataset_root: Path) -> tuple[str, str]:
    manifest_path = dataset_root / "import_manifest.json"
    manifest = _read_json(manifest_path) if manifest_path.exists() else {}
    start_timestamp = _optional_manifest_str(manifest, "start_timestamp")
    end_timestamp = _optional_manifest_str(manifest, "end_timestamp")
    if start_timestamp and end_timestamp:
        return start_timestamp, end_timestamp

    rows = load_historical_dataset(dataset_root)
    if not rows:
        raise ValueError(f"validation dataset root has no snapshots: {dataset_root}")
    return rows[0].timestamp.isoformat(), rows[-1].timestamp.isoformat()


def _active_recommendation_ids(recommendations_payload: Mapping[str, Any]) -> list[str]:
    recommendations = recommendations_payload.get("recommendations", [])
    if not isinstance(recommendations, list):
        raise ValueError("recommendations must be a list")
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


def _validation_metadata(
    *,
    recommendation_ids: list[str],
    runtime_env_overrides: Mapping[str, str],
    recorded_at_bj: str | None,
) -> dict[str, Any]:
    if not isinstance(runtime_env_overrides, Mapping):
        raise ValueError("runtime_env_overrides must be an object")
    env_snapshot: dict[str, str] = {}
    for key, value in runtime_env_overrides.items():
        if not isinstance(key, str):
            raise ValueError("runtime_env_overrides keys must be strings")
        if not isinstance(value, str):
            raise ValueError(f"runtime_env_overrides.{key} must be a string")
        env_snapshot[key] = value
    if recorded_at_bj is not None and not isinstance(recorded_at_bj, str):
        raise ValueError("recorded_at_bj must be a string")
    return {
        "generated_by": "paper_optimization.validation",
        "recommendation_ids": recommendation_ids,
        "runtime_env_overrides": env_snapshot,
        "runtime_observability": {
            "runtime_fields": [
                "optimization_summary",
                "promotion_decision",
                "latest_candidates",
                "latest_allocations",
            ]
        },
        "rollback_plan": {
            "rollback_target": BASELINE_VARIANT_NAME,
            "rollback_trigger": "validation_regresses_against_baseline",
            "observation_window": "14d",
        },
        "validation_context": {
            "source": "paper_optimization_loop",
            "recorded_at_bj": recorded_at_bj,
        },
    }


def _validation_config_payload(
    *,
    dataset_root: Path,
    variant_name: str,
    recommendation_ids: list[str],
    runtime_env_overrides: Mapping[str, str],
    recorded_at_bj: str | None,
) -> dict[str, Any]:
    start_timestamp, end_timestamp = _dataset_time_bounds(dataset_root)
    return {
        "dataset_root": str(dataset_root),
        "experiment_kind": "walk_forward_validation",
        "sample_windows": [
            {
                "name": "full_history",
                "start": start_timestamp,
                "end": end_timestamp,
                "split": "in_sample",
            }
        ],
        "forward_return_windows": [],
        "costs": {
            "fee_bps": 4.0,
            "slippage_bps": 2.0,
            "funding_bps_per_day": 1.0,
        },
        "baseline_name": DEFAULT_BASELINE_NAME,
        "variant_name": variant_name,
        "universe": {
            "listing_age_days": 30,
            "min_quote_volume_usdt_24h": {
                "spot": 1_000_000.0,
                "futures": 1_000_000.0,
            },
            "require_complete_funding": True,
        },
        "capital": {
            "model": "shared_pool",
            "initial_equity": 100_000.0,
            "risk_per_trade": 0.02,
            "max_open_risk": 0.03,
        },
        "experiment_params": {
            "evaluation_window": "3d",
            "walk_forward": {
                "in_sample_size": 3,
                "out_of_sample_size": 1,
                "step_size": 1,
            },
        },
        "metadata": _validation_metadata(
            recommendation_ids=recommendation_ids,
            runtime_env_overrides=runtime_env_overrides,
            recorded_at_bj=recorded_at_bj,
        ),
    }


def _run_validation_bundle(*, config_path: Path, bundles_dir: Path) -> Path:
    exit_code = backtest_cli.main(
        [
            "run",
            "--config",
            str(config_path),
            "--output-dir",
            str(bundles_dir),
        ]
    )
    if exit_code != 0:
        raise RuntimeError(f"backtest validation bundle generation failed for {config_path}")
    config_payload = _read_json(config_path)
    return bundles_dir / (
        f"walk_forward_validation__{config_payload['baseline_name']}__{config_payload['variant_name']}"
    )


def _write_blocked_promotion_decision(
    *,
    recommendations_payload: Mapping[str, Any],
    promotion_decision_path: Path,
    baseline_env: Mapping[str, str],
    recorded_at_bj: str | None,
    summary: str,
) -> dict[str, Any]:
    payload = build_promotion_decision(
        recommendations_payload=recommendations_payload,
        baseline_env=baseline_env,
        recorded_at_bj=recorded_at_bj,
    )
    payload["status"] = "blocked"
    payload["decision"] = "awaiting_backtest"
    payload["summary"] = summary
    _write_json(promotion_decision_path, payload)
    return payload


def run_paper_optimization_validation(
    *,
    recommendations_path: Path,
    promotion_decision_path: Path,
    optimization_dir: Path,
    repo_root: Path | None = None,
    dataset_root: str | Path | None = None,
    baseline_env: Mapping[str, str] | None = None,
    recorded_at_bj: str | None = None,
) -> dict[str, Any]:
    recommendations_payload = _read_json(recommendations_path)
    baseline_env_snapshot = _baseline_env_snapshot(baseline_env)
    active_recommendation_ids = _active_recommendation_ids(recommendations_payload)

    if not active_recommendation_ids:
        payload = build_promotion_decision(
            recommendations_payload=recommendations_payload,
            baseline_env=baseline_env_snapshot,
            recorded_at_bj=recorded_at_bj,
        )
        _write_json(promotion_decision_path, payload)
        return payload

    resolved_dataset_root = resolve_validation_dataset_root(
        dataset_root=dataset_root,
        repo_root=repo_root,
    )
    if resolved_dataset_root is None or not resolved_dataset_root.exists():
        return _write_blocked_promotion_decision(
            recommendations_payload=recommendations_payload,
            promotion_decision_path=promotion_decision_path,
            baseline_env=baseline_env_snapshot,
            recorded_at_bj=recorded_at_bj,
            summary="validation dataset root unavailable; automatic validation did not run",
        )

    validation_dir = optimization_dir / "validation"
    configs_dir = validation_dir / "configs"
    bundles_dir = validation_dir / "bundles"
    comparison_dir = validation_dir / "comparison"

    variant_env_overrides = materialize_env_overrides(
        recommendations_payload,
        baseline_env=baseline_env_snapshot,
    )

    baseline_config_path = configs_dir / "baseline_config.json"
    variant_config_path = configs_dir / "variant_config.json"
    _write_json(
        baseline_config_path,
        _validation_config_payload(
            dataset_root=resolved_dataset_root,
            variant_name=BASELINE_VARIANT_NAME,
            recommendation_ids=active_recommendation_ids,
            runtime_env_overrides={},
            recorded_at_bj=recorded_at_bj,
        ),
    )
    _write_json(
        variant_config_path,
        _validation_config_payload(
            dataset_root=resolved_dataset_root,
            variant_name=CANDIDATE_VARIANT_NAME,
            recommendation_ids=active_recommendation_ids,
            runtime_env_overrides=variant_env_overrides,
            recorded_at_bj=recorded_at_bj,
        ),
    )

    try:
        baseline_bundle = _run_validation_bundle(config_path=baseline_config_path, bundles_dir=bundles_dir)
        variant_bundle = _run_validation_bundle(config_path=variant_config_path, bundles_dir=bundles_dir)
        comparison = compare_backtest_bundles(
            baseline_bundle=baseline_bundle,
            variant_bundle=variant_bundle,
        )
    except Exception as exc:
        return _write_blocked_promotion_decision(
            recommendations_payload=recommendations_payload,
            promotion_decision_path=promotion_decision_path,
            baseline_env=baseline_env_snapshot,
            recorded_at_bj=recorded_at_bj,
            summary=f"automatic validation failed before promotion compare: {exc}",
        )

    _write_json(comparison_dir / "promotion_gate.json", comparison["promotion_gate"])
    _write_json(comparison_dir / "decision_summary.json", comparison["decision_summary"])

    payload = build_promotion_decision(
        recommendations_payload=recommendations_payload,
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
        baseline_env=baseline_env_snapshot,
        compare_backtest_bundles_fn=lambda **_kwargs: comparison,
        recorded_at_bj=recorded_at_bj,
    )
    payload["validation_dataset_root"] = str(resolved_dataset_root)
    payload["validation_artifacts"] = {
        "baseline_config": str(baseline_config_path),
        "variant_config": str(variant_config_path),
        "promotion_gate": str(comparison_dir / "promotion_gate.json"),
        "decision_summary": str(comparison_dir / "decision_summary.json"),
    }
    _write_json(promotion_decision_path, payload)
    return payload


__all__ = [
    "CANDIDATE_VARIANT_NAME",
    "BASELINE_VARIANT_NAME",
    "DEFAULT_BASELINE_NAME",
    "VALIDATION_DATASET_ROOT_ENV",
    "resolve_validation_dataset_root",
    "run_paper_optimization_validation",
]
