from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading_system.app.config import LifecycleConfig
from trading_system.app.portfolio.lifecycle import advance_lifecycle_positions
from trading_system.app.signals.entry_profile import resolve_entry_profile
from trading_system.app.signals.rotation_engine import generate_rotation_candidates
from trading_system.app.signals.short_engine import generate_short_candidates
from trading_system.app.signals.trend_engine import generate_trend_candidates
from trading_system.app.storage.state_store import RuntimeStateV2
from trading_system.paper_snapshots import _market_context_payload


def _tf(
    *,
    close: float,
    ema20: float,
    ema50: float,
    ret: float,
    atr_pct: float = 0.02,
    volume: float = 2_000_000_000,
) -> dict[str, float]:
    return {
        "close": close,
        "ema_20": ema20,
        "ema_50": ema50,
        "atr_pct": atr_pct,
        "return_pct_7d": ret,
        "return_pct_3d": ret,
        "return_pct_24h": ret,
        "return_pct_8h": ret,
        "return_pct_4h": ret,
        "volume_usdt_24h": volume,
    }


def _short_term_long_market(*, trigger_ok: bool = True) -> dict[str, object]:
    tf15 = _tf(close=101.2 if trigger_ok else 99.7, ema20=100.8, ema50=100.0, ret=0.0012)
    return {
        "symbols": {
            "BTCUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": _tf(close=100.0, ema20=98.0, ema50=96.0, ret=0.0),
                "4h": _tf(close=101.0, ema20=100.0, ema50=98.0, ret=0.0035),
                "1h": _tf(close=100.9, ema20=100.4, ema50=99.5, ret=0.0012),
                "30m": _tf(close=101.0, ema20=100.5, ema50=99.8, ret=0.0010),
                "15m": tf15,
            }
        }
    }


def _short_term_rotation_market() -> dict[str, object]:
    major = {
        "sector": "majors",
        "liquidity_tier": "top",
        "daily": _tf(close=100.0, ema20=99.0, ema50=98.0, ret=0.0, volume=15_000_000_000),
        "4h": _tf(close=100.0, ema20=99.6, ema50=99.0, ret=0.0002, volume=15_000_000_000),
        "1h": _tf(close=100.0, ema20=99.7, ema50=99.2, ret=0.0001, volume=15_000_000_000),
        "30m": _tf(close=100.0, ema20=99.7, ema50=99.3, ret=0.0, volume=15_000_000_000),
        "15m": _tf(close=100.0, ema20=99.8, ema50=99.4, ret=0.0, volume=15_000_000_000),
    }
    sol = {
        "sector": "alt_l1",
        "liquidity_tier": "high",
        "daily": _tf(close=50.0, ema20=49.2, ema50=48.0, ret=0.001, atr_pct=0.045),
        "4h": _tf(close=50.8, ema20=50.0, ema50=49.0, ret=0.0035, atr_pct=0.045),
        "1h": _tf(close=50.7, ema20=50.4, ema50=49.8, ret=0.0011, atr_pct=0.045),
        "30m": _tf(close=50.75, ema20=50.42, ema50=49.95, ret=0.0010, atr_pct=0.045),
        "15m": _tf(close=50.8, ema20=50.45, ema50=50.0, ret=0.0011, atr_pct=0.045),
    }
    return {"symbols": {"BTCUSDT": major, "ETHUSDT": major.copy(), "SOLUSDT": sol}}


def _short_term_short_market(*, trigger_ok: bool = True) -> dict[str, object]:
    return {
        "symbols": {
            "BTCUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": _tf(close=100.0, ema20=101.0, ema50=102.0, ret=-0.026, volume=15_000_000_000),
                "4h": _tf(close=98.5, ema20=99.5, ema50=101.0, ret=-0.013, volume=15_000_000_000),
                "1h": _tf(close=98.2, ema20=99.0, ema50=100.2, ret=-0.004, volume=15_000_000_000),
                "30m": _tf(close=98.0, ema20=98.8, ema50=99.7, ret=-0.002, volume=15_000_000_000),
                "15m": _tf(close=97.8 if trigger_ok else 99.1, ema20=98.4, ema50=99.5, ret=-0.0015, volume=15_000_000_000),
            }
        }
    }


