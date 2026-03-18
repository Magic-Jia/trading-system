from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from trading_system.app.universe.sector_map import sector_for_symbol


def _get_value(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _position_notional(position: Mapping[str, Any] | Any) -> float:
    notional = _to_float(_get_value(position, "notional", 0.0))
    if notional > 0:
        return notional

    qty = abs(_to_float(_get_value(position, "qty", 0.0)))
    price = _to_float(_get_value(position, "mark_price", 0.0)) or _to_float(_get_value(position, "entry_price", 0.0))
    return qty * price


def exposure_snapshot(account: Mapping[str, Any] | Any) -> dict[str, Any]:
    equity = max(_to_float(_get_value(account, "equity", 0.0)), 0.0)
    positions = _get_value(account, "open_positions", [])
    if not isinstance(positions, list):
        positions = []

    gross_notional = 0.0
    net_long_notional = 0.0
    net_short_notional = 0.0
    major_notional = 0.0
    alt_notional = 0.0
    sector_notional: dict[str, float] = {}
    symbol_notional: dict[str, float] = {}

    for raw_position in positions:
        position = raw_position if isinstance(raw_position, Mapping) else raw_position
        symbol = str(_get_value(position, "symbol", "")).upper()
        if not symbol:
            continue
        side = str(_get_value(position, "side", "LONG")).upper()
        notional = abs(_position_notional(position))
        if notional <= 0:
            continue

        sector = str(_get_value(position, "sector", "")).strip() or sector_for_symbol(symbol)
        gross_notional += notional
        symbol_notional[symbol] = symbol_notional.get(symbol, 0.0) + notional
        sector_notional[sector] = sector_notional.get(sector, 0.0) + notional

        if sector == "majors":
            major_notional += notional
        else:
            alt_notional += notional

        if side == "SHORT":
            net_short_notional += notional
        else:
            net_long_notional += notional

    net_exposure_notional = net_long_notional - net_short_notional
    active_risk_pct = (gross_notional / equity) if equity else 0.0
    net_exposure_pct = (net_exposure_notional / equity) if equity else 0.0

    sector_risk = {
        sector: (notional / equity) if equity else 0.0
        for sector, notional in sorted(sector_notional.items(), key=lambda item: item[0])
    }
    symbol_risk = {
        symbol: (notional / equity) if equity else 0.0
        for symbol, notional in sorted(symbol_notional.items(), key=lambda item: item[0])
    }

    posture = "flat"
    if net_exposure_notional > 0:
        posture = "net_long"
    elif net_exposure_notional < 0:
        posture = "net_short"

    return {
        "equity": equity,
        "gross_notional": gross_notional,
        "active_risk_pct": active_risk_pct,
        "net_long_notional": net_long_notional,
        "net_short_notional": net_short_notional,
        "net_exposure_notional": net_exposure_notional,
        "net_exposure_pct": net_exposure_pct,
        "net_posture": posture,
        "major_notional": major_notional,
        "alt_notional": alt_notional,
        "sector_risk": sector_risk,
        "symbol_risk": symbol_risk,
    }
