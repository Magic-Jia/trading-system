from __future__ import annotations

import copy
import math

import pytest

from trading_system.app.runtime.runtime_safety_evidence import validate_runtime_incident_bundle


def _valid_incident_bundle() -> dict:
    return {
        "schema_version": "runtime_incident_replay_bundle.v1",
        "incident_id": "incident-20260516-100000Z-BTCUSDT",
        "runtime_config_hash": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "generated_at": "2026-05-16T10:01:00Z",
        "replay_window": {
            "started_at": "2026-05-16T10:00:00Z",
            "ended_at": "2026-05-16T10:00:40Z",
            "max_clock_skew_seconds": 2,
            "max_event_age_seconds": 300,
        },
        "clocks": {
            "runtime_observed_at": "2026-05-16T10:00:00Z",
            "exchange_observed_at": "2026-05-16T10:00:01Z",
            "monotonic_started_ns": 1000,
            "monotonic_ended_ns": 2000,
        },
        "evidence_refs": {
            "logs": ["runtime/paper/prod/logs/incident.log"],
            "metrics": ["runtime/paper/prod/metrics/incident.prom"],
            "traces": ["runtime/paper/prod/traces/incident.jsonl"],
        },
        "remediation": {
            "status": "complete",
            "owner": "runtime-ops",
            "updated_at": "2026-05-16T10:00:50Z",
            "fail_closed": True,
        },
        "events": [
            {
                "event_id": "evt-001-signal",
                "event_type": "signal",
                "occurred_at": "2026-05-16T10:00:01Z",
                "payload": {"symbol": "BTCUSDT", "setup_type": "TREND", "score": 0.82},
            },
            {
                "event_id": "evt-002-order-intent",
                "event_type": "order_intent",
                "occurred_at": "2026-05-16T10:00:02Z",
                "payload": {"intent_id": "intent-BTCUSDT-001", "side": "BUY", "quantity": 0.1},
            },
            {
                "event_id": "evt-003-risk-check",
                "event_type": "risk_check",
                "occurred_at": "2026-05-16T10:00:03Z",
                "payload": {"passed": True, "max_notional": 1000.0},
            },
            {
                "event_id": "evt-004-kill-switch",
                "event_type": "kill_switch_decision",
                "occurred_at": "2026-05-16T10:00:04Z",
                "payload": {"decision": "allow", "fail_closed": False},
            },
            {
                "event_id": "evt-005-submit",
                "event_type": "submit",
                "occurred_at": "2026-05-16T10:00:05Z",
                "payload": {"client_order_id": "paper-BTCUSDT-001", "status": "submitted"},
            },
            {
                "event_id": "evt-006-ack",
                "event_type": "ack",
                "occurred_at": "2026-05-16T10:00:06Z",
                "payload": {"exchange_order_id": "ex-001", "status": "acknowledged"},
            },
            {
                "event_id": "evt-007-fill",
                "event_type": "fill",
                "occurred_at": "2026-05-16T10:00:07Z",
                "payload": {"filled_quantity": 0.1, "price": 68000.5},
            },
            {
                "event_id": "evt-008-cancel",
                "event_type": "cancel",
                "occurred_at": "2026-05-16T10:00:08Z",
                "payload": {"status": "not_required", "reason": "fully_filled"},
            },
            {
                "event_id": "evt-009-reconcile",
                "event_type": "reconcile",
                "occurred_at": "2026-05-16T10:00:09Z",
                "payload": {"state": "matched", "fail_closed": False},
            },
        ],
    }


