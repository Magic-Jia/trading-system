from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_system.app.backtest.derivatives_risk import build_derivatives_position_risk_report


def _valid_position(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": "BTCUSDT",
        "side": "long",
        "quantity": 0.5,
        "margin_mode": "isolated",
        "position_mode": "one_way",
        "leverage": 10.0,
        "entry_price": 50000.0,
        "mark_price": 51000.0,
        "maintenance_margin_rate": 0.005,
        "wallet_balance": 3000.0,
        "funding_rate": 0.0001,
        "funding_period_hours": 8.0,
        "funding_observed_at": "2026-05-17T08:00:00Z",
        "funding_period_id": "BTCUSDT-20260517T080000Z",
        "adl_quantile": 2,
    }
    payload.update(overrides)
    return payload


def test_builds_isolated_derivatives_position_risk_report() -> None:
    report = build_derivatives_position_risk_report(
        _valid_position(),
        as_of=datetime(2026, 5, 17, 8, 30, tzinfo=UTC),
    )

    assert report["schema_version"] == "derivatives_position_risk_report.v1"
    assert report["symbol"] == "BTCUSDT"
    assert report["decision"] == "pass"
    assert report["reason_codes"] == []
    assert report["margin_mode"] == "isolated"
    assert report["position_mode"] == "one_way"
    assert report["leverage"] == pytest.approx(10.0)
    assert report["entry_price"] == pytest.approx(50000.0)
    assert report["mark_price"] == pytest.approx(51000.0)
    assert report["maintenance_margin_rate"] == pytest.approx(0.005)
    assert report["wallet_balance"] == pytest.approx(3000.0)
    assert report["notional"] == pytest.approx(25500.0)
    assert report["unrealized_pnl"] == pytest.approx(500.0)
    assert report["estimated_liquidation_price"] == pytest.approx(45226.13065326633)
    assert report["funding_payment"] == pytest.approx(-2.55)
    assert report["adl_risk_bucket"] == "medium"
    assert report["adl_quality"] == "venue_reported_quantile"


def test_cross_margin_uses_conservative_review_hold_when_venue_formula_is_unknown() -> None:
    report = build_derivatives_position_risk_report(
        _valid_position(margin_mode="cross"),
        as_of=datetime(2026, 5, 17, 8, 30, tzinfo=UTC),
    )

    assert report["decision"] == "review_hold"
    assert report["estimated_liquidation_price"] is None
    assert "cross_margin_liquidation_formula_unknown" in report["reason_codes"]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("quantity", 0.0, "quantity_invalid"),
        ("quantity", -1.0, "quantity_invalid"),
        ("entry_price", float("nan"), "entry_price_invalid"),
        ("mark_price", None, "mark_price_missing"),
        ("wallet_balance", True, "wallet_balance_invalid"),
        ("maintenance_margin_rate", -0.01, "maintenance_margin_rate_invalid"),
        ("leverage", 0.0, "leverage_invalid"),
        ("leverage", 126.0, "leverage_invalid"),
    ],
)
def test_derivatives_position_risk_report_fails_closed_on_invalid_numerics(
    field: str,
    value: object,
    reason: str,
) -> None:
    report = build_derivatives_position_risk_report(
        _valid_position(**{field: value}),
        as_of=datetime(2026, 5, 17, 8, 30, tzinfo=UTC),
    )

    assert report["decision"] == "fail_closed"
    assert reason in report["reason_codes"]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("side", "flat", "side_invalid"),
        ("margin_mode", "portfolio", "margin_mode_invalid"),
        ("position_mode", "hedge", "position_mode_invalid"),
    ],
)
def test_derivatives_position_risk_report_fails_closed_on_invalid_domains(
    field: str,
    value: object,
    reason: str,
) -> None:
    report = build_derivatives_position_risk_report(
        _valid_position(**{field: value}),
        as_of=datetime(2026, 5, 17, 8, 30, tzinfo=UTC),
    )

    assert report["decision"] == "fail_closed"
    assert reason in report["reason_codes"]


def test_derivatives_position_risk_report_fails_closed_on_stale_funding_timestamp() -> None:
    report = build_derivatives_position_risk_report(
        _valid_position(funding_observed_at="2026-05-16T23:59:59Z"),
        as_of=datetime(2026, 5, 17, 8, 30, tzinfo=UTC),
    )

    assert report["decision"] == "fail_closed"
    assert "funding_timestamp_stale" in report["reason_codes"]


def test_derivatives_position_risk_report_fails_closed_on_duplicate_funding_period_identity() -> None:
    report = build_derivatives_position_risk_report(
        _valid_position(
            funding_period_id="BTCUSDT-20260517T080000Z",
            prior_funding_period_ids=["BTCUSDT-20260517T080000Z"],
        ),
        as_of=datetime(2026, 5, 17, 8, 30, tzinfo=UTC),
    )

    assert report["decision"] == "fail_closed"
    assert "funding_period_identity_duplicate" in report["reason_codes"]
