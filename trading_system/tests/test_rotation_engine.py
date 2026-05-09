import pytest

from trading_system.app.signals.entry_profile import ACTIVE_PAPER_ENTRY_PROFILE
from trading_system.app.signals.rotation_engine import generate_rotation_candidates
from trading_system.app.signals.scoring import score_rotation_candidate


def _set_h1_extension(market: dict, symbol: str, extension_pct: float) -> None:
    h1 = market["symbols"][symbol]["1h"]
    h1["ema_20"] = h1["close"] / (1.0 + extension_pct)


def test_score_rotation_candidate_rewards_leadership_persistence_and_liquidity():
    candidate = {
        "relative_strength_rank": 0.92,
        "persistence": 0.88,
        "pullback_quality": 0.72,
        "liquidity_quality": 0.81,
        "volatility_quality": 0.67,
    }
    score = score_rotation_candidate(candidate)
    assert score["total"] > 0
    assert score["components"]["relative_strength_rank"] > score["components"]["pullback_quality"]
    assert score["components"]["persistence"] > 0


def test_generate_rotation_candidates_uses_rotation_universe_and_emits_relative_strength_setups(load_fixture):
    market = load_fixture("market_context_v2.json")
    rotation_universe = [
        {"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
        {"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high"},
        {"symbol": "ADAUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
        {"symbol": "DOGEUSDT", "sector": "memes", "liquidity_tier": "medium"},
    ]
    _set_h1_extension(market, "SOLUSDT", 0.007459)
    _set_h1_extension(market, "LINKUSDT", 0.007459)

    candidates = generate_rotation_candidates(market, rotation_universe=rotation_universe)

    assert candidates
    assert {candidate.engine for candidate in candidates} == {"rotation"}
    assert {candidate.setup_type for candidate in candidates} <= {"RS_PULLBACK", "RS_REACCELERATION"}
    assert {candidate.symbol for candidate in candidates} == {"SOLUSDT", "LINKUSDT", "ADAUSDT"}
    assert all(candidate.sector != "majors" for candidate in candidates)


def test_generate_rotation_candidates_emit_explicit_stop_loss_and_invalidation_source(load_fixture):
    market = load_fixture("market_context_v2.json")
    rotation_universe = [
        {"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
        {"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high"},
        {"symbol": "ADAUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
    ]

    candidates = generate_rotation_candidates(market, rotation_universe=rotation_universe)

    assert candidates
    for candidate in candidates:
        assert candidate.stop_loss > 0
        assert candidate.stop_loss < market["symbols"][candidate.symbol]["daily"]["close"]
        assert candidate.invalidation_source == "rotation_pullback_failure_below_1h_ema50"


def test_generate_rotation_candidates_rejects_overheated_crowded_leader(load_fixture):
    market = load_fixture("market_context_v2.json")
    rotation_universe = [
        {"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
        {"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high"},
        {"symbol": "ADAUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
    ]
    derivatives = {
        "rows": [
            {
                "symbol": "SOLUSDT",
                "funding_rate": 0.00024,
                "open_interest_usdt": 2900000000,
                "open_interest_change_24h_pct": 0.059,
                "mark_price_change_24h_pct": 0.026,
                "taker_buy_sell_ratio": 1.11,
                "basis_bps": 31,
            },
            {
                "symbol": "LINKUSDT",
                "funding_rate": 0.00004,
                "open_interest_usdt": 1750000000,
                "open_interest_change_24h_pct": 0.01,
                "mark_price_change_24h_pct": 0.012,
                "taker_buy_sell_ratio": 1.01,
                "basis_bps": 12,
            },
            {
                "symbol": "ADAUSDT",
                "funding_rate": 0.00021,
                "open_interest_usdt": 1250000000,
                "open_interest_change_24h_pct": 0.041,
                "mark_price_change_24h_pct": 0.018,
                "taker_buy_sell_ratio": 1.04,
                "basis_bps": 24,
            },
        ]
    }
    _set_h1_extension(market, "SOLUSDT", 0.007459)
    _set_h1_extension(market, "LINKUSDT", 0.007459)

    candidates = generate_rotation_candidates(
        market,
        rotation_universe=rotation_universe,
        derivatives=derivatives,
    )

    assert {candidate.symbol for candidate in candidates} == {"LINKUSDT"}


def test_generate_rotation_candidates_reject_funding_basis_blowoff_even_when_strength_extension_and_relative_strength_pass(load_fixture):
    market = load_fixture("market_context_v2.json")
    rotation_universe = [
        {"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
        {"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high"},
    ]
    derivatives = {
        "rows": [
            {
                "symbol": "SOLUSDT",
                "funding_rate": 0.00022,
                "open_interest_usdt": 2900000000,
                "open_interest_change_24h_pct": 0.01,
                "mark_price_change_24h_pct": 0.012,
                "taker_buy_sell_ratio": 1.0,
                "basis_bps": 26,
            },
            {
                "symbol": "LINKUSDT",
                "funding_rate": 0.00003,
                "open_interest_usdt": 1750000000,
                "open_interest_change_24h_pct": 0.009,
                "mark_price_change_24h_pct": 0.008,
                "taker_buy_sell_ratio": 1.0,
                "basis_bps": 10,
            },
        ]
    }
    _set_h1_extension(market, "SOLUSDT", 0.007459)
    _set_h1_extension(market, "LINKUSDT", 0.007459)

    candidates = generate_rotation_candidates(
        market,
        rotation_universe=rotation_universe,
        derivatives=derivatives,
    )

    assert {candidate.symbol for candidate in candidates} == {"LINKUSDT"}


def test_generate_rotation_candidates_require_absolute_strength_alongside_relative_strength(load_fixture):
    market = load_fixture("market_context_v2.json")
    rotation_universe = [
        {"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high"},
        {"symbol": "ADAUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
    ]
    derivatives = {
        "rows": [
            {
                "symbol": "LINKUSDT",
                "funding_rate": 0.00004,
                "open_interest_usdt": 1750000000,
                "open_interest_change_24h_pct": 0.01,
                "mark_price_change_24h_pct": 0.012,
                "taker_buy_sell_ratio": 1.01,
                "basis_bps": 12,
            },
            {
                "symbol": "ADAUSDT",
                "funding_rate": 0.00003,
                "open_interest_usdt": 1250000000,
                "open_interest_change_24h_pct": 0.009,
                "mark_price_change_24h_pct": 0.006,
                "taker_buy_sell_ratio": 1.0,
                "basis_bps": 10,
            },
        ]
    }

    market["symbols"]["BTCUSDT"]["daily"]["return_pct_7d"] = 0.008
    market["symbols"]["BTCUSDT"]["4h"]["return_pct_3d"] = 0.004
    market["symbols"]["BTCUSDT"]["1h"]["return_pct_24h"] = 0.001
    market["symbols"]["ETHUSDT"]["daily"]["return_pct_7d"] = 0.007
    market["symbols"]["ETHUSDT"]["4h"]["return_pct_3d"] = 0.003
    market["symbols"]["ETHUSDT"]["1h"]["return_pct_24h"] = 0.001

    market["symbols"]["ADAUSDT"]["daily"]["return_pct_7d"] = 0.012
    market["symbols"]["ADAUSDT"]["4h"]["return_pct_3d"] = 0.004
    market["symbols"]["ADAUSDT"]["1h"]["return_pct_24h"] = 0.0015
    _set_h1_extension(market, "LINKUSDT", 0.007459)

    candidates = generate_rotation_candidates(
        market,
        rotation_universe=rotation_universe,
        derivatives=derivatives,
    )

    assert {candidate.symbol for candidate in candidates} == {"LINKUSDT"}


def _modest_relative_strength_rotation_market() -> dict[str, object]:
    return {
        "symbols": {
            "BTCUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {"close": 100.0, "ema_20": 99.0, "ema_50": 98.0, "atr_pct": 0.035, "return_pct_7d": 0.002, "volume_usdt_24h": 20_000_000_000},
                "4h": {"close": 100.0, "ema_20": 99.5, "ema_50": 98.5, "return_pct_3d": 0.001, "volume_usdt_24h": 20_000_000_000},
                "1h": {"close": 100.0, "ema_20": 99.6, "ema_50": 99.0, "return_pct_24h": 0.0002, "volume_usdt_24h": 20_000_000_000},
            },
            "ETHUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {"close": 100.0, "ema_20": 99.1, "ema_50": 98.2, "atr_pct": 0.036, "return_pct_7d": 0.001, "volume_usdt_24h": 12_000_000_000},
                "4h": {"close": 100.0, "ema_20": 99.4, "ema_50": 98.6, "return_pct_3d": 0.0005, "volume_usdt_24h": 12_000_000_000},
                "1h": {"close": 100.0, "ema_20": 99.5, "ema_50": 99.1, "return_pct_24h": 0.0001, "volume_usdt_24h": 12_000_000_000},
            },
            "LINKUSDT": {
                "sector": "oracle",
                "liquidity_tier": "high",
                "daily": {"close": 103.0, "ema_20": 101.0, "ema_50": 99.0, "atr_pct": 0.055, "return_pct_7d": 0.014, "volume_usdt_24h": 1_500_000_000},
                "4h": {"close": 103.0, "ema_20": 102.0, "ema_50": 100.0, "return_pct_3d": 0.005, "volume_usdt_24h": 1_500_000_000},
                "1h": {"close": 103.0, "ema_20": 102.4, "ema_50": 101.5, "return_pct_24h": 0.0015, "volume_usdt_24h": 1_500_000_000},
            },
        }
    }


def test_generate_rotation_candidates_keep_default_absolute_strength_gate_conservative_for_modest_relative_strength():
    candidates = generate_rotation_candidates(
        _modest_relative_strength_rotation_market(),
        rotation_universe=[{"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high"}],
    )

    assert candidates == []


def test_generate_rotation_candidates_active_paper_profile_allows_modest_relative_strength():
    candidates = generate_rotation_candidates(
        _modest_relative_strength_rotation_market(),
        rotation_universe=[{"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high"}],
        entry_profile=ACTIVE_PAPER_ENTRY_PROFILE,
    )

    assert [candidate.symbol for candidate in candidates] == ["LINKUSDT"]
    assert candidates[0].stop_loss > 0


def _active_paper_soft_relative_strength_rotation_market() -> dict[str, object]:
    return {
        "symbols": {
            "BTCUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {"close": 100.0, "ema_20": 99.2, "ema_50": 98.5, "atr_pct": 0.032, "return_pct_7d": 0.003, "volume_usdt_24h": 20_000_000_000},
                "4h": {"close": 100.0, "ema_20": 99.4, "ema_50": 98.7, "return_pct_3d": 0.001, "volume_usdt_24h": 20_000_000_000},
                "1h": {"close": 100.0, "ema_20": 99.6, "ema_50": 99.0, "return_pct_24h": 0.0002, "volume_usdt_24h": 20_000_000_000},
            },
            "ETHUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {"close": 101.0, "ema_20": 100.1, "ema_50": 99.4, "atr_pct": 0.033, "return_pct_7d": 0.002, "volume_usdt_24h": 12_000_000_000},
                "4h": {"close": 101.0, "ema_20": 100.3, "ema_50": 99.7, "return_pct_3d": 0.0008, "volume_usdt_24h": 12_000_000_000},
                "1h": {"close": 101.0, "ema_20": 100.5, "ema_50": 100.0, "return_pct_24h": 0.0002, "volume_usdt_24h": 12_000_000_000},
            },
            "SOLUSDT": {
                "sector": "alt_l1",
                "liquidity_tier": "high",
                "daily": {"close": 100.8, "ema_20": 100.0, "ema_50": 101.5, "atr_pct": 0.052, "return_pct_7d": 0.018, "volume_usdt_24h": 2_500_000_000},
                "4h": {"close": 101.2, "ema_20": 100.4, "ema_50": 101.6, "return_pct_3d": 0.006, "volume_usdt_24h": 2_500_000_000},
                "1h": {"close": 101.3, "ema_20": 100.8, "ema_50": 101.0, "return_pct_24h": 0.0018, "volume_usdt_24h": 2_500_000_000},
            },
        }
    }


def test_generate_rotation_candidates_active_paper_allows_soft_relative_strength_leader_only_for_profile():
    market = _active_paper_soft_relative_strength_rotation_market()
    universe = [
        {
            "symbol": "SOLUSDT",
            "sector": "alt_l1",
            "liquidity_tier": "high",
            "liquidity_meta": {"rolling_notional": 2_200_000_000, "slippage_bps": 4.0},
        }
    ]
    regime = {"label": "RISK_ON_ROTATION", "suppression_rules": []}

    default_candidates = generate_rotation_candidates(market, rotation_universe=universe, regime=regime)
    active_candidates = generate_rotation_candidates(
        market,
        rotation_universe=universe,
        regime=regime,
        entry_profile=ACTIVE_PAPER_ENTRY_PROFILE,
    )

    assert default_candidates == []
    assert [candidate.symbol for candidate in active_candidates] == ["SOLUSDT"]
    assert active_candidates[0].stop_loss > 0
    assert active_candidates[0].timeframe_meta["h4_structure"] == "active_paper_soft_reclaim"


def _active_paper_current_market_style_rotation_market() -> dict[str, object]:
    market = _active_paper_soft_relative_strength_rotation_market()
    market["symbols"]["BTCUSDT"]["4h"]["return_pct_3d"] = -0.02
    market["symbols"]["ETHUSDT"]["4h"]["return_pct_3d"] = -0.018
    market["symbols"]["BNBUSDT"] = {
        "sector": "exchange",
        "liquidity_tier": "high",
        "daily": {"close": 638.0, "ema_20": 626.0, "ema_50": 629.0, "atr_pct": 0.026, "return_pct_7d": 0.013, "volume_usdt_24h": 40_000_000},
        "4h": {"close": 638.0, "ema_20": 636.0, "ema_50": 633.0, "return_pct_3d": -0.006, "volume_usdt_24h": 40_000_000},
        "1h": {"close": 638.0, "ema_20": 637.0, "ema_50": 636.0, "return_pct_24h": 0.002, "volume_usdt_24h": 40_000_000},
    }
    return market


def test_generate_rotation_candidates_active_paper_allows_current_market_style_relative_pullback_only_for_profile():
    market = _active_paper_current_market_style_rotation_market()
    universe = [
        {
            "symbol": "BNBUSDT",
            "sector": "exchange",
            "liquidity_tier": "high",
            "liquidity_meta": {"rolling_notional": 200_000_000, "slippage_bps": 5.0},
        }
    ]
    regime = {"label": "MIXED", "suppression_rules": []}

    default_candidates = generate_rotation_candidates(market, rotation_universe=universe, regime=regime)
    active_candidates = generate_rotation_candidates(
        market,
        rotation_universe=universe,
        regime=regime,
        entry_profile=ACTIVE_PAPER_ENTRY_PROFILE,
    )

    assert default_candidates == []
    assert [candidate.symbol for candidate in active_candidates] == ["BNBUSDT"]
    assert active_candidates[0].stop_loss > 0
    assert active_candidates[0].timeframe_meta["h4_structure"] == "active_paper_soft_reclaim"


def test_generate_rotation_candidates_reject_overextended_longs_even_when_absolute_strength_is_high(load_fixture):
    market = load_fixture("market_context_v2.json")
    rotation_universe = [
        {"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
        {"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high"},
    ]
    derivatives = {
        "rows": [
            {
                "symbol": "SOLUSDT",
                "funding_rate": 0.00004,
                "open_interest_usdt": 2900000000,
                "open_interest_change_24h_pct": 0.01,
                "mark_price_change_24h_pct": 0.018,
                "taker_buy_sell_ratio": 1.01,
                "basis_bps": 12,
            },
            {
                "symbol": "LINKUSDT",
                "funding_rate": 0.00003,
                "open_interest_usdt": 1750000000,
                "open_interest_change_24h_pct": 0.009,
                "mark_price_change_24h_pct": 0.008,
                "taker_buy_sell_ratio": 1.0,
                "basis_bps": 10,
            },
        ]
    }
    _set_h1_extension(market, "SOLUSDT", 0.013366)
    _set_h1_extension(market, "LINKUSDT", 0.007459)

    market["symbols"]["SOLUSDT"]["4h"]["close"] = 155.0
    market["symbols"]["SOLUSDT"]["1h"]["close"] = 153.0

    candidates = generate_rotation_candidates(
        market,
        rotation_universe=rotation_universe,
        derivatives=derivatives,
    )

    assert {candidate.symbol for candidate in candidates} == {"LINKUSDT"}


def test_generate_rotation_candidates_respects_regime_suppression(load_fixture):
    market = load_fixture("market_context_v2.json")
    candidates = generate_rotation_candidates(
        market,
        rotation_universe=[
            {"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
            {"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high"},
        ],
        regime={"suppression_rules": ["rotation"]},
    )

    assert candidates == []


def test_generate_rotation_candidates_rejects_non_string_suppression_rule(load_fixture):
    market = load_fixture("market_context_v2.json")

    with pytest.raises(ValueError, match="regime.suppression_rules\\[0\\]"):
        generate_rotation_candidates(
            market,
            rotation_universe=[
                {"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
            ],
            regime={"suppression_rules": [True]},
        )


def _soft_rotation_reclaim_market(
    *,
    sol_daily_close: float = 103.0,
    sol_daily_ema50: float = 101.0,
    sol_h4_return_pct: float = 0.018,
    sol_h1_return_pct: float = 0.004,
    sol_h1_ema20: float = 103.2,
) -> dict[str, object]:
    return {
        "symbols": {
            "BTCUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {"close": 100.0, "ema_20": 99.2, "ema_50": 98.5, "atr_pct": 0.032, "return_pct_7d": 0.012, "volume_usdt_24h": 20_000_000_000},
                "4h": {"close": 100.0, "ema_20": 99.4, "ema_50": 98.7, "return_pct_3d": 0.004, "volume_usdt_24h": 20_000_000_000},
                "1h": {"close": 100.0, "ema_20": 99.6, "ema_50": 99.0, "return_pct_24h": 0.001, "volume_usdt_24h": 20_000_000_000},
            },
            "ETHUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {"close": 101.0, "ema_20": 100.1, "ema_50": 99.4, "atr_pct": 0.033, "return_pct_7d": 0.011, "volume_usdt_24h": 12_000_000_000},
                "4h": {"close": 101.0, "ema_20": 100.3, "ema_50": 99.7, "return_pct_3d": 0.003, "volume_usdt_24h": 12_000_000_000},
                "1h": {"close": 101.0, "ema_20": 100.5, "ema_50": 100.0, "return_pct_24h": 0.001, "volume_usdt_24h": 12_000_000_000},
            },
            "SOLUSDT": {
                "sector": "alt_l1",
                "liquidity_tier": "high",
                "daily": {"close": sol_daily_close, "ema_20": 100.0, "ema_50": sol_daily_ema50, "atr_pct": 0.05, "return_pct_7d": 0.045, "volume_usdt_24h": 2_400_000_000},
                "4h": {"close": 104.0, "ema_20": 103.0, "ema_50": 102.0, "return_pct_3d": sol_h4_return_pct, "volume_usdt_24h": 2_400_000_000},
                "1h": {"close": 104.0, "ema_20": sol_h1_ema20, "ema_50": 102.5, "return_pct_24h": sol_h1_return_pct, "volume_usdt_24h": 2_400_000_000},
            },
        }
    }


def test_generate_rotation_candidates_allows_soft_daily_reclaim_in_risk_on_rotation():
    candidates = generate_rotation_candidates(
        _soft_rotation_reclaim_market(),
        rotation_universe=[{"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"}],
        regime={"label": "RISK_ON_ROTATION", "suppression_rules": []},
    )

    assert [candidate.symbol for candidate in candidates] == ["SOLUSDT"]


@pytest.mark.parametrize("invalid_symbol", [True, 123])
def test_generate_rotation_candidates_rejects_present_non_string_universe_symbol(invalid_symbol: object):
    with pytest.raises(ValueError, match=r"rotation_universe\.symbol"):
        generate_rotation_candidates(
            _soft_rotation_reclaim_market(),
            rotation_universe=[{"symbol": invalid_symbol, "sector": "alt_l1", "liquidity_tier": "high"}],
            regime={"label": "RISK_ON_ROTATION", "suppression_rules": []},
        )


def test_generate_rotation_candidates_rejects_present_string_numeric_required_timeframe_field():
    market = _soft_rotation_reclaim_market()
    universe = [{"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"}]
    regime = {"label": "RISK_ON_ROTATION", "suppression_rules": []}

    assert [
        candidate.symbol
        for candidate in generate_rotation_candidates(
            market,
            rotation_universe=universe,
            regime=regime,
        )
    ] == ["SOLUSDT"]

    market["symbols"]["SOLUSDT"]["daily"]["close"] = "103.0"

    with pytest.raises(ValueError, match=r"SOLUSDT\.daily\.close"):
        generate_rotation_candidates(
            market,
            rotation_universe=universe,
            regime=regime,
        )


@pytest.mark.parametrize(
    ("timeframe", "field", "invalid_value"),
    [
        ("daily", "close", True),
        ("4h", "ema_20", float("nan")),
        ("1h", "return_pct_24h", float("inf")),
        ("daily", "atr_pct", float("-inf")),
    ],
)
def test_generate_rotation_candidates_rejects_present_invalid_required_timeframe_numerics(
    timeframe: str,
    field: str,
    invalid_value: object,
):
    market = _soft_rotation_reclaim_market()
    universe = [{"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"}]
    regime = {"label": "RISK_ON_ROTATION", "suppression_rules": []}

    assert [
        candidate.symbol
        for candidate in generate_rotation_candidates(
            market,
            rotation_universe=universe,
            regime=regime,
        )
    ] == ["SOLUSDT"]

    market["symbols"]["SOLUSDT"][timeframe][field] = invalid_value

    with pytest.raises(ValueError, match=rf"SOLUSDT\.{timeframe}\.{field}"):
        generate_rotation_candidates(
            market,
            rotation_universe=universe,
            regime=regime,
        )


def test_generate_rotation_candidates_allows_soft_daily_reclaim_when_daily_close_is_just_above_ema50():
    candidates = generate_rotation_candidates(
        _soft_rotation_reclaim_market(sol_daily_close=102.0, sol_daily_ema50=101.0),
        rotation_universe=[{"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"}],
        regime={"label": "RISK_ON_ROTATION", "suppression_rules": []},
    )

    assert [candidate.symbol for candidate in candidates] == ["SOLUSDT"]


def test_generate_rotation_candidates_allows_soft_daily_reclaim_even_when_daily_close_is_extended_above_ema50():
    candidates = generate_rotation_candidates(
        _soft_rotation_reclaim_market(sol_daily_close=104.0, sol_daily_ema50=101.0),
        rotation_universe=[{"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"}],
        regime={"label": "RISK_ON_ROTATION", "suppression_rules": []},
    )

    assert [candidate.symbol for candidate in candidates] == ["SOLUSDT"]


def test_generate_rotation_candidates_rejects_soft_daily_reclaim_outside_rotation_regime():
    candidates = generate_rotation_candidates(
        _soft_rotation_reclaim_market(),
        rotation_universe=[{"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"}],
        regime={"label": "MIXED", "suppression_rules": []},
    )

    assert candidates == []


def test_generate_rotation_candidates_rejects_reacceleration_when_h1_extension_is_too_low():
    candidates = generate_rotation_candidates(
        _soft_rotation_reclaim_market(
            sol_h4_return_pct=0.025,
            sol_h1_return_pct=0.008,
            sol_h1_ema20=104.0 / 1.006112,
        ),
        rotation_universe=[{"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"}],
        regime={"label": "RISK_ON_ROTATION", "suppression_rules": []},
    )

    assert candidates == []


@pytest.mark.parametrize("h1_extension_pct", [0.007459, 0.013366])
def test_generate_rotation_candidates_allows_reacceleration_when_h1_extension_qualifies(h1_extension_pct: float):
    candidates = generate_rotation_candidates(
        _soft_rotation_reclaim_market(
            sol_h4_return_pct=0.025,
            sol_h1_return_pct=0.008,
            sol_h1_ema20=104.0 / (1.0 + h1_extension_pct),
        ),
        rotation_universe=[{"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"}],
        regime={"label": "RISK_ON_ROTATION", "suppression_rules": []},
    )

    assert [(candidate.symbol, candidate.setup_type) for candidate in candidates] == [
        ("SOLUSDT", "RS_REACCELERATION")
    ]
