from __future__ import annotations

from ..config import RiskConfig
from ..types import AccountSnapshot, TradeSignal, ValidationResult
from .guardrails import evaluate_guardrails
from .position_sizer import size_signal


def validate_signal(
    signal: TradeSignal,
    account: AccountSnapshot,
    config: RiskConfig,
    volatility_pct: float | None = None,
) -> tuple[ValidationResult, dict]:
    reasons: list[str] = []
    metrics: dict = {}

    if signal.entry_price <= 0:
        reasons.append("entry_price 必须大于 0")
    if signal.stop_loss <= 0:
        reasons.append("stop_loss 必须大于 0")
    if signal.symbol.endswith("USDT") is False:
        reasons.append("当前 MVP 仅支持 USDT 计价合约")

    if signal.side == "LONG" and signal.stop_loss >= signal.entry_price:
        reasons.append("LONG 信号的止损必须低于入场")
    if signal.side == "SHORT" and signal.stop_loss <= signal.entry_price:
        reasons.append("SHORT 信号的止损必须高于入场")

    if account.equity <= 0 or account.available_balance <= 0:
        reasons.append("账户权益或可用余额无效，拒绝开仓")

    if reasons:
        return ValidationResult(False, "BLOCK", reasons=reasons, metrics=metrics), {}

    sizing = size_signal(signal, account, config, volatility_pct=volatility_pct)
    metrics.update(
        {
            "sizing_qty": sizing.qty,
            "risk_budget_usdt": sizing.risk_budget_usdt,
            "planned_loss_usdt": sizing.planned_loss_usdt,
            "planned_notional_usdt": sizing.planned_notional_usdt,
        }
    )
    if not sizing.allowed or sizing.qty <= 0:
        reasons.append("仓位计算结果无效，拒绝开仓")
        return ValidationResult(False, "BLOCK", reasons=reasons, metrics=metrics), {"sizing": sizing}

    allowed, guard_reasons, guard_metrics = evaluate_guardrails(signal, account, config, planned_notional=sizing.planned_notional_usdt)
    metrics.update(guard_metrics)
    reasons.extend(guard_reasons)

    severity = "INFO"
    if not allowed:
        severity = "BLOCK"
    elif sizing.risk_pct_of_equity >= config.default_risk_pct * 0.9:
        severity = "WARN"
        reasons.append("该笔风险接近单笔默认预算上限")

    return ValidationResult(allowed, severity, reasons=reasons, metrics=metrics), {"sizing": sizing}
