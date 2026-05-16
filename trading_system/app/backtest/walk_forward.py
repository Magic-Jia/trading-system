from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from statistics import pstdev
from typing import Any, Iterable, Mapping, Sequence

from .metrics import calmar_ratio, expectancy, max_drawdown, payoff_ratio, sharpe_ratio, sortino_ratio, total_return, win_rate
from .types import DatasetSnapshotRow


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    window_index: int
    in_sample: tuple[DatasetSnapshotRow, ...]
    out_of_sample: tuple[DatasetSnapshotRow, ...]


def _row_key(row: DatasetSnapshotRow) -> tuple[object, str]:
    return row.timestamp, row.run_id


def effective_walk_forward_step_size(
    *,
    out_of_sample_size: int,
    step_size: int | None,
) -> int:
    effective_step_size = out_of_sample_size if step_size is None else step_size
    if effective_step_size <= 0:
        raise ValueError("step_size must be positive")
    return effective_step_size


def build_walk_forward_windows(
    rows: Iterable[DatasetSnapshotRow],
    *,
    in_sample_size: int,
    out_of_sample_size: int,
    step_size: int | None = None,
) -> list[WalkForwardWindow]:
    if in_sample_size <= 0:
        raise ValueError("in_sample_size must be positive")
    if out_of_sample_size <= 0:
        raise ValueError("out_of_sample_size must be positive")

    effective_step_size = effective_walk_forward_step_size(
        out_of_sample_size=out_of_sample_size,
        step_size=step_size,
    )
    ordered_rows = sorted(rows, key=_row_key)
    minimum_window_size = in_sample_size + out_of_sample_size
    if len(ordered_rows) < minimum_window_size:
        return []

    windows: list[WalkForwardWindow] = []
    for start in range(0, len(ordered_rows) - minimum_window_size + 1, effective_step_size):
        in_sample = tuple(ordered_rows[start : start + in_sample_size])
        out_of_sample = tuple(ordered_rows[start + in_sample_size : start + minimum_window_size])
        if _row_key(in_sample[-1]) >= _row_key(out_of_sample[0]):
            raise ValueError("walk-forward window must preserve in-sample then out-of-sample ordering")
        windows.append(
            WalkForwardWindow(
                window_index=len(windows) + 1,
                in_sample=in_sample,
                out_of_sample=out_of_sample,
            )
        )

    return windows


def _strict_finite_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field_name} must be a finite number")
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise ValueError(f"{field_name} must be a finite number")
    return numeric_value


def summarize_return_scorecard(returns: Sequence[float]) -> dict[str, float | int]:
    numeric_returns = [
        _strict_finite_number(value, field_name=f"returns[{index}]")
        for index, value in enumerate(returns)
    ]
    return {
        "total_return": round(total_return(numeric_returns), 6),
        "max_drawdown": round(max_drawdown(numeric_returns), 6),
        "sharpe": round(sharpe_ratio(numeric_returns), 6),
        "sortino": round(sortino_ratio(numeric_returns), 6),
        "calmar": round(calmar_ratio(numeric_returns), 6),
        "win_rate": round(win_rate(numeric_returns), 6),
        "payoff_ratio": round(payoff_ratio(numeric_returns), 6),
        "expectancy": round(expectancy(numeric_returns), 6),
        "trade_count": len(numeric_returns),
    }


def summarize_walk_forward_segment(
    rows: Sequence[DatasetSnapshotRow],
    *,
    evaluation_window: str,
) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=_row_key)
    if not ordered_rows:
        return {
            "run_ids": [],
            "snapshot_count": 0,
            "start_timestamp": None,
            "end_timestamp": None,
            "scorecard": summarize_return_scorecard(()),
        }

    returns = []
    for row in ordered_rows:
        if evaluation_window not in row.forward_returns:
            raise ValueError(f"forward_returns.{evaluation_window} must be present")
        returns.append(
            _strict_finite_number(
                row.forward_returns[evaluation_window],
                field_name=f"forward_returns.{evaluation_window}",
            )
        )
    return {
        "run_ids": [row.run_id for row in ordered_rows],
        "snapshot_count": len(ordered_rows),
        "start_timestamp": ordered_rows[0].timestamp.isoformat(),
        "end_timestamp": ordered_rows[-1].timestamp.isoformat(),
        "scorecard": summarize_return_scorecard(returns),
    }


