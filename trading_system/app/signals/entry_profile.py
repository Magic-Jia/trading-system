from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

ENTRY_PROFILE_ENV = "TRADING_ENTRY_PROFILE"


@dataclass(frozen=True, slots=True)
class EntryProfile:
    name: str
    trend_daily_floor: float
    trend_h4_floor: float
    trend_h1_floor: float
    rotation_daily_floor: float
    rotation_h4_floor: float
    rotation_h1_floor: float
    trend_m30_floor: float = 0.0
    trend_m15_floor: float = 0.0
    rotation_m30_floor: float = 0.0
    rotation_m15_floor: float = 0.0
    target_trades_per_day_min: int | None = None
    target_trades_per_day_max: int | None = None
    objective_label: str = "signal_quality_target"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


CONSERVATIVE_ENTRY_PROFILE = EntryProfile(
    name="conservative",
    trend_daily_floor=0.03,
    trend_h4_floor=0.01,
    trend_h1_floor=0.003,
    rotation_daily_floor=0.03,
    rotation_h4_floor=0.01,
    rotation_h1_floor=0.003,
)

ACTIVE_PAPER_ENTRY_PROFILE = EntryProfile(
    name="active_paper",
    trend_daily_floor=0.01,
    trend_h4_floor=0.003,
    trend_h1_floor=0.001,
    rotation_daily_floor=0.01,
    rotation_h4_floor=0.003,
    rotation_h1_floor=0.001,
)

SHORT_TERM_ENTRY_PROFILE = EntryProfile(
    name="short_term",
    trend_daily_floor=0.0,
    trend_h4_floor=0.002,
    trend_h1_floor=0.0008,
    rotation_daily_floor=0.0,
    rotation_h4_floor=0.002,
    rotation_h1_floor=0.0008,
    trend_m30_floor=0.0005,
    trend_m15_floor=0.0005,
    rotation_m30_floor=0.0005,
    rotation_m15_floor=0.0005,
)

INTRADAY_MULTI_ENTRY_PROFILE = EntryProfile(
    name="intraday_multi",
    trend_daily_floor=0.0,
    trend_h4_floor=0.0012,
    trend_h1_floor=0.0005,
    rotation_daily_floor=0.0,
    rotation_h4_floor=0.0012,
    rotation_h1_floor=0.0005,
    trend_m30_floor=0.0003,
    trend_m15_floor=0.0003,
    rotation_m30_floor=0.0003,
    rotation_m15_floor=0.0003,
    target_trades_per_day_min=2,
    target_trades_per_day_max=6,
    objective_label="target_trade_frequency_research",
)

SCOUT_ENTRY_PROFILE = EntryProfile(
    name="scout",
    trend_daily_floor=0.0,
    trend_h4_floor=-0.01,
    trend_h1_floor=-0.005,
    rotation_daily_floor=0.0,
    rotation_h4_floor=-0.01,
    rotation_h1_floor=-0.005,
    trend_m30_floor=0.0,
    trend_m15_floor=0.0,
    rotation_m30_floor=0.0,
    rotation_m15_floor=0.0,
)

_ENTRY_PROFILES = {
    CONSERVATIVE_ENTRY_PROFILE.name: CONSERVATIVE_ENTRY_PROFILE,
    ACTIVE_PAPER_ENTRY_PROFILE.name: ACTIVE_PAPER_ENTRY_PROFILE,
    SHORT_TERM_ENTRY_PROFILE.name: SHORT_TERM_ENTRY_PROFILE,
    INTRADAY_MULTI_ENTRY_PROFILE.name: INTRADAY_MULTI_ENTRY_PROFILE,
    SCOUT_ENTRY_PROFILE.name: SCOUT_ENTRY_PROFILE,
    "exploratory_paper": ACTIVE_PAPER_ENTRY_PROFILE,
    "daily_multi": INTRADAY_MULTI_ENTRY_PROFILE,
    "aggressive_testnet": SCOUT_ENTRY_PROFILE,
}


def resolve_entry_profile(value: EntryProfile | str | None = None) -> EntryProfile:
    if isinstance(value, EntryProfile):
        return value
    if value is None or str(value).strip() == "":
        return CONSERVATIVE_ENTRY_PROFILE
    key = str(value).strip().lower().replace("-", "_")
    try:
        return _ENTRY_PROFILES[key]
    except KeyError as exc:
        allowed = ", ".join(sorted(_ENTRY_PROFILES))
        raise ValueError(f"entry profile must be one of {allowed}; got {value!r}") from exc
