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
_MISSING = object()


def _to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _strict_float(value: Any, field: str, *, default: float | None = None) -> float:
    if value is _MISSING or value is None:
        if default is None:
            raise ValueError(f"{field} must be numeric")
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric, not boolean")
    if isinstance(value, str):
        raise ValueError(f"{field} must be numeric, not string")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc


def _strict_mapping(value: Any, field: str, *, default_empty: bool = True) -> dict[str, Any]:
    if value is _MISSING or value is None:
        if default_empty:
            return {}
        raise ValueError(f"{field} must be a mapping")
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return dict(value)


def _strict_canonical_string(
    value: Any,
    field: str,
    *,
    default: str | None = None,
    case: str | None = None,
    allow_empty: bool = False,
) -> str:
    if value is _MISSING or value is None:
        if default is not None:
            return default
        if allow_empty:
            return ""
        raise ValueError(f"{field} must be a string")
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if value.strip() != value or (not value and not allow_empty):
        raise ValueError(f"{field} must be a canonical string")
    if case == "lower" and value.lower() != value:
        raise ValueError(f"{field} must be lowercase")
    if case == "upper" and value.upper() != value:
        raise ValueError(f"{field} must be uppercase")
    return value


def _strict_exposure_risk_map(exposure: Mapping[str, Any], key: str) -> dict[str, float]:
    raw_map = exposure.get(key, {})
    if raw_map is None:
        raw_map = {}
    if not isinstance(raw_map, Mapping):
        raise ValueError(f"exposure.{key} must be a mapping")

    risk_map: dict[str, float] = {}
    for raw_key, raw_risk in raw_map.items():
        if not isinstance(raw_key, str) or not raw_key or raw_key.strip() != raw_key:
            raise ValueError(f"exposure.{key} keys must be canonical non-empty strings")
        if isinstance(raw_risk, bool):
            raise ValueError(f"exposure.{key}.{raw_key} risk must be numeric, not boolean")
        risk_map[raw_key] = _to_float(raw_risk)
    return risk_map


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def _candidate_value(candidate: EngineCandidate | Mapping[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(key, default)
    return getattr(candidate, key, default)


def _normalize_candidate(candidate: EngineCandidate | Mapping[str, Any]) -> dict[str, Any]:
    engine = _strict_canonical_string(_candidate_value(candidate, "engine", _MISSING), "candidate.engine", case="lower")
    setup_type = _strict_canonical_string(
        _candidate_value(candidate, "setup_type", _MISSING), "candidate.setup_type", case="upper"
    )
    symbol = _strict_canonical_string(_candidate_value(candidate, "symbol", _MISSING), "candidate.symbol", case="upper")
    side = _strict_canonical_string(_candidate_value(candidate, "side", _MISSING), "candidate.side", default="LONG", case="upper")
    score = _strict_float(_candidate_value(candidate, "score", _MISSING), "candidate.score", default=0.0)
    raw_sector = _candidate_value(candidate, "sector", _MISSING)
    sector = _strict_canonical_string(raw_sector, "candidate.sector", allow_empty=True) if raw_sector is not _MISSING else ""
    timeframe_meta = _strict_mapping(_candidate_value(candidate, "timeframe_meta", _MISSING), "candidate.timeframe_meta")
    liquidity_meta = _strict_mapping(_candidate_value(candidate, "liquidity_meta", _MISSING), "candidate.liquidity_meta")
    return {
        "engine": engine,
        "setup_type": setup_type,
        "symbol": symbol,
        "side": side,
        "score": score,
        "sector": sector or sector_for_symbol(symbol),
        "timeframe_meta": timeframe_meta,
        "liquidity_meta": liquidity_meta,
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
        if not isinstance(key, str) or not key or key.strip() != key or key.lower() != key:
            raise ValueError("regime.bucket_targets keys must be canonical strings")
        merged[key] = max(_strict_float(value, f"regime.bucket_targets.{key}"), 0.0)
    return merged


def _suppressed_engines(regime: RegimeSnapshot | Mapping[str, Any] | None) -> set[str]:
    suppressed: set[str] = set()
    for key in ("suppressed_engines", "suppression_rules"):
        rows = _regime_value(regime, key, [])
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, str) or not row or row.strip() != row or row.lower() != row:
                    raise ValueError(f"regime.{key} entries must be canonical strings")
                suppressed.add(row)
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


def _allow_single_alt_seed_without_majors(
    *,
    entry_profile_name: str,
    engine: str,
    side: str,
    sector: str,
    open_positions_count: int,
    major_risk: float,
    alt_risk: float,
    current_active_risk_pct: float,
    portfolio_risk_used: float,
) -> bool:
    if entry_profile_name not in {"active_paper", "exploratory_paper"}:
        return False
    if engine != "rotation":
        return False
    if side.upper() != "LONG":
        return False
    if sector == "majors":
        return False
    epsilon = 1e-9
    return (
        open_positions_count == 0
        and major_risk <= epsilon
        and alt_risk <= epsilon
        and current_active_risk_pct <= epsilon
        and portfolio_risk_used <= epsilon
    )


def _alt_seed_risk_cap(total_risk_cap: float) -> float:
    return max(total_risk_cap * 0.15, _MIN_RISK_BUDGET)


def _quality_multiplier(score: float) -> float:
    return _clamp(0.8 + (0.4 * _clamp(score, 0.0, 1.0)), 0.8, 1.2)


def _crowding_multiplier(candidate: Mapping[str, Any]) -> float:
    timeframe_meta = _strict_mapping(candidate.get("timeframe_meta", _MISSING), "candidate.timeframe_meta")
    derivatives = _strict_mapping(
        timeframe_meta.get("derivatives", _MISSING), "candidate.timeframe_meta.derivatives"
    )
    side = _strict_canonical_string(candidate.get("side", _MISSING), "candidate.side", default="LONG", case="upper")
    crowding_bias = _strict_canonical_string(
        derivatives.get("crowding_bias", _MISSING),
        "candidate.timeframe_meta.derivatives.crowding_bias",
        default="balanced",
        case="lower",
    )
    crowding_score = abs(
        _strict_float(
            derivatives.get("crowding_score", _MISSING),
            "candidate.timeframe_meta.derivatives.crowding_score",
            default=0.0,
        )
    )
    basis_bps = _strict_float(
        derivatives.get("basis_bps", _MISSING), "candidate.timeframe_meta.derivatives.basis_bps", default=0.0
    )
    funding_rate = _strict_float(
        derivatives.get("funding_rate", _MISSING), "candidate.timeframe_meta.derivatives.funding_rate", default=0.0
    )

    multiplier = 1.0
    same_side_crowding = (side == "LONG" and crowding_bias == "crowded_long") or (
        side == "SHORT" and crowding_bias == "crowded_short"
    )
    if same_side_crowding:
        multiplier *= 0.82
    elif crowding_bias.startswith("crowded_"):
        multiplier *= 0.92

    if crowding_score >= 3.0:
        multiplier *= 0.86
    elif crowding_score >= 2.0:
        multiplier *= 0.92

    if side == "LONG":
        if basis_bps >= 15.0:
            multiplier *= 0.92
        elif basis_bps >= 10.0:
            multiplier *= 0.96
        if funding_rate >= 0.0001:
            multiplier *= 0.94
        elif funding_rate >= 0.00005:
            multiplier *= 0.98
    else:
        if basis_bps <= -15.0:
            multiplier *= 0.92
        elif basis_bps <= -10.0:
            multiplier *= 0.96
        if funding_rate <= -0.0001:
            multiplier *= 0.94
        elif funding_rate <= -0.00005:
            multiplier *= 0.98

    return _clamp(multiplier, 0.5, 1.0)


def _execution_friction_multiplier(candidate: Mapping[str, Any]) -> float:
    liquidity_meta = _strict_mapping(candidate.get("liquidity_meta", _MISSING), "candidate.liquidity_meta")
    spread_bps = _strict_float(liquidity_meta.get("spread_bps", _MISSING), "candidate.liquidity_meta.spread_bps", default=0.0)
    slippage_bps = _strict_float(
        liquidity_meta.get("slippage_bps", _MISSING), "candidate.liquidity_meta.slippage_bps", default=0.0
    )
    volume_usdt_24h = _strict_float(
        liquidity_meta.get("volume_usdt_24h", _MISSING), "candidate.liquidity_meta.volume_usdt_24h", default=0.0
    )

    multiplier = 1.0
    if spread_bps > 5.0:
        multiplier *= 0.9
    elif spread_bps > 3.0:
        multiplier *= 0.95
    elif spread_bps > 1.5:
        multiplier *= 0.98

    if slippage_bps > 15.0:
        multiplier *= 0.86
    elif slippage_bps > 10.0:
        multiplier *= 0.93
    elif slippage_bps > 5.0:
        multiplier *= 0.97

    if 0.0 < volume_usdt_24h < 750_000_000.0:
        multiplier *= 0.95
    elif 0.0 < volume_usdt_24h < 1_000_000_000.0:
        multiplier *= 0.98

    return _clamp(multiplier, 0.55, 1.0)


def _regime_hazard_multiplier(regime: RegimeSnapshot | Mapping[str, Any] | None) -> float:
    execution_hazard = str(_regime_value(regime, "execution_hazard", "none")).lower()
    execution_policy = str(_regime_value(regime, "execution_policy", "normal")).lower()

    multiplier = 1.0
    if execution_hazard == "compress_risk":
        multiplier *= 0.84
    if execution_policy == "downsize":
        multiplier *= 0.95
    elif execution_policy == "suppress":
        multiplier *= 0.9

    return _clamp(multiplier, 0.6, 1.0)


def _late_stage_heat_multiplier(
    regime: RegimeSnapshot | Mapping[str, Any] | None,
    *,
    side: str,
) -> float:
    late_stage_heat = str(_regime_value(regime, "late_stage_heat", "none")).lower()
    if late_stage_heat == "none":
        return 1.0
    if side.upper() != "LONG":
        return 1.0
    return 0.8


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
    current_active_risk_pct = _to_float(exposure.get("active_risk_pct", 0.0))
    net_exposure_pct = _to_float(exposure.get("net_exposure_pct", 0.0))
    sector_risk = _strict_exposure_risk_map(exposure, "sector_risk")
    symbol_risk = _strict_exposure_risk_map(exposure, "symbol_risk")
    major_risk = sector_risk.get("majors", 0.0)
    alt_risk = sum(risk for sector, risk in sector_risk.items() if sector.lower() != "majors")

    cycle_symbol_risk: dict[str, float] = defaultdict(float, symbol_risk)
    cycle_sector_risk: dict[str, float] = defaultdict(float, sector_risk)
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
            "account_active_risk_pct": round(current_active_risk_pct, 6),
            "bucket_targets": dict(bucket_targets),
            "bucket_cap": round(bucket_caps.get(engine, 0.0), 6),
            "bucket_risk_used_before": round(bucket_risk_used.get(engine, 0.0), 6),
            "conflict_checked": False,
            "major_alt_balance_ok": True,
            "open_positions_count": len(open_positions),
            "max_open_positions": max_open_positions,
        }

        if current_active_risk_pct >= total_risk_cap:
            reasons.append(f"总风险暴露已达上限：{current_active_risk_pct:.2%} >= {total_risk_cap:.2%}")
            meta["account_total_risk_limit_hit"] = True

        if len(open_positions) >= max_open_positions:
            reasons.append(f"持仓数已达上限：{len(open_positions)} / {max_open_positions}")
            meta["open_positions_limit_hit"] = True

        validation = validate_candidate_for_allocation(candidate, account)
        reasons.extend(validation.reasons)
        meta.update(validation.metrics)
        if reasons or not validation.allowed:
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
        quality_multiplier = _quality_multiplier(candidate["score"])
        crowding_multiplier = _crowding_multiplier(candidate)
        execution_friction_multiplier = _execution_friction_multiplier(candidate)
        regime_hazard_multiplier = _regime_hazard_multiplier(regime)
        late_stage_heat_multiplier = _late_stage_heat_multiplier(regime, side=side)
        aggressiveness_multiplier = (
            quality_multiplier
            * crowding_multiplier
            * execution_friction_multiplier
            * regime_hazard_multiplier
            * late_stage_heat_multiplier
        )
        meta.update(
            {
                "initial_risk_budget": round(initial_budget, 6),
                "quality_multiplier": round(quality_multiplier, 6),
                "crowding_multiplier": round(crowding_multiplier, 6),
                "execution_friction_multiplier": round(execution_friction_multiplier, 6),
                "regime_hazard_multiplier": round(regime_hazard_multiplier, 6),
                "late_stage_heat_multiplier": round(late_stage_heat_multiplier, 6),
                "aggressiveness_multiplier": round(aggressiveness_multiplier, 6),
            }
        )
        final_budget = initial_budget * aggressiveness_multiplier
        downsized = False

        if crowding_multiplier < 0.999999:
            reasons.append("crowding reduced aggressiveness")
            downsized = True
        if execution_friction_multiplier < 0.999999:
            reasons.append("execution friction reduced aggressiveness")
            downsized = True
        if regime_hazard_multiplier < 0.999999:
            reasons.append("defensive regime hazard reduced aggressiveness")
            downsized = True
        if late_stage_heat_multiplier < 0.999999:
            reasons.append("late-stage heat reduced long aggressiveness")
            downsized = True

        duplicate_key = (engine, side, _setup_family(candidate["setup_type"]))
        accepted_count = duplicate_counts[duplicate_key]
        if accepted_count > 0:
            final_budget *= _duplicate_penalty_factor(accepted_count)
            downsized = True
            reasons.append("duplicate setup crowding penalty applied")

        bucket_cap = max(bucket_caps.get(engine, 0.0), 0.0)
        bucket_remaining = max(bucket_cap - bucket_risk_used.get(engine, 0.0), 0.0)
        portfolio_remaining = max(total_risk_cap - current_active_risk_pct - portfolio_risk_used, 0.0)
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

        alt_seed_exception_applied = _allow_single_alt_seed_without_majors(
            entry_profile_name=str(getattr(app_config.entry_profile, "name", "")).strip().lower(),
            engine=engine,
            side=side,
            sector=sector,
            open_positions_count=len(open_positions),
            major_risk=major_risk,
            alt_risk=alt_risk,
            current_active_risk_pct=current_active_risk_pct,
            portfolio_risk_used=portfolio_risk_used,
        )
        if alt_seed_exception_applied:
            seed_cap = min(allowed_budget, _alt_seed_risk_cap(total_risk_cap))
            meta["alt_seed_exception_applied"] = True
            meta["active_paper_first_rotation_probe"] = True
            meta["alt_seed_risk_cap"] = round(seed_cap, 6)
            if final_budget > seed_cap:
                final_budget = seed_cap
                downsized = True
                reasons.append("active-paper first rotation probe cap applied")

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
        if not major_alt_ok and alt_seed_exception_applied:
            major_alt_ok = True
            reasons.append("active-paper first rotation probe allowed without prior major exposure")
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
