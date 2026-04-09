# Target Scale-Out and Runner Management Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved long-only target / scale-out / runner contract end to end: freeze first / second target state on entry or legacy migration, emit ordered exit decisions, reconcile executable reduce quantities against live position state, and surface the resulting state cleanly in runtime reporting.

**Architecture:** Keep `exit_policy.py` pure: it should only read normalized position state and emit ordered `ExitDecision` rows. Put target freezing, legacy migration, invariant checks, quantity reconciliation, and terminalization helpers in a focused portfolio helper module so `positions.py`, `lifecycle.py`, and reporting all share one source of truth. Preserve the current harness split: dry-run keeps preview-only behavior, while paper mode gains deterministic management-action state apply so target-hit / runner-protected state can actually advance and be tested.

**Tech Stack:** Python 3.12, pytest, existing `trading_system` runtime-state / paper-execution pipeline

---

## Working context

- Plan file: `docs/superpowers/plans/2026-04-09-target-scaleout-runner-implementation-plan.md`
- Spec to implement: `docs/superpowers/specs/2026-04-09-target-scaleout-runner-management-design.md`
- Current code anchors:
  - `trading_system/app/main.py:372-429, 683-735`
  - `trading_system/app/portfolio/positions.py:78-193`
  - `trading_system/app/portfolio/exit_policy.py:1-159`
  - `trading_system/app/portfolio/lifecycle.py:67-328`
  - `trading_system/app/execution/executor.py:35-74`
  - `trading_system/app/reporting/daily_report.py:52-149`
- Keep the slice TDD-first: red test, minimal code, focused green run, then commit.

## File structure / responsibility map

- Create: `trading_system/app/portfolio/target_management.py`
  - Canonical target / scale-out / runner helpers shared across entry freeze, legacy migration, reconciliation, and status terminalization.
- Modify: `trading_system/app/main.py`
  - Seed new positions with frozen target-management fields when creating strategy orders; wire management execution / preview branching after suggestions are built.
- Modify: `trading_system/app/portfolio/positions.py`
  - Persist frozen target fields and sticky management state on entry fills, account sync, legacy migration, and management-action fills.
- Modify: `trading_system/app/portfolio/exit_policy.py`
  - Replace single-`take_profit` logic with ordered first-target / second-target / runner-stop decisions while preserving invalidation precedence and defensive gating.
- Modify: `trading_system/app/portfolio/lifecycle.py`
  - Convert `ExitDecision` rows into reconciled management suggestions / intents using original-position fractions, stage-filled quantities, and symbol step rules.
- Modify: `trading_system/app/execution/executor.py`
  - Add paper-mode management-action execution path that applies fills sequentially and stops after the first failure / unsupported action; retain preview-only behavior in dry-run.
- Modify: `trading_system/app/reporting/daily_report.py`
  - Surface approved B-view target / runner fields plus audit-ready review rows.
- Modify: `trading_system/tests/test_exit_policy.py`
  - Lock ordered-decision semantics, side gating, runner-stop behavior, and defensive collision rules.
- Create: `trading_system/tests/test_target_management_state.py`
  - Verify entry freeze, legacy `take_profit` migration, sticky persistence on account sync, and invalid-runner degradation helpers.
- Create: `trading_system/tests/test_management_execution.py`
  - Verify reconciliation math, sequential paper execution, per-action writeback, and `satisfied_by_external_reduction` terminalization.
- Create: `trading_system/tests/test_target_scaleout_runner_cycle.py`
  - Focused end-to-end cycle test for runtime-state management suggestions / previews / lifecycle summary without adding to the giant `test_main_v2_cycle.py`.
- Modify: `trading_system/tests/test_reporting.py`
  - Lock default B-view and review-action surfaces for first / second target and runner fields.

## Implementation notes to keep fixed

- Scope is strictly `side == "LONG"`; short rules are untouched.
- `first_target_price` is the canonical new-field target; legacy `take_profit` is migration input only.
- First-target selection rule is fixed:
  - use `structure_target_price` only when `>= 1R` and `< 2R`
  - otherwise fallback to `1R`
- `second_target_price` is always `2R`
- Invariant must always hold for active long target state:
  - `stop_loss < entry_price < first_target_price < second_target_price`
- Scale-out fractions are fixed and based on original size:
  - first stage `0.50`
  - second stage `0.25`
  - runner remainder `0.25`
- `ExitDecision.qty_fraction` for `PARTIAL_TAKE_PROFIT` always means original-position fraction.
- Ordered list contract is fixed:
  - execution must preserve list order
  - if action 1 fails / is unsupported / is only partially filled short of stage completion, action 2 must not run that round
- Gap-through rule is fixed:
  - if price is already through second target and both stages are pending, emit first-stage partial first, then second-stage partial
- `runner_protected = true` may only be written after second stage completes and a runner still remains.
- Runner stop is fixed to `first_target_price`; if runner state is invalid (`runner_protected=true` but no usable `runner_stop_price`), do not guess a stop.
- `satisfied_by_external_reduction` is a terminal status, not a standard hit:
  - `*_target_hit` stays `false`
  - no new partial action should be emitted afterward
- Dry-run remains preview-only.
- Paper mode should execute management actions deterministically enough to let runtime-state advance and be asserted.

## Chunk 1: Core target-state, exit-policy, reconciliation, and paper-writeback slice

### Task 1: Add a canonical target-management helper and freeze / migrate state at position boundaries

**Files:**
- Create: `trading_system/app/portfolio/target_management.py`
- Modify: `trading_system/app/main.py:372-429, 683-704`
- Modify: `trading_system/app/portfolio/positions.py:78-193`
- Test: `trading_system/tests/test_target_management_state.py`

- [ ] **Step 1: Write the failing target-state tests**

