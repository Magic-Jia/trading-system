from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DATA_DIR = BASE / "data"
STATE_FILE = DATA_DIR / "runtime_state.json"


@dataclass(frozen=True, slots=True)
class RiskConfig:
    default_risk_pct: float = float(os.environ.get("TRADING_DEFAULT_RISK_PCT", "0.01"))
    max_notional_pct: float = float(os.environ.get("TRADING_MAX_NOTIONAL_PCT", "0.12"))
    max_total_risk_pct: float = float(os.environ.get("TRADING_MAX_TOTAL_RISK_PCT", "0.03"))
    max_symbol_risk_pct: float = float(os.environ.get("TRADING_MAX_SYMBOL_RISK_PCT", "0.015"))
    max_open_positions: int = int(os.environ.get("TRADING_MAX_OPEN_POSITIONS", "8"))
    min_stop_distance_pct: float = float(os.environ.get("TRADING_MIN_STOP_DISTANCE_PCT", "0.003"))
    max_stop_distance_pct: float = float(os.environ.get("TRADING_MAX_STOP_DISTANCE_PCT", "0.08"))
    high_volatility_threshold_pct: float = float(os.environ.get("TRADING_HIGH_VOL_THRESHOLD_PCT", "0.06"))
    high_vol_risk_multiplier: float = float(os.environ.get("TRADING_HIGH_VOL_RISK_MULTIPLIER", "0.5"))
    cooldown_minutes: int = int(os.environ.get("TRADING_COOLDOWN_MINUTES", "30"))


@dataclass(frozen=True, slots=True)
class RegimeConfig:
    risk_on_confidence_threshold: float = float(
        os.environ.get("TRADING_REGIME_RISK_ON_CONFIDENCE_THRESHOLD", "0.65")
    )
    risk_off_confidence_threshold: float = float(
        os.environ.get("TRADING_REGIME_RISK_OFF_CONFIDENCE_THRESHOLD", "0.70")
    )


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    min_liquidity_usdt_24h: float = float(os.environ.get("TRADING_UNIVERSE_MIN_LIQUIDITY_USDT_24H", "500000000"))
    max_symbols: int = int(os.environ.get("TRADING_UNIVERSE_MAX_SYMBOLS", "20"))


@dataclass(frozen=True, slots=True)
class AllocatorConfig:
    sector_cap_pct: float = float(os.environ.get("TRADING_ALLOCATOR_SECTOR_CAP_PCT", "0.35"))
    trend_bucket_weight: float = float(os.environ.get("TRADING_ALLOCATOR_TREND_BUCKET_WEIGHT", "0.70"))
    rotation_bucket_weight: float = float(os.environ.get("TRADING_ALLOCATOR_ROTATION_BUCKET_WEIGHT", "0.30"))
    short_bucket_weight: float = float(os.environ.get("TRADING_ALLOCATOR_SHORT_BUCKET_WEIGHT", "0.00"))


@dataclass(frozen=True, slots=True)
class LifecycleConfig:
    confirm_r_multiple: float = float(os.environ.get("TRADING_LIFECYCLE_CONFIRM_R_MULTIPLE", "0.80"))
    protect_r_multiple: float = float(os.environ.get("TRADING_LIFECYCLE_PROTECT_R_MULTIPLE", "1.20"))
    exit_r_multiple: float = float(os.environ.get("TRADING_LIFECYCLE_EXIT_R_MULTIPLE", "2.00"))


@dataclass(frozen=True, slots=True)
class AppConfig:
    data_dir: Path = DATA_DIR
    state_file: Path = STATE_FILE
    risk: RiskConfig = RiskConfig()
    regime: RegimeConfig = RegimeConfig()
    universe: UniverseConfig = UniverseConfig()
    allocator: AllocatorConfig = AllocatorConfig()
    lifecycle: LifecycleConfig = LifecycleConfig()


DEFAULT_CONFIG = AppConfig()
