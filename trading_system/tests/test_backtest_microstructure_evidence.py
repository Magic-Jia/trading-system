from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_system.app.backtest.microstructure_evidence import (
    build_microstructure_gate,
    simulate_depth_driven_taker_fill,
    write_microstructure_gate,
)


def test_builds_synthetic_microstructure_gate_when_coverage_is_sufficient(tmp_path: Path) -> None:
    manifest = {
        "evidence_source": {"type": "synthetic_fixture", "run_id": "unit-test-only"},
        "coverage": {
            "l2_snapshot_coverage": 0.995,
            "l2_update_coverage": 0.992,
            "tick_coverage": 0.991,
        },
    }

    gate = build_microstructure_gate(manifest, min_coverage=0.99)

    assert gate["schema_version"] == "market_microstructure_gate_input.v1"
    assert gate["evidence_source"] == {"type": "synthetic_fixture", "run_id": "unit-test-only"}
    assert gate["checks"] == {
        "l2_tick_coverage_met": True,
        "depth_driven_taker_met": False,
    }
    assert gate["coverage"] == {
        "l2_snapshot_coverage": 0.995,
        "l2_update_coverage": 0.992,
        "tick_coverage": 0.991,
        "min_required_coverage": 0.99,
    }
    assert gate["reasons"] == ["depth_driven_taker_evidence_missing"]

    output_path = write_microstructure_gate(manifest, tmp_path, min_coverage=0.99)
    assert output_path == tmp_path / "market_microstructure_gate.json"
    assert json.loads(output_path.read_text()) == gate


def test_rejects_missing_or_low_l2_tick_coverage() -> None:
    gate = build_microstructure_gate(
        {
            "coverage": {
                "l2_snapshot_coverage": 0.995,
                "l2_update_coverage": 0.50,
            }
        },
        min_coverage=0.99,
    )

    assert gate["evidence_source"] == {"type": "synthetic_fixture"}
    assert gate["checks"]["l2_tick_coverage_met"] is False
    assert gate["checks"]["depth_driven_taker_met"] is False
    assert gate["coverage"]["tick_coverage"] is None
    assert "l2_tick_coverage_below_threshold" in gate["reasons"]
    assert "depth_driven_taker_evidence_missing" in gate["reasons"]


def test_rejects_invalid_coverage_values() -> None:
    with pytest.raises(ValueError, match="l2_snapshot_coverage"):
        build_microstructure_gate(
            {"coverage": {"l2_snapshot_coverage": 1.5, "l2_update_coverage": 1, "tick_coverage": 1}}
        )


def test_depth_driven_buy_fill_consumes_asks_and_reports_vwap() -> None:
    fill = simulate_depth_driven_taker_fill(
        side="buy",
        quantity=3.0,
        reference_price=100.0,
        bids=[{"price": 99.9, "quantity": 10}],
        asks=[{"price": 100.1, "quantity": 1}, {"price": 100.4, "quantity": 2}],
    )

    assert fill == {
        "side": "buy",
        "requested_quantity": 3.0,
        "filled_quantity": 3.0,
        "residual_quantity": 0.0,
        "complete": True,
        "vwap": pytest.approx(100.3),
        "slippage_bps": pytest.approx(30.0),
        "consumed_levels": [
            {"price": 100.1, "quantity": 1.0},
            {"price": 100.4, "quantity": 2.0},
        ],
    }


def test_depth_driven_sell_fill_consumes_bids_and_reports_vwap() -> None:
    fill = simulate_depth_driven_taker_fill(
        side="sell",
        quantity=2.5,
        reference_price=100.0,
        bids=[{"price": 99.9, "quantity": 1.0}, {"price": 99.7, "quantity": 5.0}],
        asks=[{"price": 100.1, "quantity": 10}],
    )

    assert fill["complete"] is True
    assert fill["filled_quantity"] == 2.5
    assert fill["residual_quantity"] == 0.0
    assert fill["vwap"] == pytest.approx(99.78)
    assert fill["slippage_bps"] == pytest.approx(22.0)
    assert fill["consumed_levels"] == [
        {"price": 99.9, "quantity": 1.0},
        {"price": 99.7, "quantity": 1.5},
    ]


