from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping

from trading_system.app.config import DEFAULT_CONFIG, AppConfig
from trading_system.app.market_regime.classifier import classify_regime
from trading_system.app.portfolio.allocator import allocate_candidates
from trading_system.app.risk.validator import validate_candidate_for_allocation
from trading_system.app.signals.rotation_engine import generate_rotation_candidates
from trading_system.app.signals.short_engine import generate_short_candidates
from trading_system.app.signals.trend_engine import generate_trend_candidates
from trading_system.app.universe.builder import UniverseBuildResult, build_universes

from .costs import fee_cost, funding_cost, slippage_cost
from .dataset import load_historical_dataset
from .metrics import calmar_ratio, max_drawdown, sharpe_ratio, sortino_ratio, total_return, turnover
from .portfolio import decision_to_ledger_row, evaluate_candidate
from .types import (
    BacktestConfig,
    BacktestCosts,
    BaselineReplayResult,
    DatasetSnapshotRow,
    InstrumentSnapshotRow,
    PortfolioCandidate,
    PortfolioPosition,
    PortfolioScorecardRow,
    PortfolioState,
    TradeLedgerRow,
)
from .universe import filter_universe


def _candidate_row(candidate: Any) -> dict[str, Any]:
    if is_dataclass(candidate):
        return asdict(candidate)
    if isinstance(candidate, Mapping):
        return dict(candidate)
    raise TypeError(f"unsupported candidate type: {type(candidate)!r}")


def _rank_key(row: Mapping[str, Any]) -> tuple[float, str, str]:
    return (-float(row.get("score", 0.0) or 0.0), str(row.get("symbol", "")), str(row.get("engine", "")))


def _regime_dict(row: DatasetSnapshotRow) -> dict[str, Any]:
    override = row.meta.get("regime_override")
    if isinstance(override, Mapping):
        return dict(override)
    return asdict(classify_regime(row.market, row.derivatives))


def _universes_payload(universes: UniverseBuildResult) -> dict[str, Any]:
    return {
        "major_universe": universes.major_universe,
        "rotation_universe": universes.rotation_universe,
        "short_universe": universes.short_universe,
        "major_count": len(universes.major_universe),
        "rotation_count": len(universes.rotation_universe),
        "short_count": len(universes.short_universe),
    }


def _suppression_payload(regime: Mapping[str, Any]) -> dict[str, Any]:
    rules = {str(rule).lower() for rule in list(regime.get("suppression_rules", []))}
    return {
        "rules": sorted(rules),
        "rotation_suppressed": "rotation" in rules,
        "trend_suppressed": "trend" in rules,
        "short_suppressed": "short" in rules,
        "execution_policy": regime.get("execution_policy", "normal"),
    }


def _validated_candidates(candidates: list[dict[str, Any]], account: Mapping[str, Any]) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for row in candidates:
        validation = validate_candidate_for_allocation(row, account)
        candidate = dict(row)
        candidate["validation"] = {
            "allowed": validation.allowed,
            "reasons": list(validation.reasons),
            "metrics": dict(validation.metrics),
        }
        if validation.allowed:
            validated.append(candidate)
    return validated


def _allocation_rows(
    account: Mapping[str, Any],
    validated_candidates: list[dict[str, Any]],
    regime: Mapping[str, Any],
    *,
    app_config: AppConfig,
) -> list[dict[str, Any]]:
    ranked = sorted(validated_candidates, key=_rank_key)
    decisions = allocate_candidates(account=account, candidates=ranked, regime=regime, config=app_config)
    allocations: list[dict[str, Any]] = []
    for candidate, decision in zip(ranked, decisions):
        allocations.append(
            {
                "symbol": candidate.get("symbol"),
                "engine": candidate.get("engine"),
                "setup_type": candidate.get("setup_type"),
                "score": candidate.get("score"),
                "status": decision.status,
                "rank": decision.rank,
                "reasons": list(decision.reasons),
                "final_risk_budget": decision.final_risk_budget,
                "meta": dict(decision.meta),
            }
        )
    return allocations


