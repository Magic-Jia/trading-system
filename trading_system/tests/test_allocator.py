from trading_system.app.config import DEFAULT_CONFIG
from trading_system.app.risk.regime_risk import scaled_risk_budget
from trading_system.app.portfolio.exposure import exposure_snapshot
from trading_system.app.portfolio.allocator import allocate_candidates
import pytest


def _flat_account(load_fixture):
    account = load_fixture("account_snapshot_v2.json")
    account["open_positions"] = []
    account["open_orders"] = []
    return account


def _seeded_major_account(load_fixture, *, notional: float = 1000.0):
    account = _flat_account(load_fixture)
    account["open_positions"] = [
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "qty": round(notional / 64000.0, 8),
            "entry_price": 64000.0,
            "mark_price": 64000.0,
            "notional": notional,
        }
    ]
    return account


def test_scaled_risk_budget_respects_engine_tier_and_regime_confidence():
    budget = scaled_risk_budget(base_risk_pct=0.008, regime_multiplier=0.5, confidence=0.4)
    assert budget < 0.008


def test_exposure_snapshot_summarizes_sector_and_direction(load_fixture):
    account = load_fixture("account_snapshot_v2.json")
    snapshot = exposure_snapshot(account)
    assert "net_long_notional" in snapshot
    assert "sector_risk" in snapshot


def test_allocator_rejects_candidates_from_suppressed_bucket(load_fixture, sample_rotation_candidates):
    account = _flat_account(load_fixture)
    regime = {"suppressed_engines": ["rotation"], "bucket_targets": {"trend": 1.0, "rotation": 0.0, "short": 0.0}}
    decisions = allocate_candidates(account=account, candidates=sample_rotation_candidates, regime=regime)
    assert decisions
    assert all(d.status == "REJECTED" for d in decisions)
    assert all("suppressed" in " ".join(d.reasons).lower() for d in decisions)


def test_allocator_accepts_rotation_candidates_when_bucket_enabled(load_fixture, sample_rotation_candidates):
    account = _seeded_major_account(load_fixture)
    regime = {"bucket_targets": {"trend": 0.4, "rotation": 0.6, "short": 0.0}, "suppressed_engines": []}
    decisions = allocate_candidates(account=account, candidates=sample_rotation_candidates, regime=regime)
    accepted = [decision for decision in decisions if decision.status in {"ACCEPTED", "DOWNSIZED"}]
    assert accepted
    assert all(decision.engine == "rotation" for decision in accepted)
    assert all(decision.final_risk_budget > 0 for decision in accepted)


def test_allocator_downweights_duplicate_trend_breakouts(load_fixture, sample_trend_candidates):
    account = _flat_account(load_fixture)
    regime = {"bucket_targets": {"trend": 0.6, "rotation": 0.2, "short": 0.2}, "suppressed_engines": []}
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates, regime=regime)
    assert any(d.status in {"ACCEPTED", "DOWNSIZED"} for d in decisions)
    assert any(d.status == "DOWNSIZED" for d in decisions[1:])


def test_allocator_respects_total_active_risk_cap(load_fixture, sample_trend_candidates):
    account = load_fixture("account_snapshot_v2.json")
    regime = {"bucket_targets": {"trend": 0.9, "rotation": 0.1, "short": 0.0}, "suppressed_engines": []}
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates, regime=regime)
    accepted_risk = sum(d.final_risk_budget for d in decisions if d.status in {"ACCEPTED", "DOWNSIZED"})
    assert accepted_risk <= decisions[0].meta["portfolio_total_risk_cap"]


def test_allocator_respects_net_exposure_and_major_alt_balance(load_fixture, sample_trend_candidates):
    account = load_fixture("account_snapshot_v2.json")
    regime = {"bucket_targets": {"trend": 0.5, "rotation": 0.0, "short": 0.0}, "suppressed_engines": ["rotation", "short"]}
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates, regime=regime)
    accepted = [d for d in decisions if d.status in {"ACCEPTED", "DOWNSIZED"}]
    assert all(d.engine == "trend" for d in accepted)
    assert all(d.meta["net_exposure_after"] <= d.meta["net_exposure_cap"] for d in accepted)
    assert all(d.meta["major_alt_balance_ok"] is True for d in accepted)


