from __future__ import annotations

from ..config import RiskConfig
from ..types import AccountSnapshot, SizingResult, TradeSignal
from .regime_risk import scaled_risk_budget


def _effective_risk_pct(
    config: RiskConfig,
    volatility_pct: float | None,
    *,
    regime_multiplier: float | None,
    confidence: float | None,
    engine_tier_multiplier: float,
) -> tuple[float, list[str]]:
    notes: list[str] = []
    base_risk_pct = config.default_risk_pct
    if volatility_pct is not None and volatility_pct >= config.high_volatility_threshold_pct:
        base_risk_pct *= config.high_vol_risk_multiplier
        notes.append(
            f"波动率 {volatility_pct:.2%} 高于阈值 {config.high_volatility_threshold_pct:.2%}，单笔风险预算降至 {base_risk_pct:.2%}"
        )
    else:
        notes.append(f"单笔默认风险预算 {base_risk_pct:.2%}")

    risk_pct = scaled_risk_budget(
        base_risk_pct=base_risk_pct,
        regime_multiplier=1.0 if regime_multiplier is None else regime_multiplier,
        confidence=1.0 if confidence is None else confidence,
        engine_tier_multiplier=engine_tier_multiplier,
    )
    if risk_pct != base_risk_pct:
        notes.append(f"按 regime/confidence 调整后风险预算 {risk_pct:.2%}")

    return risk_pct, notes


def size_signal(
    signal: TradeSignal,
    account: AccountSnapshot,
    config: RiskConfig,
    volatility_pct: float | None = None,
    regime_multiplier: float | None = None,
    confidence: float | None = None,
    engine_tier_multiplier: float = 1.0,
) -> SizingResult:
    stop_distance = signal.risk_per_unit()
    if stop_distance <= 0:
        return SizingResult(
            allowed=False,
            qty=0.0,
            risk_budget_usdt=0.0,
            planned_loss_usdt=0.0,
            planned_notional_usdt=0.0,
            stop_distance=stop_distance,
            risk_pct_of_equity=0.0,
            max_notional_cap_usdt=0.0,
            notes=["止损距离无效，拒绝 sizing"],
        )

    risk_pct, notes = _effective_risk_pct(
        config,
        volatility_pct,
        regime_multiplier=regime_multiplier,
        confidence=confidence,
        engine_tier_multiplier=engine_tier_multiplier,
    )
    equity = max(account.equity, 0.0)
    risk_budget = equity * risk_pct
    max_notional_cap = equity * config.max_notional_pct

    raw_qty = risk_budget / stop_distance
    capped_qty = min(raw_qty, max_notional_cap / signal.entry_price)
    planned_notional = capped_qty * signal.entry_price
    planned_loss = capped_qty * stop_distance

    notes.extend(
        [
            "仓位由账户权益 × 风险预算 ÷ 止损距离反推",
            f"单笔名义仓位上限 {config.max_notional_pct:.2%} of equity",
        ]
    )

    return SizingResult(
        allowed=capped_qty > 0,
        qty=round(capped_qty, 6),
        risk_budget_usdt=round(risk_budget, 4),
        planned_loss_usdt=round(planned_loss, 4),
        planned_notional_usdt=round(planned_notional, 4),
        stop_distance=round(stop_distance, 6),
        risk_pct_of_equity=round((planned_loss / equity) if equity else 0.0, 6),
        max_notional_cap_usdt=round(max_notional_cap, 4),
        notes=notes,
    )
