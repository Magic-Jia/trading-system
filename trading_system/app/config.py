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
class AppConfig:
    data_dir: Path = DATA_DIR
    state_file: Path = STATE_FILE
    risk: RiskConfig = RiskConfig()


DEFAULT_CONFIG = AppConfig()