def test_allocator_enforces_symbol_and_sector_caps(load_fixture, sample_trend_candidates):
    account = _seeded_major_account(load_fixture)
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates)
    assert any(d.meta.get("symbol_cap_checked") for d in decisions)
    assert any(d.meta.get("sector_cap_checked") for d in decisions)
    assert any(d.status == "REJECTED" and (d.meta.get("symbol_cap_hit") or d.meta.get("sector_cap_hit")) for d in decisions)


def test_allocator_checks_conflict_against_existing_exposure(load_fixture, sample_trend_candidates):
    account = load_fixture("account_snapshot_v2.json")
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates)
    assert any("existing exposure" in " ".join(d.reasons).lower() or d.meta.get("conflict_checked") for d in decisions)


def test_allocator_blocks_when_open_position_limit_already_reached(load_fixture):
    account = load_fixture("account_snapshot_v2.json")
    account["open_positions"] = list(account["open_positions"]) + [
        {
            "symbol": "ADAUSDT",
            "side": "LONG",
            "qty": 1000,
            "entry_price": 0.8,
            "mark_price": 0.82,
            "notional": 820.0,
        },
        {
            "symbol": "LINKUSDT",
            "side": "LONG",
            "qty": 200,
            "entry_price": 15.0,
            "mark_price": 15.5,
            "notional": 3100.0,
        },
        {
            "symbol": "XLMUSDT",
            "side": "LONG",
            "qty": 4000,
            "entry_price": 0.12,
            "mark_price": 0.125,
            "notional": 500.0,
        },
        {
            "symbol": "ATOMUSDT",
            "side": "LONG",
            "qty": 150,
            "entry_price": 10.0,
            "mark_price": 10.4,
            "notional": 1560.0,
        },
        {
            "symbol": "DOGEUSDT",
            "side": "LONG",
            "qty": 10000,
            "entry_price": 0.18,
            "mark_price": 0.185,
            "notional": 1850.0,
        },
    ]
    candidates = [
        {
            "engine": "trend",
            "setup_type": "BREAKOUT",
            "symbol": "XRPUSDT",
            "side": "LONG",
            "score": 0.91,
            "sector": "payments",
        }
    ]
    decisions = allocate_candidates(account=account, candidates=candidates)

    assert decisions
    assert all(d.status == "REJECTED" for d in decisions)
    assert all("持仓数" in " ".join(d.reasons) or d.meta.get("open_positions_limit_hit") for d in decisions)


def test_allocator_blocks_when_account_total_risk_is_already_over_cap(load_fixture):
    account = load_fixture("account_snapshot_v2.json")
    candidates = [
        {
            "engine": "trend",
            "setup_type": "BREAKOUT",
            "symbol": "XRPUSDT",
            "side": "LONG",
            "score": 0.91,
            "sector": "payments",
        }
    ]

    decisions = allocate_candidates(account=account, candidates=candidates)

    assert decisions
    assert all(d.status == "REJECTED" for d in decisions)
    assert all("总风险暴露" in " ".join(d.reasons) or d.meta.get("account_total_risk_limit_hit") for d in decisions)


def test_allocator_blocks_when_existing_sector_risk_already_exceeds_cap():
    account = {
        "equity": 100000.0,
        "available_balance": 60000.0,
        "futures_wallet_balance": 100000.0,
        "open_positions": [
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.03,
                "entry_price": 65000.0,
                "mark_price": 67000.0,
                "notional": 2010.0,
            }
        ],
    }
    candidates = [
        {
            "engine": "trend",
            "setup_type": "BREAKOUT",
            "symbol": "ETHUSDT",
            "side": "LONG",
            "score": 0.85,
            "sector": "majors",
        }
    ]

    decisions = allocate_candidates(account=account, candidates=candidates)

    assert decisions
    assert all(d.status == "REJECTED" for d in decisions)
    assert all("sector risk" in " ".join(d.reasons).lower() or d.meta.get("sector_cap_hit") for d in decisions)


def test_allocator_uses_remaining_account_risk_headroom(load_fixture):
    account = load_fixture("account_snapshot_v2.json")
    account["open_positions"] = [
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "qty": 0.04328358,
            "entry_price": 67000.0,
            "mark_price": 67000.0,
            "notional": 2900.0,
        }
    ]
    candidates = [
        {
            "engine": "trend",
            "setup_type": "BREAKOUT",
            "symbol": "DOGEUSDT",
            "side": "LONG",
            "score": 0.91,
            "sector": "payments",
        }
    ]

    decisions = allocate_candidates(account=account, candidates=candidates)

    assert decisions
    accepted = [d for d in decisions if d.status in {"ACCEPTED", "DOWNSIZED"}]
    assert accepted
    current_active_risk = exposure_snapshot(account)["active_risk_pct"]
    expected_remaining = max(DEFAULT_CONFIG.risk.max_total_risk_pct - current_active_risk, 0.0)
    assert accepted[0].final_risk_budget <= expected_remaining + 1e-9