```python
from trading_system.app.portfolio.target_management import (
    derive_target_management_fields,
    ensure_target_management_state,
)
from trading_system.app.portfolio.positions import apply_executed_intent, sync_positions_from_account
from trading_system.app.storage.state_store import RuntimeStateV2
from trading_system.app.types import AccountSnapshot, OrderIntent, PositionSnapshot


def test_derive_target_management_fields_prefers_structure_target_between_1r_and_2r():
    payload = derive_target_management_fields(
        side="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        structure_target_price=107.5,
        legacy_take_profit=None,
        original_position_qty=2.0,
    )

    assert payload["first_target_price"] == pytest.approx(107.5)
    assert payload["first_target_source"] == "structure"
    assert payload["second_target_price"] == pytest.approx(110.0)
    assert payload["scale_out_plan"] == {"first": 0.5, "second": 0.25, "runner": 0.25, "basis": "original_position"}

def test_derive_target_management_fields_accepts_structure_target_exactly_at_1r():
    payload = derive_target_management_fields(
        side="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        structure_target_price=105.0,
        legacy_take_profit=None,
        original_position_qty=2.0,
    )

    assert payload["first_target_price"] == pytest.approx(105.0)
    assert payload["first_target_source"] == "structure"

def test_derive_target_management_fields_falls_back_to_1r_when_structure_target_is_too_near_or_too_far():
    too_near = derive_target_management_fields(
        side="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        structure_target_price=104.0,
        legacy_take_profit=None,
        original_position_qty=2.0,
    )
    too_far = derive_target_management_fields(
        side="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        structure_target_price=110.0,
        legacy_take_profit=None,
        original_position_qty=2.0,
    )
    no_structure = derive_target_management_fields(
        side="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        structure_target_price=None,
        legacy_take_profit=None,
        original_position_qty=2.0,
    )

    assert too_near["first_target_price"] == pytest.approx(105.0)
    assert too_near["first_target_source"] == "fallback_1r"
    assert too_far["first_target_price"] == pytest.approx(105.0)
    assert too_far["first_target_source"] == "fallback_1r"
    assert no_structure["first_target_price"] == pytest.approx(105.0)
    assert no_structure["first_target_source"] == "fallback_1r"

def test_ensure_target_management_state_maps_invalid_legacy_take_profit_back_to_1r():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 2.0,
            "take_profit": 111.0,
            "original_position_qty": 2.0,
        }
    )

    assert position["first_target_price"] == pytest.approx(105.0)
    assert position["first_target_source"] == "fallback_1r"
    assert position["second_target_price"] == pytest.approx(110.0)

def test_ensure_target_management_state_maps_legacy_take_profit_and_completed_partial():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 1.0,
            "take_profit": 107.0,
            "original_position_qty": 2.0,
            "legacy_partial_filled_qty": 1.0,
        }
    )

    assert position["first_target_price"] == pytest.approx(107.0)
    assert position["first_target_source"] == "legacy_take_profit_mapped"
    assert position["first_target_status"] == "filled"
    assert position["first_target_hit"] is True
    assert position["first_target_filled_qty"] == pytest.approx(1.0)

def test_ensure_target_management_state_keeps_legacy_stage_one_pending_when_only_partially_filled():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 1.3,
            "take_profit": 107.0,
            "original_position_qty": 2.0,
            "legacy_partial_filled_qty": 0.7,
        }
    )

    assert position["first_target_source"] == "legacy_take_profit_mapped"
    assert position["first_target_status"] == "pending"
    assert position["first_target_hit"] is False
    assert position["first_target_filled_qty"] == pytest.approx(0.7)

def test_ensure_target_management_state_terminalizes_legacy_stage_one_when_external_reduction_makes_it_unreachable():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 0.04,
            "remaining_position_qty": 0.04,
            "take_profit": 107.0,
            "original_position_qty": 2.0,
            "legacy_partial_filled_qty": 0.7,
            "symbol_step_size": 0.01,
            "min_order_qty": 0.1,
        }
    )

    assert position["first_target_status"] == "satisfied_by_external_reduction"
    assert position["first_target_hit"] is False


def test_sync_positions_from_account_preserves_existing_target_management_state(monkeypatch):
    monkeypatch.setattr("trading_system.app.portfolio.positions._now_bj", lambda: "2026-04-09T18:00:00+08:00")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.5,
                "entry_price": 100.0,
                "mark_price": 111.0,
                "stop_loss": 95.0,
                "take_profit": 107.0,
                "first_target_price": 107.0,
                "first_target_source": "legacy_take_profit_mapped",
                "second_target_price": 110.0,
                "second_target_source": "fixed_2r",
                "original_position_qty": 2.0,
                "remaining_position_qty": 0.5,
                "first_target_status": "filled",
                "first_target_hit": True,
                "first_target_filled_qty": 1.0,
                "second_target_status": "filled",
                "second_target_hit": True,
                "second_target_filled_qty": 0.5,
                "runner_protected": True,
                "runner_stop_price": 107.0,
                "tracked_from_snapshot": True,
                "tracked_from_intent": True,
            }
        },
    )

    sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=1000.0,
            available_balance=1000.0,
            futures_wallet_balance=1000.0,
            open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=111.0)],
        ),
    )

    assert state.positions["BTCUSDT"]["runner_protected"] is True
    assert state.positions["BTCUSDT"]["runner_stop_price"] == pytest.approx(107.0)
    assert state.positions["BTCUSDT"]["remaining_position_qty"] == pytest.approx(0.5)
```

- [ ] **Step 2: Run the target-state tests to verify they fail**

Run:
```bash
uv run --with pytest python -m pytest trading_system/tests/test_target_management_state.py -q -p no:cacheprovider
```

Expected:
- FAIL because `target_management.py` does not exist and positions do not persist the new fields yet

- [ ] **Step 3: Implement the canonical helper and wire entry / sync boundaries**

Create `trading_system/app/portfolio/target_management.py` with the minimal shared contract:

```python
from __future__ import annotations

from typing import Any, Mapping

TARGET_STATUS_PENDING = "pending"
TARGET_STATUS_FILLED = "filled"
TARGET_STATUS_EXTERNAL = "satisfied_by_external_reduction"
FIRST_STAGE_FRACTION = 0.50
SECOND_STAGE_FRACTION = 0.25
RUNNER_FRACTION = 0.25


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
    risk_unit = entry_price - float(stop_loss or 0.0)
    if entry_price <= 0 or risk_unit <= 0:
        return {}
    first_target_1r = round(entry_price + risk_unit, 8)
    second_target_price = round(entry_price + risk_unit * 2.0, 8)
    candidate = _select_first_target(
        structure_target_price=structure_target_price,
        legacy_take_profit=legacy_take_profit,
        first_target_1r=first_target_1r,
        second_target_price=second_target_price,
    )
    return {
        "original_position_qty": round(original_position_qty, 8),
        "remaining_position_qty": round(original_position_qty, 8),
        "first_target_price": candidate["price"],
        "first_target_source": candidate["source"],
        "second_target_price": second_target_price,
        "second_target_source": "fixed_2r",
        "scale_out_plan": {"first": 0.5, "second": 0.25, "runner": 0.25, "basis": "original_position"},
        "first_target_status": TARGET_STATUS_PENDING,
        "first_target_hit": False,
        "first_target_filled_qty": 0.0,
        "second_target_status": TARGET_STATUS_PENDING,
        "second_target_hit": False,
        "second_target_filled_qty": 0.0,
        "runner_protected": False,
        "runner_stop_price": None,
    }


def ensure_target_management_state(position: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(position)
    if str(payload.get("side") or "").upper() != "LONG":
        return payload
    if payload.get("first_target_price") and payload.get("second_target_price"):
        return _with_default_target_state(payload)
    derived = derive_target_management_fields(
        side=str(payload.get("side") or "LONG"),
        entry_price=float(payload.get("entry_price") or 0.0),
        stop_loss=payload.get("stop_loss"),
        structure_target_price=payload.get("structure_target_price"),
        legacy_take_profit=payload.get("take_profit"),
        original_position_qty=float(payload.get("original_position_qty") or payload.get("qty") or 0.0),
    )
    payload.update(derived)
    return _apply_legacy_stage_seed(payload)
```

Wire it in these places:
- `main.py`
  - stop defaulting `take_profit` to `entry * 1.04`; instead pass through target-management seed data in `signal.meta`
  - when building `OrderIntent.meta`, include `structure_target_price`, `original_position_qty`, symbol-step placeholders if already known, and the derived target fields
- `positions.py`
  - after building / refreshing each position dict, call `ensure_target_management_state(...)`
  - preserve all existing target-management fields on account sync
  - seed new filled positions from `OrderIntent.meta`

Implementation rules:
- legacy mapping is one-time and produces `first_target_source == "legacy_take_profit_mapped"`
- first-target selection must explicitly preserve the spec boundaries:
  - valid structure target only when `>= 1R` and `< 2R`
  - structure target `< 1R` falls back to `1R`
  - structure target `>= 2R` falls back to `1R`
  - missing structure target falls back to `1R`
  - legacy `take_profit` values that are invalid for first-target use also fall back to `1R`
- if legacy partial history indicates stage 1 is already done, seed `first_target_status="filled"` and `first_target_hit=True`
- if legacy partial history exists but stage 1 is only partially filled, seed `first_target_status="pending"`, `first_target_hit=False`, and preserve the migrated `first_target_filled_qty` so later rounds only sell the remainder
- if a migrated legacy position has already been externally reduced enough that stage 1 is mathematically unreachable, immediately terminalize it to `first_target_status="satisfied_by_external_reduction"` with `first_target_hit=False`
- only leave a long position unchanged when it is a truly sparse legacy snapshot that lacks both frozen target fields and usable migration inputs; do not bypass the normal 1R fallback for fresh strategy-seeded positions that still have valid `entry_price` / `stop_loss`
- never overwrite an already-frozen `first_target_price` / `second_target_price` on sync

- [ ] **Step 4: Run the target-state tests to verify they pass**

Run:
```bash
uv run --with pytest python -m pytest trading_system/tests/test_target_management_state.py -q -p no:cacheprovider
```

Expected:
- PASS

- [ ] **Step 5: Commit the target-state boundary changes**

```bash
git add trading_system/app/portfolio/target_management.py trading_system/app/main.py trading_system/app/portfolio/positions.py trading_system/tests/test_target_management_state.py
git commit -m "feat: freeze target management state"
```

### Task 2: Teach `exit_policy.py` to emit ordered first-target / second-target / runner decisions

**Files:**
- Modify: `trading_system/app/portfolio/exit_policy.py:1-159`
- Test: `trading_system/tests/test_exit_policy.py`

- [ ] **Step 1: Extend the exit-policy tests with ordered-decision cases**

```python
def test_evaluate_exit_policy_emits_first_and_second_partials_in_order_on_gap_through_second_target():
    decisions = evaluate_exit_policy(
        _position(
            side="LONG",
            mark_price=110.5,
            stop_loss=95.0,
            first_target_price=105.0,
            second_target_price=110.0,
            first_target_status="pending",
            second_target_status="pending",
            runner_protected=False,
        )
    )

    assert [(item.action, item.qty_fraction, item.meta["target_stage"]) for item in decisions] == [
        ("PARTIAL_TAKE_PROFIT", 0.5, "first"),
        ("PARTIAL_TAKE_PROFIT", 0.25, "second"),
    ]
    assert decisions[1].meta["runner_stop_price"] == pytest.approx(105.0)
    assert decisions[1].meta["runner_protected"] is True


def test_evaluate_exit_policy_emits_runner_exit_after_second_target_protection():
    decisions = evaluate_exit_policy(
        _position(
            mark_price=104.5,
            first_target_price=105.0,
            second_target_price=110.0,
            first_target_status="filled",
            second_target_status="filled",
            runner_protected=True,
            runner_stop_price=105.0,
        )
    )

    assert decisions == [
        ExitDecision(
            action="EXIT",
            qty_fraction=1.0,
            priority="HIGH",
            reason="runner 保护价已被击穿，建议退出当前剩余全部尾仓。",
            reference_price=pytest.approx(104.5),
            meta={"exit_trigger": "runner_stop_hit", "runner_stop_price": pytest.approx(105.0)},
        )
    ]


def test_evaluate_exit_policy_skips_invalid_runner_state_without_guessing_stop():
    decisions = evaluate_exit_policy(
        _position(
            mark_price=104.5,
            first_target_price=105.0,
            second_target_price=110.0,
            runner_protected=True,
            runner_stop_price=None,
            first_target_status="filled",
            second_target_status="filled",
        )
    )

    assert decisions == []


def test_evaluate_exit_policy_does_not_stack_defensive_de_risk_on_same_round_as_target_stage():
    decisions = evaluate_exit_policy(
        _position(
            mark_price=105.0,
            first_target_price=105.0,
            second_target_price=110.0,
            first_target_status="pending",
            second_target_status="pending",
        ),
        regime={"label": "CRASH_DEFENSIVE", "execution_policy": "downsize", "risk_multiplier": 0.35},
    )

    assert [item.meta.get("exit_trigger") for item in decisions] == ["first_target_hit"]
```

