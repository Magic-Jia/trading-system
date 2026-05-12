from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from numbers import Real
from typing import Any, Mapping, Sequence

from .exit_policies import ExitFillQuality, evaluate_exit_policy
from .types import ExitPolicyParams


@dataclass(frozen=True, slots=True)
class _TradePrintPoint:
    timestamp: datetime
    price: float


def serialize_exit_policy(policy: ExitPolicyParams | None) -> dict[str, Any] | None:
    if policy is None:
        return None
    return {
        "name": policy.name,
        "after_cost_buffer_bps": policy.after_cost_buffer_bps,
        "activation_minute": policy.activation_minute,
        "giveback_fraction": policy.giveback_fraction,
        "giveback_min_bps": policy.giveback_min_bps,
        "no_breakeven_time_stop_minute": policy.no_breakeven_time_stop_minute,
    }


def build_exit_policy_experiment(
    *,
    trades: Sequence[Mapping[str, Any]],
    policy: ExitPolicyParams,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evaluation_rows = [_evaluate_trade(index=index, trade=trade, policy=policy) for index, trade in enumerate(trades, start=1)]
    summary = {
        "total_trades": len(evaluation_rows),
        "evaluated_count": sum(1 for row in evaluation_rows if row["evaluation_status"] != "skipped"),
        "triggered_count": sum(1 for row in evaluation_rows if row["evaluation_status"] == "triggered"),
        "not_triggered_count": sum(1 for row in evaluation_rows if row["evaluation_status"] == "not_triggered"),
        "no_evidence_count": sum(1 for row in evaluation_rows if row["evaluation_status"] == "no_evidence"),
        "skipped_count": sum(1 for row in evaluation_rows if row["evaluation_status"] == "skipped"),
    }
    base_metadata = dict(metadata or {})
    base_metadata.update(
        {
            "artifact_type": "opt_in_offline_diagnostic",
            "changes_baseline_ledger": False,
            "policy": serialize_exit_policy(policy),
        }
    )
    return {
        "metadata": base_metadata,
        "summary": summary,
        "evaluation_rows": evaluation_rows,
    }


def _evaluate_trade(*, index: int, trade: Mapping[str, Any], policy: ExitPolicyParams) -> dict[str, Any]:
    identity = {
        "trade_index": index,
        "symbol": _optional_present_string(trade, field="symbol", index=index),
        "market_type": _optional_present_string(trade, field="market_type", index=index),
        "base_asset": _optional_present_string(trade, field="base_asset", index=index),
        "side": _optional_present_string(trade, field="side", index=index),
        "status": _optional_present_string(trade, field="status", index=index),
        "entry_timestamp": _iso_or_none(_parse_timestamp(trade.get("entry_timestamp"))),
        "baseline_exit_timestamp": _iso_or_none(_parse_timestamp(trade.get("exit_timestamp"))),
        "entry_price": _optional_present_finite_number(trade, field="entry_price", index=index),
        "baseline_exit_price": _optional_present_finite_number(trade, field="exit_price", index=index),
        "qty": _optional_present_finite_number(trade, field="qty", index=index),
    }
    side = identity["side"]
    entry_timestamp = _parse_timestamp(trade.get("entry_timestamp"))
    fixed_exit_timestamp = _parse_timestamp(trade.get("exit_timestamp"))
    entry_price = identity["entry_price"]

    if side not in {"long", "short"} or entry_timestamp is None or fixed_exit_timestamp is None or entry_price is None or entry_price <= 0.0:
        return {
            **identity,
            "trade_print_path": None,
            "evaluation_status": "skipped",
            "evaluation_reason": "missing_required_fields",
            "diagnostic_exit_timestamp": None,
            "diagnostic_exit_price": None,
            "diagnostic_exit_price_source": "none",
            "diagnostic_fill_quality": "no_evidence",
            "diagnostic_policy_gross_pnl": None,
            "diagnostic_policy_net_pnl": None,
        }

    trade_print_path, raw_trade_prints = _trade_print_source(trade)
    if trade_print_path is None:
        return {
            **identity,
            "trade_print_path": None,
            "evaluation_status": "no_evidence",
            "evaluation_reason": "missing_trade_print_path",
            "diagnostic_exit_timestamp": None,
            "diagnostic_exit_price": None,
            "diagnostic_exit_price_source": "none",
            "diagnostic_fill_quality": "no_evidence",
            "diagnostic_policy_gross_pnl": None,
            "diagnostic_policy_net_pnl": None,
        }

    trade_prints = _trade_print_points(raw_trade_prints, path=f"trades[{index}].{trade_print_path}")
    if not trade_prints:
        return {
            **identity,
            "trade_print_path": trade_print_path,
            "evaluation_status": "no_evidence",
            "evaluation_reason": "no_eligible_trade_prints",
            "diagnostic_exit_timestamp": None,
            "diagnostic_exit_price": None,
            "diagnostic_exit_price_source": "none",
            "diagnostic_fill_quality": "no_evidence",
            "diagnostic_policy_gross_pnl": None,
            "diagnostic_policy_net_pnl": None,
        }

    evaluation = evaluate_exit_policy(
        side=side,
        entry_price=entry_price,
        entry_timestamp=entry_timestamp,
        fixed_exit_timestamp=fixed_exit_timestamp,
        trade_prints=trade_prints,
        policy=policy,
        total_cost_bps=_total_cost_bps(trade, entry_price=entry_price),
    )
    evaluation_status = "triggered" if evaluation.triggered else "not_triggered"
    diagnostic_gross_pnl, diagnostic_net_pnl = _diagnostic_pnl(
        trade=trade,
        side=side,
        entry_price=entry_price,
        exit_price=evaluation.exit_price,
        fill_quality=evaluation.fill_quality,
    )
    return {
        **identity,
        "trade_print_path": trade_print_path,
        "evaluation_status": evaluation_status,
        "evaluation_reason": evaluation.exit_policy_reason,
        "diagnostic_exit_timestamp": _iso_or_none(evaluation.exit_timestamp),
        "diagnostic_exit_price": evaluation.exit_price,
        "diagnostic_exit_price_source": evaluation.exit_price_source,
        "diagnostic_fill_quality": evaluation.fill_quality,
        "diagnostic_policy_gross_pnl": diagnostic_gross_pnl,
        "diagnostic_policy_net_pnl": diagnostic_net_pnl,
    }


def _trade_print_source(trade: Mapping[str, Any]) -> tuple[str | None, Any]:
    if "exit_trade_prints" in trade:
        return "exit_trade_prints", trade.get("exit_trade_prints")
    if "trade_prints" in trade:
        return "trade_prints", trade.get("trade_prints")
    return None, None


def _trade_print_points(raw_trade_prints: Any, *, path: str) -> tuple[_TradePrintPoint, ...]:
    if not isinstance(raw_trade_prints, Sequence) or isinstance(raw_trade_prints, (str, bytes, bytearray)):
        raise ValueError(f"{path} must be a sequence of trade print mappings")
    points: list[_TradePrintPoint] = []
    for row_index, item in enumerate(raw_trade_prints, start=1):
        row_path = f"{path}[{row_index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{row_path} must be a mapping")
        timestamp = _required_present_timestamp(item, field="timestamp", path=row_path)
        price = _required_present_positive_finite_number(item, field="price", path=row_path)
        points.append(_TradePrintPoint(timestamp=timestamp, price=price))
    return tuple(points)


