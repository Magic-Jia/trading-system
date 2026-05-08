from __future__ import annotations

import dataclasses
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from .types import (
    BacktestConfig,
    BacktestCosts,
    CapitalModelConfig,
    ExitPolicyParams,
    ExperimentParams,
    ForwardReturnWindow,
    PromotionMetadata,
    SampleWindow,
    SetupRewriteParams,
    SetupRewriteRule,
    UniverseFilterConfig,
    WalkForwardConfig,
)


def _require(raw: dict[str, Any], field_name: str) -> Any:
    if field_name not in raw:
        raise ValueError(f"missing required field: {field_name}")
    return raw[field_name]


def _strict_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _finite_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite number")
    return number


def _positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _canonical_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a canonical string")
    return value


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
                name=_canonical_string(_require(item, "name"), field_name=f"sample_windows[{index}].name"),
                start=_parse_timestamp(
                    _canonical_string(_require(item, "start"), field_name=f"sample_windows[{index}].start"),
                    field_name=f"sample_windows[{index}].start",
                ),
                end=_parse_timestamp(
                    _canonical_string(_require(item, "end"), field_name=f"sample_windows[{index}].end"),
                    field_name=f"sample_windows[{index}].end",
                ),
                split=_canonical_string(item.get("split", "in_sample"), field_name=f"sample_windows[{index}].split"),
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
        windows.append(
            ForwardReturnWindow(
                name=_canonical_string(_require(item, "name"), field_name=f"forward_return_windows[{index}].name"),
                hours=_positive_int(_require(item, "hours"), field_name=f"forward_return_windows[{index}].hours"),
            )
        )
    return tuple(windows)


