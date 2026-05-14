from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import timedelta
import math
import re
from statistics import median
from typing import Any, Iterable, Literal, Mapping, Sequence

from .metrics import expectancy, max_drawdown, payoff_ratio, sharpe_ratio, sortino_ratio, total_return, win_rate
from .types import DatasetSnapshotRow, TradeLedgerRow

EvaluationStatus = Literal["ok", "insufficient_data"]
_COST_STRESS_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_REGIME_LABEL_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    window_index: int
    train_rows: tuple[DatasetSnapshotRow, ...]
    test_rows: tuple[DatasetSnapshotRow, ...]

    @property
    def train_start(self) -> Any:
        return self.train_rows[0].timestamp

    @property
    def train_end(self) -> Any:
        return self.train_rows[-1].timestamp

    @property
    def test_start(self) -> Any:
        return self.test_rows[0].timestamp

    @property
    def test_end(self) -> Any:
        return self.test_rows[-1].timestamp


@dataclass(frozen=True, slots=True)
class WalkForwardWindowEvaluation:
    window_index: int
    train_start: Any
    train_end: Any
    test_start: Any
    test_end: Any
    in_sample_metrics: dict[str, float | int]
    out_of_sample_metrics: dict[str, float | int]
    in_sample_trade_ids: tuple[str, ...]
    out_of_sample_trade_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_index": self.window_index,
            "train_period": {
                "start": self.train_start.isoformat(),
                "end": self.train_end.isoformat(),
            },
            "test_period": {
                "start": self.test_start.isoformat(),
                "end": self.test_end.isoformat(),
            },
            "splits": {
                "in_sample": {
                    "label": "IS",
                    "trade_ids": list(self.in_sample_trade_ids),
                    "metrics": dict(self.in_sample_metrics),
                },
                "out_of_sample": {
                    "label": "OOS",
                    "trade_ids": list(self.out_of_sample_trade_ids),
                    "metrics": dict(self.out_of_sample_metrics),
                },
            },
        }


@dataclass(frozen=True, slots=True)
class WalkForwardEvaluationResult:
    status: EvaluationStatus
    reason: str | None
    windows: tuple[WalkForwardWindowEvaluation, ...]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "metadata": dict(self.metadata),
            "windows": [window.to_dict() for window in self.windows],
        }


@dataclass(frozen=True, slots=True)
class CostStressScenario:
    name: str
    fee_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    funding_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _COST_STRESS_NAME_PATTERN.fullmatch(self.name) is None:
            raise ValueError("cost stress scenario name must be a canonical non-empty string")
        for field_name, value in (
            ("fee_multiplier", self.fee_multiplier),
            ("slippage_multiplier", self.slippage_multiplier),
            ("funding_multiplier", self.funding_multiplier),
        ):
            if isinstance(value, bool):
                raise ValueError(f"{field_name} must be a finite non-bool non-negative number")
            try:
                multiplier = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field_name} must be a finite non-bool non-negative number") from exc
            if not math.isfinite(multiplier) or multiplier < 0:
                raise ValueError(f"{field_name} must be a finite non-bool non-negative number")
            object.__setattr__(self, field_name, multiplier)

    def to_dict(self) -> dict[str, float | str]:
        return {
            "name": self.name,
            "fee_multiplier": self.fee_multiplier,
            "slippage_multiplier": self.slippage_multiplier,
            "funding_multiplier": self.funding_multiplier,
        }


@dataclass(frozen=True, slots=True)
class CostStressResult:
    scenario: CostStressScenario
    base_metrics: dict[str, float | int]
    stressed_metrics: dict[str, float | int]
    stressed_trades: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": f"cost_stress:{self.scenario.name}",
            "scenario": self.scenario.to_dict(),
            "base_metrics": dict(self.base_metrics),
            "stressed_metrics": dict(self.stressed_metrics),
            "stressed_trades": [dict(trade) for trade in self.stressed_trades],
        }