- [ ] **Step 2: Run the exit-policy tests to verify they fail**

Run:
```bash
uv run --with pytest python -m pytest trading_system/tests/test_exit_policy.py -q -p no:cacheprovider
```

Expected:
- FAIL because `evaluate_exit_policy()` still only knows single `take_profit` partials and generic defensive de-risk

- [ ] **Step 3: Implement ordered target / runner evaluation**

Refactor `evaluate_exit_policy()` around small helpers like these:

```python
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


def _second_target_decision(mark_price: float, second_target_price: float, first_target_price: float, invalidation_meta: dict[str, Any]) -> ExitDecision:
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
```

Behavior to implement:
- keep thesis invalidation as immediate top-priority return
- only LONG positions use the new target logic
- if `mark_price >= second_target_price` and both stages are pending, emit first-stage then second-stage decisions in one ordered list
- if `runner_protected` is already true, never emit another second-target protection action; only evaluate runner stop breach
- if runner state is invalid (`runner_protected` true but missing / invalid `runner_stop_price`), emit nothing and let reporting surface the dirty state
- do not emit defensive `DE_RISK` on the same round as a pending first or second target stage that is currently triggerable
- preserve existing invalidation metadata in every emitted target / runner decision

- [ ] **Step 4: Run the exit-policy tests to verify they pass**

Run:
```bash
uv run --with pytest python -m pytest trading_system/tests/test_exit_policy.py -q -p no:cacheprovider
```

Expected:
- PASS

- [ ] **Step 5: Commit the ordered exit-policy change**

```bash
git add trading_system/app/portfolio/exit_policy.py trading_system/tests/test_exit_policy.py
git commit -m "feat: add target and runner exit decisions"
```

### Task 3: Reconcile stage quantities against live position state when building management intents

**Files:**
- Modify: `trading_system/app/portfolio/lifecycle.py:67-328`
- Modify: `trading_system/app/portfolio/target_management.py`
- Test: `trading_system/tests/test_management_execution.py`

- [ ] **Step 1: Write the failing reconciliation tests**

```python
from trading_system.app.portfolio.lifecycle import build_management_action_intents
from trading_system.app.storage.state_store import RuntimeStateV2


def test_build_management_action_intents_uses_original_position_basis_and_stage_fill_progress():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.6,
                "remaining_position_qty": 0.6,
                "original_position_qty": 2.0,
                "entry_price": 100.0,
                "mark_price": 110.2,
                "stop_loss": 95.0,
                "first_target_price": 105.0,
                "second_target_price": 110.0,
                "first_target_status": "filled",
                "first_target_hit": True,
                "first_target_filled_qty": 1.0,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "symbol_step_size": 0.1,
                "min_order_qty": 0.1,
            }
        },
    )
    rows = [{
        "symbol": "BTCUSDT",
        "side": "LONG",
        "action": "PARTIAL_TAKE_PROFIT",
        "qty_fraction": 0.25,
        "reference_price": 110.2,
        "meta": {"exit_trigger": "second_target_hit", "target_stage": "second", "fraction_basis": "original_position"},
    }]

    intents = build_management_action_intents(state, rows)

    assert intents[0].qty == pytest.approx(0.5)


def test_build_management_action_intents_skips_second_stage_when_reconciled_qty_falls_below_min_order_qty():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.07,
                "remaining_position_qty": 0.07,
                "original_position_qty": 0.28,
                "entry_price": 100.0,
                "mark_price": 110.2,
                "stop_loss": 95.0,
                "first_target_price": 105.0,
                "second_target_price": 110.0,
                "first_target_status": "filled",
                "first_target_hit": True,
                "first_target_filled_qty": 0.14,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "symbol_step_size": 0.01,
                "min_order_qty": 0.1,
            }
        },
    )

    intents = build_management_action_intents(state, [{
        "symbol": "BTCUSDT",
        "side": "LONG",
        "action": "PARTIAL_TAKE_PROFIT",
        "qty_fraction": 0.25,
        "reference_price": 110.2,
        "meta": {"exit_trigger": "second_target_hit", "target_stage": "second", "fraction_basis": "original_position"},
    }])

    assert intents == []
```

- [ ] **Step 2: Run the reconciliation tests to verify they fail**

Run:
```bash
uv run --with pytest python -m pytest trading_system/tests/test_management_execution.py -q -p no:cacheprovider -k build_management_action_intents
```

Expected:
- FAIL because `build_management_action_intents()` still multiplies current `position_qty * qty_fraction` and ignores stage-fill progress / symbol-step rules

- [ ] **Step 3: Implement reusable reconciliation math and feed it into intent building**

Add helpers in `target_management.py` such as:

```python
def reconciled_stage_qty(position: Mapping[str, Any], *, stage: str) -> float | None:
    original_qty = _float(position.get("original_position_qty"))
    remaining_qty = _float(position.get("remaining_position_qty") or position.get("qty"))
    step = _float(position.get("symbol_step_size")) or 0.0
    min_qty = _float(position.get("min_order_qty"))
    target_fraction = FIRST_STAGE_FRACTION if stage == "first" else SECOND_STAGE_FRACTION
    filled_qty = _float(position.get(f"{stage}_target_filled_qty"))
    requested_qty = original_qty * target_fraction
    stage_remaining_qty = max(requested_qty - filled_qty, 0.0)
    raw_executable_qty = min(stage_remaining_qty, remaining_qty)
    executable_qty = _floor_to_step(raw_executable_qty, step)
    if executable_qty <= 0:
        return None
    if min_qty is not None and min_qty > 0 and executable_qty < min_qty:
        return None
    return round(executable_qty, 8)
```

Then update `build_management_action_intents()` so that:
- `PARTIAL_TAKE_PROFIT` with `meta.target_stage in {"first", "second"}` uses `reconciled_stage_qty()` instead of `position_qty * qty_fraction`
- if the reconciled qty is `None`, skip the intent entirely
- `EXIT 1.0` still closes current remaining qty
- every intent carries target-state metadata needed later by paper execution:
  - `target_stage`
  - `fraction_basis`
  - `requested_qty`
  - `reconciled_qty`
  - `runner_stop_price` / `runner_protected` when present

