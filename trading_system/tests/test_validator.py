from trading_system.app.risk.validator import validate_candidate_for_allocation
from trading_system.app.types import AccountSnapshot, PositionSnapshot


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
