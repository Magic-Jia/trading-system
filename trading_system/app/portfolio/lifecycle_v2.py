from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from trading_system.app.types import LifecycleState


@dataclass(frozen=True, slots=True)
class LifecycleThresholds:
    confirm_r_multiple: float = 0.8
    protect_r_multiple: float = 1.2
    exit_r_multiple: float = 2.0


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _state(value: LifecycleState | str) -> LifecycleState:
    if isinstance(value, LifecycleState):
        return value
    try:
        return LifecycleState(str(value))
    except ValueError:
        return LifecycleState.INIT


def _thresholds(config: Mapping[str, Any] | Any | None) -> LifecycleThresholds:
    if config is None:
        return LifecycleThresholds()
    if isinstance(config, Mapping):
        return LifecycleThresholds(
            confirm_r_multiple=_as_float(config.get("confirm_r_multiple"), 0.8),
            protect_r_multiple=_as_float(config.get("protect_r_multiple"), 1.2),
            exit_r_multiple=_as_float(config.get("exit_r_multiple"), 2.0),
        )

    return LifecycleThresholds(
        confirm_r_multiple=_as_float(getattr(config, "confirm_r_multiple", None), 0.8),
        protect_r_multiple=_as_float(getattr(config, "protect_r_multiple", None), 1.2),
        exit_r_multiple=_as_float(getattr(config, "exit_r_multiple", None), 2.0),
    )


def advance_lifecycle_transition(
    current_state: LifecycleState | str,
    signals: Mapping[str, Any] | None = None,
    *,
    config: Mapping[str, Any] | Any | None = None,
) -> tuple[LifecycleState, list[str]]:
    thresholds = _thresholds(config)
    context = signals or {}
    state = _state(current_state)

    r_multiple = _as_float(context.get("r_multiple"), 0.0)
    confirmed = bool(context.get("confirmed", False))
    payload_ready = bool(context.get("payload_ready", confirmed))
    trend_mature = bool(context.get("trend_mature", False))
    stop_hit = bool(context.get("stop_hit", False))
    exit_requested = bool(context.get("exit_requested", False))
    force_exit = bool(context.get("force_exit", False))
    protect_breached = bool(context.get("protect_breached", False))
    target_hit = bool(context.get("target_hit", False))

    if force_exit:
        return LifecycleState.EXIT, ["force_exit_requested"]

    if state == LifecycleState.INIT:
        if confirmed and r_multiple >= thresholds.confirm_r_multiple:
            return LifecycleState.CONFIRM, ["init_to_confirm_confirmed"]
        return LifecycleState.INIT, ["init_waiting_confirmation"]

    if state == LifecycleState.CONFIRM:
        if payload_ready:
            return LifecycleState.PAYLOAD, ["confirm_to_payload_ready"]
        return LifecycleState.CONFIRM, ["confirm_waiting_payload"]

    if state == LifecycleState.PAYLOAD:
        if trend_mature and r_multiple >= thresholds.protect_r_multiple:
            return LifecycleState.PROTECT, ["payload_to_protect_trend_mature"]
        if stop_hit or exit_requested:
            return LifecycleState.EXIT, ["payload_to_exit_risk_trigger"]
        return LifecycleState.PAYLOAD, ["payload_active"]

    if state == LifecycleState.PROTECT:
        if stop_hit or protect_breached or exit_requested:
            return LifecycleState.EXIT, ["protect_to_exit_risk_trigger"]
        if target_hit and r_multiple >= thresholds.exit_r_multiple:
            return LifecycleState.EXIT, ["protect_to_exit_target_hit"]
        return LifecycleState.PROTECT, ["protect_active"]

    return LifecycleState.EXIT, ["already_exit"]


def advance_lifecycle_state(
    current_state: LifecycleState | str,
    signals: Mapping[str, Any] | None = None,
    *,
    config: Mapping[str, Any] | Any | None = None,
) -> LifecycleState:
    next_state, _ = advance_lifecycle_transition(current_state, signals, config=config)
    return next_state