def summarize_walk_forward_window(
    window: WalkForwardWindow,
    *,
    evaluation_window: str,
) -> dict[str, Any]:
    return {
        "window_index": window.window_index,
        "in_sample": summarize_walk_forward_segment(
            window.in_sample,
            evaluation_window=evaluation_window,
        ),
        "out_of_sample": summarize_walk_forward_segment(
            window.out_of_sample,
            evaluation_window=evaluation_window,
        ),
    }


def _scorecard_metric(
    window_summary: Mapping[str, Any],
    *,
    split: str,
    metric: str,
) -> float:
    segment = dict(window_summary.get(split, {}))
    scorecard = dict(segment.get("scorecard", {}))
    return _strict_finite_number(
        scorecard.get(metric, 0.0),
        field_name=f"{split}.scorecard.{metric}",
    )


def _value_band(values: Sequence[float]) -> dict[str, float]:
    numeric_values = sorted(
        _strict_finite_number(value, field_name=f"values[{index}]")
        for index, value in enumerate(values)
    )
    if not numeric_values:
        return {"min": 0.0, "median": 0.0, "max": 0.0}

    midpoint = len(numeric_values) // 2
    median = (
        numeric_values[midpoint]
        if len(numeric_values) % 2 == 1
        else (numeric_values[midpoint - 1] + numeric_values[midpoint]) / 2.0
    )
    return {
        "min": round(numeric_values[0], 6),
        "median": round(median, 6),
        "max": round(numeric_values[-1], 6),
    }