def test_incomplete_depth_fill_keeps_gate_conservative() -> None:
    fill = simulate_depth_driven_taker_fill(
        side="buy",
        quantity=5.0,
        reference_price=100.0,
        bids=[],
        asks=[{"price": 100.1, "quantity": 2.0}],
    )
    gate = build_microstructure_gate(
        {
            "coverage": {
                "l2_snapshot_coverage": 1.0,
                "l2_update_coverage": 1.0,
                "tick_coverage": 1.0,
            },
            "depth_driven_taker_fills": [fill],
        }
    )

    assert fill["complete"] is False
    assert fill["residual_quantity"] == 3.0
    assert gate["checks"]["depth_driven_taker_met"] is False
    assert "depth_driven_taker_incomplete_fill" in gate["reasons"]


def test_complete_depth_fills_satisfy_depth_driven_taker_gate() -> None:
    fill = simulate_depth_driven_taker_fill(
        side="buy",
        quantity=1.0,
        reference_price=100.0,
        bids=[],
        asks=[{"price": 100.2, "quantity": 1.0}],
    )
    gate = build_microstructure_gate(
        {
            "coverage": {
                "l2_snapshot_coverage": 1.0,
                "l2_update_coverage": 1.0,
                "tick_coverage": 1.0,
            },
            "depth_driven_taker_fills": [fill],
        }
    )

    assert gate["checks"] == {"l2_tick_coverage_met": True, "depth_driven_taker_met": True}
    assert gate["reasons"] == []
    assert gate["depth_driven_taker"] == {"fill_count": 1, "complete_fill_count": 1, "incomplete_fill_count": 0}


def test_rejects_non_boolean_depth_driven_taker_override() -> None:
    with pytest.raises(ValueError, match="depth_driven_taker_met must be a boolean"):
        build_microstructure_gate(
            {
                "coverage": {
                    "l2_snapshot_coverage": 1.0,
                    "l2_update_coverage": 1.0,
                    "tick_coverage": 1.0,
                },
                "depth_driven_taker_met": "false",
            }
        )


def test_rejects_boolean_coverage_values() -> None:
    with pytest.raises(ValueError, match="l2_snapshot_coverage must be a number between 0 and 1"):
        build_microstructure_gate(
            {
                "coverage": {
                    "l2_snapshot_coverage": True,
                    "l2_update_coverage": 1.0,
                    "tick_coverage": 1.0,
                }
            }
        )


def test_rejects_string_coverage_values() -> None:
    with pytest.raises(ValueError, match="l2_snapshot_coverage must be a number between 0 and 1"):
        build_microstructure_gate(
            {
                "coverage": {
                    "l2_snapshot_coverage": "1.0",
                    "l2_update_coverage": 1.0,
                    "tick_coverage": 1.0,
                }
            }
        )


def test_rejects_non_object_depth_driven_fill_entries() -> None:
    with pytest.raises(ValueError, match="depth_driven_taker_fills entries must be mappings"):
        build_microstructure_gate(
            {
                "coverage": {
                    "l2_snapshot_coverage": 1.0,
                    "l2_update_coverage": 1.0,
                    "tick_coverage": 1.0,
                },
                "depth_driven_taker_fills": ["not-a-fill"],
            }
        )


def test_microstructure_gate_rejects_non_string_evidence_source_type() -> None:
    with pytest.raises(ValueError, match="evidence_source type must be a string"):
        build_microstructure_gate(
            {
                "evidence_source": {"type": 123},
                "coverage": {
                    "l2_snapshot_coverage": 1.0,
                    "l2_update_coverage": 1.0,
                    "tick_coverage": 1.0,
                },
            }
        )


def test_microstructure_gate_rejects_non_string_evidence_source_run_id() -> None:
    with pytest.raises(ValueError, match="evidence_source run_id must be a string"):
        build_microstructure_gate(
            {
                "evidence_source": {"type": "historical_l2_tick_archive", "run_id": 123},
                "coverage": {
                    "l2_snapshot_coverage": 1.0,
                    "l2_update_coverage": 1.0,
                    "tick_coverage": 1.0,
                },
            }
        )


def test_microstructure_gate_rejects_unknown_evidence_source_fields() -> None:
    with pytest.raises(ValueError, match="unknown evidence_source field: extra"):
        build_microstructure_gate(
            {
                "evidence_source": {"type": "historical_l2_tick_archive", "extra": "not-allowed"},
                "coverage": {
                    "l2_snapshot_coverage": 1.0,
                    "l2_update_coverage": 1.0,
                    "tick_coverage": 1.0,
                },
            }
        )


