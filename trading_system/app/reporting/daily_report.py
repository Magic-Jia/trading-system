from __future__ import annotations

from typing import Any, Mapping, Sequence


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _rotation_leader_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    timeframe_meta = dict(candidate.get("timeframe_meta") or {})
    relative_strength = dict(timeframe_meta.get("relative_strength") or {})
    liquidity_meta = dict(candidate.get("liquidity_meta") or {})
    return {
        "symbol": str(candidate.get("symbol", "")),
        "score": round(_float(candidate.get("score")), 6),
        "daily_spread": round(_float(relative_strength.get("daily_spread")), 6),
        "h4_spread": round(_float(relative_strength.get("h4_spread")), 6),
        "h1_spread": round(_float(relative_strength.get("h1_spread")), 6),
        "volume_usdt_24h": _float(liquidity_meta.get("volume_usdt_24h")),
        "slippage_bps": _float(liquidity_meta.get("slippage_bps")),
    }


def build_rotation_report(
    *,
    rotation_candidates: Sequence[Mapping[str, Any]],
    allocations: Sequence[Mapping[str, Any]],
    executions: Sequence[Mapping[str, Any]],
    rotation_universe: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    ranked = sorted(
        [dict(row) for row in rotation_candidates if str(row.get("symbol", ""))],
        key=lambda row: (-_float(row.get("score")), str(row.get("symbol", ""))),
    )
    rotation_symbols = {str(row.get("symbol", "")) for row in ranked if row.get("symbol")}
    accepted_symbols = sorted(
        {
            str(row.get("symbol", ""))
            for row in allocations
            if str(row.get("engine", "")).lower() == "rotation" and row.get("status") in {"ACCEPTED", "DOWNSIZED"}
        }
    )
    executed_symbols = sorted(
        {
            str(row.get("symbol", ""))
            for row in executions
            if str(row.get("symbol", "")) in rotation_symbols and row.get("status") == "FILLED"
        }
    )
    leaders = [_rotation_leader_row(row) for row in ranked[:3]]
    return {
        "universe_count": len(rotation_universe),
        "candidate_count": len(ranked),
        "accepted_symbols": accepted_symbols,
        "executed_symbols": executed_symbols,
        "leaders": leaders,
    }
