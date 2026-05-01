from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .types import (
    BacktestConfig,
    BacktestCosts,
    CapitalModelConfig,
    ExperimentParams,
    ForwardReturnWindow,
    PromotionMetadata,
    SampleWindow,
    UniverseFilterConfig,
    WalkForwardConfig,
)


def _require(raw: dict[str, Any], field_name: str) -> Any:
    if field_name not in raw:
        raise ValueError(f"missing required field: {field_name}")
    return raw[field_name]


def _parse_timestamp(value: str, *, field_name: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid timestamp for {field_name}: {value}") from exc


def _resolve_dataset_root(config_path: Path, raw_root: str) -> Path:
    root = Path(raw_root)
    if not root.is_absolute():
        root = (config_path.parent / root).resolve()
    return root


def _resolve_optional_path(config_path: Path, raw_path: str | None) -> str | None:
    if raw_path is None:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = config_path.parent / path
    return str(path)


def _load_sample_windows(raw: list[dict[str, Any]]) -> tuple[SampleWindow, ...]:
    windows: list[SampleWindow] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"sample_windows[{index}] must be an object")
        windows.append(
            SampleWindow(
                name=str(_require(item, "name")),
                start=_parse_timestamp(str(_require(item, "start")), field_name=f"sample_windows[{index}].start"),
                end=_parse_timestamp(str(_require(item, "end")), field_name=f"sample_windows[{index}].end"),
                split=str(item.get("split", "in_sample")),
            )
        )
    return tuple(windows)


def _load_forward_windows(raw: Any) -> tuple[ForwardReturnWindow, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("forward_return_windows must be a list")
    windows: list[ForwardReturnWindow] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"forward_return_windows[{index}] must be an object")
        windows.append(ForwardReturnWindow(name=str(_require(item, "name")), hours=int(_require(item, "hours"))))
    return tuple(windows)


