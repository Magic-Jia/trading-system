from __future__ import annotations

from typing import Any, Mapping

TARGET_STATUS_PENDING = "pending"
TARGET_STATUS_FILLED = "filled"
TARGET_STATUS_EXTERNAL = "satisfied_by_external_reduction"
FIRST_STAGE_FRACTION = 0.50
SECOND_STAGE_FRACTION = 0.25
RUNNER_FRACTION = 0.25
SECOND_TARGET_SOURCE = "fixed_2r"
_QTY_EPSILON = 1e-12


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_qty(value: float) -> float:
    return round(float(value), 8)


def _qty_epsilon(step_size: float | None) -> float:
    if step_size is None or step_size <= 0:
        return _QTY_EPSILON
    return max(step_size / 2.0, _QTY_EPSILON)


def _floor_to_step(value: float, step_size: float | None) -> float:
    if value <= 0:
        return 0.0
    if step_size is None or step_size <= 0:
        return value
    steps = int(value / step_size)
    return max(steps * step_size, 0.0)


def _valid_first_target(candidate: float, *, first_target_1r: float, second_target_price: float) -> bool:
    return candidate >= first_target_1r and candidate < second_target_price


def _select_first_target(
    *,
    structure_target_price: float | None,
    legacy_take_profit: float | None,
    first_target_1r: float,
    second_target_price: float,
) -> dict[str, Any]:
    structure = _float(structure_target_price)
    if structure is not None and _valid_first_target(
        structure,
        first_target_1r=first_target_1r,
        second_target_price=second_target_price,
    ):
        return {"price": _round_qty(structure), "source": "structure"}

    legacy = _float(legacy_take_profit)
    if legacy is not None and _valid_first_target(
        legacy,
        first_target_1r=first_target_1r,
        second_target_price=second_target_price,
    ):
        return {"price": _round_qty(legacy), "source": "legacy_take_profit_mapped"}

    return {"price": _round_qty(first_target_1r), "source": "fallback_1r"}


def derive_target_management_fields(
    *,
    side: str,
    entry_price: float,
    stop_loss: float | None,
    structure_target_price: float | None,
    legacy_take_profit: float | None,
    original_position_qty: float,
) -> dict[str, Any]:
    if str(side).upper() != "LONG":
        return {}

    stop = _float(stop_loss)
    if entry_price <= 0 or stop is None:
        return {}

    risk_unit = entry_price - stop
    if risk_unit <= 0:
        return {}

    first_target_1r = _round_qty(entry_price + risk_unit)
    second_target_price = _round_qty(entry_price + risk_unit * 2.0)
    candidate = _select_first_target(
        structure_target_price=structure_target_price,
        legacy_take_profit=legacy_take_profit,
        first_target_1r=first_target_1r,
        second_target_price=second_target_price,
    )
    if original_position_qty <= 0:
        return {}

    qty = _round_qty(original_position_qty)
    return {
        "original_position_qty": qty,
        "remaining_position_qty": qty,
        "first_target_price": candidate["price"],
        "first_target_source": candidate["source"],
        "second_target_price": second_target_price,
        "second_target_source": SECOND_TARGET_SOURCE,
        "scale_out_plan": {
            "first": FIRST_STAGE_FRACTION,
            "second": SECOND_STAGE_FRACTION,
            "runner": RUNNER_FRACTION,
            "basis": "original_position",
        },
        "first_target_status": TARGET_STATUS_PENDING,
        "first_target_hit": False,
        "first_target_filled_qty": 0.0,
        "second_target_status": TARGET_STATUS_PENDING,
        "second_target_hit": False,
        "second_target_filled_qty": 0.0,
        "runner_protected": False,
        "runner_stop_price": None,
    }