def _bounded_ratio(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def summarize_walk_forward_robustness(
    window_summaries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not window_summaries:
        return {
            "in_sample_scorecard": summarize_return_scorecard(()),
            "out_of_sample_scorecard": summarize_return_scorecard(()),
            "performance_dispersion": {
                "window_count": 0,
                "positive_window_ratio": 0.0,
                "average_out_of_sample_return": 0.0,
                "return_std_dev": 0.0,
                "best_window_total_return": 0.0,
                "worst_window_total_return": 0.0,
            },
            "worst_window": None,
        }

    in_sample_returns = [_scorecard_metric(window, split="in_sample", metric="total_return") for window in window_summaries]
    out_of_sample_returns = [_scorecard_metric(window, split="out_of_sample", metric="total_return") for window in window_summaries]
    positive_window_ratio = sum(1 for value in out_of_sample_returns if value > 0.0) / len(out_of_sample_returns)
    worst_window_summary = min(
        window_summaries,
        key=lambda window: _scorecard_metric(window, split="out_of_sample", metric="total_return"),
    )
    worst_out_of_sample = dict(worst_window_summary.get("out_of_sample", {}))

    return {
        "in_sample_scorecard": summarize_return_scorecard(in_sample_returns),
        "out_of_sample_scorecard": summarize_return_scorecard(out_of_sample_returns),
        "performance_dispersion": {
            "window_count": len(window_summaries),
            "positive_window_ratio": round(positive_window_ratio, 6),
            "average_out_of_sample_return": round(expectancy(out_of_sample_returns), 6),
            "return_std_dev": round(pstdev(out_of_sample_returns), 6) if len(out_of_sample_returns) > 1 else 0.0,
            "best_window_total_return": round(max(out_of_sample_returns), 6),
            "worst_window_total_return": round(min(out_of_sample_returns), 6),
        },
        "worst_window": {
            "window_index": int(worst_window_summary.get("window_index", 0)),
            "start_timestamp": worst_out_of_sample.get("start_timestamp"),
            "end_timestamp": worst_out_of_sample.get("end_timestamp"),
            "scorecard": dict(worst_out_of_sample.get("scorecard", {})),
        },
    }


def summarize_parameter_stability(
    window_summaries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not window_summaries:
        zero_band = _value_band(())
        return {
            "edge_retention_ratio": 0.0,
            "worst_window_retention_ratio": 0.0,
            "positive_window_ratio": 0.0,
            "parameter_stability_score": 0.0,
            "sensitivity_bands": {
                "out_of_sample_total_return": zero_band,
                "out_of_sample_sharpe": zero_band,
                "out_of_sample_calmar": zero_band,
            },
            "stability_score_threshold": 0.5,
            "selected_optimum": {
                "parameters": {"walk_forward_window": 0.0},
                "metric": "out_of_sample_total_return",
                "value": 0.0,
            },
            "stability_surface": [
                {
                    "parameter_name": "walk_forward_window",
                    "tested_values": [0.0],
                    "tested_range": {"min": 0.0, "max": 0.0},
                    "neighborhood_metrics": {
                        "mean_neighbor_metric": 0.0,
                        "worst_neighbor_metric": 0.0,
                        "neighbor_count": 1,
                    },
                }
            ],
            "isolated_spike": {
                "is_isolated": False,
                "rejection_reason": None,
            },
        }

    in_sample_returns = [_scorecard_metric(window, split="in_sample", metric="total_return") for window in window_summaries]
    out_of_sample_returns = [_scorecard_metric(window, split="out_of_sample", metric="total_return") for window in window_summaries]
    out_of_sample_sharpes = [_scorecard_metric(window, split="out_of_sample", metric="sharpe") for window in window_summaries]
    out_of_sample_calmars = [_scorecard_metric(window, split="out_of_sample", metric="calmar") for window in window_summaries]

    mean_in_sample_return = expectancy(in_sample_returns)
    mean_out_of_sample_return = expectancy(out_of_sample_returns)
    worst_window_return = min(out_of_sample_returns)
    positive_window_ratio = sum(1 for value in out_of_sample_returns if value > 0.0) / len(out_of_sample_returns)
    edge_retention_ratio = mean_out_of_sample_return / mean_in_sample_return if mean_in_sample_return > 0.0 else 0.0
    worst_window_retention_ratio = worst_window_return / mean_in_sample_return if mean_in_sample_return > 0.0 else 0.0
    parameter_stability_score = (
        _bounded_ratio(edge_retention_ratio)
        + _bounded_ratio(worst_window_retention_ratio)
        + positive_window_ratio
    ) / 3.0
    best_out_of_sample_return = max(out_of_sample_returns)
    best_window_position = out_of_sample_returns.index(best_out_of_sample_return) + 1
    neighbor_returns = [
        value
        for index, value in enumerate(out_of_sample_returns, start=1)
        if abs(index - best_window_position) == 1
    ]
    if not neighbor_returns:
        neighbor_returns = [best_out_of_sample_return]
    mean_neighbor_metric = expectancy(neighbor_returns)
    worst_neighbor_metric = min(neighbor_returns)
    stability_score_threshold = 0.5
    isolated_spike_reason = (
        "selected_optimum_neighbors_fail_threshold"
        if parameter_stability_score >= stability_score_threshold
        and best_out_of_sample_return > 0.0
        and worst_neighbor_metric <= 0.0
        else None
    )

    return {
        "edge_retention_ratio": round(edge_retention_ratio, 6),
        "worst_window_retention_ratio": round(worst_window_retention_ratio, 6),
        "positive_window_ratio": round(positive_window_ratio, 6),
        "parameter_stability_score": round(parameter_stability_score, 6),
        "sensitivity_bands": {
            "out_of_sample_total_return": _value_band(out_of_sample_returns),
            "out_of_sample_sharpe": _value_band(out_of_sample_sharpes),
            "out_of_sample_calmar": _value_band(out_of_sample_calmars),
        },
        "stability_score_threshold": stability_score_threshold,
        "selected_optimum": {
            "parameters": {"walk_forward_window": float(best_window_position)},
            "metric": "out_of_sample_total_return",
            "value": round(best_out_of_sample_return, 6),
        },
        "stability_surface": [
            {
                "parameter_name": "walk_forward_window",
                "tested_values": [float(index) for index in range(1, len(out_of_sample_returns) + 1)],
                "tested_range": {"min": 1.0, "max": float(len(out_of_sample_returns))},
                "neighborhood_metrics": {
                    "mean_neighbor_metric": round(mean_neighbor_metric, 6),
                    "worst_neighbor_metric": round(worst_neighbor_metric, 6),
                    "neighbor_count": len(neighbor_returns),
                },
            }
        ],
        "isolated_spike": {
            "is_isolated": isolated_spike_reason is not None,
            "rejection_reason": isolated_spike_reason,
        },
    }
