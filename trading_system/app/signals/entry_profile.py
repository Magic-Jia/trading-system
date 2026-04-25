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

_ENTRY_PROFILES = {
    CONSERVATIVE_ENTRY_PROFILE.name: CONSERVATIVE_ENTRY_PROFILE,
    ACTIVE_PAPER_ENTRY_PROFILE.name: ACTIVE_PAPER_ENTRY_PROFILE,
    "exploratory_paper": ACTIVE_PAPER_ENTRY_PROFILE,
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
