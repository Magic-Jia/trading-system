from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping

from trading_system.app.config import DEFAULT_CONFIG, AppConfig, normalize_engine_names
from trading_system.app.market_regime.classifier import classify_regime
from trading_system.app.portfolio.allocator import allocate_candidates
from trading_system.app.risk.validator import validate_candidate_for_allocation
from trading_system.app.signals.rotation_engine import generate_rotation_candidates
from trading_system.app.signals.short_engine import generate_short_candidates
from trading_system.app.signals.trend_engine import generate_trend_candidates
from trading_system.app.universe.builder import UniverseBuildResult, build_universes

from .costs import fee_bps_for_market, fee_cost, funding_cost, slippage_bps_for_tier, slippage_cost
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
    PortfolioDecision,
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


def _regime_dict(row: DatasetSnapshotRow, *, disabled_engines: frozenset[str] | None = None) -> dict[str, Any]:
    override = row.meta.get("regime_override")
    if isinstance(override, Mapping):
        return dict(override)
    return asdict(classify_regime(row.market, row.derivatives, disabled_engines=disabled_engines))


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
    disabled_engines = frozenset(normalize_engine_names(getattr(resolved_config.execution, "disabled_engines", ())))
    regime = _regime_dict(row, disabled_engines=disabled_engines)
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
    engine: str = ""
    setup_type: str = ""
    score: float = 0.0
    stop_loss: float = 0.0
    take_profit: float | None = None
    cost_coverage_ratio: float | None = None


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


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0.0 else None


def _path_high_low(row: DatasetSnapshotRow, symbol: str) -> tuple[float | None, float | None]:
    payload = _symbol_payload(row, symbol)
    highs: list[float] = []
    lows: list[float] = []
    for timeframe in ("15m", "30m", "1h"):
        timeframe_row = payload.get(timeframe)
        if not isinstance(timeframe_row, Mapping):
            continue
        high = _float_or_none(timeframe_row.get("high"))
        low = _float_or_none(timeframe_row.get("low"))
        if high is not None:
            highs.append(high)
        if low is not None:
            lows.append(low)
    if not highs or not lows:
        return None, None
    return max(highs), min(lows)


def _mfe_mae_from_path(
    *,
    side: str,
    entry_price: float,
    exit_price: float,
    path_high: float,
    path_low: float,
) -> tuple[float, float, float]:
    raw_move_pct = (exit_price - entry_price) / entry_price if entry_price > 0.0 else 0.0
    if side == "long":
        exit_move_pct = raw_move_pct
        mfe_pct = max(0.0, (path_high - entry_price) / entry_price) if entry_price > 0.0 else 0.0
        mae_pct = max(0.0, (entry_price - path_low) / entry_price) if entry_price > 0.0 else 0.0
    else:
        exit_move_pct = -raw_move_pct
        mfe_pct = max(0.0, (entry_price - path_low) / entry_price) if entry_price > 0.0 else 0.0
        mae_pct = max(0.0, (path_high - entry_price) / entry_price) if entry_price > 0.0 else 0.0
    return mfe_pct, mae_pct, exit_move_pct


def _funding_rate(row: DatasetSnapshotRow, symbol: str) -> float:
    for item in row.derivatives:
        if str(item.get("symbol", "")) == symbol:
            return float(item.get("funding_rate", 0.0) or 0.0)
    return 0.0