def test_allocator_sizes_stronger_rotation_candidate_more_aggressively_when_other_risk_is_similar(load_fixture):
    account = _seeded_major_account(load_fixture)
    regime = {"bucket_targets": {"trend": 0.35, "rotation": 0.65, "short": 0.0}, "suppressed_engines": []}
    candidates = [
        {
            "engine": "rotation",
            "setup_type": "RS_REACCELERATION",
            "symbol": "LINKUSDT",
            "side": "LONG",
            "score": 0.91,
            "sector": "oracle",
            "timeframe_meta": {"derivatives": {"crowding_bias": "balanced", "basis_bps": 6.0, "funding_rate": 0.00002}},
            "liquidity_meta": {"spread_bps": 1.2, "slippage_bps": 5.0, "volume_usdt_24h": 1_250_000_000.0},
        },
        {
            "engine": "rotation",
            "setup_type": "RS_REACCELERATION",
            "symbol": "AAVEUSDT",
            "side": "LONG",
            "score": 0.73,
            "sector": "defi",
            "timeframe_meta": {"derivatives": {"crowding_bias": "balanced", "basis_bps": 6.0, "funding_rate": 0.00002}},
            "liquidity_meta": {"spread_bps": 1.2, "slippage_bps": 5.0, "volume_usdt_24h": 1_250_000_000.0},
        },
    ]

    ranked_candidates = sorted(candidates, key=lambda row: (-float(row["score"]), str(row["symbol"]), str(row["engine"])))
    decisions = allocate_candidates(account=account, candidates=candidates, regime=regime)
    accepted = {
        candidate["symbol"]: decision
        for candidate, decision in zip(ranked_candidates, decisions)
        if decision.status in {"ACCEPTED", "DOWNSIZED"}
    }

    assert set(accepted) == {"LINKUSDT", "AAVEUSDT"}
    assert accepted["LINKUSDT"].final_risk_budget > accepted["AAVEUSDT"].final_risk_budget
    assert accepted["LINKUSDT"].meta["aggressiveness_multiplier"] > accepted["AAVEUSDT"].meta["aggressiveness_multiplier"]
    assert accepted["LINKUSDT"].meta["quality_multiplier"] > accepted["AAVEUSDT"].meta["quality_multiplier"]


def test_allocator_crowding_and_execution_friction_can_compress_aggressiveness_without_rejecting_candidate(load_fixture):
    account = _seeded_major_account(load_fixture)
    regime = {"bucket_targets": {"trend": 0.35, "rotation": 0.65, "short": 0.0}, "suppressed_engines": []}
    candidates = [
        {
            "engine": "rotation",
            "setup_type": "RS_REACCELERATION",
            "symbol": "SOLUSDT",
            "side": "LONG",
            "score": 0.84,
            "sector": "alt_l1",
            "timeframe_meta": {
                "derivatives": {
                    "crowding_bias": "balanced",
                    "crowding_score": 1.0,
                    "basis_bps": 8.0,
                    "funding_rate": 0.00003,
                }
            },
            "liquidity_meta": {"spread_bps": 1.4, "slippage_bps": 6.0, "volume_usdt_24h": 1_600_000_000.0},
        },
        {
            "engine": "rotation",
            "setup_type": "RS_REACCELERATION",
            "symbol": "AVAXUSDT",
            "side": "LONG",
            "score": 0.84,
            "sector": "gaming",
            "timeframe_meta": {
                "derivatives": {
                    "crowding_bias": "crowded_long",
                    "crowding_score": 3.0,
                    "basis_bps": 18.0,
                    "funding_rate": 0.00012,
                }
            },
            "liquidity_meta": {"spread_bps": 5.2, "slippage_bps": 16.0, "volume_usdt_24h": 720_000_000.0},
        },
    ]

    ranked_candidates = sorted(candidates, key=lambda row: (-float(row["score"]), str(row["symbol"]), str(row["engine"])))
    decisions = allocate_candidates(account=account, candidates=candidates, regime=regime)
    accepted = {
        candidate["symbol"]: decision
        for candidate, decision in zip(ranked_candidates, decisions)
        if decision.status in {"ACCEPTED", "DOWNSIZED"}
    }

    assert set(accepted) == {"SOLUSDT", "AVAXUSDT"}
    assert accepted["AVAXUSDT"].final_risk_budget < accepted["SOLUSDT"].final_risk_budget
    assert accepted["AVAXUSDT"].meta["crowding_multiplier"] < accepted["SOLUSDT"].meta["crowding_multiplier"]
    assert accepted["AVAXUSDT"].meta["execution_friction_multiplier"] < accepted["SOLUSDT"].meta["execution_friction_multiplier"]
    assert accepted["AVAXUSDT"].meta["aggressiveness_multiplier"] < 1.0
    assert any("crowding" in reason.lower() or "friction" in reason.lower() for reason in accepted["AVAXUSDT"].reasons)