- [ ] **Step 4: Run the reconciliation tests to verify they pass**

Run:
```bash
uv run --with pytest python -m pytest trading_system/tests/test_management_execution.py -q -p no:cacheprovider -k build_management_action_intents
```

Expected:
- PASS

- [ ] **Step 5: Commit the reconciliation change**

```bash
git add trading_system/app/portfolio/target_management.py trading_system/app/portfolio/lifecycle.py trading_system/tests/test_management_execution.py
git commit -m "feat: reconcile target management intent quantities"
```

### Task 4: Execute management actions sequentially in paper mode and write back target / runner state per action

**Files:**
- Modify: `trading_system/app/portfolio/positions.py:155-193`
- Modify: `trading_system/app/execution/executor.py:35-74`
- Modify: `trading_system/app/main.py:728-747`
- Modify: `trading_system/app/portfolio/target_management.py`
- Test: `trading_system/tests/test_management_execution.py`

- [ ] **Step 1: Add failing paper-writeback tests**

```python
from trading_system.app.execution.executor import OrderExecutor
from trading_system.app.storage.state_store import RuntimeStateV2
from trading_system.app.types import ManagementActionIntent


def test_execute_management_actions_writes_back_first_then_second_stage_and_runner_state(tmp_path, app_config):
    executor = OrderExecutor(app_config, mode="paper")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 2.0,
                "remaining_position_qty": 2.0,
                "entry_price": 100.0,
                "mark_price": 110.5,
                "stop_loss": 95.0,
                "first_target_price": 105.0,
                "second_target_price": 110.0,
                "original_position_qty": 2.0,
                "first_target_status": "pending",
                "first_target_hit": False,
                "first_target_filled_qty": 0.0,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "runner_protected": False,
                "runner_stop_price": None,
            }
        },
    )
    intents = [
        ManagementActionIntent(intent_id="mgmt-btcusdt-partial-first", symbol="BTCUSDT", action="PARTIAL_TAKE_PROFIT", side="LONG", position_qty=2.0, qty=1.0, reference_price=110.5, meta={"target_stage": "first", "exit_trigger": "first_target_hit", "fraction_basis": "original_position"}),
        ManagementActionIntent(intent_id="mgmt-btcusdt-partial-second", symbol="BTCUSDT", action="PARTIAL_TAKE_PROFIT", side="LONG", position_qty=1.0, qty=0.5, reference_price=110.5, meta={"target_stage": "second", "exit_trigger": "second_target_hit", "fraction_basis": "original_position", "runner_protected": True, "runner_stop_price": 105.0}),
    ]

    results = executor.execute_management_actions(intents, state)

    assert [row["intent"]["action"] for row in results] == ["PARTIAL_TAKE_PROFIT", "PARTIAL_TAKE_PROFIT"]
    assert state.positions["BTCUSDT"]["qty"] == pytest.approx(0.5)
    assert state.positions["BTCUSDT"]["remaining_position_qty"] == pytest.approx(0.5)
    assert state.positions["BTCUSDT"]["first_target_status"] == "filled"
    assert state.positions["BTCUSDT"]["second_target_status"] == "filled"
    assert state.positions["BTCUSDT"]["runner_protected"] is True
    assert state.positions["BTCUSDT"]["runner_stop_price"] == pytest.approx(105.0)


def run_management_terminalization_pass(state: RuntimeState) -> None:
    for symbol, position in list(state.positions.items()):
        state.positions[symbol] = terminalize_all_unreachable_stages(dict(position))


def test_run_management_terminalization_pass_marks_second_stage_satisfied_by_external_reduction_even_without_actions():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.04,
                "remaining_position_qty": 0.04,
                "original_position_qty": 1.0,
                "first_target_status": "filled",
                "first_target_hit": True,
                "first_target_filled_qty": 0.5,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "symbol_step_size": 0.01,
                "min_order_qty": 0.1,
            }
        },
    )

    run_management_terminalization_pass(state)

    assert state.positions["BTCUSDT"]["second_target_status"] == "satisfied_by_external_reduction"
    assert state.positions["BTCUSDT"]["second_target_hit"] is False


def test_main_runs_terminalization_pass_when_no_management_intents(monkeypatch, tmp_path):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    output_path.write_text(json.dumps({
        "updated_at_bj": "2026-04-09T20:00:00+08:00",
        "positions": {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.04,
                "remaining_position_qty": 0.04,
                "entry_price": 100.0,
                "mark_price": 110.5,
                "stop_loss": 95.0,
                "first_target_price": 105.0,
                "second_target_price": 110.0,
                "original_position_qty": 1.0,
                "first_target_status": "filled",
                "first_target_hit": True,
                "first_target_filled_qty": 0.5,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "runner_protected": False,
                "runner_stop_price": None,
                "symbol_step_size": 0.01,
                "min_order_qty": 0.1,
                "status": "OPEN"
            }
        },
        "management_suggestions": [],
        "management_action_previews": []
    }))
    account_path.write_text(json.dumps({"equity": 1000.0, "available_balance": 1000.0, "futures_wallet_balance": 1000.0, "open_positions": [{"symbol": "BTCUSDT", "side": "LONG", "qty": 0.04, "entry_price": 100.0, "mark_price": 110.5, "unrealized_pnl": 0.42, "notional": 4.42, "leverage": 3.0}], "open_orders": []}))
    market_path.write_text(json.dumps([]))
    deriv_path.write_text(json.dumps({}))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "dry-run")
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])

    main_module.main()

    state = json.loads(output_path.read_text())
    assert state["positions"]["BTCUSDT"]["second_target_status"] == "satisfied_by_external_reduction"
    assert state["positions"]["BTCUSDT"]["second_target_hit"] is False


def test_execute_management_actions_stops_same_round_sequence_when_first_stage_remains_pending(monkeypatch, tmp_path, app_config):
    executor = OrderExecutor(app_config, mode="paper")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 2.0,
                "remaining_position_qty": 2.0,
                "entry_price": 100.0,
                "mark_price": 110.5,
                "stop_loss": 95.0,
                "first_target_price": 105.0,
                "second_target_price": 110.0,
                "original_position_qty": 2.0,
                "first_target_status": "pending",
                "first_target_hit": False,
                "first_target_filled_qty": 0.0,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "runner_protected": False,
                "runner_stop_price": None,
            }
        },
    )
    intents = [
        ManagementActionIntent(intent_id="mgmt-btcusdt-partial-first", symbol="BTCUSDT", action="PARTIAL_TAKE_PROFIT", side="LONG", position_qty=2.0, qty=1.0, reference_price=110.5, meta={"target_stage": "first", "exit_trigger": "first_target_hit", "fraction_basis": "original_position"}),
        ManagementActionIntent(intent_id="mgmt-btcusdt-partial-second", symbol="BTCUSDT", action="PARTIAL_TAKE_PROFIT", side="LONG", position_qty=1.0, qty=0.5, reference_price=110.5, meta={"target_stage": "second", "exit_trigger": "second_target_hit", "fraction_basis": "original_position", "runner_protected": True, "runner_stop_price": 105.0}),
    ]

    def fake_apply(state, intent):
        position = dict(state.positions[intent.symbol])
        if intent.meta["target_stage"] == "first":
            position["qty"] = 1.4
            position["remaining_position_qty"] = 1.4
            position["first_target_filled_qty"] = 0.6
            position["first_target_status"] = "pending"
            position["first_target_hit"] = False
            state.positions[intent.symbol] = position
            return position
        raise AssertionError("second-stage intent should not run after incomplete first stage")

    monkeypatch.setattr("trading_system.app.execution.executor.apply_management_action_fill", fake_apply)

    results = executor.execute_management_actions(intents, state)

    assert [row["intent"]["intent_id"] for row in results] == ["mgmt-btcusdt-partial-first"]
    assert state.positions["BTCUSDT"]["second_target_status"] == "pending"


def test_apply_management_action_fill_keeps_runner_unprotected_on_partial_second_stage_fill():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "remaining_position_qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 110.5,
                "stop_loss": 95.0,
                "first_target_price": 105.0,
                "second_target_price": 110.0,
                "original_position_qty": 2.0,
                "first_target_status": "filled",
                "first_target_hit": True,
                "first_target_filled_qty": 1.0,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "runner_protected": False,
                "runner_stop_price": None,
            }
        },
    )

    updated = apply_management_action_fill(
        state,
        ManagementActionIntent(
            intent_id="mgmt-btcusdt-partial-second",
            symbol="BTCUSDT",
            action="PARTIAL_TAKE_PROFIT",
            side="LONG",
            position_qty=1.0,
            qty=0.2,
            reference_price=110.5,
            meta={"target_stage": "second", "exit_trigger": "second_target_hit", "fraction_basis": "original_position", "runner_protected": True, "runner_stop_price": 105.0},
        ),
    )

    assert updated["second_target_status"] == "pending"
    assert updated["second_target_hit"] is False
    assert updated["runner_protected"] is False
    assert updated["runner_stop_price"] is None
```

