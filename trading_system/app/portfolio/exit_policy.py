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


def _invalidation_reason_with_context(position: Mapping[str, Any]) -> str | None:
    invalidation_meta = _invalidation_fields(position)
    invalidation_source = str(invalidation_meta.get("invalidation_source") or "").strip()
    invalidation_reason = str(invalidation_meta.get("invalidation_reason") or "").strip()
    if invalidation_reason and invalidation_source:
        return f"{invalidation_reason}（{invalidation_source}）"
    if invalidation_reason:
        return invalidation_reason
    return None


def _defensive_regime(regime: Mapping[str, Any] | None) -> bool:
    if not regime:
        return False
    label = str(regime.get("label") or "").upper()
    execution_hazard = str(regime.get("execution_hazard") or "").lower()
    risk_multiplier = _float(regime.get("risk_multiplier"))
    if "DEFENSIVE" in label:
        return True
    return execution_hazard == "compress_risk" and (risk_multiplier is not None and risk_multiplier <= 0.5)


def _target_fields(position: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "first_target_price": _float(position.get("first_target_price")),
        "second_target_price": _float(position.get("second_target_price")),
        "first_target_status": str(position.get("first_target_status") or "pending"),
        "second_target_status": str(position.get("second_target_status") or "pending"),
        "runner_protected": bool(position.get("runner_protected")),
        "runner_stop_price": _float(position.get("runner_stop_price")),
    }


def _first_target_decision(mark_price: float, first_target_price: float, invalidation_meta: dict[str, Any]) -> ExitDecision:
    return ExitDecision(
        action="PARTIAL_TAKE_PROFIT",
        qty_fraction=0.5,
        priority="MEDIUM",
        reason="已触及第一目标位，建议先兑现 50% 仓位。",
        reference_price=round(mark_price, 8),
        meta={
            "exit_trigger": "first_target_hit",
            "target_stage": "first",
            "target_price": round(first_target_price, 8),
            "fraction_basis": "original_position",
            **invalidation_meta,
        },
    )


def _second_target_decision(
    mark_price: float,
    second_target_price: float,
    first_target_price: float,
    invalidation_meta: dict[str, Any],
) -> ExitDecision:
    return ExitDecision(
        action="PARTIAL_TAKE_PROFIT",
        qty_fraction=0.25,
        priority="MEDIUM",
        reason="已触及第二目标位，建议再兑现 25% 仓位并把尾仓保护价抬到第一目标位。",
        reference_price=round(mark_price, 8),
        meta={
            "exit_trigger": "second_target_hit",
            "target_stage": "second",
            "target_price": round(second_target_price, 8),
            "fraction_basis": "original_position",
            "runner_stop_price": round(first_target_price, 8),
            "runner_protected": True,
            **invalidation_meta,
        },
    )


def _runner_stop_decision(mark_price: float, runner_stop_price: float, invalidation_meta: dict[str, Any]) -> ExitDecision:
    return ExitDecision(
        action="EXIT",
        qty_fraction=1.0,
        priority="HIGH",
        reason="runner 保护价已被击穿，建议退出当前剩余全部尾仓。",
        reference_price=round(mark_price, 8),
        meta={
            "exit_trigger": "runner_stop_hit",
            "runner_stop_price": round(runner_stop_price, 8),
            **invalidation_meta,
        },
    )


def _legacy_take_profit_decision(
    *,
    mark_price: float,
    take_profit: float,
    invalidation_meta: dict[str, Any],
    invalidation_context: str | None,
) -> ExitDecision:
    reason = "已触及第一目标位，建议先兑现 50% 仓位并保留剩余仓位观察延伸。"
    if invalidation_context:
        reason = f"{invalidation_context}仍是当前失效条件，已触及第一目标位，建议先兑现 50% 仓位并保留剩余仓位观察延伸。"
    return ExitDecision(
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
    invalidation_context = _invalidation_reason_with_context(position)
    decisions: list[ExitDecision] = []

    if bool(position.get("invalidation_triggered")):
        reason = "当前 thesis invalidation 已触发，建议先于硬止损执行 fail-fast 退出。"
        if invalidation_context:
            reason = f"{invalidation_context}已触发 thesis invalidation，建议先于硬止损执行 fail-fast 退出。"
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

    target_fields = _target_fields(position)
    first_target_price = target_fields["first_target_price"]
    second_target_price = target_fields["second_target_price"]
    first_target_status = target_fields["first_target_status"]
    second_target_status = target_fields["second_target_status"]
    runner_protected = target_fields["runner_protected"]
    runner_stop_price = target_fields["runner_stop_price"]
    has_new_target_fields = first_target_price is not None and second_target_price is not None

    if side == "LONG" and has_new_target_fields:
        if runner_protected:
            if runner_stop_price is None:
                return []
            if mark_price <= runner_stop_price:
                return [_runner_stop_decision(mark_price, runner_stop_price, invalidation_meta)]
            return []

        first_pending = first_target_status == "pending"
        second_pending = second_target_status == "pending"
        first_hit = first_pending and mark_price >= first_target_price
        second_hit = second_pending and mark_price >= second_target_price

        if second_hit and first_pending:
            decisions.append(_first_target_decision(mark_price, first_target_price, invalidation_meta))
        elif first_hit:
            decisions.append(_first_target_decision(mark_price, first_target_price, invalidation_meta))

        if second_hit and not runner_protected and (not first_pending or mark_price >= second_target_price):
            decisions.append(_second_target_decision(mark_price, second_target_price, first_target_price, invalidation_meta))

        if decisions:
            return decisions
    elif take_profit is not None and _in_favor(side, mark_price, take_profit):
        decisions.append(
            _legacy_take_profit_decision(
                mark_price=mark_price,
                take_profit=take_profit,
                invalidation_meta=invalidation_meta,
                invalidation_context=invalidation_context,
            )
        )

    risk_unit = _risk_unit(side, entry_price, stop_loss)
    in_profit = _in_favor(side, mark_price, entry_price)
    sufficiently_in_profit = risk_unit > 0 and abs(mark_price - entry_price) >= risk_unit * 0.5
    has_triggerable_pending_target_stage = (
        side == "LONG"
        and has_new_target_fields
        and (
            (first_target_status == "pending" and mark_price >= (first_target_price or 0.0))
            or (second_target_status == "pending" and mark_price >= (second_target_price or 0.0))
        )
    )
    if _defensive_regime(regime) and in_profit and sufficiently_in_profit and not has_triggerable_pending_target_stage:
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