@dataclass(frozen=True, slots=True)
class RegimeBucket:
    label: str
    row_count: int
    row_ids: tuple[str, ...]
    metrics: dict[str, float | int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "row_count": self.row_count,
            "row_ids": list(self.row_ids),
            "metrics": dict(self.metrics),
        }


def _row_key(row: DatasetSnapshotRow) -> tuple[Any, str]:
    return row.timestamp, row.run_id


def _trade_id(trade: TradeLedgerRow) -> str:
    return f"{trade.symbol}@{trade.entry_timestamp.isoformat()}"


def _ordered_rows(rows: Iterable[DatasetSnapshotRow]) -> list[DatasetSnapshotRow]:
    return sorted(rows, key=_row_key)


def build_walk_forward_windows(
    rows: Iterable[DatasetSnapshotRow],
    *,
    train_size: int,
    test_size: int,
    step_size: int | None = None,
) -> tuple[WalkForwardWindow, ...]:
    if train_size <= 0:
        raise ValueError("train_size must be positive")
    if test_size <= 0:
        raise ValueError("test_size must be positive")
    effective_step_size = test_size if step_size is None else step_size
    if effective_step_size <= 0:
        raise ValueError("step_size must be positive")

    ordered = _ordered_rows(rows)
    minimum_size = train_size + test_size
    if len(ordered) < minimum_size:
        return ()

    windows: list[WalkForwardWindow] = []
    for start in range(0, len(ordered) - minimum_size + 1, effective_step_size):
        train_rows = tuple(ordered[start : start + train_size])
        test_rows = tuple(ordered[start + train_size : start + minimum_size])
        if train_rows[-1].timestamp >= test_rows[0].timestamp:
            raise ValueError("walk-forward train rows must end before test rows start")
        windows.append(
            WalkForwardWindow(
                window_index=len(windows) + 1,
                train_rows=train_rows,
                test_rows=test_rows,
            )
        )
    return tuple(windows)


def _row_cadence(rows: Sequence[DatasetSnapshotRow]) -> timedelta:
    ordered = _ordered_rows(rows)
    deltas = [
        ordered[index].timestamp - ordered[index - 1].timestamp
        for index in range(1, len(ordered))
        if ordered[index].timestamp > ordered[index - 1].timestamp
    ]
    if not deltas:
        return timedelta(days=1)
    return median(deltas)


def _segment_end_exclusive(rows: Sequence[DatasetSnapshotRow], cadence: timedelta) -> Any:
    return rows[-1].timestamp + cadence


def _trades_in_period(
    trade_ledger: Sequence[TradeLedgerRow],
    *,
    start: Any,
    end_exclusive: Any,
) -> tuple[TradeLedgerRow, ...]:
    return tuple(
        trade
        for trade in trade_ledger
        if start <= trade.entry_timestamp < end_exclusive
    )


def _metric_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or isinstance(value, str):
        raise ValueError(f"{path} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{path} must be a finite number")
    return number


