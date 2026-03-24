import pytest

from trading_system.app.config import RiskConfig
from trading_system.app.risk.validator import validate_candidate_for_allocation, validate_signal
from trading_system.app.types import AccountSnapshot, PositionSnapshot, TradeSignal


def test_validate_candidate_for_allocation_blocks_existing_symbol_exposure():
    candidate = {
        "engine": "trend",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "score": 0.82,
    }
    account = AccountSnapshot(
        equity=100000.0,
        available_balance=50000.0,
        futures_wallet_balance=100000.0,
        open_positions=[
            PositionSnapshot(
                symbol="BTCUSDT",
                side="LONG",
                qty=0.5,
                entry_price=62000.0,
                notional=31000.0,
            )
        ],
    )

    result = validate_candidate_for_allocation(candidate, account)

    assert result.allowed is False
    assert result.severity == "BLOCK"
    assert "existing exposure detected on symbol" in result.reasons
    assert result.metrics["has_existing_symbol_exposure"] is True


def test_validate_candidate_for_allocation_blocks_excess_major_coin_correlation():
    candidate = {
        "engine": "trend",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "score": 0.76,
    }
    account = AccountSnapshot(
        equity=100000.0,
        available_balance=50000.0,
        futures_wallet_balance=100000.0,
        open_positions=[
            PositionSnapshot(symbol="ETHUSDT", side="LONG", qty=10.0, entry_price=3200.0, notional=32000.0),
            PositionSnapshot(symbol="BNBUSDT", side="LONG", qty=20.0, entry_price=500.0, notional=10000.0),
            PositionSnapshot(symbol="SOLUSDT", side="LONG", qty=100.0, entry_price=150.0, notional=15000.0),
        ],
    )

    result = validate_candidate_for_allocation(candidate, account)

    assert result.allowed is False
    assert result.severity == "BLOCK"
    assert any("correlated" in reason.lower() for reason in result.reasons)
    assert result.metrics["correlated_positions"] == 3


def test_validate_signal_blocks_when_planned_notional_pushes_net_exposure_over_cap():
    signal = TradeSignal(
        signal_id="net-exposure-block",
        symbol="XRPUSDT",
        side="LONG",
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        source="strategy",
        timeframe="4h",
        tags=["v2", "trend"],
        meta={"setup_type": "BREAKOUT", "score": 0.9},
    )
    account = AccountSnapshot(
        equity=1000.0,
        available_balance=500.0,
        futures_wallet_balance=1000.0,
        open_positions=[
            PositionSnapshot(
                symbol="ADAUSDT",
                side="LONG",
                qty=8.0,
                entry_price=100.0,
                mark_price=100.0,
                notional=800.0,
            )
        ],
    )
    config = RiskConfig(
        max_total_risk_pct=1.0,
        max_symbol_risk_pct=1.0,
        max_net_exposure_pct=0.85,
    )

    result, context = validate_signal(signal, account, config)

    assert result.allowed is False
    assert result.severity == "BLOCK"
    assert any("净敞口" in reason for reason in result.reasons)
    assert result.metrics["current_net_exposure_pct"] == pytest.approx(0.8, abs=1e-6)
    assert result.metrics["net_exposure_after_pct"] == pytest.approx(0.92, abs=1e-6)
    assert context["sizing"].planned_notional_usdt == pytest.approx(120.0, abs=1e-6)
