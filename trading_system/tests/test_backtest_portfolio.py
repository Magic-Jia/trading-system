from __future__ import annotations

import pytest

from trading_system.app.backtest.portfolio import (
    calculate_dynamic_position_cap,
    decision_to_ledger_row,
    evaluate_candidate,
    position_size_from_risk,
    validate_portfolio_lifecycle,
)
from trading_system.app.backtest.types import (
    CapitalModelConfig,
    FundingMarginLiquidationEvidence,
    PortfolioCandidate,
    PortfolioDecision,
    PortfolioLifecycleEvidence,
    PortfolioPosition,
    PortfolioState,
    ProtectiveStopEvidence,
)


def sample_capital_config() -> CapitalModelConfig:
    return CapitalModelConfig(
        model="shared_pool",
        initial_equity=100_000.0,
        risk_per_trade=0.005,
        max_open_risk=0.03,
    )


def make_candidate(
    *,
    symbol: str,
    market_type: str,
    base_asset: str,
    entry_price: float,
    stop_loss: float,
    side: str = "long",
) -> PortfolioCandidate:
    return PortfolioCandidate(
        symbol=symbol,
        market_type=market_type,
        base_asset=base_asset,
        side=side,
        entry_price=entry_price,
        stop_loss=stop_loss,
    )


def make_position(
    *,
    symbol: str,
    market_type: str,
    base_asset: str,
    risk_budget: float = 0.005,
    position_notional: float = 20_000.0,
    qty: float = 0.4,
    side: str = "long",
    protective_stop_id: str | None = None,
) -> PortfolioPosition:
    return PortfolioPosition(
        symbol=symbol,
        market_type=market_type,
        base_asset=base_asset,
        side=side,
        risk_budget=risk_budget,
        position_notional=position_notional,
        qty=qty,
        protective_stop_id=protective_stop_id,
    )


def make_portfolio_state(
    *,
    initial_equity: float,
    open_positions: list[PortfolioPosition] | None = None,
    open_risk_fraction: float | None = None,
    capital_usage_fraction: float | None = None,
    active_positions: int | None = None,
    lifecycle_evidence: PortfolioLifecycleEvidence | None = None,
) -> PortfolioState:
    return PortfolioState(
        initial_equity=initial_equity,
        open_positions=tuple(open_positions or ()),
        open_risk_fraction=open_risk_fraction,
        capital_usage_fraction=capital_usage_fraction,
        active_positions=active_positions,
        lifecycle_evidence=lifecycle_evidence,
    )


def test_position_size_from_risk_rejects_coerced_candidate_prices() -> None:
    candidate = make_candidate(
        symbol="BTCUSDT",
        market_type="spot",
        base_asset="BTC",
        entry_price="50000",
        stop_loss=47_500.0,
    )

    with pytest.raises(ValueError, match="candidate.entry_price must be a finite number"):
        position_size_from_risk(candidate, equity=100_000.0, risk_budget=0.01)


def test_position_size_from_risk_uses_stop_distance() -> None:
    candidate = make_candidate(
        symbol="BTCUSDT",
        market_type="spot",
        base_asset="BTC",
        entry_price=50_000.0,
        stop_loss=47_500.0,
    )

    sizing = position_size_from_risk(candidate, equity=100_000.0, risk_budget=0.01)

    assert sizing.qty == pytest.approx(0.4)
    assert sizing.position_notional == pytest.approx(20_000.0)


def test_allocate_candidate_rejects_coerced_base_asset_identity() -> None:
    state = make_portfolio_state(
        initial_equity=100_000.0,
        open_positions=[
            make_position(symbol="BTCUSDT", market_type="spot", base_asset=True)
        ],
    )
    candidate = make_candidate(
        symbol="BTCUSDT_PERP",
        market_type="futures",
        base_asset=True,
        entry_price=60_000.0,
        stop_loss=57_000.0,
    )

    with pytest.raises(ValueError, match="candidate.base_asset must be a canonical string"):
        evaluate_candidate(candidate, state=state, capital=sample_capital_config())


def test_allocate_candidate_rejects_coerced_position_side() -> None:
    state = make_portfolio_state(
        initial_equity=100_000.0,
        open_positions=[
            make_position(symbol="BTCUSDT", market_type="spot", base_asset="BTC", side=True)
        ],
    )
    candidate = make_candidate(
        symbol="BTCUSDT_PERP",
        market_type="futures",
        base_asset="BTC",
        entry_price=60_000.0,
        stop_loss=57_000.0,
        side=True,
    )

    with pytest.raises(ValueError, match="position.side must be a portfolio side"):
        evaluate_candidate(candidate, state=state, capital=sample_capital_config())