- [ ] **Step 2: Run the paper-writeback tests to verify they fail**

Run:
```bash
uv run --with pytest python -m pytest trading_system/tests/test_management_execution.py -q -p no:cacheprovider -k "execute_management_actions or terminalization_pass or apply_management_action_fill or no_management_intents"
```

Expected:
- FAIL because there is no management execution path, no no-action terminalization pass, and no sequencing guard for incomplete first-stage fills yet

- [ ] **Step 3: Implement sequential paper execution, no-action terminalization, and per-action state apply**

Add these primitives:

```python
def apply_management_action_fill(state: RuntimeState, intent: ManagementActionIntent) -> dict[str, Any]:
    position = dict(state.positions[intent.symbol])
    filled_qty = round(float(intent.qty or 0.0), 8)
    remaining_qty = max(round(float(position.get("qty", 0.0)) - filled_qty, 8), 0.0)
    position["qty"] = remaining_qty
    position["remaining_position_qty"] = remaining_qty

    stage = str((intent.meta or {}).get("target_stage") or "")
    if intent.action == "PARTIAL_TAKE_PROFIT" and stage in {"first", "second"}:
        key = f"{stage}_target_filled_qty"
        position[key] = round(float(position.get(key, 0.0) or 0.0) + filled_qty, 8)
        if stage_completed(position, stage=stage):
            position[f"{stage}_target_status"] = "filled"
            position[f"{stage}_target_hit"] = True
            if stage == "second" and remaining_qty > 0:
                position["runner_protected"] = bool((intent.meta or {}).get("runner_protected"))
                position["runner_stop_price"] = (intent.meta or {}).get("runner_stop_price")
            elif stage == "second":
                position["runner_protected"] = False
                position["runner_stop_price"] = None
        elif stage == "second":
            position["second_target_status"] = "pending"
            position["second_target_hit"] = False
            position["runner_protected"] = False
            position["runner_stop_price"] = None
    elif intent.action == "EXIT":
        position["qty"] = 0.0
        position["remaining_position_qty"] = 0.0
        position["runner_protected"] = False
        position["runner_stop_price"] = None

    state.positions[intent.symbol] = terminalize_all_unreachable_stages(position)
    return state.positions[intent.symbol]


def run_management_terminalization_pass(state: RuntimeState) -> None:
    for symbol, position in list(state.positions.items()):
        state.positions[symbol] = terminalize_all_unreachable_stages(dict(position))
```

And in `executor.py`:
- add `execute_management_action()` and `execute_management_actions()`
- for paper mode, treat management intents as deterministic reduce-only fills, apply them sequentially, and stop if one returns unsupported / zero qty / failure
- after each applied target-stage action, re-read the mutated position; if that stage is still `pending`, break the same-round sequence so later actions cannot leapfrog an incomplete earlier stage
- for dry-run, keep current `preview_management_actions()` behavior unchanged

Then update `main.py`:
- build management suggestions first
- build intents second
- run `run_management_terminalization_pass(state)` once every round after management suggestions are derived, even if `management_intents` is empty
- if `config.execution.mode == "paper"`, call `executor.execute_management_actions(...)` before final save
- after paper execution, run `run_management_terminalization_pass(state)` again so external reductions / rounding can settle to `satisfied_by_external_reduction`
- always persist `management_suggestions`
- in dry-run continue storing `management_action_previews`
- in paper mode either store the executed-management results in a new local variable or still also keep previews for observability, but do not skip the actual state writeback