def replay_snapshot(
    row: DatasetSnapshotRow,
    *,
    app_config: AppConfig | None = None,
    costs: BacktestCosts | None = None,
) -> dict[str, Any]:
    resolved_config = app_config or DEFAULT_CONFIG
    resolved_costs = costs or BacktestCosts(fee_bps=0.0, slippage_bps=0.0, funding_bps_per_day=0.0)
    regime = _regime_dict(row)
    universes = build_universes(row.market, derivatives=row.derivatives)

    raw_candidates = {
        "trend": [_candidate_row(candidate) for candidate in generate_trend_candidates(row.market, derivatives=row.derivatives)],
        "rotation": [
            _candidate_row(candidate)
            for candidate in generate_rotation_candidates(
                row.market,
                rotation_universe=universes.rotation_universe,
                derivatives=row.derivatives,
                regime=regime,
            )
        ],
        "short": [
            _candidate_row(candidate)
            for candidate in generate_short_candidates(
                row.market,
                short_universe=universes.short_universe,
                derivatives=row.derivatives,
                regime=regime,
            )
        ],
    }
    all_candidates = [candidate for engine_rows in raw_candidates.values() for candidate in engine_rows]
    account = dict(row.account or {"equity": 0.0, "available_balance": 0.0, "futures_wallet_balance": 0.0, "open_positions": []})
    validated_candidates = _validated_candidates(all_candidates, account)
    allocations = _allocation_rows(account, validated_candidates, regime, app_config=resolved_config)
    return {
        "snapshot": {"run_id": row.run_id, "timestamp": row.timestamp.isoformat()},
        "regime": regime,
        "suppression": _suppression_payload(regime),
        "universes": _universes_payload(universes),
        "raw_candidates": raw_candidates,
        "validated_candidates": validated_candidates,
        "allocations": allocations,
        "execution_assumptions": {
            "fee_bps": resolved_costs.fee_bps,
            "slippage_bps": resolved_costs.slippage_bps,
            "funding_bps_per_day": resolved_costs.funding_bps_per_day,
        },
    }


@dataclass(frozen=True, slots=True)
class _OpenTrade:
    symbol: str
    market_type: str
    base_asset: str
    side: str
    status: str
    entry_timestamp: Any
    entry_price: float
    qty: float
    position_notional: float
    liquidity_tier: str
    funding_rate: float


def _experiment_name(config: BacktestConfig) -> str:
    return f"{config.baseline_name}__{config.variant_name}"


def _windowed_rows(config: BacktestConfig) -> list[DatasetSnapshotRow]:
    rows = load_historical_dataset(config.dataset_root)
    if not config.sample_windows:
        return rows
    window = config.sample_windows[0]
    return [row for row in rows if window.start <= row.timestamp <= window.end]


def _empty_replay_result(config: BacktestConfig) -> BaselineReplayResult:
    return BaselineReplayResult(
        portfolio_summary=PortfolioScorecardRow(
            experiment_name=_experiment_name(config),
            total_return=0.0,
            max_drawdown=0.0,
            sharpe=0.0,
            sortino=0.0,
            calmar=0.0,
            turnover=0.0,
            trade_count=0,
        ),
        trade_ledger=(),
        rejection_ledger=(),
        cost_breakdown={"fees": 0.0, "slippage": 0.0, "funding": 0.0},
        gross_period_returns=(),
        net_period_returns=(),
    )


def _market_symbols(row: DatasetSnapshotRow) -> Mapping[str, Any]:
    symbols = row.market.get("symbols")
    if isinstance(symbols, Mapping):
        return symbols
    return {}


def _symbol_payload(row: DatasetSnapshotRow, symbol: str) -> Mapping[str, Any]:
    payload = _market_symbols(row).get(symbol)
    if isinstance(payload, Mapping):
        return payload
    return {}


def _reference_price(row: DatasetSnapshotRow, symbol: str) -> float:
    payload = _symbol_payload(row, symbol)
    for timeframe in ("daily", "4h", "1h"):
        timeframe_row = payload.get(timeframe)
        if isinstance(timeframe_row, Mapping):
            price = float(timeframe_row.get("close", 0.0) or 0.0)
            if price > 0.0:
                return price
    return 0.0