def _load_float_map(raw: Any, *, field_name: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be an object")
    parsed: dict[str, float] = {}
    for key, value in raw.items():
        parsed[str(key)] = float(value)
    return parsed


def _load_universe(raw: Any) -> UniverseFilterConfig:
    if not isinstance(raw, dict):
        raise ValueError("universe must be an object")
    return UniverseFilterConfig(
        listing_age_days=int(_require(raw, "listing_age_days")),
        min_quote_volume_usdt_24h=_load_float_map(
            _require(raw, "min_quote_volume_usdt_24h"),
            field_name="universe.min_quote_volume_usdt_24h",
        ),
        require_complete_funding=bool(raw.get("require_complete_funding", True)),
    )


def _load_capital(raw: Any) -> CapitalModelConfig:
    if not isinstance(raw, dict):
        raise ValueError("capital must be an object")
    return CapitalModelConfig(
        model=str(_require(raw, "model")),
        initial_equity=float(_require(raw, "initial_equity")),
        risk_per_trade=float(_require(raw, "risk_per_trade")),
        max_open_risk=float(_require(raw, "max_open_risk")),
    )


def _load_costs(raw: Any, *, experiment_kind: str) -> BacktestCosts:
    if not isinstance(raw, dict):
        raise ValueError("costs must be an object")
    if experiment_kind == "full_market_baseline" or (
        experiment_kind == "walk_forward_validation" and (isinstance(raw.get("fee_bps"), dict) or "slippage_tiers" in raw or "funding_mode" in raw)
    ):
        return BacktestCosts(
            fee_bps_by_market=_load_float_map(_require(raw, "fee_bps"), field_name="costs.fee_bps"),
            slippage_bps_by_tier=_load_float_map(_require(raw, "slippage_tiers"), field_name="costs.slippage_tiers"),
            funding_mode=str(_require(raw, "funding_mode")),
        )
    return BacktestCosts(
        fee_bps=float(_require(raw, "fee_bps")),
        slippage_bps=float(_require(raw, "slippage_bps")),
        funding_bps_per_day=float(raw.get("funding_bps_per_day", 0.0)),
    )


def _load_walk_forward(raw: Any) -> WalkForwardConfig:
    if not isinstance(raw, dict):
        raise ValueError("experiment_params.walk_forward must be an object")
    return WalkForwardConfig(
        in_sample_size=int(_require(raw, "in_sample_size")),
        out_of_sample_size=int(_require(raw, "out_of_sample_size")),
        step_size=int(raw["step_size"]) if "step_size" in raw else None,
    )


def _load_disabled_engines(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("experiment_params.disabled_engines must be a list")
    normalized: list[str] = []
    for item in raw:
        engine = str(item).strip().lower()
        if not engine:
            continue
        if engine not in normalized:
            normalized.append(engine)
    return tuple(normalized)


def _load_allowed_short_setup_types(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("experiment_params.allowed_short_setup_types must be a list")
    normalized: list[str] = []
    for item in raw:
        setup_type = str(item).strip().upper()
        if not setup_type:
            continue
        if setup_type not in normalized:
            normalized.append(setup_type)
    return tuple(normalized)


def _load_quarantined_short_setup_types(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("experiment_params.quarantined_short_setup_types must be a list")
    normalized: list[str] = []
    for item in raw:
        setup_type = str(item).strip().upper()
        if not setup_type:
            continue
        if setup_type not in normalized:
            normalized.append(setup_type)
    return tuple(normalized)


def _load_upper_unique_tuple(raw: Any, *, field_name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"experiment_params.{field_name} must be a list")
    normalized: list[str] = []
    for item in raw:
        value = str(item).strip().upper()
        if not value:
            continue
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _load_experiment_params(raw: Any, *, experiment_kind: str) -> ExperimentParams | None:
    if raw is None:
        if experiment_kind in {
            "rotation_suppression",
            "allocator_friction",
            "engine_filter_ablation",
            "long_gate_telemetry",
            "walk_forward_validation",
            "public_strategy_factors",
            "llm_trend_breakout",
        }:
            raise ValueError(f"experiment_params are required for {experiment_kind}")
        return None
    if not isinstance(raw, dict):
        raise ValueError("experiment_params must be an object")

    params = ExperimentParams(
        evaluation_window=str(raw["evaluation_window"]) if "evaluation_window" in raw else None,
        soft_score_floor=float(raw["soft_score_floor"]) if "soft_score_floor" in raw else None,
        walk_forward=_load_walk_forward(raw["walk_forward"]) if "walk_forward" in raw else None,
        public_strategy_families=tuple(str(item) for item in raw.get("public_strategy_families", ())),
        minimum_effectiveness_sample_count=int(raw.get("minimum_effectiveness_sample_count", 30)),
        disabled_engines=_load_disabled_engines(raw.get("disabled_engines")),
        allowed_short_setup_types=_load_allowed_short_setup_types(raw.get("allowed_short_setup_types")),
        quarantined_setup_types=_load_upper_unique_tuple(raw.get("quarantined_setup_types"), field_name="quarantined_setup_types"),
        quarantined_short_setup_types=_load_quarantined_short_setup_types(raw.get("quarantined_short_setup_types")),
        entry_profile=str(raw["entry_profile"]).strip() if raw.get("entry_profile") is not None else None,
        llm_label_path=str(raw["llm_label_path"]).strip() if raw.get("llm_label_path") is not None else None,
        require_llm_label=bool(raw.get("require_llm_label", True)),
        symbols=_load_upper_unique_tuple(raw.get("symbols"), field_name="symbols"),
        minimum_final_score=float(raw.get("minimum_final_score", 0.75)),
        minimum_label_confidence=float(raw.get("minimum_label_confidence", 0.5)),
        reject_high_fomo=bool(raw.get("reject_high_fomo", False)),
        allowed_setup_types=_load_upper_unique_tuple(raw.get("allowed_setup_types"), field_name="allowed_setup_types"),
        minimum_cost_coverage_ratio=float(raw.get("minimum_cost_coverage_ratio", 0.0)),
    )

    if experiment_kind == "rotation_suppression":
        if params.evaluation_window is None:
            raise ValueError("experiment_params.evaluation_window is required for rotation_suppression")
        if params.soft_score_floor is None:
            raise ValueError("experiment_params.soft_score_floor is required for rotation_suppression")
    elif experiment_kind in {"allocator_friction", "engine_filter_ablation", "long_gate_telemetry"}:
        if params.evaluation_window is None:
            raise ValueError(f"experiment_params.evaluation_window is required for {experiment_kind}")
    elif experiment_kind == "walk_forward_validation":
        if params.evaluation_window is None:
            raise ValueError("experiment_params.evaluation_window is required for walk_forward_validation")
        if params.walk_forward is None:
            raise ValueError("experiment_params.walk_forward is required for walk_forward_validation")
    elif experiment_kind == "public_strategy_factors":
        if params.evaluation_window is None:
            raise ValueError("experiment_params.evaluation_window is required for public_strategy_factors")
        if not params.public_strategy_families:
            raise ValueError("experiment_params.public_strategy_families is required for public_strategy_factors")
    elif experiment_kind == "llm_trend_breakout":
        if params.evaluation_window is None:
            raise ValueError("experiment_params.evaluation_window is required for llm_trend_breakout")
        if params.llm_label_path is None:
            raise ValueError("experiment_params.llm_label_path is required for llm_trend_breakout")

    return params



def _load_promotion_metadata(raw: Any) -> PromotionMetadata | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("promotion_metadata must be an object")
    runtime_fields_raw = raw.get("runtime_fields", [])
    if not isinstance(runtime_fields_raw, list):
        raise ValueError("promotion_metadata.runtime_fields must be a list")
    runtime_fields = tuple(str(item) for item in runtime_fields_raw)
    return PromotionMetadata(
        runtime_fields=runtime_fields,
        rollback_target=str(raw["rollback_target"]) if raw.get("rollback_target") is not None else None,
        rollback_trigger=str(raw["rollback_trigger"]) if raw.get("rollback_trigger") is not None else None,
        observation_window=str(raw["observation_window"]) if raw.get("observation_window") is not None else None,
    )



def load_backtest_config(path: str | Path) -> BacktestConfig:
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    dataset_root = _resolve_dataset_root(config_path, str(_require(raw, "dataset_root")))
    experiment_kind = str(_require(raw, "experiment_kind"))
    sample_windows = _load_sample_windows(list(_require(raw, "sample_windows")))
    costs = _load_costs(_require(raw, "costs"), experiment_kind=experiment_kind)

    experiment_params = _load_experiment_params(raw.get("experiment_params"), experiment_kind=experiment_kind)
    if experiment_kind == "llm_trend_breakout" and experiment_params is not None:
        experiment_params = dataclasses.replace(
            experiment_params,
            llm_label_path=_resolve_optional_path(config_path, experiment_params.llm_label_path),
        )

    return BacktestConfig(
        dataset_root=dataset_root,
        experiment_kind=experiment_kind,
        sample_windows=sample_windows,
        forward_return_windows=_load_forward_windows(raw.get("forward_return_windows")),
        costs=costs,
        baseline_name=str(_require(raw, "baseline_name")),
        variant_name=str(_require(raw, "variant_name")),
        universe=_load_universe(raw["universe"]) if raw.get("universe") is not None else None,
        capital=_load_capital(raw["capital"]) if raw.get("capital") is not None else None,
        experiment_params=experiment_params,
        promotion_metadata=_load_promotion_metadata(raw.get("promotion_metadata")),
        metadata=dict(raw.get("metadata") or {}),
    )
