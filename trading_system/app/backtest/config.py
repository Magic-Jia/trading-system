from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .types import BacktestConfig, BacktestCosts, CapitalModelConfig, ForwardReturnWindow, SampleWindow, UniverseFilterConfig


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
    if experiment_kind == "full_market_baseline":
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


def load_backtest_config(path: str | Path) -> BacktestConfig:
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    dataset_root = _resolve_dataset_root(config_path, str(_require(raw, "dataset_root")))
    experiment_kind = str(_require(raw, "experiment_kind"))
    sample_windows = _load_sample_windows(list(_require(raw, "sample_windows")))
    costs = _load_costs(_require(raw, "costs"), experiment_kind=experiment_kind)

    return BacktestConfig(
        dataset_root=dataset_root,
        experiment_kind=experiment_kind,
        sample_windows=sample_windows,
        forward_return_windows=_load_forward_windows(raw.get("forward_return_windows")),
        costs=costs,
        baseline_name=str(_require(raw, "baseline_name")),
        variant_name=str(_require(raw, "variant_name")),
        universe=_load_universe(_require(raw, "universe")) if experiment_kind == "full_market_baseline" else None,
        capital=_load_capital(_require(raw, "capital")) if experiment_kind == "full_market_baseline" else None,
        metadata=dict(raw.get("metadata") or {}),
    )