def _funding_rate(row: DatasetSnapshotRow, symbol: str) -> float:
    for item in row.derivatives:
        if str(item.get("symbol", "")) == symbol:
            return float(item.get("funding_rate", 0.0) or 0.0)
    return 0.0


def _raw_full_market_candidates(row: DatasetSnapshotRow) -> list[dict[str, Any]]:
    regime = _regime_dict(row)
    universes = build_universes(row.market, derivatives=row.derivatives)
    raw_candidates = [
        *[_candidate_row(candidate) for candidate in generate_trend_candidates(row.market, derivatives=row.derivatives)],
        *[
            _candidate_row(candidate)
            for candidate in generate_rotation_candidates(
                row.market,
                rotation_universe=universes.rotation_universe,
                derivatives=row.derivatives,
                regime=regime,
            )
        ],
        *[
            _candidate_row(candidate)
            for candidate in generate_short_candidates(
                row.market,
                short_universe=universes.short_universe,
                derivatives=row.derivatives,
                regime=regime,
            )
        ],
    ]
    return sorted(raw_candidates, key=_rank_key)


def _portfolio_candidate(
    candidate_row: Mapping[str, Any],
    *,
    instrument: InstrumentSnapshotRow,
    row: DatasetSnapshotRow,
) -> PortfolioCandidate | None:
    entry_price = _reference_price(row, instrument.symbol)
    stop_loss = float(candidate_row.get("stop_loss", 0.0) or 0.0)
    if entry_price <= 0.0 or stop_loss <= 0.0:
        return None
    return PortfolioCandidate(
        symbol=instrument.symbol,
        market_type=instrument.market_type,
        base_asset=instrument.base_asset,
        side="long" if str(candidate_row.get("side", "")).upper() == "LONG" else "short",
        entry_price=entry_price,
        stop_loss=stop_loss,
    )


def _portfolio_state(equity: float, positions: list[PortfolioPosition]) -> PortfolioState:
    return PortfolioState(initial_equity=equity, open_positions=tuple(positions))


def _trade_row(
    open_trade: _OpenTrade,
    *,
    exit_row: DatasetSnapshotRow,
    costs: BacktestCosts,
) -> tuple[TradeLedgerRow, float, float, float, float, float]:
    exit_price = _reference_price(exit_row, open_trade.symbol) or open_trade.entry_price
    direction = 1.0 if open_trade.side == "long" else -1.0
    holding_hours = (exit_row.timestamp - open_trade.entry_timestamp).total_seconds() / 3600.0
    gross_pnl = (exit_price - open_trade.entry_price) * open_trade.qty * direction
    fees = fee_cost(position_notional=open_trade.position_notional, market_type=open_trade.market_type, costs=costs)
    slippage = slippage_cost(
        position_notional=open_trade.position_notional,
        liquidity_tier=open_trade.liquidity_tier,
        costs=costs,
    )
    funding = funding_cost(
        position_notional=open_trade.position_notional,
        market_type=open_trade.market_type,
        side=open_trade.side,
        funding_rate=open_trade.funding_rate,
        holding_hours=holding_hours,
        costs=costs,
    )
    net_pnl = gross_pnl - fees - slippage - funding
    denominator = open_trade.position_notional if open_trade.position_notional > 0.0 else 1.0
    return (
        TradeLedgerRow(
            symbol=open_trade.symbol,
            market_type=open_trade.market_type,
            base_asset=open_trade.base_asset,
            side=open_trade.side,
            status=open_trade.status,
            entry_timestamp=open_trade.entry_timestamp,
            exit_timestamp=exit_row.timestamp,
            entry_price=open_trade.entry_price,
            exit_price=exit_price,
            qty=open_trade.qty,
            position_notional=open_trade.position_notional,
            holding_hours=holding_hours,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            gross_return_pct=gross_pnl / denominator,
            net_return_pct=net_pnl / denominator,
            fee_paid=fees,
            slippage_paid=slippage,
            funding_paid=funding,
        ),
        gross_pnl,
        net_pnl,
        fees,
        slippage,
        funding,
    )


