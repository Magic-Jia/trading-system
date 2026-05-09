from __future__ import annotations

import json

from trading_system.app.paper_optimization.promotion import (
    build_promotion_decision,
    materialize_env_overrides,
    write_promotion_decision,
)


def test_materialize_env_overrides_applies_multiply_and_set_operations() -> None:
    payload = {
        "recommendations": [
            {
                "id": "lower-total-risk-budget",
                "overlay_ops": [
                    {
                        "env": "TRADING_MAX_TOTAL_RISK_PCT",
                        "op": "multiply",
                        "factor": 0.8,
                        "default": 0.03,
                        "minimum": 0.005,
                        "precision": 4,
                    }
                ],
            },
            {
                "id": "disable-rotation",
                "overlay_ops": [
                    {
                        "env": "TRADING_DISABLED_ENGINES",
                        "op": "set",
                        "value": "rotation",
                    }
                ],
            },
        ]
    }

    overrides = materialize_env_overrides(
        payload,
        baseline_env={
            "TRADING_MAX_TOTAL_RISK_PCT": "0.03",
            "TRADING_DISABLED_ENGINES": "short",
        },
    )

    assert overrides == {
        "TRADING_MAX_TOTAL_RISK_PCT": "0.024",
        "TRADING_DISABLED_ENGINES": "rotation",
    }


def test_build_promotion_decision_observes_when_no_recommendations_exist() -> None:
    payload = build_promotion_decision(
        recommendations_payload={"recommendations": []},
        recorded_at_bj="2026-04-24T12:05:00+08:00",
    )

    assert payload["status"] == "observe"
    assert payload["decision"] == "observe"
    assert payload["recommendation_count"] == 0
    assert payload["variant"]["env_overrides"] == {}


def test_build_promotion_decision_waits_for_backtest_when_bundles_are_missing() -> None:
    payload = build_promotion_decision(
        recommendations_payload={
            "recommendations": [
                {
                    "id": "lower-total-risk-budget",
                    "overlay_ops": [
                        {
                            "env": "TRADING_MAX_TOTAL_RISK_PCT",
                            "op": "multiply",
                            "factor": 0.8,
                            "default": 0.03,
                            "minimum": 0.005,
                            "precision": 4,
                        }
                    ],
                }
            ]
        },
        baseline_env={"TRADING_MAX_TOTAL_RISK_PCT": "0.03"},
        recorded_at_bj="2026-04-24T12:05:00+08:00",
    )

    assert payload["status"] == "recommend"
    assert payload["decision"] == "awaiting_backtest"
    assert payload["applied_recommendation_ids"] == ["lower-total-risk-budget"]
    assert payload["variant"]["env_overrides"] == {"TRADING_MAX_TOTAL_RISK_PCT": "0.024"}


def test_build_promotion_decision_uses_compare_result_when_validation_bundles_are_available() -> None:
    captured: dict[str, object] = {}

    def fake_compare(*, baseline_bundle, variant_bundle):
        captured["baseline_bundle"] = baseline_bundle
        captured["variant_bundle"] = variant_bundle
        return {
            "promotion_gate": {"decision": "promote", "why": "fixture"},
            "decision_summary": {"decision": "promote", "summary": "validated"},
        }

    payload = build_promotion_decision(
        recommendations_payload={
            "recommendations": [
                {
                    "id": "reduce-trend-bucket-weight",
                    "overlay_ops": [
                        {
                            "env": "TRADING_ALLOCATOR_TREND_BUCKET_WEIGHT",
                            "op": "multiply",
                            "factor": 0.75,
                            "default": 0.7,
                            "minimum": 0.0,
                            "precision": 4,
                        }
                    ],
                }
            ]
        },
        baseline_bundle="/tmp/baseline",
        variant_bundle="/tmp/variant",
        compare_backtest_bundles_fn=fake_compare,
        recorded_at_bj="2026-04-24T12:05:00+08:00",
    )

    assert captured == {
        "baseline_bundle": "/tmp/baseline",
        "variant_bundle": "/tmp/variant",
    }
    assert payload["status"] == "promote"
    assert payload["decision"] == "promote"
    assert payload["baseline_bundle"] == "/tmp/baseline"
    assert payload["variant_bundle"] == "/tmp/variant"
    assert payload["summary"] == "validated"
    assert payload["variant"]["env_overrides"] == {"TRADING_ALLOCATOR_TREND_BUCKET_WEIGHT": "0.525"}