def _ledger_metrics(
    trades: Sequence[TradeLedgerRow],
    *,
    net_pnls: Sequence[float] | None = None,
    net_returns: Sequence[float] | None = None,
    fees: Sequence[float] | None = None,
    slippage: Sequence[float] | None = None,
    funding: Sequence[float] | None = None,
) -> dict[str, float | int]:
    effective_net_pnls = (
        [_metric_number(value, f"net_pnls[{index}]") for index, value in enumerate(net_pnls)]
        if net_pnls is not None
        else [_metric_number(trade.net_pnl, f"trades[{index}].net_pnl") for index, trade in enumerate(trades)]
    )
    effective_net_returns = (
        [_metric_number(value, f"net_returns[{index}]") for index, value in enumerate(net_returns)]
        if net_returns is not None
        else [
            _metric_number(trade.net_return_pct, f"trades[{index}].net_return_pct")
            for index, trade in enumerate(trades)
        ]
    )
    effective_fees = (
        [_metric_number(value, f"fees[{index}]") for index, value in enumerate(fees)]
        if fees is not None
        else [_metric_number(trade.fee_paid, f"trades[{index}].fee_paid") for index, trade in enumerate(trades)]
    )
    effective_slippage = (
        [_metric_number(value, f"slippage[{index}]") for index, value in enumerate(slippage)]
        if slippage is not None
        else [
            _metric_number(trade.slippage_paid, f"trades[{index}].slippage_paid")
            for index, trade in enumerate(trades)
        ]
    )
    effective_funding = (
        [_metric_number(value, f"funding[{index}]") for index, value in enumerate(funding)]
        if funding is not None
        else [
            _metric_number(trade.funding_paid, f"trades[{index}].funding_paid")
            for index, trade in enumerate(trades)
        ]
    )
    gross_returns = [
        _metric_number(trade.gross_return_pct, f"trades[{index}].gross_return_pct")
        for index, trade in enumerate(trades)
    ]
    return {
        "trade_count": len(trades),
        "gross_pnl": round(sum(_metric_number(trade.gross_pnl, f"trades[{index}].gross_pnl") for index, trade in enumerate(trades)), 6),
        "net_pnl": round(sum(effective_net_pnls), 6),
        "fees": round(sum(effective_fees), 6),
        "slippage": round(sum(effective_slippage), 6),
        "funding": round(sum(effective_funding), 6),
        "total_gross_return": round(total_return(gross_returns), 6),
        "total_net_return": round(total_return(effective_net_returns), 6),
        "max_drawdown": round(max_drawdown(effective_net_returns), 6),
        "sharpe": round(sharpe_ratio(effective_net_returns), 6),
        "sortino": round(sortino_ratio(effective_net_returns), 6),
        "win_rate": round(win_rate(effective_net_pnls), 6),
        "payoff_ratio": round(payoff_ratio(effective_net_pnls), 6),
        "expectancy": round(expectancy(effective_net_pnls), 6),
    }


def build_walk_forward_evaluation(
    *,
    rows: Iterable[DatasetSnapshotRow],
    trade_ledger: Iterable[TradeLedgerRow],
    train_size: int,
    test_size: int,
    step_size: int | None = None,
) -> WalkForwardEvaluationResult:
    ordered = _ordered_rows(rows)
    effective_step_size = test_size if step_size is None else step_size
    windows = build_walk_forward_windows(
        ordered,
        train_size=train_size,
        test_size=test_size,
        step_size=effective_step_size,
    )
    metadata = {
        "row_count": len(ordered),
        "window_count": len(windows),
        "train_size": train_size,
        "test_size": test_size,
        "step_size": effective_step_size,
        "trade_timestamp_basis": "entry_timestamp",
    }
    if not windows:
        return WalkForwardEvaluationResult(
            status="insufficient_data",
            reason="dataset shorter than train_size + test_size",
            windows=(),
            metadata=metadata,
        )

    trades = tuple(sorted(trade_ledger, key=lambda trade: (trade.entry_timestamp, trade.symbol)))
    cadence = _row_cadence(ordered)
    evaluated_windows: list[WalkForwardWindowEvaluation] = []
    for window in windows:
        train_trades = _trades_in_period(
            trades,
            start=window.train_rows[0].timestamp,
            end_exclusive=_segment_end_exclusive(window.train_rows, cadence),
        )
        test_trades = _trades_in_period(
            trades,
            start=window.test_rows[0].timestamp,
            end_exclusive=_segment_end_exclusive(window.test_rows, cadence),
        )
        evaluated_windows.append(
            WalkForwardWindowEvaluation(
                window_index=window.window_index,
                train_start=window.train_start,
                train_end=window.train_end,
                test_start=window.test_start,
                test_end=window.test_end,
                in_sample_metrics=_ledger_metrics(train_trades),
                out_of_sample_metrics=_ledger_metrics(test_trades),
                in_sample_trade_ids=tuple(_trade_id(trade) for trade in train_trades),
                out_of_sample_trade_ids=tuple(_trade_id(trade) for trade in test_trades),
            )
        )

    return WalkForwardEvaluationResult(
        status="ok",
        reason=None,
        windows=tuple(evaluated_windows),
        metadata=metadata,
    )


