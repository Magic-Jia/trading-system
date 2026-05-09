from __future__ import annotations

import pytest

from trading_system.app.universe.liquidity_filter import evaluate_liquidity


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rolling_notional", "not-a-number"),
        ("depth_proxy_notional", "not-a-number"),
        ("slippage_bps", "not-a-number"),
        ("listing_age_days", "not-a-number"),
    ],
)
def test_evaluate_liquidity_rejects_present_invalid_numeric_metrics(field: str, value: object) -> None:
    metrics = {
        "rolling_notional": 2_000_000.0,
        "depth_proxy_notional": 500_000.0,
        "slippage_bps": 5.0,
        "listing_age_days": 90.0,
    }
    metrics[field] = value

    with pytest.raises(ValueError, match=field):
        evaluate_liquidity(metrics)