def test_write_promotion_decision_persists_json_payload(tmp_path) -> None:
    recommendations_path = tmp_path / "recommendations.json"
    output_path = tmp_path / "promotion_decision.json"
    recommendations_path.write_text(
        json.dumps(
            {
                "recommendations": [
                    {
                        "id": "lower-total-risk-budget",
                        "overlay_ops": [
                            {
                                "env": "TRADING_MAX_TOTAL_RISK_PCT",
                                "op": "multiply",
                                "factor": 0.8,
                                "default": 0.03,
                                "minimum": 0.005,
                                "precision": 4,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = write_promotion_decision(
        recommendations_path=recommendations_path,
        promotion_decision_path=output_path,
        baseline_env={"TRADING_MAX_TOTAL_RISK_PCT": "0.03"},
        recorded_at_bj="2026-04-24T12:05:00+08:00",
    )

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written == payload
    assert written["decision"] == "awaiting_backtest"
    assert written["variant"]["env_overrides"] == {"TRADING_MAX_TOTAL_RISK_PCT": "0.024"}

def test_materialize_env_overrides_rejects_non_object_overlay_ops() -> None:
    payload = {
        "recommendations": [
            {
                "id": "bad-overlay",
                "overlay_ops": ["not-an-object"],
            }
        ]
    }

    import pytest

    with pytest.raises(ValueError, match="overlay_ops entries must be objects"):
        materialize_env_overrides(payload, baseline_env={})

def test_materialize_env_overrides_rejects_non_string_env_name() -> None:
    payload = {
        "recommendations": [
            {
                "id": "bad-env",
                "overlay_ops": [
                    {"env": 123, "op": "set", "value": "rotation"},
                ],
            }
        ]
    }

    import pytest

    with pytest.raises(ValueError, match="overlay_ops.env must be a string"):
        materialize_env_overrides(payload, baseline_env={})

def test_materialize_env_overrides_rejects_boolean_numeric_overlay_values() -> None:
    payload = {
        "recommendations": [
            {
                "id": "bad-factor",
                "overlay_ops": [
                    {
                        "env": "TRADING_MAX_TOTAL_RISK_PCT",
                        "op": "multiply",
                        "factor": True,
                        "default": 0.03,
                        "precision": 4,
                    }
                ],
            }
        ]
    }

    import pytest

    with pytest.raises(ValueError, match="overlay_ops.factor must be numeric"):
        materialize_env_overrides(payload, baseline_env={})

def test_materialize_env_overrides_rejects_invalid_numeric_overlay_values() -> None:
    payload = {
        "recommendations": [
            {
                "id": "bad-factor",
                "overlay_ops": [
                    {
                        "env": "TRADING_MAX_TOTAL_RISK_PCT",
                        "op": "multiply",
                        "factor": "not-a-number",
                        "default": 0.03,
                        "precision": 4,
                    }
                ],
            }
        ]
    }

    import pytest

    with pytest.raises(ValueError, match="overlay_ops.factor must be numeric"):
        materialize_env_overrides(payload, baseline_env={})


def test_materialize_env_overrides_rejects_invalid_baseline_numeric_values() -> None:
    payload = {
        "recommendations": [
            {
                "id": "bad-base",
                "overlay_ops": [
                    {
                        "env": "TRADING_MAX_TOTAL_RISK_PCT",
                        "op": "multiply",
                        "factor": 0.8,
                        "default": 0.03,
                        "precision": 4,
                    }
                ],
            }
        ]
    }

    import pytest

    with pytest.raises(ValueError, match="overlay_ops.base_value must be numeric"):
        materialize_env_overrides(payload, baseline_env={"TRADING_MAX_TOTAL_RISK_PCT": "bad"})

def test_materialize_env_overrides_rejects_non_string_set_values() -> None:
    payload = {
        "recommendations": [
            {
                "id": "bad-set",
                "overlay_ops": [
                    {"env": "TRADING_DISABLED_ENGINES", "op": "set", "value": 123},
                ],
            }
        ]
    }

    import pytest

    with pytest.raises(ValueError, match="overlay_ops.value must be a string"):
        materialize_env_overrides(payload, baseline_env={})

def test_materialize_env_overrides_rejects_non_object_recommendations() -> None:
    payload = {"recommendations": ["not-an-object"]}

    import pytest

    with pytest.raises(ValueError, match="recommendations entries must be objects"):
        materialize_env_overrides(payload, baseline_env={})


def test_materialize_env_overrides_rejects_non_list_overlay_ops() -> None:
    payload = {
        "recommendations": [
            {
                "id": "bad-overlay-list",
                "overlay_ops": "not-a-list",
            }
        ]
    }

    import pytest

    with pytest.raises(ValueError, match="overlay_ops must be a list"):
        materialize_env_overrides(payload, baseline_env={})

def test_build_promotion_decision_rejects_non_string_recommendation_ids() -> None:
    import pytest

    with pytest.raises(ValueError, match="recommendations.id must be a string"):
        build_promotion_decision(
            recommendations_payload={
                "recommendations": [
                    {
                        "id": 123,
                        "overlay_ops": [],
                    }
                ]
            },
            recorded_at_bj="2026-04-24T12:05:00+08:00",
        )

def test_build_promotion_decision_rejects_non_object_compare_sections() -> None:
    import pytest

    def fake_compare(*, baseline_bundle, variant_bundle):
        return {
            "promotion_gate": "not-an-object",
            "decision_summary": {"decision": "hold", "summary": "blocked"},
        }

    with pytest.raises(ValueError, match="promotion_gate must be an object"):
        build_promotion_decision(
            recommendations_payload={"recommendations": [{"id": "rec", "overlay_ops": []}]},
            baseline_bundle="/tmp/baseline",
            variant_bundle="/tmp/variant",
            compare_backtest_bundles_fn=fake_compare,
            recorded_at_bj="2026-04-24T12:05:00+08:00",
        )

def test_build_promotion_decision_rejects_non_string_compare_decisions() -> None:
    import pytest

    def fake_compare(*, baseline_bundle, variant_bundle):
        return {
            "promotion_gate": {"decision": 123},
            "decision_summary": {"decision": "hold", "summary": "blocked"},
        }

    with pytest.raises(ValueError, match="promotion_gate.decision must be a string"):
        build_promotion_decision(
            recommendations_payload={"recommendations": [{"id": "rec", "overlay_ops": []}]},
            baseline_bundle="/tmp/baseline",
            variant_bundle="/tmp/variant",
            compare_backtest_bundles_fn=fake_compare,
            recorded_at_bj="2026-04-24T12:05:00+08:00",
        )
