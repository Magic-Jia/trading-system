from __future__ import annotations

from ..config import RiskConfig
from ..types import AccountSnapshot, PositionSnapshot, TradeSignal


MAJOR_COIN_PREFIXES = ("BTC", "ETH", "BNB", "SOL")


def existing_symbol_risk(account: AccountSnapshot, symbol: str) -> float:
    total = 0.0
    for pos in account.open_positions:
        if pos.symbol == symbol:
            total += abs(pos.notional)
    return total


def current_open_risk_pct(account: AccountSnapshot) -> float:
    equity = max(account.equity, 0.0)
    if equity <= 0:
        return 0.0
    total_notional = sum(abs(pos.notional) for pos in account.open_positions)
    return total_notional / equity


def correlated_positions(account: AccountSnapshot, signal: TradeSignal) -> list[PositionSnapshot]:
    prefix = signal.symbol.replace("USDT", "")
    peers: list[PositionSnapshot] = []
    for pos in account.open_positions:
        if pos.symbol == signal.symbol:
            peers.append(pos)
            continue
        pos_prefix = pos.symbol.replace("USDT", "")
        if prefix.startswith(MAJOR_COIN_PREFIXES) and pos_prefix.startswith(MAJOR_COIN_PREFIXES):
            peers.append(pos)
    return peers


def evaluate_guardrails(
    signal: TradeSignal,
    account: AccountSnapshot,
    config: RiskConfig,
    planned_notional: float,
) -> tuple[bool, list[str], dict[str, float | int]]:
    reasons: list[str] = []
    metrics: dict[str, float | int] = {}

    stop_distance_pct = signal.risk_per_unit() / signal.entry_price if signal.entry_price else 0.0
    metrics["stop_distance_pct"] = round(stop_distance_pct, 6)
    metrics["current_open_risk_pct"] = round(current_open_risk_pct(account), 6)
    metrics["open_positions"] = len(account.open_positions)

    if len(account.open_positions) >= config.max_open_positions:
        reasons.append(f"当前持仓数 {len(account.open_positions)} 已达到上限 {config.max_open_positions}")

    if stop_distance_pct < config.min_stop_distance_pct:
        reasons.append(f"止损太近：{stop_distance_pct:.2%} < {config.min_stop_distance_pct:.2%}")

    if stop_distance_pct > config.max_stop_distance_pct:
        reasons.append(f"止损太宽：{stop_distance_pct:.2%} > {config.max_stop_distance_pct:.2%}")

    total_risk_after = current_open_risk_pct(account) + (planned_notional / account.equity if account.equity else 0.0)
    metrics["total_risk_after_pct"] = round(total_risk_after, 6)
    if total_risk_after > config.max_total_risk_pct:
        reasons.append(f"总风险暴露将升至 {total_risk_after:.2%}，超过上限 {config.max_total_risk_pct:.2%}")

    symbol_risk_after = (existing_symbol_risk(account, signal.symbol) + planned_notional) / account.equity if account.equity else 0.0
    metrics["symbol_risk_after_pct"] = round(symbol_risk_after, 6)
    if symbol_risk_after > config.max_symbol_risk_pct:
        reasons.append(f"单标的风险将升至 {symbol_risk_after:.2%}，超过上限 {config.max_symbol_risk_pct:.2%}")

    peers = correlated_positions(account, signal)
    metrics["correlated_positions"] = len(peers)
    if len(peers) >= 3:
        reasons.append(f"高度相关仓位过多：已发现 {len(peers)} 个相关仓位")

    return len(reasons) == 0, reasons, metrics