def _raw_full_market_candidates(
    row: DatasetSnapshotRow,
    *,
    disabled_engines: frozenset[str] | None = None,
    allowed_short_setup_types: frozenset[str] | None = None,
    entry_profile: str | None = None,
) -> list[dict[str, Any]]:
    regime = _regime_dict(row, disabled_engines=disabled_engines)
    universes = build_universes(row.market, derivatives=row.derivatives)
    disabled = disabled_engines or frozenset()
    allowed_short_setups = allowed_short_setup_types or frozenset()
    raw_candidates: list[dict[str, Any]] = []
    if "trend" not in disabled:
        raw_candidates.extend(
            [
                _candidate_row(candidate)
                for candidate in generate_trend_candidates(
                    row.market,
                    derivatives=row.derivatives,
                    entry_profile=entry_profile,
                )
            ]
        )
    if "rotation" not in disabled:
        raw_candidates.extend(
            [
                _candidate_row(candidate)
                for candidate in generate_rotation_candidates(
                    row.market,
                    rotation_universe=universes.rotation_universe,
                    derivatives=row.derivatives,
                    regime=regime,
                    entry_profile=entry_profile,
                )
            ]
        )
    if "short" not in disabled:
        short_candidates = [
            _candidate_row(candidate)
            for candidate in generate_short_candidates(
                row.market,
                short_universe=universes.short_universe,
                derivatives=row.derivatives,
                regime=regime,
                entry_profile=entry_profile,
            )
        ]
        if allowed_short_setups:
            short_candidates = [
                candidate
                for candidate in short_candidates
                if str(candidate.get("setup_type", "")).strip().upper() in allowed_short_setups
            ]
        raw_candidates.extend(short_candidates)
    return sorted(raw_candidates, key=_rank_key)


def _candidate_take_profit_price(entry_price: float, stop_loss: float, side: str, r_multiple: float = 1.5) -> float | None:
    risk_per_unit = abs(float(entry_price) - float(stop_loss))
    if entry_price <= 0.0 or stop_loss <= 0.0 or risk_per_unit <= 0.0:
        return None
    if side.upper() == "LONG":
        return float(entry_price) + risk_per_unit * r_multiple
    if side.upper() == "SHORT":
        return float(entry_price) - risk_per_unit * r_multiple
    return None


def _portfolio_candidate(
    candidate_row: Mapping[str, Any],
    *,
    instrument: InstrumentSnapshotRow,
    row: DatasetSnapshotRow,
) -> PortfolioCandidate | None:
    entry_price = _reference_price(row, instrument.symbol)
    stop_loss = float(candidate_row.get("stop_loss", 0.0) or 0.0)
    side = str(candidate_row.get("side", "")).upper()
    take_profit_raw = candidate_row.get("take_profit")
    take_profit = float(take_profit_raw) if take_profit_raw is not None else None
    if take_profit is None or take_profit <= 0:
        take_profit = _candidate_take_profit_price(entry_price, stop_loss, side)
    if entry_price <= 0.0 or stop_loss <= 0.0:
        return None
    return PortfolioCandidate(
        symbol=instrument.symbol,
        market_type=instrument.market_type,
        base_asset=instrument.base_asset,
        side="long" if side == "LONG" else "short",
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit if take_profit is not None and take_profit > 0 else None,
    )


def _portfolio_state(equity: float, positions: list[PortfolioPosition]) -> PortfolioState:
    return PortfolioState(initial_equity=equity, open_positions=tuple(positions))


def _candidate_cost_coverage_ratio(
    candidate: PortfolioCandidate,
    *,
    instrument: InstrumentSnapshotRow,
    costs: BacktestCosts,
) -> float | None:
    if candidate.entry_price <= 0.0 or candidate.take_profit is None or candidate.take_profit <= 0.0:
        return None
    if candidate.side == "long":
        reward = float(candidate.take_profit) - float(candidate.entry_price)
    else:
        reward = float(candidate.entry_price) - float(candidate.take_profit)
    if reward <= 0.0:
        return 0.0
    expected_reward_pct = reward / float(candidate.entry_price)
    roundtrip_cost_bps = 2.0 * (
        fee_bps_for_market(costs, candidate.market_type)
        + slippage_bps_for_tier(costs, instrument.liquidity_tier)
    )
    required_cost_pct = roundtrip_cost_bps / 10_000.0
    if required_cost_pct <= 0.0:
        return None
    return expected_reward_pct / required_cost_pct


def _candidate_cost_coverage_ok(
    candidate: PortfolioCandidate,
    *,
    instrument: InstrumentSnapshotRow,
    costs: BacktestCosts,
    minimum_cost_coverage_ratio: float,
) -> bool:
    if minimum_cost_coverage_ratio <= 0.0:
        return True
    if candidate.entry_price <= 0.0 or candidate.take_profit is None or candidate.take_profit <= 0.0:
        return True
    coverage_ratio = _candidate_cost_coverage_ratio(candidate, instrument=instrument, costs=costs)
    return coverage_ratio is None or coverage_ratio >= float(minimum_cost_coverage_ratio)


