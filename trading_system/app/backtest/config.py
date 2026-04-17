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


def load_backtest_config(path: str | Path) -> BacktestConfig:
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    dataset_root = _resolve_dataset_root(config_path, str(_require(raw, "dataset_root")))
    experiment_kind = str(_require(raw, "experiment_kind"))
    sample_windows = _load_sample_windows(list(_require(raw, "sample_windows")))
    raw_costs = _require(raw, "costs")
    if not isinstance(raw_costs, dict):
        raise ValueError("costs must be an object")

    markets: tuple[str, ...] = ()
    universe: UniverseFilterConfig | None = None
    capital: CapitalModelConfig | None = None

    if experiment_kind == "full_market_baseline":
        raw_markets = _require(raw, "markets")
        if not isinstance(raw_markets, list):
            raise ValueError("markets must be a list")
        markets = tuple(str(item) for item in raw_markets)

        raw_universe = _require(raw, "universe")
        if not isinstance(raw_universe, dict):
            raise ValueError("universe must be an object")
        universe = UniverseFilterConfig(
            listing_age_days=int(_require(raw_universe, "listing_age_days")),
            min_quote_volume_usdt_24h={
                str(key): float(value) for key, value in dict(_require(raw_universe, "min_quote_volume_usdt_24h")).items()
            },
            require_complete_funding=bool(raw_universe.get("require_complete_funding", True)),
        )

        raw_capital = _require(raw, "capital")
        if not isinstance(raw_capital, dict):
            raise ValueError("capital must be an object")
        capital = CapitalModelConfig(
            model=str(_require(raw_capital, "model")),
            initial_equity=float(_require(raw_capital, "initial_equity")),
            risk_per_trade=float(_require(raw_capital, "risk_per_trade")),
            max_open_risk=float(_require(raw_capital, "max_open_risk")),
        )

        costs = BacktestCosts(
            fee_bps_by_market={str(key): float(value) for key, value in dict(_require(raw_costs, "fee_bps")).items()},
            slippage_bps_by_tier={
                str(key): float(value) for key, value in dict(_require(raw_costs, "slippage_tiers")).items()
            },
            funding_mode=str(_require(raw_costs, "funding_mode")),
        )
    else:
        costs = BacktestCosts(
            fee_bps=float(_require(raw_costs, "fee_bps")),
            slippage_bps=float(_require(raw_costs, "slippage_bps")),
            funding_bps_per_day=float(raw_costs.get("funding_bps_per_day", 0.0)),
        )

    return BacktestConfig(
        dataset_root=dataset_root,
        experiment_kind=experiment_kind,
        sample_windows=sample_windows,
        forward_return_windows=_load_forward_windows(raw.get("forward_return_windows")),
        costs=costs,
        baseline_name=str(_require(raw, "baseline_name")),
        variant_name=str(_require(raw, "variant_name")),
        markets=markets,
        universe=universe,
        capital=capital,
        metadata=dict(raw.get("metadata") or {}),
    )
