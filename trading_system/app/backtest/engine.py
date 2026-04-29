from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass, replace
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
from .execution_sim import (
    DepthLevel,
    ExecutionFill,
    OrderBookSnapshot,
    TradePrint,
    next_bar_ohlcv_fill,
    reference_close_fill,
    simulate_maker_limit_fill,
    simulate_taker_depth_fill,
    simulate_taker_fill,
)
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
    entry_reference_timeframe: str = ""
    entry_reference_price: float = 0.0
    gate_timeframes: tuple[str, ...] = ()
    trigger_timeframes: tuple[str, ...] = ()
    execution_price_source: str = "ohlcv_close"
    fill_model: str = "reference_close"
    fill_quality: str = "approximate"
    execution_timeframe: str = ""
    execution_lag_bars: int = 0
    requested_quantity: float | None = None
    requested_notional: float | None = None
    filled_quantity: float | None = None
    filled_notional: float | None = None
    unfilled_quantity: float | None = None
    depth_levels_consumed: int | None = None
    execution_impact_bps: float | None = None
    slippage_bps: float | None = None


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


def _reference_price_with_timeframe(
    row: DatasetSnapshotRow,
    symbol: str,
    *,
    timeframes: tuple[str, ...] = ("daily", "4h", "1h"),
) -> tuple[float, str]:
    payload = _symbol_payload(row, symbol)
    for timeframe in timeframes:
        timeframe_row = payload.get(timeframe)
        if isinstance(timeframe_row, Mapping):
            price = float(timeframe_row.get("close", 0.0) or 0.0)
            if price > 0.0:
                return price, timeframe
    return 0.0, ""


def _reference_price(row: DatasetSnapshotRow, symbol: str) -> float:
    price, _timeframe = _reference_price_with_timeframe(row, symbol)
    return price


def _reference_close_execution(row: DatasetSnapshotRow, symbol: str, side: str):
    return reference_close_fill(
        symbol=symbol,
        side="sell" if side == "short" else "buy",
        quantity=0.0,
        close_price=_reference_price(row, symbol),
    )


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0.0 else None


def _datetime_or_none(value: Any):
    if hasattr(value, "isoformat"):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _execution_evidence(
    row: DatasetSnapshotRow,
    symbol: str,
) -> tuple[tuple[OrderBookSnapshot, ...], tuple[TradePrint, ...]]:
    execution = _symbol_payload(row, symbol).get("execution")
    if not isinstance(execution, Mapping):
        return (), ()

    order_books: list[OrderBookSnapshot] = []
    raw_book = execution.get("order_book")
    raw_books = execution.get("order_books")
    book_rows: list[Any] = []
    if isinstance(raw_book, Mapping):
        book_rows.append(raw_book)
    if isinstance(raw_books, list):
        book_rows.extend(raw_books)
    for item in book_rows:
        if not isinstance(item, Mapping):
            continue
        bid = _float_or_none(item.get("bid"))
        ask = _float_or_none(item.get("ask"))
        if bid is None or ask is None:
            continue
        timestamp = _datetime_or_none(item.get("timestamp")) or row.timestamp
        order_books.append(
            OrderBookSnapshot(
                timestamp=timestamp,
                symbol=symbol,
                bid=bid,
                ask=ask,
                bid_size=_float_or_none(item.get("bid_size", item.get("bidSize"))),
                ask_size=_float_or_none(item.get("ask_size", item.get("askSize"))),
                bid_levels=_depth_levels(item.get("bids")),
                ask_levels=_depth_levels(item.get("asks")),
            )
        )

    trades: list[TradePrint] = []
    raw_trades = execution.get("trades")
    if isinstance(raw_trades, list):
        for item in raw_trades:
            if not isinstance(item, Mapping):
                continue
            price = _float_or_none(item.get("price"))
            quantity = _float_or_none(item.get("quantity"))
            if price is None or quantity is None:
                continue
            timestamp = _datetime_or_none(item.get("timestamp")) or row.timestamp
            trades.append(TradePrint(timestamp=timestamp, symbol=symbol, price=price, quantity=quantity))
    return tuple(order_books), tuple(trades)