def test_allocate_candidate_respects_shared_capital_and_base_asset_crowding() -> None:
    state = make_portfolio_state(
        initial_equity=100_000.0,
        open_positions=[make_position(symbol="BTCUSDT", market_type="spot", base_asset="BTC")],
    )
    candidate = make_candidate(
        symbol="BTCUSDT_PERP",
        market_type="futures",
        base_asset="BTC",
        entry_price=60_000.0,
        stop_loss=57_000.0,
    )

    decision = evaluate_candidate(candidate, state=state, capital=sample_capital_config())

    assert decision.status == "rejected"
    assert "base_asset_same_direction_crowding" in decision.reasons
    assert decision.final_risk_budget == pytest.approx(0.0)
    assert decision.position_notional == pytest.approx(0.0)
    assert decision.qty == pytest.approx(0.0)


def test_allocate_candidate_resizes_when_risk_budget_is_partially_available() -> None:
    state = make_portfolio_state(
        initial_equity=100_000.0,
        open_risk_fraction=0.0275,
    )
    candidate = make_candidate(
        symbol="ETHUSDT",
        market_type="spot",
        base_asset="ETH",
        entry_price=3_000.0,
        stop_loss=2_850.0,
    )

    decision = evaluate_candidate(candidate, state=state, capital=sample_capital_config())

    assert decision.status == "resized"
    assert "open_risk_budget_limited" in decision.reasons
    assert decision.final_risk_budget == pytest.approx(0.0025)
    assert decision.position_notional == pytest.approx(5_000.0)
    assert decision.qty == pytest.approx(5_000.0 / 3_000.0)


def test_calculate_dynamic_position_cap_uses_open_risk_capital_usage_and_active_positions() -> None:
    state = make_portfolio_state(
        initial_equity=100_000.0,
        open_positions=[
            make_position(symbol="BTCUSDT", market_type="spot", base_asset="BTC"),
            make_position(symbol="ETHUSDT", market_type="spot", base_asset="ETH"),
        ],
        open_risk_fraction=0.02,
        capital_usage_fraction=0.50,
        active_positions=2,
    )
    candidate = make_candidate(
        symbol="SOLUSDT",
        market_type="spot",
        base_asset="SOL",
        entry_price=200.0,
        stop_loss=190.0,
    )

    cap = calculate_dynamic_position_cap(candidate, state=state, capital=sample_capital_config())

    assert cap == 4


def test_decision_ledger_rejects_coerced_identity_and_status_fields() -> None:
    candidate = make_candidate(
        symbol=True,
        market_type="spot",
        base_asset="SOL",
        entry_price=200.0,
        stop_loss=190.0,
    )
    decision = PortfolioDecision(
        status="accepted",
        reasons=(),
        final_risk_budget=0.005,
        position_notional=10_000.0,
        qty=50.0,
    )

    with pytest.raises(ValueError, match="candidate.symbol must be a canonical string"):
        decision_to_ledger_row(candidate, decision)


def test_decision_ledgers_capture_accept_resize_and_reject_statuses() -> None:
    accepted = PortfolioDecision(
        status="accepted",
        reasons=(),
        final_risk_budget=0.005,
        position_notional=10_000.0,
        qty=50.0,
    )
    resized = PortfolioDecision(
        status="resized",
        reasons=("open_risk_budget_limited",),
        final_risk_budget=0.0025,
        position_notional=5_000.0,
        qty=25.0,
    )
    rejected = PortfolioDecision(
        status="rejected",
        reasons=("base_asset_same_direction_crowding",),
        final_risk_budget=0.0,
        position_notional=0.0,
        qty=0.0,
    )
    candidate = make_candidate(
        symbol="SOLUSDT",
        market_type="spot",
        base_asset="SOL",
        entry_price=200.0,
        stop_loss=190.0,
    )

    accepted_row = decision_to_ledger_row(candidate, accepted)
    resized_row = decision_to_ledger_row(candidate, resized)
    rejected_row = decision_to_ledger_row(candidate, rejected)

    assert accepted_row.status == "accepted"
    assert accepted_row.qty == pytest.approx(50.0)
    assert resized_row.status == "resized"
    assert resized_row.reasons == ("open_risk_budget_limited",)
    assert rejected_row.status == "rejected"
    assert rejected_row.reasons == ("base_asset_same_direction_crowding",)


def test_lifecycle_validation_rejects_duplicate_protective_stop_evidence() -> None:
    state = make_portfolio_state(
        initial_equity=100_000.0,
        open_positions=[
            make_position(
                symbol="BTCUSDT",
                market_type="spot",
                base_asset="BTC",
                protective_stop_id="stop-btc-1",
            )
        ],
        lifecycle_evidence=PortfolioLifecycleEvidence(
            protective_stops=(
                ProtectiveStopEvidence(
                    stop_id="stop-btc-1",
                    symbol="BTCUSDT",
                    status="active",
                    stop_loss=47_500.0,
                    updated_at_counter=1,
                ),
                ProtectiveStopEvidence(
                    stop_id="stop-btc-1",
                    symbol="BTCUSDT",
                    status="active",
                    stop_loss=47_400.0,
                    updated_at_counter=2,
                ),
            ),
            funding_margin_liquidation=(
                FundingMarginLiquidationEvidence(
                    evidence_id="risk-btc-1",
                    symbol="BTCUSDT",
                    timestamp_ms=1_700_000_000_000,
                    order_counter=10,
                    funding_rate_bps=0.25,
                    margin_ratio=0.5,
                    liquidation_price=42_000.0,
                    liquidation_distance_fraction=0.12,
                ),
            ),
        ),
    )

    report = validate_portfolio_lifecycle(state, promotion_grade=True)

    assert report.valid is False
    assert report.reasons == ("duplicate_protective_stop_evidence",)