- [ ] **Step 4: Run the paper-writeback tests to verify they pass**

Run:
```bash
uv run --with pytest python -m pytest trading_system/tests/test_management_execution.py -q -p no:cacheprovider -k "execute_management_actions or terminalization or apply_management_action_fill or no_management_intents"
```

Expected:
- PASS

- [ ] **Step 5: Commit the management execution / writeback change**

```bash
git add trading_system/app/portfolio/positions.py trading_system/app/portfolio/target_management.py trading_system/app/execution/executor.py trading_system/app/main.py trading_system/tests/test_management_execution.py
git commit -m "feat: apply target management state in paper execution"
```

## Chunk 2: Reporting, focused dry-run coverage, and final verification

### Task 5: Surface the new state in reporting and lock the end-to-end dry-run cycle

**Files:**
- Modify: `trading_system/app/reporting/daily_report.py:52-149`
- Modify: `trading_system/tests/test_reporting.py:241-377`
- Create: `trading_system/tests/test_target_scaleout_runner_cycle.py`

- [x] **Step 1: Write the failing reporting and cycle tests**

```python
def test_build_lifecycle_report_surfaces_b_view_target_runner_fields():
    summary = build_lifecycle_report(
        lifecycle_updates={
            "BTCUSDT": {
                "state": "PROTECT",
                "reason_codes": ["payload_to_protect_trend_mature"],
                "r_multiple": 2.0,
                "first_target_hit": True,
                "second_target_hit": True,
                "first_target_status": "filled",
                "second_target_status": "filled",
                "runner_protected": True,
                "runner_stop_price": 105.0,
                "scale_out_plan": {"first": 0.5, "second": 0.25, "runner": 0.25, "basis": "original_position"},
                "second_target_source": "fixed_2r",
            },
            "ETHUSDT": {
                "state": "PAYLOAD",
                "reason_codes": ["payload_waiting_second_stage"],
                "r_multiple": 0.9,
                "first_target_hit": False,
                "second_target_hit": False,
                "first_target_status": "satisfied_by_external_reduction",
                "second_target_status": "pending",
                "runner_protected": False,
                "runner_stop_price": None,
                "scale_out_plan": {"first": 0.5, "second": 0.25, "runner": 0.25, "basis": "original_position"},
                "second_target_source": "fixed_2r",
            },
        },
        management_suggestions=[
            {
                "symbol": "BTCUSDT",
                "action": "PARTIAL_TAKE_PROFIT",
                "priority": "MEDIUM",
                "qty_fraction": 0.25,
                "meta": {
                    "target_stage": "second",
                    "fraction_basis": "original_position",
                    "runner_stop_price": 105.0,
                    "invalidation_source": "trend_breakout_failure_below_4h_ema20",
                    "invalidation_reason": "breakout continuation lost 4h breakout support",
                    "stop_family": "structure_stop",
                    "stop_reference": "4h_ema20",
                    "stop_policy_source": "shared_taxonomy",
                },
            }
        ],
    )

    leader = summary["leaders"][0]
    assert leader["symbol"] == "BTCUSDT"
    assert leader["first_target_hit"] is True
    assert leader["second_target_hit"] is True
    assert leader["runner_protected"] is True
    assert leader["runner_stop_price"] == pytest.approx(105.0)
    assert leader["scale_out_plan"] == {"first": 0.5, "second": 0.25, "runner": 0.25, "basis": "original_position"}
    assert leader["second_target_source"] == "fixed_2r"
    assert summary["review_actions"][0]["target_stage"] == "second"
    assert summary["review_actions"][0]["fraction_basis"] == "original_position"
    assert summary["review_actions"][0]["runner_stop_price"] == pytest.approx(105.0)
    audit_rows = {row["symbol"]: row for row in summary["audit_target_states"]}
    assert audit_rows["BTCUSDT"] == {"symbol": "BTCUSDT", "first_target_status": "filled", "second_target_status": "filled"}
    assert audit_rows["ETHUSDT"] == {"symbol": "ETHUSDT", "first_target_status": "satisfied_by_external_reduction", "second_target_status": "pending"}


def test_main_dry_run_cycle_surfaces_gap_through_suggestions_and_previews(monkeypatch, tmp_path):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    output_path.write_text(json.dumps({
        "updated_at_bj": "2026-04-09T20:00:00+08:00",
        "positions": {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 2.0,
                "remaining_position_qty": 2.0,
                "entry_price": 100.0,
                "mark_price": 110.5,
                "stop_loss": 95.0,
                "take_profit": 107.0,
                "first_target_price": 105.0,
                "first_target_source": "fallback_1r",
                "second_target_price": 110.0,
                "second_target_source": "fixed_2r",
                "original_position_qty": 2.0,
                "first_target_status": "pending",
                "first_target_hit": False,
                "first_target_filled_qty": 0.0,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "runner_protected": False,
                "runner_stop_price": None,
                "scale_out_plan": {"first": 0.5, "second": 0.25, "runner": 0.25, "basis": "original_position"},
                "status": "OPEN"
            }
        },
        "management_suggestions": [],
        "management_action_previews": []
    }))
    account_path.write_text(json.dumps({"equity": 1000.0, "available_balance": 1000.0, "futures_wallet_balance": 1000.0, "open_positions": [{"symbol": "BTCUSDT", "side": "LONG", "qty": 2.0, "entry_price": 100.0, "mark_price": 110.5, "unrealized_pnl": 21.0, "notional": 221.0, "leverage": 3.0}], "open_orders": []}))
    market_path.write_text(json.dumps([]))
    deriv_path.write_text(json.dumps({}))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "dry-run")
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])

    main_module.main()

    state = json.loads(output_path.read_text())
    btc_rows = [row for row in state["management_suggestions"] if row["symbol"] == "BTCUSDT"]
    assert [row["meta"]["target_stage"] for row in btc_rows if row["action"] == "PARTIAL_TAKE_PROFIT"] == ["first", "second"]
    preview_rows = [row for row in state["management_action_previews"] if row["intent"]["symbol"] == "BTCUSDT"]
    assert [row["preview"]["intent"]["meta"].get("target_stage") for row in preview_rows] == ["first", "second"]
    assert [row["preview"]["intent"]["qty"] for row in preview_rows] == [1.0, 0.5]
    assert state["lifecycle_summary"]["management_action_counts"]["PARTIAL_TAKE_PROFIT"] == 2
    position = state["positions"]["BTCUSDT"]
    assert position["remaining_position_qty"] == pytest.approx(2.0)
    assert position["first_target_status"] == "pending"
    assert position["first_target_hit"] is False
    assert position["first_target_filled_qty"] == pytest.approx(0.0)
    assert position["second_target_status"] == "pending"
    assert position["second_target_hit"] is False
    assert position["second_target_filled_qty"] == pytest.approx(0.0)
    assert position["runner_protected"] is False
    assert position["runner_stop_price"] is None
```