def _trade_row(
    open_trade: _OpenTrade,
    *,
    exit_row: DatasetSnapshotRow,
    costs: BacktestCosts,
) -> tuple[TradeLedgerRow, float, float, float, float, float]:
    exit_price = _reference_price(exit_row, open_trade.symbol) or open_trade.entry_price
    direction = 1.0 if open_trade.side == "long" else -1.0
    holding_hours = (exit_row.timestamp - open_trade.entry_timestamp).total_seconds() / 3600.0
    path_high, path_low = _path_high_low(exit_row, open_trade.symbol)
    if path_high is None or path_low is None:
        path_high = max(open_trade.entry_price, exit_price)
        path_low = min(open_trade.entry_price, exit_price)
    else:
        path_high = max(path_high, open_trade.entry_price, exit_price)
        path_low = min(path_low, open_trade.entry_price, exit_price)
    mfe_pct, mae_pct, exit_move_pct = _mfe_mae_from_path(
        side=open_trade.side,
        entry_price=open_trade.entry_price,
        exit_price=exit_price,
        path_high=path_high,
        path_low=path_low,
    )
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
            engine=open_trade.engine,
            setup_type=open_trade.setup_type,
            score=open_trade.score,
            stop_loss=open_trade.stop_loss,
            take_profit=open_trade.take_profit,
            exit_reason="fixed_horizon",
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
            exit_move_pct=exit_move_pct,
            cost_coverage_ratio=open_trade.cost_coverage_ratio,
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
    return _replay_full_market_baseline_rows(config, rows)


def _replay_full_market_baseline_rows(
    config: BacktestConfig,
    rows: list[DatasetSnapshotRow] | tuple[DatasetSnapshotRow, ...],
) -> BaselineReplayResult:
    if len(rows) < 2:
        return _empty_replay_result(config)
    disabled_engines = frozenset(config.experiment_params.disabled_engines) if config.experiment_params is not None else frozenset()
    allowed_short_setup_types = (
        frozenset(config.experiment_params.allowed_short_setup_types) if config.experiment_params is not None else frozenset()
    )
    entry_profile = config.experiment_params.entry_profile if config.experiment_params is not None else None
    minimum_cost_coverage_ratio = (
        config.experiment_params.minimum_cost_coverage_ratio if config.experiment_params is not None else 0.0
    )

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
        for candidate_row in _raw_full_market_candidates(
            row,
            disabled_engines=disabled_engines,
            allowed_short_setup_types=allowed_short_setup_types,
            entry_profile=entry_profile,
        ):
            symbol = str(candidate_row.get("symbol", ""))
            instrument = included_by_symbol.get(symbol)
            if instrument is None:
                continue
            candidate = _portfolio_candidate(candidate_row, instrument=instrument, row=row)
            if candidate is None:
                continue
            if not _candidate_cost_coverage_ok(
                candidate,
                instrument=instrument,
                costs=config.costs,
                minimum_cost_coverage_ratio=minimum_cost_coverage_ratio,
            ):
                rejection_ledger.append(
                    decision_to_ledger_row(
                        candidate,
                        PortfolioDecision(
                            status="rejected",
                            reasons=("minimum_cost_coverage_not_met",),
                            final_risk_budget=0.0,
                            position_notional=0.0,
                            qty=0.0,
                        ),
                    )
                )
                continue
            cost_coverage_ratio = _candidate_cost_coverage_ratio(candidate, instrument=instrument, costs=config.costs)
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
                    engine=str(candidate_row.get("engine", "")),
                    setup_type=str(candidate_row.get("setup_type", "")),
                    score=float(candidate_row.get("score", 0.0) or 0.0),
                    stop_loss=candidate.stop_loss,
                    take_profit=candidate.take_profit,
                    cost_coverage_ratio=cost_coverage_ratio,
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
