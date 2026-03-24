from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..config import RiskConfig
from ..types import AccountSnapshot, TradeSignal, ValidationResult
from .guardrails import MAJOR_COIN_PREFIXES, evaluate_guardrails
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


def _get_account_value(account: AccountSnapshot | Mapping[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(account, Mapping):
        return account.get(key, default)
    return getattr(account, key, default)


def _position_symbol(position: Any) -> str:
    if isinstance(position, Mapping):
        return str(position.get("symbol", "")).upper()
    return str(getattr(position, "symbol", "")).upper()


def _allocator_correlated_positions(
    symbol: str,
    positions: list[Any],
) -> list[Any]:
    prefix = symbol.replace("USDT", "")
    peers: list[Any] = []
    for position in positions:
        pos_symbol = _position_symbol(position)
        if pos_symbol == symbol:
            peers.append(position)
            continue
        pos_prefix = pos_symbol.replace("USDT", "")
        if prefix.startswith(MAJOR_COIN_PREFIXES) and pos_prefix.startswith(MAJOR_COIN_PREFIXES):
            peers.append(position)
    return peers


def validate_candidate_for_allocation(
    candidate: Mapping[str, Any],
    account: AccountSnapshot | Mapping[str, Any],
) -> ValidationResult:
    reasons: list[str] = []
    metrics: dict[str, Any] = {"conflict_checked": True}

    engine = str(candidate.get("engine", "")).strip().lower()
    symbol = str(candidate.get("symbol", "")).strip().upper()
    side = str(candidate.get("side", "")).strip().upper()
    score = float(candidate.get("score", 0.0) or 0.0)

    if not engine:
        reasons.append("candidate engine 缺失")
    if not symbol.endswith("USDT"):
        reasons.append("candidate symbol 必须为 USDT 计价")
    if side not in {"LONG", "SHORT"}:
        reasons.append("candidate side 必须是 LONG 或 SHORT")
    if score <= 0:
        reasons.append("candidate score 必须大于 0")

    equity = float(_get_account_value(account, "equity", 0.0) or 0.0)
    if equity <= 0:
        reasons.append("账户权益无效，无法进行 allocator 风控")

    positions = _get_account_value(account, "open_positions", [])
    if not isinstance(positions, list):
        positions = []
    has_existing_symbol_exposure = any(_position_symbol(position) == symbol for position in positions)
    correlated_positions = _allocator_correlated_positions(symbol, positions)
    metrics["has_existing_symbol_exposure"] = has_existing_symbol_exposure
    metrics["correlated_positions"] = len(correlated_positions)

    if reasons:
        return ValidationResult(False, "BLOCK", reasons=reasons, metrics=metrics)

    severity = "INFO"
    allowed = True
    if has_existing_symbol_exposure:
        severity = "BLOCK"
        allowed = False
        reasons.append("existing exposure detected on symbol")
    elif len(correlated_positions) >= 3:
        severity = "BLOCK"
        allowed = False
        reasons.append(f"correlated exposure too high: {len(correlated_positions)} major-coin peers already open")

    return ValidationResult(allowed, severity, reasons=reasons, metrics=metrics)


def validate_candidate_for_execution(candidate: Mapping[str, Any]) -> ValidationResult:
    reasons: list[str] = []
    metrics: dict[str, Any] = {}

    meta = candidate.get("meta")
    candidate_meta = meta if isinstance(meta, Mapping) else {}

    stop_loss_raw = candidate.get("stop_loss", candidate_meta.get("stop_loss"))
    invalidation_source_raw = candidate.get("invalidation_source", candidate_meta.get("invalidation_source"))

    try:
        stop_loss = float(stop_loss_raw) if stop_loss_raw is not None else 0.0
    except (TypeError, ValueError):
        stop_loss = 0.0

    invalidation_source = str(invalidation_source_raw or "").strip()

    metrics["has_explicit_stop_loss"] = stop_loss > 0
    metrics["has_invalidation_source"] = bool(invalidation_source)

    if stop_loss <= 0:
        reasons.append("候选缺少显式止损 stop_loss，拒绝执行")
    if not invalidation_source:
        reasons.append("候选缺少 invalidation_source，拒绝执行")

    return ValidationResult(
        allowed=not reasons,
        severity="BLOCK" if reasons else "INFO",
        reasons=reasons,
        metrics=metrics,
    )
