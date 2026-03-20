from __future__ import annotations

from typing import Any, Mapping, Sequence


_LIFECYCLE_STATES = ("INIT", "CONFIRM", "PAYLOAD", "PROTECT", "EXIT")


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


def _short_leader_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    timeframe_meta = dict(candidate.get("timeframe_meta") or {})
    liquidity_meta = dict(candidate.get("liquidity_meta") or {})
    return {
        "symbol": str(candidate.get("symbol", "")),
        "setup_type": str(candidate.get("setup_type", "")),
        "score": round(_float(candidate.get("score")), 6),
        "daily_bias": str(timeframe_meta.get("daily_bias", "")),
        "h4_structure": str(timeframe_meta.get("h4_structure", "")),
        "h1_trigger": str(timeframe_meta.get("h1_trigger", "")),
        "volume_usdt_24h": _float(liquidity_meta.get("volume_usdt_24h")),
        "liquidity_tier": str(liquidity_meta.get("liquidity_tier", "")),
    }


def _lifecycle_leader_row(symbol: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "state": str(payload.get("state", "INIT")).upper(),
        "r_multiple": round(_float(payload.get("r_multiple")), 6),
        "reason_codes": [str(code) for code in payload.get("reason_codes", [])],
    }


def build_lifecycle_report(
    *,
    lifecycle_updates: Mapping[str, Mapping[str, Any]],
    management_suggestions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    state_counts = {state: 0 for state in _LIFECYCLE_STATES}
    leaders: list[dict[str, Any]] = []
    pending_confirmation_symbols: list[str] = []
    protected_symbols: list[str] = []
    exit_symbols: list[str] = []

    for symbol, payload in sorted(lifecycle_updates.items()):
        state = str(payload.get("state", "INIT")).upper()
        if state in state_counts:
            state_counts[state] += 1
        leader = _lifecycle_leader_row(symbol, payload)
        leaders.append(leader)
        if state == "INIT":
            pending_confirmation_symbols.append(symbol)
        elif state == "PROTECT":
            protected_symbols.append(symbol)
        elif state == "EXIT":
            exit_symbols.append(symbol)

    leaders.sort(key=lambda row: (-_float(row.get("r_multiple")), str(row.get("symbol", ""))))
    attention_symbols = sorted(
        {
            str(row.get("symbol", ""))
            for row in management_suggestions
            if str(row.get("symbol", ""))
        }
        | set(exit_symbols)
    )
    return {
        "tracked_count": len(lifecycle_updates),
        "state_counts": state_counts,
        "pending_confirmation_symbols": pending_confirmation_symbols,
        "protected_symbols": protected_symbols,
        "exit_symbols": exit_symbols,
        "attention_symbols": attention_symbols,
        "leaders": leaders[:3],
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


def build_short_report(
    *,
    short_candidates: Sequence[Mapping[str, Any]],
    allocations: Sequence[Mapping[str, Any]],
    short_universe: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    ranked = sorted(
        [dict(row) for row in short_candidates if str(row.get("symbol", ""))],
        key=lambda row: (-_float(row.get("score")), str(row.get("symbol", ""))),
    )
    accepted_symbols = sorted(
        {
            str(row.get("symbol", ""))
            for row in allocations
            if str(row.get("engine", "")).lower() == "short" and row.get("status") in {"ACCEPTED", "DOWNSIZED"}
        }
    )
    deferred_execution_symbols = sorted(
        {
            str(row.get("symbol", ""))
            for row in allocations
            if str(row.get("engine", "")).lower() == "short"
            and dict(row.get("execution") or {}).get("reason") == "short_execution_not_enabled"
        }
    )
    leaders = [_short_leader_row(row) for row in ranked[:3]]
    return {
        "universe_count": len(short_universe),
        "candidate_count": len(ranked),
        "accepted_symbols": accepted_symbols,
        "deferred_execution_symbols": deferred_execution_symbols,
        "leaders": leaders,
    }
