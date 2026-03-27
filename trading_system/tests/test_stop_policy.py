import pytest

from trading_system.app.risk.stop_policy import StopPolicy, build_stop_policy


def _trend_payload() -> dict:
    return {
        "daily": {"close": 100.0, "ema_20": 96.0, "ema_50": 92.0, "atr_pct": 0.04},
        "4h": {"close": 100.0, "ema_20": 98.0, "ema_50": 95.0, "atr_pct": 0.018},
        "1h": {"close": 100.0, "ema_20": 99.0, "ema_50": 97.5, "atr_pct": 0.008},
    }


def _rotation_payload() -> dict:
    return {
        "daily": {"close": 48.0, "ema_20": 45.0, "ema_50": 41.0, "atr_pct": 0.055},
        "4h": {"close": 48.0, "ema_20": 47.1, "ema_50": 45.8, "atr_pct": 0.02},
        "1h": {"close": 48.0, "ema_20": 47.7, "ema_50": 46.9, "atr_pct": 0.011},
    }


def _short_payload() -> dict:
    return {
        "daily": {"close": 100.0, "ema_20": 103.0, "ema_50": 106.0, "atr_pct": 0.04},
        "4h": {"close": 99.5, "ema_20": 102.0, "ema_50": 105.0, "atr_pct": 0.018},
        "1h": {"close": 99.0, "ema_20": 100.8, "ema_50": 101.5, "atr_pct": 0.008},
    }


def test_build_stop_policy_maps_breakout_continuation_to_structure_stop():
    policy = build_stop_policy(
        _trend_payload(),
        engine="trend",
        setup_type="BREAKOUT_CONTINUATION",
        side="LONG",
    )

    assert policy == StopPolicy(
        stop_loss=pytest.approx(98.0),
        stop_family="structure_stop",
        stop_reference="4h_ema20",
        invalidation_source="trend_breakout_failure_below_4h_ema20",
        invalidation_reason="breakout continuation lost 4h breakout support",
    )


def test_build_stop_policy_maps_rotation_reacceleration_to_failure_stop():
    policy = build_stop_policy(
        _rotation_payload(),
        engine="rotation",
        setup_type="RS_REACCELERATION",
        side="LONG",
    )

    assert policy == StopPolicy(
        stop_loss=pytest.approx(46.9),
        stop_family="failure_stop",
        stop_reference="1h_ema50",
        invalidation_source="rotation_pullback_failure_below_1h_ema50",
        invalidation_reason="rotation leadership failed on the 1h pullback structure",
    )


def test_build_stop_policy_maps_breakdown_short_to_continuation_structure_stop():
    policy = build_stop_policy(
        _short_payload(),
        engine="short",
        setup_type="BREAKDOWN_SHORT",
        side="SHORT",
    )

    assert policy == StopPolicy(
        stop_loss=pytest.approx(102.0),
        stop_family="structure_stop",
        stop_reference="4h_ema20",
        invalidation_source="short_breakdown_failure_above_4h_ema20",
        invalidation_reason="breakdown continuation lost 4h breakdown resistance",
    )


def test_build_stop_policy_maps_failed_bounce_short_to_failure_stop():
    policy = build_stop_policy(
        _short_payload(),
        engine="short",
        setup_type="FAILED_BOUNCE_SHORT",
        side="SHORT",
    )

    assert policy == StopPolicy(
        stop_loss=pytest.approx(101.5),
        stop_family="failure_stop",
        stop_reference="1h_ema50",
        invalidation_source="short_failed_bounce_reclaim_above_1h_ema50",
        invalidation_reason="failed-bounce short reclaimed the 1h rejection structure",
    )


def test_build_stop_policy_tightens_longs_under_crash_defensive_regime():
    base = build_stop_policy(
        _trend_payload(),
        engine="trend",
        setup_type="BREAKOUT_CONTINUATION",
        side="LONG",
    )
    defensive = build_stop_policy(
        _trend_payload(),
        engine="trend",
        setup_type="BREAKOUT_CONTINUATION",
        side="LONG",
        regime={"label": "CRASH_DEFENSIVE"},
    )

    assert defensive == StopPolicy(
        stop_loss=pytest.approx(99.0),
        stop_family="squeeze_stop",
        stop_reference="1h_ema20_or_1d_atr_band",
        invalidation_source="crash_defensive_squeeze_loss_below_1h_ema20_or_1d_atr_band",
        invalidation_reason="crash-defensive regime keeps long exposure on a tight squeeze stop",
    )
    assert defensive.stop_loss > base.stop_loss