def test_allocator_compresses_risk_budget_under_defensive_execution_hazard(load_fixture):
    account = _seeded_major_account(load_fixture)
    candidates = [
        {
            "engine": "rotation",
            "setup_type": "RS_REACCELERATION",
            "symbol": "LINKUSDT",
            "side": "LONG",
            "score": 0.9,
            "sector": "oracle",
            "timeframe_meta": {"derivatives": {"crowding_bias": "balanced", "basis_bps": 6.0, "funding_rate": 0.00002}},
            "liquidity_meta": {"spread_bps": 1.2, "slippage_bps": 5.0, "volume_usdt_24h": 1_250_000_000.0},
        }
    ]
    base_regime = {
        "bucket_targets": {"trend": 0.35, "rotation": 0.65, "short": 0.0},
        "suppressed_engines": [],
        "confidence": 0.78,
        "risk_multiplier": 0.8,
        "execution_hazard": "none",
        "late_stage_heat": "none",
    }
    defensive_regime = {
        **base_regime,
        "execution_hazard": "compress_risk",
        "execution_policy": "downsize",
    }

    base = allocate_candidates(account=account, candidates=candidates, regime=base_regime)[0]
    defensive = allocate_candidates(account=account, candidates=candidates, regime=defensive_regime)[0]

    assert base.status in {"ACCEPTED", "DOWNSIZED"}
    assert defensive.status in {"ACCEPTED", "DOWNSIZED"}
    assert defensive.final_risk_budget < base.final_risk_budget
    assert defensive.meta["aggressiveness_multiplier"] < base.meta["aggressiveness_multiplier"]
    assert defensive.meta["regime_hazard_multiplier"] < 1.0
    assert any("hazard" in reason.lower() or "defensive" in reason.lower() for reason in defensive.reasons)


def test_allocator_compresses_long_risk_budget_under_late_stage_heat(load_fixture):
    account = _seeded_major_account(load_fixture)
    candidates = [
        {
            "engine": "rotation",
            "setup_type": "RS_REACCELERATION",
            "symbol": "SOLUSDT",
            "side": "LONG",
            "score": 0.88,
            "sector": "alt_l1",
            "timeframe_meta": {"derivatives": {"crowding_bias": "balanced", "basis_bps": 7.0, "funding_rate": 0.00003}},
            "liquidity_meta": {"spread_bps": 1.3, "slippage_bps": 5.0, "volume_usdt_24h": 1_500_000_000.0},
        }
    ]
    base_regime = {
        "bucket_targets": {"trend": 0.35, "rotation": 0.65, "short": 0.0},
        "suppressed_engines": [],
        "confidence": 0.82,
        "risk_multiplier": 0.78,
        "late_stage_heat": "none",
        "execution_hazard": "none",
    }
    heated_regime = {
        **base_regime,
        "late_stage_heat": "squeeze",
    }

    base = allocate_candidates(account=account, candidates=candidates, regime=base_regime)[0]
    heated = allocate_candidates(account=account, candidates=candidates, regime=heated_regime)[0]

    assert base.status in {"ACCEPTED", "DOWNSIZED"}
    assert heated.status in {"ACCEPTED", "DOWNSIZED"}
    assert heated.final_risk_budget < base.final_risk_budget
    assert heated.meta["aggressiveness_multiplier"] < base.meta["aggressiveness_multiplier"]
    assert heated.meta["late_stage_heat_multiplier"] < 1.0
    assert any("late-stage" in reason.lower() or "heat" in reason.lower() for reason in heated.reasons)