def test_lifecycle_validation_rejects_missing_stop_state_for_promotion_grade() -> None:
    state = make_portfolio_state(
        initial_equity=100_000.0,
        open_positions=[
            make_position(symbol="ETHUSDT", market_type="futures", base_asset="ETH")
        ],
        lifecycle_evidence=PortfolioLifecycleEvidence(
            funding_margin_liquidation=(
                FundingMarginLiquidationEvidence(
                    evidence_id="risk-eth-1",
                    symbol="ETHUSDT",
                    timestamp_ms=1_700_000_000_000,
                    order_counter=11,
                    funding_rate_bps=0.1,
                    margin_ratio=0.4,
                    liquidation_price=2_500.0,
                    liquidation_distance_fraction=0.15,
                ),
            ),
        ),
    )

    report = validate_portfolio_lifecycle(state, promotion_grade=True)

    assert report.valid is False
    assert report.reasons == ("missing_protective_stop_state",)


def test_lifecycle_validation_rejects_missing_liquidation_risk_for_promotion_grade() -> None:
    state = make_portfolio_state(
        initial_equity=100_000.0,
        open_positions=[
            make_position(
                symbol="ETHUSDT",
                market_type="futures",
                base_asset="ETH",
                protective_stop_id="stop-eth-1",
            )
        ],
        lifecycle_evidence=PortfolioLifecycleEvidence(
            protective_stops=(
                ProtectiveStopEvidence(
                    stop_id="stop-eth-1",
                    symbol="ETHUSDT",
                    status="active",
                    stop_loss=2_850.0,
                    updated_at_counter=2,
                ),
            ),
        ),
    )

    report = validate_portfolio_lifecycle(state, promotion_grade=True)

    assert report.valid is False
    assert report.reasons == ("missing_liquidation_risk_evidence",)


def test_lifecycle_validation_rejects_bool_and_string_numeric_evidence() -> None:
    state = make_portfolio_state(
        initial_equity=100_000.0,
        open_positions=[
            make_position(
                symbol="SOLUSDT",
                market_type="futures",
                base_asset="SOL",
                protective_stop_id="stop-sol-1",
            )
        ],
        lifecycle_evidence=PortfolioLifecycleEvidence(
            protective_stops=(
                ProtectiveStopEvidence(
                    stop_id="stop-sol-1",
                    symbol="SOLUSDT",
                    status="active",
                    stop_loss="190.0",
                    updated_at_counter=True,
                ),
            ),
            funding_margin_liquidation=(
                FundingMarginLiquidationEvidence(
                    evidence_id="risk-sol-1",
                    symbol="SOLUSDT",
                    timestamp_ms=True,
                    order_counter=12,
                    funding_rate_bps="0.1",
                    margin_ratio=0.4,
                    liquidation_price=150.0,
                    liquidation_distance_fraction=0.2,
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="protective_stop.stop_loss must be a finite number"):
        validate_portfolio_lifecycle(state, promotion_grade=True)


def test_lifecycle_validation_accepts_valid_promotion_grade_evidence() -> None:
    state = make_portfolio_state(
        initial_equity=100_000.0,
        open_positions=[
            make_position(
                symbol="BTCUSDT",
                market_type="futures",
                base_asset="BTC",
                protective_stop_id="stop-btc-1",
            )
        ],
        lifecycle_evidence=PortfolioLifecycleEvidence(
            protective_stops=(
                ProtectiveStopEvidence(
                    stop_id="stop-btc-1",
                    symbol="BTCUSDT",
                    status="active",
                    stop_loss=47_500.0,
                    updated_at_counter=1,
                ),
            ),
            funding_margin_liquidation=(
                FundingMarginLiquidationEvidence(
                    evidence_id="risk-btc-1",
                    symbol="BTCUSDT",
                    timestamp_ms=1_700_000_000_000,
                    order_counter=10,
                    funding_rate_bps=0.25,
                    margin_ratio=0.5,
                    liquidation_price=42_000.0,
                    liquidation_distance_fraction=0.12,
                ),
            ),
        ),
    )

    report = validate_portfolio_lifecycle(state, promotion_grade=True)

    assert report.valid is True
    assert report.reasons == ()
