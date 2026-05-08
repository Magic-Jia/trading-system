import pytest

from trading_system.app.config import RiskConfig
from trading_system.app.risk.validator import (
    validate_candidate_for_allocation,
    validate_candidate_for_execution,
    validate_signal,
)
from trading_system.app.types import AccountSnapshot, PositionSnapshot, TradeSignal


def test_risk_config_defaults_enable_minimum_cost_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRADING_MINIMUM_COST_COVERAGE_RATIO", raising=False)
    monkeypatch.delenv("TRADING_ESTIMATED_ROUNDTRIP_COST_BPS", raising=False)

    config = RiskConfig()

    assert config.minimum_cost_coverage_ratio > 0
    assert config.estimated_roundtrip_cost_bps > 0


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


@pytest.mark.parametrize("equity", [True, "1000"])
def test_validate_candidate_for_allocation_rejects_invalid_present_account_equity(equity):
    candidate = {
        "engine": "trend",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "score": 0.76,
    }
    account = {
        "equity": equity,
        "open_positions": [],
    }

    result = validate_candidate_for_allocation(candidate, account)

    assert result.allowed is False
    assert result.severity == "BLOCK"
    assert "账户权益必须是大于 0 的数字，无法进行 allocator 风控" in result.reasons


def test_validate_candidate_for_allocation_rejects_present_non_list_open_positions():
    candidate = {
        "engine": "trend",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "score": 0.76,
    }
    account = {
        "equity": 100000.0,
        "open_positions": {"symbol": "ETHUSDT"},
    }

    result = validate_candidate_for_allocation(candidate, account)

    assert result.allowed is False
    assert result.severity == "BLOCK"
    assert "account open_positions 必须是列表" in result.reasons


def test_validate_candidate_for_allocation_rejects_open_position_non_string_symbol():
    candidate = {
        "engine": "trend",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "score": 0.76,
    }
    account = {
        "equity": 100000.0,
        "open_positions": [{"symbol": True}],
    }

    result = validate_candidate_for_allocation(candidate, account)

    assert result.allowed is False
    assert result.severity == "BLOCK"
    assert "open position symbol 必须是非空字符串" in result.reasons


@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    [
        ("engine", 123, "candidate engine 必须是非空字符串"),
        ("engine", "", "candidate engine 缺失"),
        ("symbol", 123, "candidate symbol 必须是非空字符串"),
        ("symbol", "BTC-PERP", "candidate symbol 必须为 USDT 计价"),
        ("side", 123, "candidate side 必须是非空字符串"),
        ("side", "BUY", "candidate side 必须是 LONG 或 SHORT"),
    ],
)
def test_validate_candidate_for_allocation_rejects_invalid_string_fields(field, value, expected_reason):
    candidate = {
        "engine": "trend",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "score": 0.76,
        field: value,
    }
    account = AccountSnapshot(equity=100000.0, available_balance=50000.0, futures_wallet_balance=100000.0)

    result = validate_candidate_for_allocation(candidate, account)

    assert result.allowed is False
    assert result.severity == "BLOCK"
    assert expected_reason in result.reasons


@pytest.mark.parametrize("score", [True, False])
def test_validate_candidate_for_allocation_rejects_bool_score(score):
    candidate = {
        "engine": "trend",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "score": score,
    }
    account = AccountSnapshot(equity=100000.0, available_balance=50000.0, futures_wallet_balance=100000.0)

    result = validate_candidate_for_allocation(candidate, account)

    assert result.allowed is False
    assert result.severity == "BLOCK"
    assert "candidate score 必须是大于 0 的数字" in result.reasons


def test_validate_candidate_for_execution_blocks_missing_explicit_stop_and_invalidation_source():
    candidate = {
        "engine": "trend",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "score": 0.82,
    }

    result = validate_candidate_for_execution(candidate)

    assert result.allowed is False
    assert result.severity == "BLOCK"
    assert any("显式止损" in reason for reason in result.reasons)
    assert any("invalidation_source" in reason for reason in result.reasons)
    assert result.metrics["has_explicit_stop_loss"] is False
    assert result.metrics["has_invalidation_source"] is False


def test_validate_candidate_for_execution_allows_without_take_profit():
    candidate = {
        "engine": "trend",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "score": 0.82,
        "stop_loss": 99.0,
        "invalidation_source": "ema_50",
    }

    result = validate_candidate_for_execution(candidate)

    assert result.allowed is True
    assert result.metrics["has_explicit_stop_loss"] is True
    assert result.metrics["has_invalidation_source"] is True


@pytest.mark.parametrize("stop_loss", [True, "99.0"])
def test_validate_candidate_for_execution_rejects_bool_and_string_stop_loss(stop_loss):
    candidate = {
        "engine": "trend",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "score": 0.82,
        "stop_loss": stop_loss,
        "invalidation_source": "ema_50",
    }

    result = validate_candidate_for_execution(candidate)

    assert result.allowed is False
    assert result.severity == "BLOCK"
    assert "候选 stop_loss 必须是大于 0 的数字" in result.reasons
    assert result.metrics["has_explicit_stop_loss"] is False
    assert result.metrics["has_invalidation_source"] is True