def test_short_term_entry_profile_is_resolvable_and_intraday_focused():
    profile = resolve_entry_profile("short-term")

    assert profile.name == "short_term"
    assert profile.trend_daily_floor == 0.0
    assert profile.trend_h4_floor <= 0.004
    assert profile.trend_m30_floor > 0
    assert profile.trend_m15_floor > 0
    assert profile.rotation_m30_floor > 0
    assert profile.rotation_m15_floor > 0


def test_scout_entry_profile_is_resolvable_with_aggressive_testnet_alias():
    profile = resolve_entry_profile("scout")
    alias = resolve_entry_profile("aggressive_testnet")

    assert profile.name == "scout"
    assert alias == profile
    assert profile.trend_h4_floor <= 0.0
    assert profile.trend_h1_floor <= 0.0
    assert profile.trend_m30_floor == 0.0
    assert profile.trend_m15_floor == 0.0
    assert profile.rotation_m30_floor == 0.0
    assert profile.rotation_m15_floor == 0.0


def test_market_context_payload_generates_30m_and_15m_snapshots(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_spot_ticker(symbol: str) -> dict[str, str]:
        return {"quoteVolume": "123456789"}

    def fake_klines(symbol: str, interval: str, *, limit: int = 60) -> list[list[object]]:
        calls.append((symbol, interval))
        rows = []
        base = 100.0
        for idx in range(limit):
            close = base + idx
            rows.append([idx, close - 0.5, close + 1.0, close - 1.0, close, 10, idx, 1000, 1, 1, 500])
        return rows

    monkeypatch.setattr("trading_system.paper_snapshots._spot_ticker", fake_spot_ticker)
    monkeypatch.setattr("trading_system.paper_snapshots._spot_klines", fake_klines)

    payload = _market_context_payload(["BTCUSDT"])
    symbol_payload = payload["symbols"]["BTCUSDT"]

    assert {"daily", "4h", "1h", "30m", "15m"} <= set(symbol_payload)
    assert symbol_payload["30m"]["return_pct_8h"] > 0
    assert symbol_payload["15m"]["return_pct_4h"] > 0
    assert ("BTCUSDT", "30m") in calls
    assert ("BTCUSDT", "15m") in calls


def test_short_term_trend_requires_15m_30m_trigger_and_uses_tighter_stop():
    rejected = generate_trend_candidates(_short_term_long_market(trigger_ok=False), entry_profile="short_term")
    accepted = generate_trend_candidates(
        _short_term_long_market(trigger_ok=True),
        entry_profile="short_term",
        include_high_liquidity_strong_names=False,
    )

    assert rejected == []
    assert [candidate.symbol for candidate in accepted] == ["BTCUSDT"]
    assert accepted[0].stop_loss == 100.0
    assert accepted[0].invalidation_source == "short_term_structure_loss_below_15m_ema50"
    assert accepted[0].timeframe_meta["trigger_timeframes"] == ["30m", "15m"]


def test_short_term_rotation_uses_4h_1h_structure_with_15m_30m_trigger():
    candidates = generate_rotation_candidates(
        _short_term_rotation_market(),
        rotation_universe=[{"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_meta": {"rolling_notional": 2_000_000_000}}],
        entry_profile="short_term",
    )

    assert [candidate.symbol for candidate in candidates] == ["SOLUSDT"]
    assert candidates[0].stop_loss == 50.0
    assert candidates[0].invalidation_source == "short_term_rotation_loss_below_15m_ema50"
    assert candidates[0].timeframe_meta["trigger_timeframes"] == ["30m", "15m"]


def _scout_major_recovery_market(*, trigger_ok: bool = True) -> dict[str, object]:
    market = _short_term_long_market(trigger_ok=trigger_ok)
    btc = market["symbols"]["BTCUSDT"]
    btc["4h"] = _tf(close=100.6, ema20=100.0, ema50=101.2, ret=-0.001)
    btc["1h"] = _tf(close=100.4, ema20=99.9, ema50=100.8, ret=-0.0004)
    btc["30m"] = _tf(close=100.7, ema20=100.2, ema50=99.8, ret=0.0)
    btc["15m"] = _tf(close=100.8 if trigger_ok else 99.7, ema20=100.3, ema50=100.0, ret=0.0)
    return market


def test_scout_trend_allows_major_recovery_but_still_requires_intraday_trigger():
    short_term = generate_trend_candidates(
        _scout_major_recovery_market(trigger_ok=True),
        entry_profile="short_term",
        include_high_liquidity_strong_names=False,
    )
    missing_trigger = generate_trend_candidates(
        _scout_major_recovery_market(trigger_ok=False),
        entry_profile="scout",
        include_high_liquidity_strong_names=False,
    )
    scout = generate_trend_candidates(
        _scout_major_recovery_market(trigger_ok=True),
        entry_profile="scout",
        include_high_liquidity_strong_names=False,
    )

    assert short_term == []
    assert missing_trigger == []
    assert [candidate.symbol for candidate in scout] == ["BTCUSDT"]
    assert scout[0].stop_loss == 100.0
    assert scout[0].invalidation_source == "scout_structure_loss_below_15m_ema50"
    assert scout[0].timeframe_meta["h1_trigger"] == "scout_intraday_recovery"


def _scout_rotation_recovery_market(*, rs_ok: bool = True, trigger_ok: bool = True) -> dict[str, object]:
    market = _short_term_rotation_market()
    market["symbols"]["BTCUSDT"]["4h"] = _tf(close=100.0, ema20=99.6, ema50=99.0, ret=-0.003, volume=15_000_000_000)
    market["symbols"]["ETHUSDT"]["4h"] = _tf(close=100.0, ema20=99.6, ema50=99.0, ret=-0.002, volume=15_000_000_000)
    sol = market["symbols"]["SOLUSDT"]
    sol["daily"] = _tf(close=50.0, ema20=49.2, ema50=48.0, ret=0.001, atr_pct=0.045)
    sol["4h"] = _tf(close=50.2, ema20=50.6, ema50=50.9, ret=-0.006 if rs_ok else -0.03, atr_pct=0.045)
    sol["1h"] = _tf(close=50.7, ema20=50.4, ema50=50.9, ret=-0.001 if rs_ok else -0.02, atr_pct=0.045)
    sol["30m"] = _tf(close=50.75, ema20=50.42, ema50=49.95, ret=0.0, atr_pct=0.045)
    sol["15m"] = _tf(close=50.8 if trigger_ok else 49.8, ema20=50.45, ema50=50.0, ret=0.0, atr_pct=0.045)
    return market


def test_scout_rotation_allows_high_liquidity_recovery_and_rejects_bad_rs_or_missing_trigger():
    universe = [{"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_meta": {"rolling_notional": 2_000_000_000}}]
    short_term = generate_rotation_candidates(
        _scout_rotation_recovery_market(rs_ok=True, trigger_ok=True),
        rotation_universe=universe,
        entry_profile="short_term",
    )
    missing_trigger = generate_rotation_candidates(
        _scout_rotation_recovery_market(rs_ok=True, trigger_ok=False),
        rotation_universe=universe,
        entry_profile="scout",
    )
    bad_rs = generate_rotation_candidates(
        _scout_rotation_recovery_market(rs_ok=False, trigger_ok=True),
        rotation_universe=universe,
        entry_profile="scout",
    )
    scout = generate_rotation_candidates(
        _scout_rotation_recovery_market(rs_ok=True, trigger_ok=True),
        rotation_universe=universe,
        entry_profile="scout",
    )

    assert short_term == []
    assert missing_trigger == []
    assert bad_rs == []
    assert [candidate.symbol for candidate in scout] == ["SOLUSDT"]
    assert scout[0].stop_loss == 50.0
    assert scout[0].invalidation_source == "scout_rotation_loss_below_15m_ema50"
    assert scout[0].timeframe_meta["h4_structure"] == "scout_intraday_recovery"


def test_short_term_short_uses_intraday_breakdown_trigger_and_tighter_stop():
    rejected = generate_short_candidates(
        _short_term_short_market(trigger_ok=False),
        short_universe=[{"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 15_000_000_000}}],
        regime={"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"short": 0.8}},
        entry_profile="short_term",
    )
    accepted = generate_short_candidates(
        _short_term_short_market(trigger_ok=True),
        short_universe=[{"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 15_000_000_000}}],
        regime={"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"short": 0.8}},
        entry_profile="short_term",
    )

    assert rejected == []
    assert [candidate.symbol for candidate in accepted] == ["BTCUSDT"]
    assert accepted[0].stop_loss == 99.5
    assert accepted[0].invalidation_source == "short_term_short_reclaim_above_15m_ema50"
    assert accepted[0].timeframe_meta["trigger_timeframes"] == ["30m", "15m"]


def test_short_term_lifecycle_exits_when_max_holding_hours_elapsed():
    opened_at = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat().replace("+00:00", "Z")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-25T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.1,
                "entry_price": 100.0,
                "mark_price": 100.6,
                "stop_loss": 99.0,
                "status": "OPEN",
                "tracked_from_intent": True,
                "entry_profile": "short_term",
                "opened_at": opened_at,
            }
        },
        latest_lifecycle={"BTCUSDT": {"state": "PAYLOAD", "reason_codes": ["payload_active"]}},
    )

    updates = advance_lifecycle_positions(state, LifecycleConfig())

    assert updates["BTCUSDT"]["state"] == "EXIT"
    assert "max_holding_hours_elapsed" in updates["BTCUSDT"]["reason_codes"]
    assert updates["BTCUSDT"]["max_holding_hours"] == 6


def test_short_term_lifecycle_defaults_are_faster_than_swing_defaults(monkeypatch):
    monkeypatch.delenv("TRADING_LIFECYCLE_CONFIRM_R_MULTIPLE", raising=False)
    monkeypatch.delenv("TRADING_LIFECYCLE_PROTECT_R_MULTIPLE", raising=False)
    monkeypatch.delenv("TRADING_LIFECYCLE_EXIT_R_MULTIPLE", raising=False)
    monkeypatch.delenv("TRADING_LIFECYCLE_MAX_HOLDING_HOURS", raising=False)

    cfg = LifecycleConfig()

    assert cfg.confirm_r_multiple <= 0.5
    assert cfg.protect_r_multiple <= 0.8
    assert cfg.exit_r_multiple <= 1.5
    assert cfg.max_holding_hours == 6


def test_intraday_multi_entry_profile_is_frequency_target_not_profit_guarantee():
    profile = resolve_entry_profile("intraday_multi")
    alias = resolve_entry_profile("daily_multi")

    assert profile.name == "intraday_multi"
    assert alias == profile
    assert 0.0 < profile.trend_h4_floor <= 0.0015
    assert 0.0 < profile.trend_h1_floor <= 0.0006
    assert profile.trend_m30_floor > 0.0
    assert profile.trend_m15_floor > 0.0
    profile_meta = profile.as_dict()
    assert profile_meta["target_trades_per_day_min"] >= 2
    assert profile_meta["target_trades_per_day_max"] >= profile_meta["target_trades_per_day_min"]
    serialized = " ".join(str(value).lower() for value in profile_meta.values())
    assert "guarantee" not in serialized
    assert "profit" not in serialized
    assert "收益保证" not in serialized


def test_intraday_multi_trend_accepts_modest_trigger_that_short_term_rejects():
    market = _short_term_long_market(trigger_ok=True)
    btc = market["symbols"]["BTCUSDT"]
    btc["4h"]["return_pct_3d"] = 0.0014
    btc["1h"]["return_pct_24h"] = 0.0006
    btc["30m"]["return_pct_8h"] = 0.0004
    btc["15m"]["return_pct_4h"] = 0.0004

    rejected = generate_trend_candidates(market, entry_profile="short_term", include_high_liquidity_strong_names=False)
    accepted = generate_trend_candidates(market, entry_profile="intraday_multi", include_high_liquidity_strong_names=False)

    assert rejected == []
    assert [candidate.symbol for candidate in accepted] == ["BTCUSDT"]
    assert accepted[0].timeframe_meta["trigger_timeframes"] == ["30m", "15m"]


def test_intraday_multi_trend_still_rejects_missing_intraday_trigger():
    market = _short_term_long_market(trigger_ok=False)
    btc = market["symbols"]["BTCUSDT"]
    btc["4h"]["return_pct_3d"] = 0.0014
    btc["1h"]["return_pct_24h"] = 0.0006

    accepted = generate_trend_candidates(market, entry_profile="intraday_multi", include_high_liquidity_strong_names=False)

    assert accepted == []