@pytest.mark.parametrize(
    "invalid_key",
    [
        123,
        "",
        " ",
        " type",
        "type ",
    ],
)
def test_microstructure_gate_rejects_noncanonical_evidence_source_keys(invalid_key: object) -> None:
    with pytest.raises(ValueError, match="evidence_source keys must be canonical strings"):
        build_microstructure_gate(
            {
                "evidence_source": {"type": "historical_l2_tick_archive", invalid_key: "bad-key"},
                "coverage": {
                    "l2_snapshot_coverage": 1.0,
                    "l2_update_coverage": 1.0,
                    "tick_coverage": 1.0,
                },
            }
        )


def test_microstructure_gate_rejects_unknown_manifest_fields() -> None:
    with pytest.raises(ValueError, match="unknown microstructure manifest field: unexpected"):
        build_microstructure_gate(
            {
                "unexpected": "not-allowed",
                "coverage": {
                    "l2_snapshot_coverage": 1.0,
                    "l2_update_coverage": 1.0,
                    "tick_coverage": 1.0,
                },
            }
        )

def test_microstructure_gate_rejects_unknown_coverage_fields() -> None:
    with pytest.raises(ValueError, match="unknown microstructure coverage field: stale_coverage_alias"):
        build_microstructure_gate(
            {
                "coverage": {
                    "l2_snapshot_coverage": 1.0,
                    "l2_update_coverage": 1.0,
                    "tick_coverage": 1.0,
                    "stale_coverage_alias": 1.0,
                }
            }
        )

def test_microstructure_gate_rejects_unknown_depth_fill_fields() -> None:
    with pytest.raises(ValueError, match="unknown depth_driven_taker_fills field: legacy_depth_sufficient"):
        build_microstructure_gate(
            {
                "coverage": {
                    "l2_snapshot_coverage": 1.0,
                    "l2_update_coverage": 1.0,
                    "tick_coverage": 1.0,
                },
                "depth_driven_taker_fills": [{"complete": True, "legacy_depth_sufficient": True}],
            }
        )

def test_microstructure_gate_rejects_padded_evidence_source_type() -> None:
    try:
        build_microstructure_gate(
            {
                "evidence_source": {"type": " historical_l2_tick_archive ", "run_id": "micro-1"},
                "coverage": {
                    "l2_snapshot_coverage": 0.99,
                    "l2_update_coverage": 0.99,
                    "tick_coverage": 0.99,
                },
                "depth_driven_taker_fills": [{"complete": True}],
            }
        )
    except ValueError as exc:
        assert "evidence_source type must be canonical" in str(exc)
    else:
        raise AssertionError("expected padded evidence_source type to be rejected")

def test_microstructure_gate_rejects_padded_depth_fill_side() -> None:
    try:
        build_microstructure_gate(
            {
                "coverage": {
                    "l2_snapshot_coverage": 0.99,
                    "l2_update_coverage": 0.99,
                    "tick_coverage": 0.99,
                },
                "depth_driven_taker_fills": [{"complete": True, "side": " buy "}],
            }
        )
    except ValueError as exc:
        assert "depth_driven_taker_fills side must be canonical" in str(exc)
    else:
        raise AssertionError("expected padded depth fill side to be rejected")

def test_microstructure_gate_rejects_string_depth_fill_quantity() -> None:
    try:
        build_microstructure_gate(
            {
                "coverage": {
                    "l2_snapshot_coverage": 0.99,
                    "l2_update_coverage": 0.99,
                    "tick_coverage": 0.99,
                },
                "depth_driven_taker_fills": [
                    {"complete": True, "side": "buy", "requested_quantity": "1.0"}
                ],
            }
        )
    except ValueError as exc:
        assert "depth_driven_taker_fills requested_quantity must be a number" in str(exc)
    else:
        raise AssertionError("expected string depth fill quantity to be rejected")

def test_microstructure_gate_rejects_string_consumed_level_price() -> None:
    try:
        build_microstructure_gate(
            {
                "coverage": {
                    "l2_snapshot_coverage": 0.99,
                    "l2_update_coverage": 0.99,
                    "tick_coverage": 0.99,
                },
                "depth_driven_taker_fills": [
                    {
                        "complete": True,
                        "side": "buy",
                        "consumed_levels": [{"price": "100.0", "quantity": 1.0}],
                    }
                ],
            }
        )
    except ValueError as exc:
        assert "depth_driven_taker_fills consumed_levels price must be a number" in str(exc)
    else:
        raise AssertionError("expected string consumed level price to be rejected")
