from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping

from trading_system.app.config import DEFAULT_CONFIG, AppConfig
from trading_system.app.market_regime.classifier import classify_regime
from trading_system.app.portfolio.allocator import allocate_candidates
from trading_system.app.risk.validator import validate_candidate_for_allocation
from trading_system.app.signals.rotation_engine import generate_rotation_candidates
from trading_system.app.signals.short_engine import generate_short_candidates
from trading_system.app.signals.trend_engine import generate_trend_candidates
from trading_system.app.universe.builder import UniverseBuildResult, build_universes

from .types import BacktestCosts, DatasetSnapshotRow


def _candidate_row(candidate: Any) -> dict[str, Any]:
    if is_dataclass(candidate):
        return asdict(candidate)
    if isinstance(candidate, Mapping):
        return dict(candidate)
    raise TypeError(f"unsupported candidate type: {type(candidate)!r}")


def _rank_key(row: Mapping[str, Any]) -> tuple[float, str, str]:
    return (-float(row.get("score", 0.0) or 0.0), str(row.get("symbol", "")), str(row.get("engine", "")))


def _regime_dict(row: DatasetSnapshotRow) -> dict[str, Any]:
    override = row.meta.get("regime_override")
    if isinstance(override, Mapping):
        return dict(override)
    return asdict(classify_regime(row.market, row.derivatives))


def _universes_payload(universes: UniverseBuildResult) -> dict[str, Any]:
    return {
        "major_universe": universes.major_universe,
        "rotation_universe": universes.rotation_universe,
        "short_universe": universes.short_universe,
        "major_count": len(universes.major_universe),
        "rotation_count": len(universes.rotation_universe),
        "short_count": len(universes.short_universe),
    }


def _suppression_payload(regime: Mapping[str, Any]) -> dict[str, Any]:
    rules = {str(rule).lower() for rule in list(regime.get("suppression_rules", []))}
    return {
        "rules": sorted(rules),
        "rotation_suppressed": "rotation" in rules,
        "trend_suppressed": "trend" in rules,
        "short_suppressed": "short" in rules,
        "execution_policy": regime.get("execution_policy", "normal"),
    }


def _validated_candidates(candidates: list[dict[str, Any]], account: Mapping[str, Any]) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for row in candidates:
        validation = validate_candidate_for_allocation(row, account)
        candidate = dict(row)
        candidate["validation"] = {
            "allowed": validation.allowed,
            "reasons": list(validation.reasons),
            "metrics": dict(validation.metrics),
        }
        if validation.allowed:
            validated.append(candidate)
    return validated


def _allocation_rows(
    account: Mapping[str, Any],
    validated_candidates: list[dict[str, Any]],
    regime: Mapping[str, Any],
    *,
    app_config: AppConfig,
) -> list[dict[str, Any]]:
    ranked = sorted(validated_candidates, key=_rank_key)
    decisions = allocate_candidates(account=account, candidates=ranked, regime=regime, config=app_config)
    allocations: list[dict[str, Any]] = []
    for candidate, decision in zip(ranked, decisions):
        allocations.append(
            {
                "symbol": candidate.get("symbol"),
                "engine": candidate.get("engine"),
                "setup_type": candidate.get("setup_type"),
                "score": candidate.get("score"),
                "status": decision.status,
                "rank": decision.rank,
                "reasons": list(decision.reasons),
                "final_risk_budget": decision.final_risk_budget,
                "meta": dict(decision.meta),
            }
        )
    return allocations


def replay_snapshot(
    row: DatasetSnapshotRow,
    *,
    app_config: AppConfig | None = None,
    costs: BacktestCosts | None = None,
) -> dict[str, Any]:
    resolved_config = app_config or DEFAULT_CONFIG
    resolved_costs = costs or BacktestCosts(fee_bps=0.0, slippage_bps=0.0, funding_bps_per_day=0.0)
    regime = _regime_dict(row)
    universes = build_universes(row.market, derivatives=row.derivatives)

    raw_candidates = {
        "trend": [_candidate_row(candidate) for candidate in generate_trend_candidates(row.market, derivatives=row.derivatives)],
        "rotation": [
            _candidate_row(candidate)
            for candidate in generate_rotation_candidates(
                row.market,
                rotation_universe=universes.rotation_universe,
                derivatives=row.derivatives,
                regime=regime,
            )
        ],
        "short": [
            _candidate_row(candidate)
            for candidate in generate_short_candidates(
                row.market,
                short_universe=universes.short_universe,
                derivatives=row.derivatives,
                regime=regime,
            )
        ],
    }
    all_candidates = [candidate for engine_rows in raw_candidates.values() for candidate in engine_rows]
    account = dict(row.account or {"equity": 0.0, "available_balance": 0.0, "futures_wallet_balance": 0.0, "open_positions": []})
    validated_candidates = _validated_candidates(all_candidates, account)
    allocations = _allocation_rows(account, validated_candidates, regime, app_config=resolved_config)
    return {
        "snapshot": {"run_id": row.run_id, "timestamp": row.timestamp.isoformat()},
        "regime": regime,
        "suppression": _suppression_payload(regime),
        "universes": _universes_payload(universes),
        "raw_candidates": raw_candidates,
        "validated_candidates": validated_candidates,
        "allocations": allocations,
        "execution_assumptions": {
            "fee_bps": resolved_costs.fee_bps,
            "slippage_bps": resolved_costs.slippage_bps,
            "funding_bps_per_day": resolved_costs.funding_bps_per_day,
        },
    }
