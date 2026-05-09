from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

from trading_system.app.types import LifecycleState


@dataclass(frozen=True, slots=True)
class LifecycleThresholds:
    confirm_r_multiple: float = 0.8
    protect_r_multiple: float = 1.2
    exit_r_multiple: float = 2.0


def _strict_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite non-bool number when present")
    try:
        candidate = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-bool number when present") from exc
    if not isfinite(candidate):
        raise ValueError(f"{field} must be a finite non-bool number when present")
    return candidate


def _state(value: LifecycleState | str) -> LifecycleState:
    if isinstance(value, LifecycleState):
        return value
    if not isinstance(value, str):
        raise ValueError("current_state must be a LifecycleState or string when present")
    try:
        return LifecycleState(value)
    except ValueError:
        return LifecycleState.INIT


def _strict_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a bool when present")
    return value


def _thresholds(config: Mapping[str, Any] | Any | None) -> LifecycleThresholds:
    if config is None:
        return LifecycleThresholds()
    if isinstance(config, Mapping):
        return LifecycleThresholds(
            confirm_r_multiple=(
                _strict_float(config["confirm_r_multiple"], "confirm_r_multiple")
                if "confirm_r_multiple" in config
                else 0.8
            ),
            protect_r_multiple=(
                _strict_float(config["protect_r_multiple"], "protect_r_multiple")
                if "protect_r_multiple" in config
                else 1.2
            ),
            exit_r_multiple=(
                _strict_float(config["exit_r_multiple"], "exit_r_multiple")
                if "exit_r_multiple" in config
                else 2.0
            ),
        )

    return LifecycleThresholds(
        confirm_r_multiple=(
            _strict_float(getattr(config, "confirm_r_multiple"), "confirm_r_multiple")
            if hasattr(config, "confirm_r_multiple")
            else 0.8
        ),
        protect_r_multiple=(
            _strict_float(getattr(config, "protect_r_multiple"), "protect_r_multiple")
            if hasattr(config, "protect_r_multiple")
            else 1.2
        ),
        exit_r_multiple=(
            _strict_float(getattr(config, "exit_r_multiple"), "exit_r_multiple")
            if hasattr(config, "exit_r_multiple")
            else 2.0
        ),
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

    r_multiple = _strict_float(context["r_multiple"], "r_multiple") if "r_multiple" in context else 0.0
    confirmed = _strict_bool(context["confirmed"], "confirmed") if "confirmed" in context else False
    payload_ready = _strict_bool(context["payload_ready"], "payload_ready") if "payload_ready" in context else confirmed
    trend_mature = _strict_bool(context["trend_mature"], "trend_mature") if "trend_mature" in context else False
    stop_hit = _strict_bool(context["stop_hit"], "stop_hit") if "stop_hit" in context else False
    exit_requested = _strict_bool(context["exit_requested"], "exit_requested") if "exit_requested" in context else False
    force_exit = _strict_bool(context["force_exit"], "force_exit") if "force_exit" in context else False
    protect_breached = (
        _strict_bool(context["protect_breached"], "protect_breached") if "protect_breached" in context else False
    )
    target_hit = _strict_bool(context["target_hit"], "target_hit") if "target_hit" in context else False

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
