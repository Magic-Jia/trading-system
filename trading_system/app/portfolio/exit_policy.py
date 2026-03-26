from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

ExitAction = Literal["PARTIAL_TAKE_PROFIT", "EXIT", "DE_RISK"]
ExitPriority = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass(slots=True)
class ExitDecision:
    action: ExitAction
    qty_fraction: float
    priority: ExitPriority
    reason: str
    reference_price: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


_OPEN_STATUSES = {None, "OPEN", "FILLED", "SENT"}


def _is_open(position: Mapping[str, Any]) -> bool:
    return position.get("status") in _OPEN_STATUSES


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _in_favor(side: str, mark_price: float, reference_price: float) -> bool:
    if side == "LONG":
        return mark_price >= reference_price
    return mark_price <= reference_price


def _risk_unit(side: str, entry_price: float, stop_loss: float | None) -> float:
    if stop_loss is None:
        return 0.0
    if side == "LONG" and stop_loss >= entry_price:
        return 0.0
    if side == "SHORT" and stop_loss <= entry_price:
        return 0.0
    return abs(entry_price - stop_loss)


def _invalidation_fields(position: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    invalidation_source = str(position.get("invalidation_source") or "").strip()
    invalidation_reason = str(position.get("invalidation_reason") or "").strip()
    if invalidation_source:
        payload["invalidation_source"] = invalidation_source
    if invalidation_reason:
        payload["invalidation_reason"] = invalidation_reason
    return payload


def _defensive_regime(regime: Mapping[str, Any] | None) -> bool:
    if not regime:
        return False
    label = str(regime.get("label") or "").upper()
    execution_hazard = str(regime.get("execution_hazard") or "").lower()
    risk_multiplier = _float(regime.get("risk_multiplier"))
    if "DEFENSIVE" in label:
        return True
    return execution_hazard == "compress_risk" and (risk_multiplier is not None and risk_multiplier <= 0.5)


def evaluate_exit_policy(
    position: Mapping[str, Any],
    *,
    regime: Mapping[str, Any] | None = None,
) -> list[ExitDecision]:
    if not _is_open(position):
        return []

    side = str(position.get("side") or "LONG").upper()
    entry_price = _float(position.get("entry_price"))
    mark_price = _float(position.get("mark_price"))
    stop_loss = _float(position.get("stop_loss"))
    take_profit = _float(position.get("take_profit"))
    if entry_price is None or entry_price <= 0 or mark_price is None or mark_price <= 0:
        return []

    invalidation_meta = _invalidation_fields(position)
    invalidation_source = str(invalidation_meta.get("invalidation_source") or "").strip()
    invalidation_reason = str(invalidation_meta.get("invalidation_reason") or "").strip()
    decisions: list[ExitDecision] = []

    if bool(position.get("invalidation_triggered")):
        reason = "当前 thesis invalidation 已触发，建议先于硬止损执行 fail-fast 退出。"
        if invalidation_reason and invalidation_source:
            reason = f"{invalidation_reason}（{invalidation_source}）已触发 thesis invalidation，建议先于硬止损执行 fail-fast 退出。"
        elif invalidation_reason:
            reason = f"{invalidation_reason} 已触发 thesis invalidation，建议先于硬止损执行 fail-fast 退出。"
        meta = {
            "exit_trigger": "thesis_invalidation",
            "position_stop_loss": stop_loss,
            **invalidation_meta,
        }
        return [
            ExitDecision(
                action="EXIT",
                qty_fraction=1.0,
                priority="HIGH",
                reason=reason,
                reference_price=round(mark_price, 8),
                meta=meta,
            )
        ]

    if take_profit is not None and _in_favor(side, mark_price, take_profit):
        reason = "已触及第一目标位，建议先兑现 50% 仓位并保留剩余仓位观察延伸。"
        if invalidation_reason and invalidation_source:
            reason = f"{invalidation_reason}（{invalidation_source}）仍是当前失效条件，已触及第一目标位，建议先兑现 50% 仓位并保留剩余仓位观察延伸。"
        elif invalidation_reason:
            reason = f"{invalidation_reason} 仍是当前失效条件，已触及第一目标位，建议先兑现 50% 仓位并保留剩余仓位观察延伸。"
        decisions.append(
            ExitDecision(
                action="PARTIAL_TAKE_PROFIT",
                qty_fraction=0.5,
                priority="MEDIUM",
                reason=reason,
                reference_price=round(mark_price, 8),
                meta={
                    "target_price": round(take_profit, 8),
                    "exit_trigger": "first_target_hit",
                    **invalidation_meta,
                },
            )
        )

    risk_unit = _risk_unit(side, entry_price, stop_loss)
    in_profit = _in_favor(side, mark_price, entry_price)
    sufficiently_in_profit = risk_unit > 0 and abs(mark_price - entry_price) >= risk_unit * 0.5
    if _defensive_regime(regime) and in_profit and sufficiently_in_profit:
        label = str((regime or {}).get("label") or "")
        execution_policy = str((regime or {}).get("execution_policy") or "")
        risk_multiplier = _float((regime or {}).get("risk_multiplier"))
        decisions.append(
            ExitDecision(
                action="DE_RISK",
                qty_fraction=0.25,
                priority="HIGH",
                reason=f"{label} regime is active, and the trade is already in profit; de-risk 25% instead of waiting for a full invalidation.",
                reference_price=round(mark_price, 8),
                meta={
                    "exit_trigger": "defensive_regime_de_risk",
                    "regime_label": label,
                    "execution_policy": execution_policy,
                    "risk_multiplier": risk_multiplier,
                },
            )
        )

    return decisions