def _daily_symbol_metric(row: DatasetSnapshotRow, key: str) -> list[float]:
    symbols = row.market.get("symbols", {}) if isinstance(row.market, Mapping) else {}
    if not isinstance(symbols, Mapping):
        return []
    values: list[float] = []
    for raw_symbol, payload in symbols.items():
        if not isinstance(payload, Mapping):
            continue
        daily = payload.get("daily")
        if not isinstance(daily, Mapping):
            continue
        if key not in daily:
            continue
        symbol = str(raw_symbol)
        values.append(_metric_number(daily[key], f"{row.run_id}.{symbol}.daily.{key}"))
    return values


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def deterministic_regime_label(row: DatasetSnapshotRow) -> str:
    meta_label = row.meta.get("regime_label")
    if meta_label is not None:
        return _canonical_regime_label(meta_label, row_id=row.run_id)
    regime = row.market.get("regime") if isinstance(row.market, Mapping) else None
    if isinstance(regime, Mapping) and regime.get("label") is not None:
        return _canonical_regime_label(regime["label"], row_id=row.run_id)

    returns = _daily_symbol_metric(row, "return_pct_7d")
    volatilities = _daily_symbol_metric(row, "atr_pct")
    closes = _daily_symbol_metric(row, "close")
    ema50s = _daily_symbol_metric(row, "ema_50")
    avg_return = _mean(returns)
    avg_volatility = _mean(volatilities)
    trend_values = [
        (close / ema50) - 1.0
        for close, ema50 in zip(closes, ema50s, strict=False)
        if ema50 != 0
    ]
    avg_trend = _mean(trend_values)

    volatility_label = "high_vol" if (avg_volatility or 0.0) >= 0.05 else "low_vol"
    direction_value = avg_return if avg_return is not None else (avg_trend or 0.0)
    trend_value = avg_trend if avg_trend is not None else direction_value
    if direction_value >= 0.02 and trend_value >= 0.0:
        direction_label = "uptrend"
    elif direction_value <= -0.02 and trend_value <= 0.0:
        direction_label = "downtrend"
    else:
        direction_label = "range"
    return f"{volatility_label}_{direction_label}"


def _canonical_regime_label(value: Any, *, row_id: str) -> str:
    if not isinstance(value, str) or _REGIME_LABEL_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{row_id} regime label must be a canonical non-empty string")
    return value


def evaluate_regime_buckets(
    rows: Iterable[DatasetSnapshotRow],
    trade_ledger: Iterable[TradeLedgerRow],
) -> tuple[RegimeBucket, ...]:
    ordered = _ordered_rows(rows)
    labels_by_run_id: dict[str, str] = {}
    for row in ordered:
        label = deterministic_regime_label(row)
        existing_label = labels_by_run_id.get(row.run_id)
        if existing_label is not None and existing_label != label:
            raise ValueError(f"duplicate regime row id with conflicting label: {row.run_id}")
        labels_by_run_id[row.run_id] = label
    row_ids_by_label: dict[str, list[str]] = {}
    for row in ordered:
        row_ids_by_label.setdefault(labels_by_run_id[row.run_id], []).append(row.run_id)

    timestamps = [row.timestamp for row in ordered]
    trades_by_label: dict[str, list[TradeLedgerRow]] = {label: [] for label in row_ids_by_label}
    for trade in sorted(trade_ledger, key=lambda item: (item.entry_timestamp, item.symbol)):
        row_index = bisect_right(timestamps, trade.entry_timestamp) - 1
        if row_index < 0:
            continue
        label = labels_by_run_id[ordered[row_index].run_id]
        trades_by_label.setdefault(label, []).append(trade)

    return tuple(
        RegimeBucket(
            label=label,
            row_count=len(row_ids),
            row_ids=tuple(row_ids),
            metrics=_ledger_metrics(trades_by_label.get(label, ())),
        )
        for label, row_ids in sorted(row_ids_by_label.items())
    )