def _optional_present_string(trade: Mapping[str, Any], *, field: str, index: int) -> str:
    if field not in trade:
        return ""
    value = trade[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"trades[{index}].{field} must be a non-blank string when present")
    return value


def _optional_present_finite_number(trade: Mapping[str, Any], *, field: str, index: int) -> float | None:
    if field not in trade or trade[field] is None:
        return None
    value = trade[field]
    if not _is_finite_number(value):
        raise ValueError(f"trades[{index}].{field} must be a finite number when present")
    return float(value)


def _required_present_timestamp(row: Mapping[str, Any], *, field: str, path: str) -> datetime:
    if field not in row:
        raise ValueError(f"{path}.{field} is required")
    timestamp = _parse_timestamp(row[field])
    if timestamp is None:
        raise ValueError(f"{path}.{field} must be an ISO timestamp")
    return timestamp


def _required_present_positive_finite_number(row: Mapping[str, Any], *, field: str, path: str) -> float:
    if field not in row:
        raise ValueError(f"{path}.{field} is required")
    value = row[field]
    if not _is_finite_number(value) or float(value) <= 0.0:
        raise ValueError(f"{path}.{field} must be a positive finite number")
    return float(value)


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(float(value))


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _position_notional(trade: Mapping[str, Any], *, entry_price: float) -> float | None:
    position_notional = _float_or_none(trade.get("position_notional"))
    if position_notional is not None and position_notional > 0.0:
        return position_notional
    qty = _float_or_none(trade.get("qty"))
    if qty is None or qty <= 0.0:
        return None
    return entry_price * qty


def _total_cost_amount(trade: Mapping[str, Any]) -> float:
    total = 0.0
    for key in ("fee_paid", "slippage_paid", "funding_paid"):
        value = _float_or_none(trade.get(key))
        total += value if value is not None else 0.0
    return total


def _total_cost_bps(trade: Mapping[str, Any], *, entry_price: float) -> float:
    position_notional = _position_notional(trade, entry_price=entry_price)
    if position_notional is None or position_notional <= 0.0:
        return 0.0
    return (_total_cost_amount(trade) / position_notional) * 10_000.0


def _diagnostic_pnl(
    *,
    trade: Mapping[str, Any],
    side: str,
    entry_price: float,
    exit_price: float | None,
    fill_quality: ExitFillQuality,
) -> tuple[float | None, float | None]:
    qty = _float_or_none(trade.get("qty"))
    if exit_price is None or qty is None or qty <= 0.0 or fill_quality != "evidence_backed":
        return None, None
    direction = 1.0 if side == "long" else -1.0
    gross_pnl = (exit_price - entry_price) * qty * direction
    net_pnl = gross_pnl - _total_cost_amount(trade)
    return gross_pnl, net_pnl
