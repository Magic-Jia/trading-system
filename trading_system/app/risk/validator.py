from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..config import RiskConfig
from ..types import AccountSnapshot, TradeSignal, ValidationResult
from .guardrails import MAJOR_COIN_PREFIXES, evaluate_guardrails
from .position_sizer import size_signal


def _expected_reward_pct(signal: TradeSignal) -> float | None:
    if signal.entry_price <= 0 or signal.take_profit is None or signal.take_profit <= 0:
        return None
    if signal.side == "LONG":
        reward = signal.take_profit - signal.entry_price
    else:
        reward = signal.entry_price - signal.take_profit
    if reward <= 0:
        return 0.0
    return reward / signal.entry_price


def _cost_coverage_required_pct(config: RiskConfig) -> float:
    if config.minimum_cost_coverage_ratio <= 0 or config.estimated_roundtrip_cost_bps <= 0:
        return 0.0
    return config.minimum_cost_coverage_ratio * (config.estimated_roundtrip_cost_bps / 10_000.0)


def validate_signal(
    signal: TradeSignal,
    account: AccountSnapshot,
    config: RiskConfig,
    volatility_pct: float | None = None,
    risk_pct_override: float | None = None,
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

    sizing = size_signal(signal, account, config, volatility_pct=volatility_pct, risk_pct_override=risk_pct_override)
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

    required_cost_coverage_pct = _cost_coverage_required_pct(config)
    if required_cost_coverage_pct > 0:
        expected_reward_pct = _expected_reward_pct(signal)
        metrics["minimum_cost_coverage_required_pct"] = required_cost_coverage_pct
        metrics["expected_reward_pct"] = expected_reward_pct
        if expected_reward_pct is None:
            reasons.append("信号缺少止盈目标，无法验证最低成本覆盖门槛")
            return ValidationResult(False, "BLOCK", reasons=reasons, metrics=metrics), {"sizing": sizing}
        if expected_reward_pct < required_cost_coverage_pct:
            reasons.append(
                "预期收益空间未达到最低成本覆盖门槛: "
                f"expected_reward_pct={expected_reward_pct:.6f}, "
                f"required_pct={required_cost_coverage_pct:.6f}"
            )
            return ValidationResult(False, "BLOCK", reasons=reasons, metrics=metrics), {"sizing": sizing}

    allowed, guard_reasons, guard_metrics = evaluate_guardrails(
        signal,
        account,
        config,
        planned_notional=sizing.planned_notional_usdt,
        planned_loss=sizing.planned_loss_usdt,
    )
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


def _position_symbol(position: Any) -> str | None:
    if isinstance(position, Mapping):
        symbol = position.get("symbol", "")
    else:
        symbol = getattr(position, "symbol", "")
    if not isinstance(symbol, str):
        return None
    canonical = symbol.strip()
    if not canonical:
        return None
    return canonical.upper()


def _allocator_correlated_positions(
    symbol: str,
    positions: list[Any],
) -> list[Any]:
    prefix = symbol.replace("USDT", "")
    peers: list[Any] = []
    for position in positions:
        pos_symbol = _position_symbol(position)
        if pos_symbol is None:
            continue
        if pos_symbol == symbol:
            peers.append(position)
            continue
        pos_prefix = pos_symbol.replace("USDT", "")
        if prefix.startswith(MAJOR_COIN_PREFIXES) and pos_prefix.startswith(MAJOR_COIN_PREFIXES):
            peers.append(position)
    return peers


def _optional_canonical_non_empty_string(value: Any, *, case: str) -> str | None:
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    canonical = value.strip()
    if not canonical:
        return ""
    if case == "upper":
        return canonical.upper()
    if case == "lower":
        return canonical.lower()
    return canonical


def _positive_numeric_score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    if not math.isfinite(score) or score <= 0:
        return None
    return score


def _positive_numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        return None
    return numeric


def validate_candidate_for_allocation(
    candidate: Mapping[str, Any],
    account: AccountSnapshot | Mapping[str, Any],
) -> ValidationResult:
    reasons: list[str] = []
    metrics: dict[str, Any] = {"conflict_checked": True}

    engine = _optional_canonical_non_empty_string(candidate.get("engine"), case="lower")
    symbol = _optional_canonical_non_empty_string(candidate.get("symbol"), case="upper")
    side = _optional_canonical_non_empty_string(candidate.get("side"), case="upper")
    score = _positive_numeric_score(candidate.get("score"))

    if engine is None:
        reasons.append("candidate engine 必须是非空字符串")
    elif not engine:
        reasons.append("candidate engine 缺失")
    if symbol is None:
        reasons.append("candidate symbol 必须是非空字符串")
    elif not symbol.endswith("USDT"):
        reasons.append("candidate symbol 必须为 USDT 计价")
    if side is None:
        reasons.append("candidate side 必须是非空字符串")
    elif side not in {"LONG", "SHORT"}:
        reasons.append("candidate side 必须是 LONG 或 SHORT")
    if score is None:
        reasons.append("candidate score 必须是大于 0 的数字")

    equity = _positive_numeric(_get_account_value(account, "equity", 0.0))
    if equity is None:
        reasons.append("账户权益必须是大于 0 的数字，无法进行 allocator 风控")

    positions = _get_account_value(account, "open_positions", [])
    if not isinstance(positions, list):
        reasons.append("account open_positions 必须是列表")
        positions = []
    for position in positions:
        if _position_symbol(position) is None:
            reasons.append("open position symbol 必须是非空字符串")
            break
    has_existing_symbol_exposure = any(_position_symbol(position) == symbol for position in positions)
    correlated_positions = _allocator_correlated_positions(symbol, positions) if symbol else []
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

    stop_loss = _positive_numeric(stop_loss_raw) if stop_loss_raw is not None else None

    if invalidation_source_raw is None:
        invalidation_source = ""
    elif isinstance(invalidation_source_raw, str):
        invalidation_source = invalidation_source_raw.strip()
    else:
        invalidation_source = None

    metrics["has_explicit_stop_loss"] = stop_loss is not None
    metrics["has_invalidation_source"] = bool(invalidation_source)

    if stop_loss is None:
        if stop_loss_raw is None:
            reasons.append("候选缺少显式止损 stop_loss，拒绝执行")
        else:
            reasons.append("候选 stop_loss 必须是大于 0 的数字")
    if invalidation_source is None:
        reasons.append("候选 invalidation_source 必须是非空字符串")
    elif not invalidation_source:
        reasons.append("候选缺少 invalidation_source，拒绝执行")

    return ValidationResult(
        allowed=not reasons,
        severity="BLOCK" if reasons else "INFO",
        reasons=reasons,
        metrics=metrics,
    )