def _with_default_target_state(payload: dict[str, Any]) -> dict[str, Any]:
    qty = _float(payload.get("qty")) or 0.0
    original_qty = _float(payload.get("original_position_qty"))
    remaining_qty = _float(payload.get("remaining_position_qty"))
    if original_qty is None or original_qty <= 0:
        original_qty = qty
    if remaining_qty is None:
        remaining_qty = qty

    payload["original_position_qty"] = _round_qty(max(original_qty, 0.0))
    payload["remaining_position_qty"] = _round_qty(max(remaining_qty, 0.0))
    payload.setdefault(
        "scale_out_plan",
        {
            "first": FIRST_STAGE_FRACTION,
            "second": SECOND_STAGE_FRACTION,
            "runner": RUNNER_FRACTION,
            "basis": "original_position",
        },
    )
    payload.setdefault("first_target_status", TARGET_STATUS_PENDING)
    payload.setdefault("first_target_hit", False)
    payload.setdefault("first_target_filled_qty", 0.0)
    payload.setdefault("second_target_status", TARGET_STATUS_PENDING)
    payload.setdefault("second_target_hit", False)
    payload.setdefault("second_target_filled_qty", 0.0)
    payload.setdefault("runner_protected", False)
    payload.setdefault("runner_stop_price", None)
    payload.setdefault("second_target_source", SECOND_TARGET_SOURCE)
    return payload


def _stage_unreachable(position: Mapping[str, Any], *, stage: str) -> bool:
    fraction = FIRST_STAGE_FRACTION if stage == "first" else SECOND_STAGE_FRACTION
    original_qty = _float(position.get("original_position_qty")) or 0.0
    if original_qty <= 0:
        return False

    filled_qty = _float(position.get(f"{stage}_target_filled_qty")) or 0.0
    target_qty = original_qty * fraction
    stage_remaining_qty = max(target_qty - filled_qty, 0.0)

    step_size = _float(position.get("symbol_step_size"))
    epsilon = _qty_epsilon(step_size)
    if stage_remaining_qty <= epsilon:
        return False

    remaining_qty = _float(position.get("remaining_position_qty"))
    if remaining_qty is None:
        remaining_qty = _float(position.get("qty")) or 0.0

    raw_executable = min(stage_remaining_qty, max(remaining_qty, 0.0))
    rounded_executable = _floor_to_step(raw_executable, step_size)
    min_order_qty = _float(position.get("min_order_qty"))

    if remaining_qty <= epsilon:
        return True
    if rounded_executable <= epsilon:
        return True
    if min_order_qty is not None and min_order_qty > 0 and rounded_executable < min_order_qty:
        return True
    return False


def _stage_fraction(stage: str) -> float:
    if stage == "first":
        return FIRST_STAGE_FRACTION
    if stage == "second":
        return SECOND_STAGE_FRACTION
    return 0.0


def stage_requested_qty(position: Mapping[str, Any], *, stage: str) -> float:
    fraction = _stage_fraction(stage)
    original_qty = _float(position.get("original_position_qty")) or 0.0
    if fraction <= 0 or original_qty <= 0:
        return 0.0
    return _round_qty(original_qty * fraction)


def stage_completed(position: Mapping[str, Any], *, stage: str) -> bool:
    target_qty = stage_requested_qty(position, stage=stage)
    if target_qty <= 0:
        return False
    filled_qty = _float(position.get(f"{stage}_target_filled_qty")) or 0.0
    step_size = _float(position.get("symbol_step_size"))
    epsilon = _qty_epsilon(step_size)
    return filled_qty + epsilon >= target_qty


def reconciled_stage_qty(position: Mapping[str, Any], *, stage: str) -> float | None:
    requested_qty = stage_requested_qty(position, stage=stage)
    if requested_qty <= 0:
        return None

    filled_qty = _float(position.get(f"{stage}_target_filled_qty")) or 0.0
    stage_remaining_qty = max(requested_qty - filled_qty, 0.0)
    if stage_remaining_qty <= 0:
        return None

    remaining_qty = _float(position.get("remaining_position_qty"))
    if remaining_qty is None:
        remaining_qty = _float(position.get("qty")) or 0.0
    raw_executable_qty = min(stage_remaining_qty, max(remaining_qty, 0.0))
    step = _float(position.get("symbol_step_size"))
    executable_qty = _floor_to_step(raw_executable_qty, step)
    if executable_qty <= 0:
        return None

    min_qty = _float(position.get("min_order_qty"))
    if min_qty is not None and min_qty > 0 and executable_qty < min_qty:
        return None
    return _round_qty(executable_qty)


