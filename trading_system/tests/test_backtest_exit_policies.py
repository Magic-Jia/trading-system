from __future__ import annotations

from datetime import datetime, timezone
from typing import NamedTuple

import pytest

from trading_system.app.backtest.exit_policies import evaluate_exit_policy
from trading_system.app.backtest.types import ExitPolicyParams


class TradePrintFixture(NamedTuple):
    timestamp: datetime
    price: float


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _trade(timestamp: str, price: float) -> TradePrintFixture:
    return TradePrintFixture(timestamp=_ts(timestamp), price=price)


def test_long_after_cost_breakeven_uses_first_trade_print_covering_cost_and_buffer() -> None:
    result = evaluate_exit_policy(
        side="long",
        entry_price=100.0,
        entry_timestamp=_ts("2026-03-10T00:00:00Z"),
        fixed_exit_timestamp=_ts("2026-03-10T00:10:00Z"),
        costs_bps=10.0,
        trade_prints=(
            _trade("2026-03-10T00:01:00Z", 100.11),
            _trade("2026-03-10T00:02:00Z", 100.13),
            _trade("2026-03-10T00:03:00Z", 100.25),
        ),
        policy=ExitPolicyParams(name="after_cost_breakeven_stop", after_cost_buffer_bps=2.0),
    )

    assert result.triggered is True
    assert result.exit_price == pytest.approx(100.13)
    assert result.exit_timestamp == _ts("2026-03-10T00:02:00Z")
    assert result.exit_policy_reason == "after_cost_breakeven_stop"
    assert result.exit_price_source == "trade_print"
    assert result.fill_quality == "evidence_backed"


def test_short_after_cost_breakeven_uses_first_lower_trade_print_covering_cost_and_buffer() -> None:
    result = evaluate_exit_policy(
        side="short",
        entry_price=100.0,
        entry_timestamp=_ts("2026-03-10T00:00:00Z"),
        fixed_exit_timestamp=_ts("2026-03-10T00:10:00Z"),
        total_cost_bps=10.0,
        trade_prints=(
            _trade("2026-03-10T00:01:00Z", 99.90),
            _trade("2026-03-10T00:02:00Z", 99.87),
            _trade("2026-03-10T00:03:00Z", 99.70),
        ),
        policy=ExitPolicyParams(name="after_cost_breakeven_stop", after_cost_buffer_bps=2.0),
    )

    assert result.triggered is True
    assert result.exit_price == pytest.approx(99.87)
    assert result.exit_timestamp == _ts("2026-03-10T00:02:00Z")
    assert result.exit_policy_reason == "after_cost_breakeven_stop"


def test_activation_minute_ignores_earlier_qualifying_trade_prints() -> None:
    result = evaluate_exit_policy(
        side="long",
        entry_price=100.0,
        entry_timestamp=_ts("2026-03-10T00:00:00Z"),
        fixed_exit_timestamp=_ts("2026-03-10T00:10:00Z"),
        costs_bps=10.0,
        trade_prints=(
            _trade("2026-03-10T00:01:00Z", 100.20),
            _trade("2026-03-10T00:05:00Z", 100.14),
        ),
        policy=ExitPolicyParams(
            name="after_cost_breakeven_stop",
            after_cost_buffer_bps=2.0,
            activation_minute=5,
        ),
    )

    assert result.triggered is True
    assert result.exit_price == pytest.approx(100.14)
    assert result.exit_timestamp == _ts("2026-03-10T00:05:00Z")


def test_mfe_giveback_triggers_after_activation_threshold_on_first_chronological_giveback() -> None:
    result = evaluate_exit_policy(
        side="long",
        entry_price=100.0,
        entry_timestamp=_ts("2026-03-10T00:00:00Z"),
        fixed_exit_timestamp=_ts("2026-03-10T00:10:00Z"),
        costs_bps=5.0,
        trade_prints=(
            _trade("2026-03-10T00:01:00Z", 100.15),
            _trade("2026-03-10T00:02:00Z", 100.30),
            _trade("2026-03-10T00:03:00Z", 100.10),
            _trade("2026-03-10T00:04:00Z", 100.05),
        ),
        policy=ExitPolicyParams(name="mfe_giveback_cut"),
    )

    assert result.triggered is True
    assert result.exit_price == pytest.approx(100.05)
    assert result.exit_timestamp == _ts("2026-03-10T00:04:00Z")
    assert result.exit_policy_reason == "mfe_giveback_cut"
    assert result.exit_price_source == "trade_print"
    assert result.fill_quality == "evidence_backed"


def test_no_breakeven_time_stop_triggers_on_first_print_at_or_after_stop_time() -> None:
    result = evaluate_exit_policy(
        side="long",
        entry_price=100.0,
        entry_timestamp=_ts("2026-03-10T00:00:00Z"),
        fixed_exit_timestamp=_ts("2026-03-10T00:10:00Z"),
        costs_bps=10.0,
        trade_prints=(
            _trade("2026-03-10T00:01:00Z", 100.09),
            _trade("2026-03-10T00:05:00Z", 100.05),
            _trade("2026-03-10T00:06:00Z", 100.20),
        ),
        policy=ExitPolicyParams(
            name="no_breakeven_time_stop",
            after_cost_buffer_bps=2.0,
            no_breakeven_time_stop_minute=5,
        ),
    )

    assert result.triggered is True
    assert result.exit_price == pytest.approx(100.05)
    assert result.exit_timestamp == _ts("2026-03-10T00:05:00Z")
    assert result.exit_policy_reason == "no_breakeven_time_stop"