- [x] **Step 2: Run the reporting and cycle tests to verify they fail**

Run:
```bash
uv run --with pytest python -m pytest trading_system/tests/test_reporting.py trading_system/tests/test_target_scaleout_runner_cycle.py -q -p no:cacheprovider
```

Expected:
- FAIL because reporting does not yet project the new fields and no focused cycle file exists

- [x] **Step 3: Implement the reporting projection and focused cycle coverage**

Update reporting helpers so that:
- `_lifecycle_leader_row()` includes, when present:
  - `first_target_hit`
  - `second_target_hit`
  - `runner_protected`
  - `runner_stop_price`
  - `scale_out_plan`
  - `second_target_source`
- `_review_action_row()` includes `target_stage`, `fraction_basis`, and `runner_stop_price` when present
- add an audit/debug surface such as `audit_target_states` that exposes `first_target_status` and `second_target_status` for replay / debug use, including nonstandard terminal states like `satisfied_by_external_reduction`
- default B-view still omits raw status enums from top-level compact summary, but the audit/debug surface must make those stage-status enums inspectable

The focused cycle test should:
- seed a long breakout position with frozen target fields already present in runtime state
- set mark price directly above `second_target_price`
- run `main()` in dry-run
- assert ordered first / second partial suggestions
- assert preview quantities are based on original size, not current qty multiplier shortcuts
- assert lifecycle summary counts the new actions deterministically

- [x] **Step 4: Run the full focused verification set**

Run:
```bash
uv run --with pytest python -m pytest trading_system/tests/test_target_management_state.py trading_system/tests/test_exit_policy.py trading_system/tests/test_management_execution.py trading_system/tests/test_reporting.py trading_system/tests/test_target_scaleout_runner_cycle.py -q -p no:cacheprovider
```

Expected:
- PASS

- [x] **Step 5: Commit the reporting and cycle coverage**

```bash
git add trading_system/app/reporting/daily_report.py trading_system/tests/test_reporting.py trading_system/tests/test_target_scaleout_runner_cycle.py
git commit -m "feat: report target and runner management state"
```

## Final verification

- [x] **Step 1: Run the full target-management test bundle**

```bash
uv run --with pytest python -m pytest \
  trading_system/tests/test_target_management_state.py \
  trading_system/tests/test_exit_policy.py \
  trading_system/tests/test_management_execution.py \
  trading_system/tests/test_reporting.py \
  trading_system/tests/test_target_scaleout_runner_cycle.py \
  -q -p no:cacheprovider
```

Expected:
- PASS

- [x] **Step 2: Run the existing neighboring regression files**

```bash
uv run --with pytest python -m pytest \
  trading_system/tests/test_paper_executor.py \
  trading_system/tests/test_main_v2_cycle.py -q -p no:cacheprovider -k "taxonomy or followthrough or de_risk or partial"
```

Expected:
- PASS with no regressions in adjacent exit / reporting behavior

- [x] **Step 3: Inspect the final diff before handoff**

Run:
```bash
git status --short
git diff --stat HEAD~5..HEAD
```

Expected:
- Only the planned portfolio / execution / reporting files and focused tests are changed

- [x] **Step 4: Prepare the implementation handoff note**

Document in the execution handoff / PR summary:
- target-management helper module introduced and why
- dry-run remains preview-only
- paper mode now advances target / runner state deterministically
- short side remains untouched
- legacy positions without target metadata still safely skip the new logic

## Execution handoff

- Scope delivered across the prior milestone commits through `07749f7`: target-state freeze and legacy migration, ordered first/second target decisions, reconciliation against live remaining size, deterministic paper writeback, and reporting plus focused cycle coverage.
- Post-closeout rerun on `bdac536` (paper lifecycle summary alignment): the focused target-management bundle still passed cleanly and the adjacent paper/runtime regression slice remained green, so no further runtime or reporting fixes were required after the final summary expectation cleanup.
- Focused verification run on 2026-04-09:
  - `uv run --with pytest python -m pytest trading_system/tests/test_target_management_state.py trading_system/tests/test_exit_policy.py trading_system/tests/test_management_execution.py trading_system/tests/test_reporting.py trading_system/tests/test_target_scaleout_runner_cycle.py -q -p no:cacheprovider`
  - Result: `37 passed in 0.25s`
- Neighboring regression run on 2026-04-09:
  - `uv run --with pytest python -m pytest trading_system/tests/test_paper_executor.py trading_system/tests/test_main_v2_cycle.py -q -p no:cacheprovider -k "taxonomy or followthrough or de_risk or partial"`
  - Result: `10 passed, 50 deselected in 0.19s`
- Refreshed closeout verification rerun on 2026-04-09 after `bdac536`:
  - `uv run --with pytest python -m pytest trading_system/tests/test_target_management_state.py trading_system/tests/test_exit_policy.py trading_system/tests/test_management_execution.py trading_system/tests/test_reporting.py trading_system/tests/test_target_scaleout_runner_cycle.py -q -p no:cacheprovider`
  - Result: `37 passed in 0.23s`
  - `uv run --with pytest python -m pytest trading_system/tests/test_paper_executor.py trading_system/tests/test_main_v2_cycle.py -q -p no:cacheprovider -k "taxonomy or followthrough or de_risk or partial"`
  - Result: `10 passed, 50 deselected in 0.65s`
- Diff inspection run on 2026-04-09:
  - `git diff --stat HEAD~5..HEAD`
  - Result: planned portfolio / execution / reporting files and focused target-management tests changed, matching the intended implementation slices before this closeout note.