def terminalize_all_unreachable_stages(position: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(position)
    if str(payload.get("side") or "").upper() != "LONG":
        return payload

    for stage in ("first", "second"):
        if str(payload.get(f"{stage}_target_status") or TARGET_STATUS_PENDING) != TARGET_STATUS_PENDING:
            continue
        if not _stage_unreachable(payload, stage=stage):
            continue
        payload[f"{stage}_target_status"] = TARGET_STATUS_EXTERNAL
        payload[f"{stage}_target_hit"] = False
        if stage == "second":
            payload["runner_protected"] = False
            payload["runner_stop_price"] = None
    return payload


def _apply_legacy_stage_seed(position: dict[str, Any], *, allow_legacy_partial_seed: bool) -> dict[str, Any]:
    legacy_partial = _float(position.get("legacy_partial_filled_qty")) if allow_legacy_partial_seed else None
    if legacy_partial is not None:
        stage_target_qty = (_float(position.get("original_position_qty")) or 0.0) * FIRST_STAGE_FRACTION
        seeded_filled_qty = max(legacy_partial, 0.0)
        if stage_target_qty > 0:
            seeded_filled_qty = min(seeded_filled_qty, stage_target_qty)
        step_size = _float(position.get("symbol_step_size"))
        epsilon = _qty_epsilon(step_size)
        position["first_target_filled_qty"] = _round_qty(seeded_filled_qty)
        if stage_target_qty > 0 and seeded_filled_qty + epsilon >= stage_target_qty:
            position["first_target_status"] = TARGET_STATUS_FILLED
            position["first_target_hit"] = True
        elif seeded_filled_qty > epsilon:
            position["first_target_status"] = TARGET_STATUS_PENDING
            position["first_target_hit"] = False

    if str(position.get("first_target_status") or TARGET_STATUS_PENDING) == TARGET_STATUS_PENDING and _stage_unreachable(
        position, stage="first"
    ):
        position["first_target_status"] = TARGET_STATUS_EXTERNAL
        position["first_target_hit"] = False
    return _with_default_target_state(position)


def ensure_target_management_state(position: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(position)
    if str(payload.get("side") or "").upper() != "LONG":
        return payload

    first_target_price = _float(payload.get("first_target_price"))
    second_target_price = _float(payload.get("second_target_price"))
    if first_target_price is not None and second_target_price is not None:
        return _with_default_target_state(payload)

    sticky_stage_state: dict[str, Any] = {}
    if first_target_price is not None:
        for key in ("first_target_status", "first_target_hit", "first_target_filled_qty"):
            if key in payload:
                sticky_stage_state[key] = payload.get(key)
    if second_target_price is not None:
        for key in (
            "second_target_status",
            "second_target_hit",
            "second_target_filled_qty",
            "runner_protected",
            "runner_stop_price",
        ):
            if key in payload:
                sticky_stage_state[key] = payload.get(key)

    original_qty = _float(payload.get("original_position_qty"))
    if original_qty is None or original_qty <= 0:
        original_qty = _float(payload.get("qty")) or 0.0
    remaining_qty = _float(payload.get("remaining_position_qty"))
    if remaining_qty is None:
        remaining_qty = _float(payload.get("qty"))

    derived = derive_target_management_fields(
        side=str(payload.get("side") or "LONG"),
        entry_price=_float(payload.get("entry_price")) or 0.0,
        stop_loss=_float(payload.get("stop_loss")),
        structure_target_price=_float(payload.get("structure_target_price")),
        legacy_take_profit=_float(payload.get("take_profit")),
        original_position_qty=original_qty,
    )
    if not derived:
        return payload

    if first_target_price is not None:
        derived["first_target_price"] = _round_qty(first_target_price)
        if "first_target_source" in payload:
            derived["first_target_source"] = payload.get("first_target_source")
        else:
            derived.pop("first_target_source", None)
    if second_target_price is not None:
        derived["second_target_price"] = _round_qty(second_target_price)
        if "second_target_source" in payload:
            derived["second_target_source"] = payload.get("second_target_source")
        else:
            derived.pop("second_target_source", None)

    payload.update(derived)
    payload.update(sticky_stage_state)
    if remaining_qty is not None and remaining_qty >= 0:
        payload["remaining_position_qty"] = _round_qty(remaining_qty)
    return _apply_legacy_stage_seed(payload, allow_legacy_partial_seed=first_target_price is None)