def run_cost_stress_tests(
    trade_ledger: Iterable[TradeLedgerRow],
    scenarios: Iterable[CostStressScenario],
) -> tuple[CostStressResult, ...]:
    trades = tuple(trade_ledger)
    results: list[CostStressResult] = []
    seen_scenario_names: set[str] = set()
    for scenario in scenarios:
        if scenario.name in seen_scenario_names:
            raise ValueError(f"duplicate cost stress scenario name: {scenario.name}")
        seen_scenario_names.add(scenario.name)
        stressed_fees = [
            _metric_number(trade.fee_paid, f"trades[{index}].fee_paid") * scenario.fee_multiplier
            for index, trade in enumerate(trades)
        ]
        stressed_slippage = [
            _metric_number(trade.slippage_paid, f"trades[{index}].slippage_paid") * scenario.slippage_multiplier
            for index, trade in enumerate(trades)
        ]
        stressed_funding = [
            _metric_number(trade.funding_paid, f"trades[{index}].funding_paid") * scenario.funding_multiplier
            for index, trade in enumerate(trades)
        ]
        stressed_net_pnls = [
            _metric_number(trade.gross_pnl, f"trades[{index}].gross_pnl")
            - stressed_fee
            - stressed_slippage_paid
            - stressed_funding_paid
            for index, (trade, stressed_fee, stressed_slippage_paid, stressed_funding_paid) in enumerate(
                zip(trades, stressed_fees, stressed_slippage, stressed_funding, strict=True)
            )
        ]
        stressed_net_returns = [
            (
                stressed_net_pnl / position_notional
                if (position_notional := _metric_number(trade.position_notional, f"trades[{index}].position_notional"))
                else 0.0
            )
            for index, (trade, stressed_net_pnl) in enumerate(zip(trades, stressed_net_pnls, strict=True))
        ]
        stressed_trades = tuple(
            {
                "trade_id": _trade_id(trade),
                "symbol": trade.symbol,
                "entry_timestamp": trade.entry_timestamp.isoformat(),
                "base_net_pnl": round(float(trade.net_pnl), 6),
                "stressed_net_pnl": round(stressed_net_pnl, 6),
                "fee_paid": round(stressed_fee, 6),
                "slippage_paid": round(stressed_slippage_paid, 6),
                "funding_paid": round(stressed_funding_paid, 6),
            }
            for trade, stressed_net_pnl, stressed_fee, stressed_slippage_paid, stressed_funding_paid in zip(
                trades,
                stressed_net_pnls,
                stressed_fees,
                stressed_slippage,
                stressed_funding,
                strict=True,
            )
        )
        results.append(
            CostStressResult(
                scenario=scenario,
                base_metrics=_ledger_metrics(trades),
                stressed_metrics=_ledger_metrics(
                    trades,
                    net_pnls=stressed_net_pnls,
                    net_returns=stressed_net_returns,
                    fees=stressed_fees,
                    slippage=stressed_slippage,
                    funding=stressed_funding,
                ),
                stressed_trades=stressed_trades,
            )
        )
    return tuple(results)


def build_evaluation_report(
    *,
    rows: Iterable[DatasetSnapshotRow],
    trade_ledger: Iterable[TradeLedgerRow],
    train_size: int,
    test_size: int,
    step_size: int | None = None,
    cost_scenarios: Iterable[CostStressScenario] = (),
) -> dict[str, Any]:
    ordered_rows = tuple(_ordered_rows(rows))
    trades = tuple(trade_ledger)
    walk_forward = build_walk_forward_evaluation(
        rows=ordered_rows,
        trade_ledger=trades,
        train_size=train_size,
        test_size=test_size,
        step_size=step_size,
    )
    regimes = evaluate_regime_buckets(ordered_rows, trades)
    stress_results = run_cost_stress_tests(trades, cost_scenarios)
    return {
        "walk_forward": walk_forward.to_dict(),
        "regimes": {
            "label": "regime_split",
            "buckets": [bucket.to_dict() for bucket in regimes],
        },
        "cost_stress": {
            "label": "cost_stress",
            "scenarios": [result.to_dict() for result in stress_results],
        },
    }