@pytest.mark.parametrize(
    ("invalidation_source", "expected_reason"),
    [
        (123, "候选 invalidation_source 必须是非空字符串"),
        ("   ", "候选缺少 invalidation_source，拒绝执行"),
    ],
)
def test_validate_candidate_for_execution_rejects_invalid_invalidation_source(
    invalidation_source, expected_reason
):
    candidate = {
        "engine": "trend",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "score": 0.82,
        "stop_loss": 99.0,
        "invalidation_source": invalidation_source,
    }

    result = validate_candidate_for_execution(candidate)

    assert result.allowed is False
    assert result.severity == "BLOCK"
    assert expected_reason in result.reasons
    assert result.metrics["has_explicit_stop_loss"] is True
    assert result.metrics["has_invalidation_source"] is False


def test_validate_signal_blocks_when_cost_gate_enabled_without_take_profit():
    signal = TradeSignal(
        signal_id="cost-coverage-missing-tp",
        symbol="BTCUSDT",
        side="LONG",
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=None,
        source="strategy",
        timeframe="4h",
        tags=["v2", "trend"],
        meta={"setup_type": "BREAKOUT", "score": 0.8},
    )
    account = AccountSnapshot(equity=1000.0, available_balance=1000.0, futures_wallet_balance=1000.0)
    config = RiskConfig(
        minimum_cost_coverage_ratio=1.0,
        estimated_roundtrip_cost_bps=50.0,
        max_notional_pct=1.0,
        max_total_risk_pct=1.0,
        max_symbol_risk_pct=1.0,
    )

    result, _context = validate_signal(signal, account, config)

    assert result.allowed is False
    assert any("止盈目标" in reason for reason in result.reasons)
    assert result.metrics["expected_reward_pct"] is None


def test_validate_signal_blocks_when_expected_reward_does_not_cover_minimum_cost():
    signal = TradeSignal(
        signal_id="cost-coverage-block",
        symbol="BTCUSDT",
        side="SHORT",
        entry_price=100.0,
        stop_loss=104.0,
        take_profit=99.5,
        source="strategy",
        timeframe="4h",
        tags=["v2", "short"],
        meta={"setup_type": "BREAKDOWN_SHORT", "score": 0.8},
    )
    account = AccountSnapshot(equity=1000.0, available_balance=1000.0, futures_wallet_balance=1000.0)
    config = RiskConfig(
        minimum_cost_coverage_ratio=2.0,
        estimated_roundtrip_cost_bps=50.0,
        max_notional_pct=1.0,
        max_total_risk_pct=1.0,
        max_symbol_risk_pct=1.0,
    )

    result, context = validate_signal(signal, account, config)

    assert result.allowed is False
    assert result.severity == "BLOCK"
    assert any("成本覆盖" in reason for reason in result.reasons)
    assert result.metrics["expected_reward_pct"] == pytest.approx(0.005, abs=1e-6)
    assert result.metrics["minimum_cost_coverage_required_pct"] == pytest.approx(0.01, abs=1e-6)
    assert context["sizing"].qty > 0


def test_validate_signal_allows_when_expected_reward_covers_minimum_cost():
    signal = TradeSignal(
        signal_id="cost-coverage-allow",
        symbol="BTCUSDT",
        side="SHORT",
        entry_price=100.0,
        stop_loss=104.0,
        take_profit=98.0,
        source="strategy",
        timeframe="4h",
        tags=["v2", "short"],
        meta={"setup_type": "BREAKDOWN_SHORT", "score": 0.8},
    )
    account = AccountSnapshot(equity=1000.0, available_balance=1000.0, futures_wallet_balance=1000.0)
    config = RiskConfig(
        minimum_cost_coverage_ratio=2.0,
        estimated_roundtrip_cost_bps=50.0,
        max_notional_pct=1.0,
        max_total_risk_pct=1.0,
        max_symbol_risk_pct=1.0,
    )

    result, _context = validate_signal(signal, account, config)

    assert result.allowed is True
    assert result.metrics["expected_reward_pct"] == pytest.approx(0.02, abs=1e-6)
    assert result.metrics["minimum_cost_coverage_required_pct"] == pytest.approx(0.01, abs=1e-6)


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


def test_validate_signal_uses_planned_loss_not_notional_for_new_trade_risk_caps():
    signal = TradeSignal(
        signal_id="scout-risk-budget-allowed",
        symbol="BTCUSDT",
        side="LONG",
        entry_price=77948.99,
        stop_loss=76828.68949046,
        take_profit=None,
        source="strategy",
        timeframe="4h",
        tags=["v2", "trend"],
        meta={"setup_type": "PULLBACK_CONTINUATION", "score": 0.91},
    )
    account = AccountSnapshot(
        equity=100000.0,
        available_balance=100000.0,
        futures_wallet_balance=100000.0,
        open_positions=[],
    )
    config = RiskConfig(
        max_notional_pct=0.12,
        max_total_risk_pct=0.03,
        max_symbol_risk_pct=0.015,
        max_net_exposure_pct=0.85,
        minimum_cost_coverage_ratio=0.0,
    )

    result, context = validate_signal(signal, account, config, risk_pct_override=0.00541)

    assert context["sizing"].planned_notional_usdt == pytest.approx(12000.0, rel=1e-4)
    assert context["sizing"].planned_loss_usdt == pytest.approx(172.4667, rel=1e-4)
    assert result.allowed is True
    assert not any("总风险暴露" in reason for reason in result.reasons)
    assert not any("单标的风险" in reason for reason in result.reasons)
    assert result.metrics["total_risk_after_pct"] == pytest.approx(0.001725, abs=1e-6)
    assert result.metrics["symbol_risk_after_pct"] == pytest.approx(0.001725, abs=1e-6)
    assert result.metrics["net_exposure_after_pct"] == pytest.approx(0.12, abs=1e-6)