def _rejects(bundle: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_runtime_incident_bundle(bundle)


def test_runtime_incident_bundle_accepts_complete_replay_contract() -> None:
    canonical = validate_runtime_incident_bundle(_valid_incident_bundle())

    assert canonical["schema_version"] == "runtime_incident_replay_bundle.v1"
    assert canonical["summary"] == {
        "event_count": 9,
        "critical_path": [
            "signal",
            "order_intent",
            "risk_check",
            "kill_switch_decision",
            "submit",
            "ack",
            "fill",
            "cancel",
            "reconcile",
        ],
        "fail_closed": True,
    }


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ("runtime_config_hash", "runtime_config_hash must be present"),
        ("clocks", "clocks must be present"),
        ("evidence_refs", "evidence_refs must be present"),
        ("remediation", "remediation must be present"),
        ("events", "events must be present"),
    ],
)
def test_runtime_incident_bundle_rejects_missing_required_sections(section: str, message: str) -> None:
    bundle = _valid_incident_bundle()
    del bundle[section]

    _rejects(bundle, message)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("generated_at",), "2026-05-16T12:01:00+02:00", "generated_at must be a canonical UTC timestamp"),
        (
            ("events", 0, "occurred_at"),
            "2026-05-16T10:00:01+00:00",
            r"events\[0\]\.occurred_at must be a canonical UTC timestamp",
        ),
        (
            ("events", 0, "occurred_at"),
            "2026-05-16T09:59:59Z",
            r"events\[0\]\.occurred_at must be inside replay_window",
        ),
        (
            ("events", 0, "occurred_at"),
            "2026-05-16T10:00:41Z",
            r"events\[0\]\.occurred_at must be inside replay_window",
        ),
        (
            ("clocks", "exchange_observed_at"),
            "2026-05-16T10:00:09Z",
            "clocks exchange_observed_at exceeds max_clock_skew_seconds",
        ),
    ],
)
def test_runtime_incident_bundle_rejects_noncanonical_stale_and_future_timestamps(
    path: tuple[object, ...],
    value: object,
    message: str,
) -> None:
    bundle = _valid_incident_bundle()
    target = bundle
    for key in path[:-1]:
        target = target[key]  # type: ignore[index,assignment]
    target[path[-1]] = value  # type: ignore[index]

    _rejects(bundle, message)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("events", 0, "event_id"), True, r"events\[0\]\.event_id must be a string"),
        (("events", 0, "event_type"), " signal", r"events\[0\]\.event_type must be canonical"),
        (
            ("events", 2, "payload", "passed"),
            "true",
            r"events\[2\]\.payload\.passed must be a boolean",
        ),
        (
            ("events", 0, "payload", "score"),
            "0.82",
            r"events\[0\]\.payload\.score must be numeric",
        ),
        (
            ("events", 0, "payload", "score"),
            math.inf,
            r"events\[0\]\.payload\.score must be finite",
        ),
        (
            ("clocks", "monotonic_started_ns"),
            False,
            "clocks monotonic_started_ns must be numeric, not boolean",
        ),
    ],
)
def test_runtime_incident_bundle_rejects_ambiguous_scalars(
    path: tuple[object, ...],
    value: object,
    message: str,
) -> None:
    bundle = _valid_incident_bundle()
    target = bundle
    for key in path[:-1]:
        target = target[key]  # type: ignore[index,assignment]
    target[path[-1]] = value  # type: ignore[index]

    _rejects(bundle, message)


def test_runtime_incident_bundle_rejects_duplicate_event_ids() -> None:
    bundle = _valid_incident_bundle()
    bundle["events"][1]["event_id"] = "evt-001-signal"

    _rejects(bundle, "duplicate incident event_id: evt-001-signal")


@pytest.mark.parametrize(
    ("event_index", "payload"),
    [
        (3, {"decision": "unknown", "fail_closed": False}),
        (8, {"state": "unknown", "fail_closed": False}),
    ],
)
def test_runtime_incident_bundle_rejects_unresolved_unknown_state_without_fail_closed(
    event_index: int,
    payload: dict,
) -> None:
    bundle = _valid_incident_bundle()
    bundle["events"][event_index]["payload"] = payload

    _rejects(bundle, r"unknown incident state must have fail_closed outcome")


def test_runtime_incident_bundle_rejects_unreplayable_critical_path_ordering() -> None:
    bundle = _valid_incident_bundle()
    reordered = copy.deepcopy(bundle["events"])
    reordered[2], reordered[4] = reordered[4], reordered[2]
    bundle["events"] = reordered

    _rejects(bundle, "incident events cannot replay critical path ordering")