def _load_float_map(raw: Any, *, field_name: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be an object")
    parsed: dict[str, float] = {}
    for key, value in raw.items():
        parsed[
            _canonical_string(key, field_name=f"{field_name} key")
        ] = _finite_number(value, field_name=f"{field_name}.{key}")
    return parsed


def _load_universe(raw: Any) -> UniverseFilterConfig:
    if not isinstance(raw, dict):
        raise ValueError("universe must be an object")
    return UniverseFilterConfig(
        listing_age_days=_positive_int(_require(raw, "listing_age_days"), field_name="universe.listing_age_days"),
        min_quote_volume_usdt_24h=_load_float_map(
            _require(raw, "min_quote_volume_usdt_24h"),
            field_name="universe.min_quote_volume_usdt_24h",
        ),
        require_complete_funding=_strict_bool(
            raw.get("require_complete_funding", True),
            field_name="universe.require_complete_funding",
        ),
    )


def _load_capital(raw: Any) -> CapitalModelConfig:
    if not isinstance(raw, dict):
        raise ValueError("capital must be an object")
    return CapitalModelConfig(
        model=_canonical_string(_require(raw, "model"), field_name="capital.model"),
        initial_equity=_finite_number(_require(raw, "initial_equity"), field_name="capital.initial_equity"),
        risk_per_trade=_finite_number(_require(raw, "risk_per_trade"), field_name="capital.risk_per_trade"),
        max_open_risk=_finite_number(_require(raw, "max_open_risk"), field_name="capital.max_open_risk"),
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


def _optional_float(raw: dict[str, Any], field_name: str) -> float | None:
    return float(raw[field_name]) if field_name in raw else None


def _optional_int(raw: dict[str, Any], field_name: str) -> int | None:
    return int(raw[field_name]) if field_name in raw else None


def _require_non_negative(value: float | int | None, *, field_name: str) -> None:
    if value is not None and value < 0:
        raise ValueError(f"experiment_params.exit_policy.{field_name} must be non-negative")


def _load_exit_policy(raw: Any) -> ExitPolicyParams | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("experiment_params.exit_policy must be an object")

    name = str(_require(raw, "name")).strip()
    if name not in {"after_cost_breakeven_stop", "mfe_giveback_cut", "no_breakeven_time_stop"}:
        raise ValueError(f"unknown exit policy: {name}")

    after_cost_buffer_bps = float(raw.get("after_cost_buffer_bps", 0.0))
    activation_minute = int(raw.get("activation_minute", 0))
    giveback_fraction = _optional_float(raw, "giveback_fraction")
    giveback_min_bps = _optional_float(raw, "giveback_min_bps")
    no_breakeven_time_stop_minute = _optional_int(raw, "no_breakeven_time_stop_minute")

    _require_non_negative(after_cost_buffer_bps, field_name="after_cost_buffer_bps")
    _require_non_negative(activation_minute, field_name="activation_minute")
    _require_non_negative(giveback_fraction, field_name="giveback_fraction")
    _require_non_negative(giveback_min_bps, field_name="giveback_min_bps")
    _require_non_negative(no_breakeven_time_stop_minute, field_name="no_breakeven_time_stop_minute")
    if giveback_fraction is not None and giveback_fraction > 1:
        raise ValueError("experiment_params.exit_policy.giveback_fraction must be between 0 and 1")

    return ExitPolicyParams(
        name=name,
        after_cost_buffer_bps=after_cost_buffer_bps,
        activation_minute=activation_minute,
        giveback_fraction=giveback_fraction,
        giveback_min_bps=giveback_min_bps,
        no_breakeven_time_stop_minute=no_breakeven_time_stop_minute,
    )


def _load_setup_rewrite(raw: Any) -> SetupRewriteParams | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("experiment_params.setup_rewrite must be an object")
    unknown_fields = set(raw) - {"rules"}
    if unknown_fields:
        raise ValueError(f"experiment_params.setup_rewrite has unknown fields: {sorted(unknown_fields)}")
    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list):
        raise ValueError("experiment_params.setup_rewrite.rules must be a list")
    rules = tuple(_load_setup_rewrite_rule(item, index=index) for index, item in enumerate(rules_raw))
    if not rules:
        raise ValueError("experiment_params.setup_rewrite.rules must not be empty")
    return SetupRewriteParams(rules=rules)


def _load_setup_rewrite_rule(raw: Any, *, index: int) -> SetupRewriteRule:
    field_name = f"experiment_params.setup_rewrite.rules[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be an object")
    name = str(_require(raw, "name")).strip()
    if name == "require_min_score":
        unknown_fields = set(raw) - {"name", "min_score"}
        if unknown_fields:
            raise ValueError(f"{field_name} has unknown fields: {sorted(unknown_fields)}")
        min_score = float(_require(raw, "min_score"))
        if min_score < 0:
            raise ValueError(f"{field_name}.min_score must be non-negative")
        return SetupRewriteRule(name=name, min_score=min_score)
    if name == "exclude_setup_types":
        unknown_fields = set(raw) - {"name", "setup_types"}
        if unknown_fields:
            raise ValueError(f"{field_name} has unknown fields: {sorted(unknown_fields)}")
        setup_types = _load_setup_rewrite_setup_types(_require(raw, "setup_types"), field_name=f"{field_name}.setup_types")
        return SetupRewriteRule(name=name, setup_types=setup_types)
    if name == "require_setup_min_score":
        unknown_fields = set(raw) - {"name", "setup_types", "min_score"}
        if unknown_fields:
            raise ValueError(f"{field_name} has unknown fields: {sorted(unknown_fields)}")
        setup_types = _load_setup_rewrite_setup_types(
            _require(raw, "setup_types"),
            field_name=f"{field_name}.setup_types",
            require_non_empty=True,
        )
        min_score = float(_require(raw, "min_score"))
        if min_score < 0:
            raise ValueError(f"{field_name}.min_score must be non-negative")
        return SetupRewriteRule(name=name, setup_types=setup_types, min_score=min_score)
    if name == "require_setup_min_cost_coverage_ratio":
        unknown_fields = set(raw) - {"name", "setup_types", "min_cost_coverage_ratio"}
        if unknown_fields:
            raise ValueError(f"{field_name} has unknown fields: {sorted(unknown_fields)}")
        setup_types = _load_setup_rewrite_setup_types(
            _require(raw, "setup_types"),
            field_name=f"{field_name}.setup_types",
            require_non_empty=True,
        )
        min_cost_coverage_ratio = float(_require(raw, "min_cost_coverage_ratio"))
        if min_cost_coverage_ratio < 0:
            raise ValueError(f"{field_name}.min_cost_coverage_ratio must be non-negative")
        return SetupRewriteRule(
            name=name,
            setup_types=setup_types,
            min_cost_coverage_ratio=min_cost_coverage_ratio,
        )
    if name == "require_setup_allowed_symbols":
        unknown_fields = set(raw) - {"name", "setup_types", "symbols"}
        if unknown_fields:
            raise ValueError(f"{field_name} has unknown fields: {sorted(unknown_fields)}")
        setup_types = _load_setup_rewrite_setup_types(
            _require(raw, "setup_types"),
            field_name=f"{field_name}.setup_types",
            require_non_empty=True,
        )
        symbols = _load_setup_rewrite_symbols(_require(raw, "symbols"), field_name=f"{field_name}.symbols")
        return SetupRewriteRule(name=name, setup_types=setup_types, symbols=symbols)
    if name == "require_after_cost_breakeven_evidence":
        unknown_fields = set(raw) - {"name"}
        if unknown_fields:
            raise ValueError(f"{field_name} has unknown fields: {sorted(unknown_fields)}")
        return SetupRewriteRule(name=name)
    raise ValueError(f"unknown setup rewrite rule: {name}")


def _load_setup_rewrite_setup_types(
    raw: Any,
    *,
    field_name: str,
    require_non_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"{field_name} must be a list")
    normalized: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} must contain only strings")
        setup_type = str(item).strip().upper()
        if not setup_type:
            continue
        if setup_type not in normalized:
            normalized.append(setup_type)
    if require_non_empty and not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return tuple(normalized)


def _load_setup_rewrite_symbols(raw: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"{field_name} must be a list")
    normalized: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} must contain only strings")
        symbol = item.strip().upper()
        if not symbol:
            continue
        if symbol not in normalized:
            normalized.append(symbol)
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return tuple(normalized)


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
        exit_policy=_load_exit_policy(raw.get("exit_policy")),
        setup_rewrite=_load_setup_rewrite(raw.get("setup_rewrite")),
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
