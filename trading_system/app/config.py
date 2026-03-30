from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .runtime_paths import RUNTIME_ENV_ENV, build_runtime_paths

BASE = Path(__file__).resolve().parents[1]
DATA_DIR = BASE / "data"
STATE_FILE = DATA_DIR / "runtime_state.json"
BASE_DIR_ENV = "TRADING_BASE_DIR"

ExecutionMode = Literal["paper", "dry-run", "live"]


def _env_float(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


def _env_int(name: str, default: str) -> int:
    return int(os.environ.get(name, default))


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_execution_mode(name: str, default: ExecutionMode = "paper") -> ExecutionMode:
    value = os.environ.get(name, default).strip().lower()
    if value not in {"paper", "dry-run", "live"}:
        raise ValueError(f"{name} must be one of paper, dry-run, live")
    return value  # type: ignore[return-value]


def _resolve_base_dir() -> Path:
    env_value = os.environ.get(BASE_DIR_ENV)
    if env_value:
        return Path(env_value)
    return BASE


def _resolve_data_dir() -> Path:
    return _resolve_base_dir() / "data"


def runtime_path_defaults_enabled() -> bool:
    return bool(os.environ.get(BASE_DIR_ENV) or os.environ.get(RUNTIME_ENV_ENV))


def _resolve_state_file() -> Path:
    if not runtime_path_defaults_enabled():
        return STATE_FILE
    runtime_paths = build_runtime_paths(
        _env_execution_mode("TRADING_EXECUTION_MODE", "paper"),
        runtime_root=_resolve_data_dir() / "runtime",
    )
    return runtime_paths.state_file


@dataclass(frozen=True, slots=True)
class RiskConfig:
    default_risk_pct: float = field(default_factory=lambda: _env_float("TRADING_DEFAULT_RISK_PCT", "0.01"))
    max_notional_pct: float = field(default_factory=lambda: _env_float("TRADING_MAX_NOTIONAL_PCT", "0.12"))
    max_total_risk_pct: float = field(default_factory=lambda: _env_float("TRADING_MAX_TOTAL_RISK_PCT", "0.03"))
    max_symbol_risk_pct: float = field(default_factory=lambda: _env_float("TRADING_MAX_SYMBOL_RISK_PCT", "0.015"))
    max_net_exposure_pct: float = field(default_factory=lambda: _env_float("TRADING_MAX_NET_EXPOSURE_PCT", "0.85"))
    max_open_positions: int = field(default_factory=lambda: _env_int("TRADING_MAX_OPEN_POSITIONS", "8"))
    min_stop_distance_pct: float = field(default_factory=lambda: _env_float("TRADING_MIN_STOP_DISTANCE_PCT", "0.003"))
    max_stop_distance_pct: float = field(default_factory=lambda: _env_float("TRADING_MAX_STOP_DISTANCE_PCT", "0.08"))
    high_volatility_threshold_pct: float = field(default_factory=lambda: _env_float("TRADING_HIGH_VOL_THRESHOLD_PCT", "0.06"))
    high_vol_risk_multiplier: float = field(default_factory=lambda: _env_float("TRADING_HIGH_VOL_RISK_MULTIPLIER", "0.5"))
    cooldown_minutes: int = field(default_factory=lambda: _env_int("TRADING_COOLDOWN_MINUTES", "30"))


@dataclass(frozen=True, slots=True)
class RegimeConfig:
    risk_on_confidence_threshold: float = field(
        default_factory=lambda: _env_float("TRADING_REGIME_RISK_ON_CONFIDENCE_THRESHOLD", "0.65")
    )
    risk_off_confidence_threshold: float = field(
        default_factory=lambda: _env_float("TRADING_REGIME_RISK_OFF_CONFIDENCE_THRESHOLD", "0.70")
    )


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    min_liquidity_usdt_24h: float = field(
        default_factory=lambda: _env_float("TRADING_UNIVERSE_MIN_LIQUIDITY_USDT_24H", "200000000")
    )
    max_symbols: int = field(default_factory=lambda: _env_int("TRADING_UNIVERSE_MAX_SYMBOLS", "20"))


@dataclass(frozen=True, slots=True)
class AllocatorConfig:
    sector_cap_pct: float = field(default_factory=lambda: _env_float("TRADING_ALLOCATOR_SECTOR_CAP_PCT", "0.35"))
    trend_bucket_weight: float = field(default_factory=lambda: _env_float("TRADING_ALLOCATOR_TREND_BUCKET_WEIGHT", "0.70"))
    rotation_bucket_weight: float = field(
        default_factory=lambda: _env_float("TRADING_ALLOCATOR_ROTATION_BUCKET_WEIGHT", "0.30")
    )
    short_bucket_weight: float = field(default_factory=lambda: _env_float("TRADING_ALLOCATOR_SHORT_BUCKET_WEIGHT", "0.00"))


@dataclass(frozen=True, slots=True)
class LifecycleConfig:
    confirm_r_multiple: float = field(default_factory=lambda: _env_float("TRADING_LIFECYCLE_CONFIRM_R_MULTIPLE", "0.80"))
    protect_r_multiple: float = field(default_factory=lambda: _env_float("TRADING_LIFECYCLE_PROTECT_R_MULTIPLE", "1.20"))
    exit_r_multiple: float = field(default_factory=lambda: _env_float("TRADING_LIFECYCLE_EXIT_R_MULTIPLE", "2.00"))


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    mode: ExecutionMode = field(default_factory=lambda: _env_execution_mode("TRADING_EXECUTION_MODE", "paper"))
    allow_live_execution: bool = field(default_factory=lambda: _env_bool("TRADING_ALLOW_LIVE_EXECUTION", False))


@dataclass(frozen=True, slots=True)
class AppConfig:
    data_dir: Path = field(default_factory=_resolve_data_dir)
    state_file: Path = field(default_factory=_resolve_state_file)
    risk: RiskConfig = field(default_factory=RiskConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    allocator: AllocatorConfig = field(default_factory=AllocatorConfig)
    lifecycle: LifecycleConfig = field(default_factory=LifecycleConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)


def build_config() -> AppConfig:
    return AppConfig()


DEFAULT_CONFIG = build_config()
