from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from trading_system.app.config import AppConfig, DEFAULT_CONFIG
from trading_system.app.risk.guardrails import evaluate_allocation_guardrails
from trading_system.app.risk.regime_risk import scaled_risk_budget
from trading_system.app.risk.validator import validate_candidate_for_allocation
from trading_system.app.types import AllocationDecision, EngineCandidate, RegimeSnapshot
from trading_system.app.universe.sector_map import sector_for_symbol

from .exposure import exposure_snapshot

_ENGINE_BASE_RISK_PCT: dict[str, float] = {"trend": 0.008, "rotation": 0.005, "short": 0.004}
_MIN_RISK_BUDGET = 1e-8
_DEFAULT_NET_EXPOSURE_CAP_PCT = 0.85


def _to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _candidate_value(candidate: EngineCandidate | Mapping[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(key, default)
    return getattr(candidate, key, default)


def _normalize_candidate(candidate: EngineCandidate | Mapping[str, Any]) -> dict[str, Any]:
    symbol = str(_candidate_value(candidate, "symbol", "")).upper().strip()
    return {
        "engine": str(_candidate_value(candidate, "engine", "")).lower().strip(),
        "setup_type": str(_candidate_value(candidate, "setup_type", "")).upper().strip(),
        "symbol": symbol,
        "side": str(_candidate_value(candidate, "side", "LONG")).upper().strip(),
        "score": _to_float(_candidate_value(candidate, "score", 0.0)),
        "sector": str(_candidate_value(candidate, "sector", "")).strip() or sector_for_symbol(symbol),
    }


def _regime_value(regime: RegimeSnapshot | Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    if regime is None:
        return default
    if isinstance(regime, Mapping):
        return regime.get(key, default)
    return getattr(regime, key, default)


def _bucket_targets(config: AppConfig, regime: RegimeSnapshot | Mapping[str, Any] | None) -> dict[str, float]:
    defaults = {
        "trend": float(config.allocator.trend_bucket_weight),
        "rotation": float(config.allocator.rotation_bucket_weight),
        "short": float(config.allocator.short_bucket_weight),
    }
    raw_targets = _regime_value(regime, "bucket_targets", {})
    if not isinstance(raw_targets, Mapping):
        return defaults

    merged = dict(defaults)
    for key, value in raw_targets.items():
        merged[str(key).lower()] = max(_to_float(value), 0.0)
    return merged


def _suppressed_engines(regime: RegimeSnapshot | Mapping[str, Any] | None) -> set[str]:
    suppressed: set[str] = set()
    for key in ("suppressed_engines", "suppression_rules"):
        rows = _regime_value(regime, key, [])
        if isinstance(rows, list):
            suppressed.update(str(row).lower().strip() for row in rows if str(row).strip())
    return suppressed


def _engine_tier_multiplier(engine: str, sector: str) -> float:
    if engine == "rotation":
        return 0.85
    if engine == "short":
        return 0.75
    if engine == "trend" and sector != "majors":
        return 0.9
    return 1.0


def _setup_family(setup_type: str) -> str:
    lowered = setup_type.lower()
    if "breakout" in lowered:
        return "breakout"
    if "pullback" in lowered:
        return "pullback"
    return lowered or "unknown"


def _duplicate_penalty_factor(accepted_count: int) -> float:
    return 0.65**accepted_count


def _major_alt_balance_ok(major_risk: float, alt_risk: float, major_target: float) -> tuple[bool, float, float]:
    total = major_risk + alt_risk
    major_share = (major_risk / total) if total > 0 else 1.0
    threshold = max(0.35, min(major_target, 0.85))
    return major_share >= threshold, major_share, threshold


def allocate_candidates(
    *,
    account: Mapping[str, Any] | Any,
    candidates: Sequence[EngineCandidate | Mapping[str, Any]],
    regime: RegimeSnapshot | Mapping[str, Any] | None = None,
    config: AppConfig | None = None,
) -> list[AllocationDecision]:
    app_config = config or DEFAULT_CONFIG
    if not candidates:
        return []

    total_risk_cap = max(float(app_config.risk.max_total_risk_pct), 0.0)
    regime_multiplier = max(_to_float(_regime_value(regime, "risk_multiplier", 1.0), 1.0), 0.0)
    confidence = max(min(_to_float(_regime_value(regime, "confidence", 1.0), 1.0), 1.0), 0.0)
    net_exposure_cap = max(_to_float(_regime_value(regime, "net_exposure_cap_pct", _DEFAULT_NET_EXPOSURE_CAP_PCT)), 0.0)
    bucket_targets = _bucket_targets(app_config, regime)
    suppressed_engines = _suppressed_engines(regime)

    bucket_caps = {engine: total_risk_cap * max(weight, 0.0) for engine, weight in bucket_targets.items()}
    bucket_risk_used: dict[str, float] = defaultdict(float)
    portfolio_risk_used = 0.0
    open_positions = account.get("open_positions", []) if isinstance(account, Mapping) else getattr(account, "open_positions", [])
    if not isinstance(open_positions, list):
        open_positions = []
    max_open_positions = int(app_config.risk.max_open_positions)

    exposure = exposure_snapshot(account)
    net_exposure_pct = _to_float(exposure.get("net_exposure_pct", 0.0))
    major_risk = _to_float(exposure.get("sector_risk", {}).get("majors", 0.0))
    alt_risk = sum(
        _to_float(risk)
        for sector, risk in dict(exposure.get("sector_risk", {})).items()
        if str(sector).lower() != "majors"
    )

    cycle_symbol_risk: dict[str, float] = defaultdict(float)
    cycle_sector_risk: dict[str, float] = defaultdict(float)
    duplicate_counts: dict[tuple[str, str, str], int] = defaultdict(int)

    symbol_cap_pct = max(float(app_config.risk.max_symbol_risk_pct), 0.0)
    sector_cap_pct = max(total_risk_cap * float(app_config.allocator.sector_cap_pct), 0.0)
    major_target = _to_float(bucket_targets.get("trend", 0.5), 0.5)

    normalized = [_normalize_candidate(candidate) for candidate in candidates]
    ranked = sorted(normalized, key=lambda row: (-row["score"], row["symbol"], row["engine"]))

    decisions: list[AllocationDecision] = []
    for rank, candidate in enumerate(ranked, start=1):
        engine = candidate["engine"]
        symbol = candidate["symbol"]
        side = candidate["side"]
        sector = candidate["sector"]

        reasons: list[str] = []
        meta: dict[str, Any] = {
            "rank_score": candidate["score"],
            "portfolio_total_risk_cap": round(total_risk_cap, 6),
            "portfolio_risk_used_before": round(portfolio_risk_used, 6),
            "bucket_targets": dict(bucket_targets),
            "bucket_cap": round(bucket_caps.get(engine, 0.0), 6),
            "bucket_risk_used_before": round(bucket_risk_used.get(engine, 0.0), 6),
            "conflict_checked": False,
            "major_alt_balance_ok": True,
            "open_positions_count": len(open_positions),
            "max_open_positions": max_open_positions,
        }

        if len(open_positions) >= max_open_positions:
            reasons.append(f"持仓数已达上限：{len(open_positions)} / {max_open_positions}")
            meta["open_positions_limit_hit"] = True
            decisions.append(
                AllocationDecision(
                    status="REJECTED",
                    engine=engine,
                    reasons=reasons,
                    meta=meta,
                    final_risk_budget=0.0,
                    rank=rank,
                )
            )
            continue

        validation = validate_candidate_for_allocation(candidate, account)
        reasons.extend(validation.reasons)
        meta.update(validation.metrics)
        if not validation.allowed:
            decisions.append(
                AllocationDecision(
                    status="REJECTED",
                    engine=engine,
                    reasons=reasons or ["candidate validation failed"],
                    meta=meta,
                    final_risk_budget=0.0,
                    rank=rank,
                )
            )
            continue

        if engine in suppressed_engines:
            reasons.append(f"engine {engine} is suppressed by regime")
            decisions.append(
                AllocationDecision(
                    status="REJECTED",
                    engine=engine,
                    reasons=reasons,
                    meta=meta,
                    final_risk_budget=0.0,
                    rank=rank,
                )
            )
            continue

        base_risk_pct = _ENGINE_BASE_RISK_PCT.get(engine, 0.0035)
        initial_budget = scaled_risk_budget(
            base_risk_pct=base_risk_pct,
            regime_multiplier=regime_multiplier,
            confidence=confidence,
            engine_tier_multiplier=_engine_tier_multiplier(engine, sector),
        )
        final_budget = initial_budget
        downsized = False

        duplicate_key = (engine, side, _setup_family(candidate["setup_type"]))
        accepted_count = duplicate_counts[duplicate_key]
        if accepted_count > 0:
            final_budget *= _duplicate_penalty_factor(accepted_count)
            downsized = True
            reasons.append("duplicate setup crowding penalty applied")

        bucket_cap = max(bucket_caps.get(engine, 0.0), 0.0)
        bucket_remaining = max(bucket_cap - bucket_risk_used.get(engine, 0.0), 0.0)
        portfolio_remaining = max(total_risk_cap - portfolio_risk_used, 0.0)
        allowed_budget = min(bucket_remaining, portfolio_remaining)
        if allowed_budget <= _MIN_RISK_BUDGET:
            reasons.append("bucket or portfolio risk budget exhausted")
            meta["risk_budget_exhausted"] = True
            decisions.append(
                AllocationDecision(
                    status="REJECTED",
                    engine=engine,
                    reasons=reasons,
                    meta=meta,
                    final_risk_budget=0.0,
                    rank=rank,
                )
            )
            continue

        if final_budget > allowed_budget:
            final_budget = allowed_budget
            downsized = True
            reasons.append("risk budget downsized to fit cap")

        guard_ok, guard_reasons, guard_meta = evaluate_allocation_guardrails(
            candidate_symbol=symbol,
            candidate_sector=sector,
            candidate_side=side,
            candidate_risk_budget=final_budget,
            symbol_risk_before_pct=cycle_symbol_risk.get(symbol, 0.0),
            sector_risk_before_pct=cycle_sector_risk.get(sector, 0.0),
            net_exposure_before_pct=net_exposure_pct,
            symbol_cap_pct=symbol_cap_pct,
            sector_cap_pct=sector_cap_pct,
            net_exposure_cap_pct=net_exposure_cap,
        )
        reasons.extend(guard_reasons)
        meta.update(guard_meta)

        projected_major_risk = major_risk + final_budget if sector == "majors" else major_risk
        projected_alt_risk = alt_risk + final_budget if sector != "majors" else alt_risk
        major_alt_ok, major_share, major_threshold = _major_alt_balance_ok(
            projected_major_risk,
            projected_alt_risk,
            major_target,
        )
        meta["major_alt_balance_ok"] = major_alt_ok
        meta["major_share_after"] = round(major_share, 6)
        meta["major_share_threshold"] = round(major_threshold, 6)

        if not guard_ok:
            decisions.append(
                AllocationDecision(
                    status="REJECTED",
                    engine=engine,
                    reasons=reasons or ["guardrails blocked candidate"],
                    meta=meta,
                    final_risk_budget=0.0,
                    rank=rank,
                )
            )
            continue

        if not major_alt_ok:
            reasons.append("major/alt balance constraint failed")
            decisions.append(
                AllocationDecision(
                    status="REJECTED",
                    engine=engine,
                    reasons=reasons,
                    meta=meta,
                    final_risk_budget=0.0,
                    rank=rank,
                )
            )
            continue

        if final_budget <= _MIN_RISK_BUDGET:
            reasons.append("final risk budget too small")
            decisions.append(
                AllocationDecision(
                    status="REJECTED",
                    engine=engine,
                    reasons=reasons,
                    meta=meta,
                    final_risk_budget=0.0,
                    rank=rank,
                )
            )
            continue

        cycle_symbol_risk[symbol] = cycle_symbol_risk.get(symbol, 0.0) + final_budget
        cycle_sector_risk[sector] = cycle_sector_risk.get(sector, 0.0) + final_budget
        bucket_risk_used[engine] = bucket_risk_used.get(engine, 0.0) + final_budget
        portfolio_risk_used += final_budget
        net_exposure_pct = _to_float(meta.get("net_exposure_after", net_exposure_pct), net_exposure_pct)
        major_risk = projected_major_risk
        alt_risk = projected_alt_risk
        duplicate_counts[duplicate_key] = accepted_count + 1

        status = "DOWNSIZED" if downsized else "ACCEPTED"
        decisions.append(
            AllocationDecision(
                status=status,
                engine=engine,
                reasons=reasons,
                meta=meta,
                final_risk_budget=round(final_budget, 6),
                rank=rank,
            )
        )

    return decisions
