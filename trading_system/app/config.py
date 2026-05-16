from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlparse

from .execution.orders import EntryOrderPolicy
from .runtime_paths import DEFAULT_RUNTIME_ENV, RUNTIME_ENV_ENV, build_runtime_paths
from .signals.entry_profile import ENTRY_PROFILE_ENV, EntryProfile, resolve_entry_profile

BASE = Path(__file__).resolve().parents[1]
DATA_DIR = BASE / "data"
STATE_FILE = DATA_DIR / "runtime_state.json"
BASE_DIR_ENV = "TRADING_BASE_DIR"

ExecutionMode = Literal["paper", "dry-run", "live", "testnet"]
_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_SAFE_EVIDENCE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _env_float(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


def _env_int(name: str, default: str) -> int:
    return int(os.environ.get(name, default))


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_canonical_utc_timestamp(value: str) -> bool:
    if not _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC).isoformat().replace("+00:00", "Z") == value


def _parse_canonical_utc_timestamp(value: str, *, field_name: str) -> datetime:
    if not _is_canonical_utc_timestamp(value):
        raise ValueError(f"{field_name} must be a canonical UTC timestamp")
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)


def _require_safe_identifier(value: str | None, *, field_name: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{field_name} must be present")
    if value != value.strip():
        raise ValueError(f"{field_name} must be canonical")
    if _SAFE_EVIDENCE_IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a safe identifier")
    return value


def _normalize_string_list(
    raw: str | list[str] | tuple[str, ...] | set[str] | frozenset[str] | None,
    *,
    transform: Callable[[str], str] = str.lower,
) -> tuple[str, ...]:
    if raw is None:
        return ()
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    normalized: list[str] = []
    for item in items:
        value = transform(str(item).strip())
        if not value:
            continue
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def normalize_engine_names(raw: str | list[str] | tuple[str, ...] | set[str] | frozenset[str] | None) -> tuple[str, ...]:
    return _normalize_string_list(raw, transform=str.lower)


def normalize_setup_types(raw: str | list[str] | tuple[str, ...] | set[str] | frozenset[str] | None) -> tuple[str, ...]:
    return _normalize_string_list(raw, transform=str.upper)


def _env_engine_list(name: str) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None:
        return ()
    return normalize_engine_names(value)


def _env_setup_type_list(name: str) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None:
        return ()
    return normalize_setup_types(value)


def _env_csv(name: str) -> tuple[str, ...]:
    return _normalize_string_list(os.environ.get(name), transform=str.upper)


def _has_testnet_futures_endpoint(endpoint: str) -> bool:
    parsed = urlparse(endpoint)
    return parsed.scheme in {"http", "https"} and parsed.netloc == "testnet.binancefuture.com"


def _endpoint_class(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.netloc == "testnet.binancefuture.com":
        return "testnet"
    if parsed.netloc in {"fapi.binance.com", "api.binance.com"} or "binance.com" in parsed.netloc:
        return "live"
    return "unknown"


def _key_scope() -> str:
    has_testnet_key = bool(os.environ.get("BINANCE_TESTNET_API_KEY") or os.environ.get("BINANCE_TESTNET_API_SECRET"))
    has_live_key = bool(
        os.environ.get("BINANCE_API_KEY") or os.environ.get("BINANCE_APIKEY") or os.environ.get("BINANCE_API_SECRET") or os.environ.get("BINANCE_SECRET")
    )
    if has_testnet_key and has_live_key:
        return "mixed"
    if has_testnet_key:
        return "testnet"
    if has_live_key:
        return "live"
    return "none"


def _runtime_environment() -> str:
    return os.environ.get(RUNTIME_ENV_ENV, DEFAULT_RUNTIME_ENV).strip().lower()


def _validate_production_approval(
    gate: str | None = None,
    approval_id: str | None = None,
    approved_at: str | None = None,
) -> tuple[str | None, str | None]:
    gate = gate or os.environ.get("TRADING_PRODUCTION_GATE")
    approval_id = approval_id or os.environ.get("TRADING_PRODUCTION_APPROVAL_ID")
    approved_at = approved_at or os.environ.get("TRADING_PRODUCTION_APPROVAL_AT")
    if gate != "production-approved":
        raise ValueError("production environment requires canonical production gate evidence")
    approval_id = _require_safe_identifier(approval_id, field_name="production approval id")
    if approved_at is None:
        raise ValueError("production approval timestamp must be present")
    approved_dt = _parse_canonical_utc_timestamp(approved_at, field_name="production approval timestamp")
    today = date(2026, 5, 16)
    if approved_dt.date() > today:
        raise ValueError("production approval timestamp must not be in the future")
    if approved_dt.date() != today:
        raise ValueError("production approval timestamp must be current")
    return approval_id, approved_at


def _env_execution_mode(name: str, default: ExecutionMode = "paper") -> ExecutionMode:
    value = os.environ.get(name, default).strip().lower()
    if value not in {"paper", "dry-run", "live", "testnet"}:
        raise ValueError(f"{name} must be one of paper, dry-run, live, testnet")
    return value  # type: ignore[return-value]


def _env_entry_order_policy(name: str, default: EntryOrderPolicy = "maker_only") -> EntryOrderPolicy:
    value = os.environ.get(name, default).strip().lower().replace("-", "_")
    if value not in {"maker_only", "taker_market"}:
        raise ValueError(f"{name} must be one of maker_only, taker_market")
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
    minimum_cost_coverage_ratio: float = field(
        default_factory=lambda: _env_float("TRADING_MINIMUM_COST_COVERAGE_RATIO", "2.0")
    )
    estimated_roundtrip_cost_bps: float = field(
        default_factory=lambda: _env_float("TRADING_ESTIMATED_ROUNDTRIP_COST_BPS", "10.0")
    )


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
    confirm_r_multiple: float = field(default_factory=lambda: _env_float("TRADING_LIFECYCLE_CONFIRM_R_MULTIPLE", "0.40"))
    protect_r_multiple: float = field(default_factory=lambda: _env_float("TRADING_LIFECYCLE_PROTECT_R_MULTIPLE", "0.80"))
    exit_r_multiple: float = field(default_factory=lambda: _env_float("TRADING_LIFECYCLE_EXIT_R_MULTIPLE", "1.50"))
    max_holding_hours: int = field(default_factory=lambda: _env_int("TRADING_LIFECYCLE_MAX_HOLDING_HOURS", "6"))


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    mode: ExecutionMode = field(default_factory=lambda: _env_execution_mode("TRADING_EXECUTION_MODE", "paper"))
    allow_live_execution: bool = field(default_factory=lambda: _env_bool("TRADING_ALLOW_LIVE_EXECUTION", False))
    environment: str = field(default_factory=_runtime_environment)
    production_gate: str | None = field(default_factory=lambda: os.environ.get("TRADING_PRODUCTION_GATE") or None)
    production_approval_id: str | None = None
    production_approval_at: str | None = None
    feishu_notifications_enabled: bool = field(
        default_factory=lambda: _env_bool("TRADING_FEISHU_NOTIFICATIONS_ENABLED", True)
    )
    feishu_app_id: str | None = field(
        default_factory=lambda: os.environ.get("TRADING_FEISHU_APP_ID") or os.environ.get("FEISHU_APP_ID") or None
    )
    feishu_app_secret: str | None = field(
        default_factory=lambda: os.environ.get("TRADING_FEISHU_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET") or None
    )
    feishu_receive_id: str | None = field(default_factory=lambda: os.environ.get("TRADING_FEISHU_RECEIVE_ID") or None)
    feishu_receive_id_type: str = field(default_factory=lambda: os.environ.get("TRADING_FEISHU_RECEIVE_ID_TYPE") or "chat_id")
    feishu_domain: str = field(
        default_factory=lambda: os.environ.get("TRADING_FEISHU_DOMAIN") or os.environ.get("FEISHU_DOMAIN") or "feishu"
    )
    testnet_order_submission_enabled: bool = field(
        default_factory=lambda: _env_bool("TRADING_TESTNET_ORDER_SUBMISSION_ENABLED", False)
    )
    testnet_allowed_symbols: tuple[str, ...] = field(default_factory=lambda: _env_csv("TRADING_TESTNET_ALLOWED_SYMBOLS"))
    testnet_max_order_notional_usdt: float = field(
        default_factory=lambda: _env_float("TRADING_TESTNET_MAX_ORDER_NOTIONAL_USDT", "25")
    )
    testnet_max_open_positions: int = field(default_factory=lambda: _env_int("TRADING_TESTNET_MAX_OPEN_POSITIONS", "1"))
    entry_order_policy: EntryOrderPolicy = field(
        default_factory=lambda: _env_entry_order_policy("TRADING_ENTRY_ORDER_POLICY", "maker_only")
    )
    maker_entry_timeout_seconds: int = field(
        default_factory=lambda: _env_int("TRADING_MAKER_ENTRY_TIMEOUT_SECONDS", "15")
    )
    disabled_engines: tuple[str, ...] = field(default_factory=lambda: _env_engine_list("TRADING_DISABLED_ENGINES"))
    disabled_setup_types: tuple[str, ...] = field(default_factory=lambda: _env_setup_type_list("TRADING_DISABLED_SETUP_TYPES"))

    def __post_init__(self) -> None:
        endpoint = os.environ.get("BINANCE_FAPI_URL", "")
        endpoint_class = _endpoint_class(endpoint)
        key_scope = _key_scope()
        prod_like_permission = self.allow_live_execution or self.mode == "live" or endpoint_class == "live" or key_scope == "live"
        if self.environment in {"research", "paper"} and prod_like_permission:
            raise ValueError("prod-like permissions are not allowed in research or paper environments")
        if self.mode in {"live", "testnet"} and self.environment == DEFAULT_RUNTIME_ENV:
            raise ValueError("order-routing configs require TRADING_RUNTIME_ENV")
        if endpoint_class == "live" and key_scope == "testnet" or endpoint_class == "testnet" and key_scope == "live":
            raise ValueError("live endpoint and key permissions must not be mixed")
        if self.environment == "prod" or self.mode == "live" or self.allow_live_execution:
            approval_id, approval_at = _validate_production_approval(
                self.production_gate,
                self.production_approval_id,
                self.production_approval_at,
            )
            object.__setattr__(self, "production_gate", "production-approved")
            object.__setattr__(self, "production_approval_id", approval_id)
            object.__setattr__(self, "production_approval_at", approval_at)
        if self.mode != "testnet":
            return
        if not _env_bool("BINANCE_USE_TESTNET", False):
            raise ValueError("testnet mode requires BINANCE_USE_TESTNET=1")
        if not _has_testnet_futures_endpoint(endpoint):
            raise ValueError("testnet mode requires the Binance Futures testnet endpoint")
        if key_scope == "live":
            raise ValueError("testnet mode must use BINANCE_TESTNET_API_KEY/BINANCE_TESTNET_API_SECRET")
        has_key = bool(os.environ.get("BINANCE_TESTNET_API_KEY") or os.environ.get("BINANCE_API_KEY"))
        has_secret = bool(
            os.environ.get("BINANCE_TESTNET_API_SECRET")
            or os.environ.get("BINANCE_API_SECRET")
            or os.environ.get("BINANCE_SECRET")
        )
        if has_key != has_secret:
            raise ValueError("testnet credentials require both api key and secret")
        if not self.testnet_allowed_symbols:
            raise ValueError("testnet mode requires an allowed symbol list")
        if self.testnet_max_order_notional_usdt <= 0:
            raise ValueError("testnet max order notional must be positive")
        if self.testnet_max_open_positions <= 0:
            raise ValueError("testnet max open positions must be positive")
        if self.maker_entry_timeout_seconds <= 0:
            raise ValueError("maker entry timeout seconds must be positive")


@dataclass(frozen=True, slots=True)
class AppConfig:
    data_dir: Path = field(default_factory=_resolve_data_dir)
    state_file: Path = field(default_factory=_resolve_state_file)
    entry_profile: EntryProfile = field(default_factory=lambda: resolve_entry_profile(os.environ.get(ENTRY_PROFILE_ENV)))
    risk: RiskConfig = field(default_factory=RiskConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    allocator: AllocatorConfig = field(default_factory=AllocatorConfig)
    lifecycle: LifecycleConfig = field(default_factory=LifecycleConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)


def build_config() -> AppConfig:
    return AppConfig()


DEFAULT_CONFIG = build_config()