def _depth_levels(value: Any) -> tuple[DepthLevel, ...]:
    if not isinstance(value, list):
        return ()
    levels: list[DepthLevel] = []
    for item in value:
        if isinstance(item, Mapping):
            price = _float_or_none(item.get("price"))
            quantity = _float_or_none(item.get("quantity", item.get("qty", item.get("size"))))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            price = _float_or_none(item[0])
            quantity = _float_or_none(item[1])
        else:
            continue
        if price is None or quantity is None:
            continue
        levels.append(DepthLevel(price=price, quantity=quantity))
    return tuple(levels)


def _path_high_low(row: DatasetSnapshotRow, symbol: str) -> tuple[float | None, float | None]:
    payload = _symbol_payload(row, symbol)
    for timeframe in ("1m", "5m", "15m", "30m", "1h"):
        timeframe_row = payload.get(timeframe)
        if not isinstance(timeframe_row, Mapping):
            continue
        high = _float_or_none(timeframe_row.get("high"))
        low = _float_or_none(timeframe_row.get("low"))
        if high is not None and low is not None:
            return high, low
    return None, None


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


def _simulate_intraday_exit(
    *,
    side: str,
    entry_price: float,
    fixed_exit_price: float,
    stop_loss: float,
    take_profit: float | None,
    path_high: float,
    path_low: float,
) -> tuple[str, float, float]:
    if entry_price <= 0.0:
        return "fixed_horizon", fixed_exit_price, 0.0
    if side == "long":
        stop_hit = stop_loss > 0.0 and path_low <= stop_loss
        take_profit_hit = take_profit is not None and take_profit > 0.0 and path_high >= take_profit
        if stop_hit:
            exit_reason = "stop_loss"
            simulated_exit_price = stop_loss
        elif take_profit_hit:
            exit_reason = "take_profit"
            simulated_exit_price = float(take_profit)
        else:
            exit_reason = "fixed_horizon"
            simulated_exit_price = fixed_exit_price
        simulated_exit_move_pct = (simulated_exit_price - entry_price) / entry_price
    else:
        stop_hit = stop_loss > 0.0 and path_high >= stop_loss
        take_profit_hit = take_profit is not None and take_profit > 0.0 and path_low <= take_profit
        if stop_hit:
            exit_reason = "stop_loss"
            simulated_exit_price = stop_loss
        elif take_profit_hit:
            exit_reason = "take_profit"
            simulated_exit_price = float(take_profit)
        else:
            exit_reason = "fixed_horizon"
            simulated_exit_price = fixed_exit_price
        simulated_exit_move_pct = (entry_price - simulated_exit_price) / entry_price
    return exit_reason, simulated_exit_price, simulated_exit_move_pct


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


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if item is not None and str(item))
    return ()


