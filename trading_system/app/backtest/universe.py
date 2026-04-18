from __future__ import annotations

from datetime import UTC, datetime
from typing import Sequence

from .types import InstrumentSnapshotRow, UniverseExclusionRow, UniverseFilterConfig


def _listing_age_days(row: InstrumentSnapshotRow, *, as_of: datetime) -> float:
    return (as_of - row.listing_timestamp.astimezone(UTC)).total_seconds() / 86_400.0


def _missing_tradeability_metadata(row: InstrumentSnapshotRow) -> bool:
    return not row.liquidity_tier.strip() or row.quantity_step <= 0.0 or row.price_tick <= 0.0


def filter_universe(
    instrument_rows: Sequence[InstrumentSnapshotRow], *, universe_config: UniverseFilterConfig
) -> tuple[list[InstrumentSnapshotRow], list[UniverseExclusionRow]]:
    included: list[InstrumentSnapshotRow] = []
    excluded: list[UniverseExclusionRow] = []
    as_of = datetime.now(UTC)

    for row in instrument_rows:
        listing_age_days = _listing_age_days(row, as_of=as_of)
        if listing_age_days < universe_config.listing_age_days:
            excluded.append(
                UniverseExclusionRow(
                    symbol=row.symbol,
                    market_type=row.market_type,
                    reason_code="listing_age_below_minimum",
                    detail={
                        "listing_age_days": listing_age_days,
                        "minimum_listing_age_days": universe_config.listing_age_days,
                    },
                )
            )
            continue

        minimum_quote_volume = universe_config.min_quote_volume_usdt_24h.get(row.market_type, 0.0)
        if row.quote_volume_usdt_24h < minimum_quote_volume:
            excluded.append(
                UniverseExclusionRow(
                    symbol=row.symbol,
                    market_type=row.market_type,
                    reason_code="quote_volume_below_minimum",
                    detail={
                        "quote_volume_usdt_24h": row.quote_volume_usdt_24h,
                        "minimum_quote_volume_usdt_24h": minimum_quote_volume,
                    },
                )
            )
            continue

        if row.market_type == "futures" and universe_config.require_complete_funding and not row.has_complete_funding:
            excluded.append(
                UniverseExclusionRow(
                    symbol=row.symbol,
                    market_type=row.market_type,
                    reason_code="missing_funding_series",
                    detail={"require_complete_funding": True},
                )
            )
            continue

        if _missing_tradeability_metadata(row):
            excluded.append(
                UniverseExclusionRow(
                    symbol=row.symbol,
                    market_type=row.market_type,
                    reason_code="missing_tradeability_metadata",
                    detail={
                        "liquidity_tier": row.liquidity_tier,
                        "quantity_step": row.quantity_step,
                        "price_tick": row.price_tick,
                    },
                )
            )
            continue

        included.append(row)

    return included, excluded