def test_no_breakeven_time_stop_does_not_trigger_when_breakeven_reached_before_stop_time() -> None:
    result = evaluate_exit_policy(
        side="long",
        entry_price=100.0,
        entry_timestamp=_ts("2026-03-10T00:00:00Z"),
        fixed_exit_timestamp=_ts("2026-03-10T00:10:00Z"),
        costs_bps=10.0,
        trade_prints=(
            _trade("2026-03-10T00:03:00Z", 100.13),
            _trade("2026-03-10T00:05:00Z", 99.90),
        ),
        policy=ExitPolicyParams(
            name="no_breakeven_time_stop",
            after_cost_buffer_bps=2.0,
            no_breakeven_time_stop_minute=5,
        ),
    )

    assert result.triggered is False
    assert result.exit_price is None
    assert result.exit_timestamp is None
    assert result.exit_policy_reason == "not_triggered"
    assert result.exit_price_source == "none"
    assert result.fill_quality == "no_evidence"


def test_no_eligible_trade_print_returns_not_triggered_without_fallback() -> None:
    result = evaluate_exit_policy(
        side="long",
        entry_price=100.0,
        entry_timestamp=_ts("2026-03-10T00:00:00Z"),
        fixed_exit_timestamp=_ts("2026-03-10T00:10:00Z"),
        costs_bps=10.0,
        trade_prints=(
            _trade("2026-03-09T23:59:00Z", 100.50),
            _trade("2026-03-10T00:11:00Z", 100.50),
        ),
        policy=ExitPolicyParams(name="after_cost_breakeven_stop"),
    )

    assert result.triggered is False
    assert result.exit_price is None
    assert result.exit_timestamp is None
    assert result.exit_policy_reason == "not_triggered"
    assert result.exit_price_source == "none"
    assert result.fill_quality == "no_evidence"


def test_eligible_trade_prints_are_bounded_and_processed_chronologically() -> None:
    result = evaluate_exit_policy(
        side="long",
        entry_price=100.0,
        entry_timestamp=_ts("2026-03-10T00:00:00Z"),
        fixed_exit_timestamp=_ts("2026-03-10T00:10:00Z"),
        costs_bps=10.0,
        trade_prints=(
            _trade("2026-03-09T23:59:00Z", 100.12),
            _trade("2026-03-10T00:03:00Z", 100.30),
            _trade("2026-03-10T00:01:00Z", 100.13),
            _trade("2026-03-10T00:02:00Z", 100.20),
            _trade("2026-03-10T00:11:00Z", 100.12),
        ),
        policy=ExitPolicyParams(name="after_cost_breakeven_stop", after_cost_buffer_bps=2.0),
    )

    assert result.triggered is True
    assert result.exit_price == pytest.approx(100.13)
    assert result.exit_timestamp == _ts("2026-03-10T00:01:00Z")


def test_unknown_exit_policy_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="^Unknown exit policy: not_real$"):
        evaluate_exit_policy(
            side="long",
            entry_price=100.0,
            entry_timestamp=_ts("2026-03-10T00:00:00Z"),
            fixed_exit_timestamp=_ts("2026-03-10T00:10:00Z"),
            costs_bps=10.0,
            trade_prints=(_trade("2026-03-10T00:01:00Z", 100.13),),
            policy=ExitPolicyParams(name="not_real"),
        )


def test_unknown_side_is_rejected() -> None:
    with pytest.raises(ValueError, match="^Unknown side: flat$"):
        evaluate_exit_policy(
            side="flat",
            entry_price=100.0,
            entry_timestamp=_ts("2026-03-10T00:00:00Z"),
            fixed_exit_timestamp=_ts("2026-03-10T00:10:00Z"),
            costs_bps=10.0,
            trade_prints=(_trade("2026-03-10T00:01:00Z", 100.13),),
            policy=ExitPolicyParams(name="after_cost_breakeven_stop"),
        )


@pytest.mark.parametrize("entry_price", [0.0, -1.0])
def test_entry_price_must_be_positive(entry_price: float) -> None:
    with pytest.raises(ValueError, match="^entry_price must be positive$"):
        evaluate_exit_policy(
            side="long",
            entry_price=entry_price,
            entry_timestamp=_ts("2026-03-10T00:00:00Z"),
            fixed_exit_timestamp=_ts("2026-03-10T00:10:00Z"),
            costs_bps=10.0,
            trade_prints=(_trade("2026-03-10T00:01:00Z", 100.13),),
            policy=ExitPolicyParams(name="after_cost_breakeven_stop"),
        )


@pytest.mark.parametrize(
    "policy",
    [
        ExitPolicyParams(name="after_cost_breakeven_stop"),
        ExitPolicyParams(name="mfe_giveback_cut"),
        ExitPolicyParams(name="no_breakeven_time_stop", no_breakeven_time_stop_minute=5),
    ],
)
def test_trade_print_price_must_be_positive_for_exit_policies(policy: ExitPolicyParams) -> None:
    with pytest.raises(ValueError, match="^trade print price must be positive$"):
        evaluate_exit_policy(
            side="long",
            entry_price=100.0,
            entry_timestamp=_ts("2026-03-10T00:00:00Z"),
            fixed_exit_timestamp=_ts("2026-03-10T00:10:00Z"),
            costs_bps=10.0,
            trade_prints=(_trade("2026-03-10T00:01:00Z", 0.0),),
            policy=policy,
        )