def replay_full_market_baseline(config: BacktestConfig) -> BaselineReplayResult:
    if config.experiment_kind != "full_market_baseline":
        raise ValueError("replay_full_market_baseline requires experiment_kind='full_market_baseline'")
    if config.capital is None or config.universe is None:
        raise ValueError("full-market baseline replay requires capital and universe config")

    rows = _windowed_rows(config)
    if len(rows) < 2:
        return _empty_replay_result(config)

    equity = float(config.capital.initial_equity)
    open_trades: list[_OpenTrade] = []
    trade_ledger: list[TradeLedgerRow] = []
    rejection_ledger = []
    gross_period_returns: list[float] = []
    net_period_returns: list[float] = []
    traded_notionals: list[float] = []
    cost_breakdown = {"fees": 0.0, "slippage": 0.0, "funding": 0.0}

    for index, row in enumerate(rows):
        if open_trades:
            starting_equity = equity if equity > 0.0 else 1.0
            gross_period_pnl = 0.0
            net_period_pnl = 0.0
            for open_trade in open_trades:
                trade_row, gross_pnl, net_pnl, fees, slippage, funding = _trade_row(open_trade, exit_row=row, costs=config.costs)
                trade_ledger.append(trade_row)
                gross_period_pnl += gross_pnl
                net_period_pnl += net_pnl
                cost_breakdown["fees"] += fees
                cost_breakdown["slippage"] += slippage
                cost_breakdown["funding"] += funding
                traded_notionals.append(open_trade.position_notional)
            gross_period_returns.append(gross_period_pnl / starting_equity)
            net_period_returns.append(net_period_pnl / starting_equity)
            equity += net_period_pnl
            open_trades = []

        if index == len(rows) - 1:
            break

        included_rows, _excluded_rows = filter_universe(row.instrument_rows, universe_config=config.universe)
        included_by_symbol = {instrument.symbol: instrument for instrument in included_rows}
        open_positions: list[PortfolioPosition] = []
        for candidate_row in _raw_full_market_candidates(row):
            symbol = str(candidate_row.get("symbol", ""))
            instrument = included_by_symbol.get(symbol)
            if instrument is None:
                continue
            candidate = _portfolio_candidate(candidate_row, instrument=instrument, row=row)
            if candidate is None:
                continue
            decision = evaluate_candidate(candidate, state=_portfolio_state(equity, open_positions), capital=config.capital)
            ledger_row = decision_to_ledger_row(candidate, decision)
            if decision.status == "rejected":
                rejection_ledger.append(ledger_row)
                continue

            open_positions.append(
                PortfolioPosition(
                    symbol=candidate.symbol,
                    market_type=candidate.market_type,
                    base_asset=candidate.base_asset,
                    side=candidate.side,
                    risk_budget=decision.final_risk_budget,
                    position_notional=decision.position_notional,
                    qty=decision.qty,
                )
            )
            open_trades.append(
                _OpenTrade(
                    symbol=candidate.symbol,
                    market_type=candidate.market_type,
                    base_asset=candidate.base_asset,
                    side=candidate.side,
                    status=decision.status,
                    entry_timestamp=row.timestamp,
                    entry_price=candidate.entry_price,
                    qty=decision.qty,
                    position_notional=decision.position_notional,
                    liquidity_tier=instrument.liquidity_tier,
                    funding_rate=_funding_rate(row, candidate.symbol),
                )
            )

    portfolio_summary = PortfolioScorecardRow(
        experiment_name=_experiment_name(config),
        total_return=total_return(net_period_returns),
        max_drawdown=max_drawdown(net_period_returns),
        sharpe=sharpe_ratio(net_period_returns),
        sortino=sortino_ratio(net_period_returns),
        calmar=calmar_ratio(net_period_returns),
        turnover=turnover(traded_notionals, average_equity=config.capital.initial_equity),
        trade_count=len(trade_ledger),
    )
    return BaselineReplayResult(
        portfolio_summary=portfolio_summary,
        trade_ledger=tuple(trade_ledger),
        rejection_ledger=tuple(rejection_ledger),
        cost_breakdown={key: float(value) for key, value in cost_breakdown.items()},
        gross_period_returns=tuple(gross_period_returns),
        net_period_returns=tuple(net_period_returns),
    )