def _candidate_timeframe_meta(candidate_row: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = candidate_row.get("timeframe_meta")
    if isinstance(meta, Mapping):
        return meta
    return {}


def _entry_reference_timeframes(candidate_row: Mapping[str, Any]) -> tuple[str, ...]:
    meta = _candidate_timeframe_meta(candidate_row)
    explicit = _string_tuple(meta.get("entry_reference_timeframes"))
    if explicit:
        return explicit
    trigger_timeframes = _string_tuple(meta.get("trigger_timeframes"))
    if any(timeframe in {"15m", "30m"} for timeframe in trigger_timeframes):
        return ("15m", "30m", "1h", "4h", "daily")
    return ("daily", "4h", "1h")


def _has_intraday_entry_metadata(candidate_row: Mapping[str, Any], entry_reference_timeframe: str) -> bool:
    meta = _candidate_timeframe_meta(candidate_row)
    trigger_timeframes = _string_tuple(meta.get("trigger_timeframes"))
    entry_reference_timeframes = _string_tuple(meta.get("entry_reference_timeframes"))
    intraday_timeframes = {"15m", "30m"}
    return (
        entry_reference_timeframe in intraday_timeframes
        or any(timeframe in intraday_timeframes for timeframe in trigger_timeframes)
        or any(timeframe in intraday_timeframes for timeframe in entry_reference_timeframes)
    )


def _entry_execution_policy(candidate_row: Mapping[str, Any]) -> str:
    raw_policy = candidate_row.get("execution_policy")
    if raw_policy is None:
        raw_policy = _candidate_timeframe_meta(candidate_row).get("execution_policy")
    return str(raw_policy or "taker").strip().lower()


def _entry_execution_fill(
    *,
    row: DatasetSnapshotRow,
    symbol: str,
    order_side: str,
    entry_price: float,
    candidate_row: Mapping[str, Any],
    entry_reference_timeframe: str,
) -> ExecutionFill:
    order_books, trades = _execution_evidence(row, symbol)
    policy = _entry_execution_policy(candidate_row)
    if policy in {"maker", "post_only", "post-only", "maker_limit"}:
        return simulate_maker_limit_fill(
            symbol=symbol,
            side="sell" if order_side == "sell" else "buy",
            limit_price=entry_price,
            quantity=1.0,
            order_books=order_books,
            trades=trades,
        )

    if order_books or trades:
        return simulate_taker_fill(
            symbol=symbol,
            side="sell" if order_side == "sell" else "buy",
            quantity=0.0,
            reference_price=entry_price,
            order_books=order_books,
            trades=trades,
        )

    if _has_intraday_entry_metadata(candidate_row, entry_reference_timeframe):
        return next_bar_ohlcv_fill(
            symbol=symbol,
            side="sell" if order_side == "sell" else "buy",
            quantity=0.0,
            reference_close=entry_price,
            symbol_payload=_symbol_payload(row, symbol),
        )

    return reference_close_fill(
        symbol=symbol,
        side="sell" if order_side == "sell" else "buy",
        quantity=0.0,
        close_price=entry_price,
    )


def _portfolio_candidate(
    candidate_row: Mapping[str, Any],
    *,
    instrument: InstrumentSnapshotRow,
    row: DatasetSnapshotRow,
) -> PortfolioCandidate | None:
    entry_price, entry_reference_timeframe = _reference_price_with_timeframe(
        row,
        instrument.symbol,
        timeframes=_entry_reference_timeframes(candidate_row),
    )
    stop_loss = float(candidate_row.get("stop_loss", 0.0) or 0.0)
    side = str(candidate_row.get("side", "")).upper()
    order_side = "sell" if side == "SHORT" else "buy"
    entry_fill = _entry_execution_fill(
        row=row,
        symbol=instrument.symbol,
        order_side=order_side,
        entry_price=entry_price,
        candidate_row=candidate_row,
        entry_reference_timeframe=entry_reference_timeframe,
    )
    executed_entry_price = float(entry_fill.fill_price if entry_fill.fill_price is not None else entry_price)
    take_profit_raw = candidate_row.get("take_profit")
    take_profit = float(take_profit_raw) if take_profit_raw is not None else None
    if take_profit is None or take_profit <= 0:
        take_profit = _candidate_take_profit_price(entry_price, stop_loss, side)
    if entry_price <= 0.0 or stop_loss <= 0.0:
        return None
    timeframe_meta = _candidate_timeframe_meta(candidate_row)
    return PortfolioCandidate(
        symbol=instrument.symbol,
        market_type=instrument.market_type,
        base_asset=instrument.base_asset,
        side="long" if side == "LONG" else "short",
        entry_price=executed_entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit if take_profit is not None and take_profit > 0 else None,
        entry_reference_timeframe=entry_reference_timeframe,
        entry_reference_price=entry_price,
        gate_timeframes=_string_tuple(timeframe_meta.get("gate_timeframes")),
        trigger_timeframes=_string_tuple(timeframe_meta.get("trigger_timeframes")),
        execution_price_source=entry_fill.execution_price_source,
        fill_model=entry_fill.fill_model,
        fill_quality=entry_fill.fill_quality,
        execution_timeframe=entry_fill.execution_timeframe,
        execution_lag_bars=entry_fill.execution_lag_bars,
        requested_quantity=entry_fill.requested_quantity,
        requested_notional=entry_fill.requested_notional,
        filled_quantity=entry_fill.filled_quantity,
        filled_notional=entry_fill.filled_notional,
        unfilled_quantity=entry_fill.unfilled_quantity,
        depth_levels_consumed=entry_fill.depth_levels_consumed,
        execution_impact_bps=entry_fill.execution_impact_bps,
        slippage_bps=entry_fill.slippage_bps,
    )


def _candidate_with_execution_fill(candidate: PortfolioCandidate, fill: ExecutionFill) -> PortfolioCandidate:
    return replace(
        candidate,
        entry_price=float(fill.fill_price if fill.fill_price is not None else candidate.entry_price),
        execution_price_source=fill.execution_price_source,
        fill_model=fill.fill_model,
        fill_quality=fill.fill_quality,
        execution_timeframe=fill.execution_timeframe,
        execution_lag_bars=fill.execution_lag_bars,
        requested_quantity=fill.requested_quantity,
        requested_notional=fill.requested_notional,
        filled_quantity=fill.filled_quantity,
        filled_notional=fill.filled_notional,
        unfilled_quantity=fill.unfilled_quantity,
        depth_levels_consumed=fill.depth_levels_consumed,
        execution_impact_bps=fill.execution_impact_bps,
        slippage_bps=fill.slippage_bps,
    )


def _depth_fill_for_decision(
    *,
    row: DatasetSnapshotRow,
    candidate: PortfolioCandidate,
    decision: PortfolioDecision,
) -> ExecutionFill | None:
    order_books, _trades = _execution_evidence(row, candidate.symbol)
    if not order_books:
        return None
    book = sorted(order_books, key=lambda item: item.timestamp)[0]
    side = "sell" if candidate.side == "short" else "buy"
    levels = book.bid_levels if side == "sell" else book.ask_levels
    if not levels:
        return None
    reference_price = candidate.entry_reference_price if candidate.entry_reference_price > 0.0 else candidate.entry_price
    return simulate_taker_depth_fill(
        symbol=candidate.symbol,
        side=side,
        quantity=decision.qty,
        reference_price=reference_price,
        order_book=book,
    )


def _decision_with_depth_fill(
    *,
    decision: PortfolioDecision,
    fill: ExecutionFill,
    candidate: PortfolioCandidate,
    equity: float,
) -> PortfolioDecision:
    filled_quantity = float(fill.filled_quantity or 0.0)
    filled_notional = float(fill.filled_notional or 0.0)
    if filled_quantity <= 0.0 or filled_notional <= 0.0:
        return PortfolioDecision(
            status="rejected",
            reasons=("depth_no_fill_evidence",),
            final_risk_budget=0.0,
            position_notional=0.0,
            qty=0.0,
        )
    reasons = tuple(decision.reasons)
    status = decision.status
    if fill.fill_quality == "partial_evidence_backed":
        status = "resized"
        reasons = tuple(dict.fromkeys((*reasons, "depth_liquidity_limited")))
    stop_distance = abs(float(candidate.entry_price) - float(candidate.stop_loss))
    risk_budget = (filled_quantity * stop_distance / equity) if equity > 0.0 and stop_distance > 0.0 else decision.final_risk_budget
    return PortfolioDecision(
        status=status,
        reasons=reasons,
        final_risk_budget=round(min(float(decision.final_risk_budget), risk_budget), 10),
        position_notional=round(filled_notional, 8),
        qty=round(filled_quantity, 8),
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
    has_intraday_path = path_high is not None and path_low is not None
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
    if has_intraday_path:
        simulated_exit_reason, simulated_exit_price, simulated_exit_move_pct = _simulate_intraday_exit(
            side=open_trade.side,
            entry_price=open_trade.entry_price,
            fixed_exit_price=exit_price,
            stop_loss=open_trade.stop_loss,
            take_profit=open_trade.take_profit,
            path_high=path_high,
            path_low=path_low,
        )
    else:
        simulated_exit_reason = "fixed_horizon"
        simulated_exit_price = exit_price
        simulated_exit_move_pct = exit_move_pct
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
    simulated_gross_pnl = (simulated_exit_price - open_trade.entry_price) * open_trade.qty * direction
    simulated_net_pnl = simulated_gross_pnl - fees - slippage - funding
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
            simulated_exit_reason=simulated_exit_reason,
            simulated_exit_price=simulated_exit_price,
            simulated_exit_move_pct=simulated_exit_move_pct,
            simulated_gross_pnl=simulated_gross_pnl,
            simulated_net_pnl=simulated_net_pnl,
            cost_coverage_ratio=open_trade.cost_coverage_ratio,
            entry_reference_timeframe=open_trade.entry_reference_timeframe,
            entry_reference_price=open_trade.entry_reference_price,
            gate_timeframes=open_trade.gate_timeframes,
            trigger_timeframes=open_trade.trigger_timeframes,
            execution_price_source=open_trade.execution_price_source,
            fill_model=open_trade.fill_model,
            fill_quality=open_trade.fill_quality,
            execution_timeframe=open_trade.execution_timeframe,
            execution_lag_bars=open_trade.execution_lag_bars,
            requested_quantity=open_trade.requested_quantity,
            requested_notional=open_trade.requested_notional,
            filled_quantity=open_trade.filled_quantity,
            filled_notional=open_trade.filled_notional,
            unfilled_quantity=open_trade.unfilled_quantity,
            depth_levels_consumed=open_trade.depth_levels_consumed,
            execution_impact_bps=open_trade.execution_impact_bps,
            slippage_bps=open_trade.slippage_bps,
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
            if candidate.fill_quality == "no_fill":
                rejection_ledger.append(
                    decision_to_ledger_row(
                        candidate,
                        PortfolioDecision(
                            status="rejected",
                            reasons=("maker_no_fill_evidence",),
                            final_risk_budget=0.0,
                            position_notional=0.0,
                            qty=0.0,
                        ),
                    )
                )
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
            depth_fill = _depth_fill_for_decision(row=row, candidate=candidate, decision=decision)
            if depth_fill is not None:
                candidate = _candidate_with_execution_fill(candidate, depth_fill)
                decision = _decision_with_depth_fill(
                    decision=decision,
                    fill=depth_fill,
                    candidate=candidate,
                    equity=equity,
                )
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
                    entry_reference_timeframe=candidate.entry_reference_timeframe,
                    entry_reference_price=candidate.entry_reference_price,
                    gate_timeframes=candidate.gate_timeframes,
                    trigger_timeframes=candidate.trigger_timeframes,
                    execution_price_source=candidate.execution_price_source,
                    fill_model=candidate.fill_model,
                    fill_quality=candidate.fill_quality,
                    execution_timeframe=candidate.execution_timeframe,
                    execution_lag_bars=candidate.execution_lag_bars,
                    requested_quantity=candidate.requested_quantity,
                    requested_notional=candidate.requested_notional,
                    filled_quantity=candidate.filled_quantity,
                    filled_notional=candidate.filled_notional,
                    unfilled_quantity=candidate.unfilled_quantity,
                    depth_levels_consumed=candidate.depth_levels_consumed,
                    execution_impact_bps=candidate.execution_impact_bps,
                    slippage_bps=candidate.slippage_bps,
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
