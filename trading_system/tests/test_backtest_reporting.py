from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_system.app.backtest import cli, reporting
from trading_system.app.backtest.evaluation import CostStressScenario, build_evaluation_report
from trading_system.app.backtest.types import (
    BacktestConfig,
    BacktestCosts,
    BaselineReplayResult,
    CapitalModelConfig,
    DatasetSnapshotRow,
    ExperimentParams,
    ExitPolicyParams,
    PortfolioDecisionLedgerRow,
    PromotionMetadata,
    PortfolioScorecardRow,
    SampleWindow,
    SetupRewriteParams,
    SetupRewriteRule,
    TradeLedgerRow,
    UniverseFilterConfig,
    WalkForwardConfig,
)


class _DuplicateKeyMapping(Mapping):
    def __init__(self, pairs: list[tuple[object, object]]) -> None:
        self._pairs = pairs

    def __getitem__(self, key: object) -> object:
        for candidate, value in self._pairs:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[object]:
        return (key for key, _value in self._pairs)

    def __len__(self) -> int:
        return len(self._pairs)

    def items(self):  # type: ignore[override]
        return iter(self._pairs)


class _StringSubclass(str):
    pass


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def test_backtest_evaluation_report_rejects_non_object_metadata() -> None:
    with pytest.raises(ValueError, match="metadata must be an object"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": []},
            },
            metadata=[("dataset_root", "dataset")],  # type: ignore[arg-type]
        )


def test_backtest_evaluation_report_rejects_duplicate_metadata_keys() -> None:
    with pytest.raises(ValueError, match="metadata keys must be unique"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": []},
            },
            metadata=_DuplicateKeyMapping(
                [
                    ("dataset_root", "dataset-a"),
                    ("dataset_root", "dataset-b"),
                ]
            ),
        )


def test_backtest_evaluation_report_rejects_invalid_cost_stress_scenario_name() -> None:
    with pytest.raises(ValueError, match=r"cost_stress.scenarios\[0\].scenario.name must be a canonical string"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": [{"scenario": {"name": True}}]},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_padded_experiment_name() -> None:
    with pytest.raises(ValueError, match="experiment_name must be a canonical string"):
        reporting.render_backtest_evaluation_report(
            experiment_name=" evaluation ",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": []},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_malformed_sample_period_shape() -> None:
    with pytest.raises(ValueError, match=r"metadata\.sample_period\.start must be a canonical string"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": []},
            },
            metadata={
                "dataset_root": "dataset",
                "sample_period": {"start": " 2026-03-10T00:00:00Z ", "end": "2026-03-11T00:00:00Z"},
            },
        )


def test_backtest_evaluation_report_rejects_non_object_sample_period() -> None:
    with pytest.raises(ValueError, match="metadata.sample_period must be an object or canonical string"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": []},
            },
            metadata={"sample_period": ["2026-03-10T00:00:00Z", "2026-03-11T00:00:00Z"]},
        )


@pytest.mark.parametrize("boundary", ("start", "end"))
def test_backtest_evaluation_report_rejects_sample_period_missing_boundary(boundary: str) -> None:
    sample_period = {
        "start": "2026-03-10T00:00:00Z",
        "end": "2026-03-11T00:00:00Z",
    }
    del sample_period[boundary]

    with pytest.raises(ValueError, match=rf"metadata\.sample_period\.{boundary} must be present"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": []},
            },
            metadata={"sample_period": sample_period},
        )


@pytest.mark.parametrize(
    ("sample_period", "expected"),
    [
        ("2026-03-10T00:00:00Z/2026-03-11T00:00:00Z", "2026-03-10T00:00:00Z/2026-03-11T00:00:00Z"),
        (
            {"start": "2026-03-10T00:00:00Z", "end": "2026-03-11T00:00:00Z"},
            {"start": "2026-03-10T00:00:00Z", "end": "2026-03-11T00:00:00Z"},
        ),
    ],
)
def test_backtest_evaluation_report_accepts_canonical_sample_period_shapes(
    sample_period: object,
    expected: object,
) -> None:
    report = reporting.render_backtest_evaluation_report(
        experiment_name="evaluation",
        evaluation={
            "walk_forward": {"metadata": {"window_count": 1}},
            "regimes": {"buckets": []},
            "cost_stress": {"scenarios": []},
        },
        metadata={"sample_period": sample_period},
    )

    assert report["summary"]["metadata"]["sample_period"] == expected


@pytest.mark.parametrize("field", ("dataset_root", "baseline_name", "variant_name"))
def test_backtest_evaluation_report_rejects_padded_report_metadata_identifiers(field: str) -> None:
    with pytest.raises(ValueError, match=rf"metadata\.{field} must be a canonical string"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": []},
            },
            metadata={field: " padded "},
        )


def test_backtest_evaluation_report_rejects_noncanonical_sample_period_string() -> None:
    with pytest.raises(ValueError, match=r"metadata\.sample_period must be a canonical string"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": []},
            },
            metadata={"sample_period": " 2026-03-10 "},
        )


def test_backtest_evaluation_report_rejects_duplicate_regime_bucket_labels() -> None:
    with pytest.raises(ValueError, match="regimes.buckets labels must be unique"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": [{"label": "low_vol_uptrend"}, {"label": "low_vol_uptrend"}]},
                "cost_stress": {"scenarios": []},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_duplicate_cost_stress_scenario_names() -> None:
    with pytest.raises(ValueError, match="cost_stress.scenarios scenario.name values must be unique"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": []},
                "cost_stress": {
                    "scenarios": [
                        {"scenario": {"name": "fees_2x"}},
                        {"scenario": {"name": "fees_2x"}},
                    ]
                },
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_false_cost_stress_scenario_name() -> None:
    with pytest.raises(ValueError, match=r"cost_stress.scenarios\[0\].scenario.name must be a canonical string"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": [{"scenario": {"name": False}}]},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_string_cost_stress_base_metric() -> None:
    with pytest.raises(
        ValueError,
        match=r"cost_stress.scenarios\[0\].base_metrics.total_net_return must be a finite number",
    ):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": []},
                "cost_stress": {
                    "scenarios": [
                        {
                            "scenario": {"name": "fees_2x"},
                            "base_metrics": {"total_net_return": "0.01"},
                        }
                    ]
                },
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_boolean_walk_forward_split_metric() -> None:
    with pytest.raises(
        ValueError,
        match=r"walk_forward.windows\[0\].splits.in_sample.metrics.total_net_return must be a finite number",
    ):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {
                    "metadata": {"window_count": 1},
                    "windows": [
                        {
                            "window_index": 1,
                            "splits": {
                                "in_sample": {
                                    "label": "IS",
                                    "trade_ids": [],
                                    "metrics": {"total_net_return": True},
                                },
                                "out_of_sample": {
                                    "label": "OOS",
                                    "trade_ids": [],
                                    "metrics": {"total_net_return": 0.01},
                                },
                            },
                        }
                    ],
                },
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": []},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_non_object_cost_stress_payload() -> None:
    with pytest.raises(ValueError, match="cost_stress must be an object"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": []},
                "cost_stress": [],
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_non_string_mapping_keys() -> None:
    with pytest.raises(ValueError, match="walk_forward key must be a string"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {1: "bad", "metadata": {"window_count": 1}},
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": []},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_invalid_regime_buckets_shape() -> None:
    with pytest.raises(ValueError, match="regimes.buckets must be a list"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": "bull"},
                "cost_stress": {"scenarios": []},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_non_object_regime_bucket_row() -> None:
    with pytest.raises(ValueError, match=r"regimes\.buckets\[0\] must be an object"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": [("label", "low_vol_uptrend")]},
                "cost_stress": {"scenarios": []},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_invalid_regime_bucket_label() -> None:
    with pytest.raises(ValueError, match=r"regimes\.buckets\[0\]\.label must be a canonical string"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {"buckets": [{"label": " low_vol_uptrend "}]},
                "cost_stress": {"scenarios": []},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_regime_bucket_trade_count_mismatch() -> None:
    with pytest.raises(
        ValueError,
        match=r"regimes\.buckets\[0\]\.metrics\.trade_count must match regimes\.buckets\[0\]\.trade_ids length",
    ):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {
                    "buckets": [
                        {
                            "label": "low_vol_uptrend",
                            "trade_ids": ["BTCUSDT@2026-01-01T00:00:00+00:00"],
                            "metrics": {"trade_count": 2},
                        }
                    ]
                },
                "cost_stress": {"scenarios": []},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_duplicate_regime_bucket_trade_ids() -> None:
    with pytest.raises(
        ValueError,
        match=r"duplicate regimes\.buckets\[0\]\.trade_id: BTCUSDT@2026-01-01T00:00:00\+00:00",
    ):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": 1}},
                "regimes": {
                    "buckets": [
                        {
                            "label": "low_vol_uptrend",
                            "trade_ids": [
                                "BTCUSDT@2026-01-01T00:00:00+00:00",
                                "BTCUSDT@2026-01-01T00:00:00+00:00",
                            ],
                            "metrics": {"trade_count": 2},
                        }
                    ]
                },
                "cost_stress": {"scenarios": []},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_invalid_walk_forward_window_count() -> None:
    with pytest.raises(ValueError, match="walk_forward.metadata.window_count must be a non-negative integer"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": True}},
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": []},
            },
            metadata={"dataset_root": "dataset"},
        )


@pytest.mark.parametrize(
    "windows",
    (
        [{"window_index": 1}, {"window_index": 1}],
        [{"window_index": 2}, {"window_index": 1}],
    ),
)
def test_backtest_evaluation_report_rejects_non_increasing_walk_forward_window_indices(
    windows: list[dict[str, int]],
) -> None:
    with pytest.raises(ValueError, match="walk_forward.windows window_index values must be strictly increasing"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": {"window_count": len(windows)}, "windows": windows},
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": []},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_backtest_evaluation_report_rejects_list_pair_walk_forward_metadata() -> None:
    with pytest.raises(ValueError, match="walk_forward.metadata must be an object"):
        reporting.render_backtest_evaluation_report(
            experiment_name="evaluation",
            evaluation={
                "walk_forward": {"metadata": [("window_count", 1)]},
                "regimes": {"buckets": []},
                "cost_stress": {"scenarios": []},
            },
            metadata={"dataset_root": "dataset"},
        )


def _minimal_cli_config(tmp_path: Path, *, experiment_kind: str = "regime_research") -> BacktestConfig:
    return BacktestConfig(
        dataset_root=tmp_path / "dataset",
        experiment_kind=experiment_kind,
        sample_windows=(),
        forward_return_windows=(),
        costs=BacktestCosts(),
        baseline_name="baseline",
        variant_name="variant",
    )


def test_backtest_cli_manifest_rejects_non_string_metadata_keys(tmp_path: Path) -> None:
    config = _minimal_cli_config(tmp_path)

    with pytest.raises(ValueError, match="metadata key must be a string"):
        cli._manifest(config, [], {}, metadata={"generated_by": "test", 1: "bad"})


def test_backtest_cli_manifest_rejects_list_of_pairs_metadata(tmp_path: Path) -> None:
    config = _minimal_cli_config(tmp_path)

    with pytest.raises(ValueError, match="metadata must be an object"):
        cli._manifest(config, [], {}, metadata=[("generated_by", "test")])  # type: ignore[arg-type]


def test_backtest_cli_manifest_rejects_unsafe_artifact_filename(tmp_path: Path) -> None:
    config = _minimal_cli_config(tmp_path)

    with pytest.raises(ValueError, match="artifact filename must be a safe relative filename"):
        cli._manifest(config, [], {"../summary.json": {"summary": {}}})


def test_regime_research_outputs_rejects_list_of_pairs_experiment_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        cli,
        "run_regime_predictive_power_experiment",
        lambda rows: {"metadata": [("window_count", 1)], "buckets": []},
        raising=False,
    )

    with pytest.raises(ValueError, match="experiment.metadata must be an object"):
        cli._regime_research_outputs(_minimal_cli_config(tmp_path), _sample_dataset_rows())


def test_regime_research_outputs_rejects_non_string_experiment_metadata_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        cli,
        "run_regime_predictive_power_experiment",
        lambda rows: {"metadata": {1: "bad"}, "buckets": []},
        raising=False,
    )

    with pytest.raises(ValueError, match="experiment.metadata key must be a string"):
        cli._regime_research_outputs(_minimal_cli_config(tmp_path), _sample_dataset_rows())


@pytest.mark.parametrize("metadata", ["window_count=1", [("window_count", 1)]])
def test_walk_forward_outputs_rejects_non_object_experiment_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    metadata: object,
) -> None:
    monkeypatch.setattr(
        cli,
        "run_walk_forward_validation_experiment",
        lambda rows, *, evaluation_window, in_sample_size, out_of_sample_size, step_size, config=None: {
            "metadata": metadata,
            "windows": [],
            "robustness_summary": {
                "out_of_sample_scorecard": {"total_return": 0.0, "trade_count": 0},
                "performance_dispersion": {"positive_window_ratio": 0.0},
            },
            "parameter_stability": {"parameter_stability_score": 0.0},
        },
        raising=False,
    )
    config = _minimal_cli_config(tmp_path, experiment_kind="walk_forward_validation")
    config = replace(
        config,
        experiment_params=ExperimentParams(
            evaluation_window="3d",
            walk_forward=WalkForwardConfig(in_sample_size=1, out_of_sample_size=1, step_size=1),
        ),
    )

    with pytest.raises(ValueError, match="experiment.metadata must be an object"):
        cli._walk_forward_validation_outputs(config, _sample_dataset_rows())


@pytest.mark.parametrize("window_count", ["1", True, -1])
def test_walk_forward_outputs_rejects_invalid_experiment_window_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    window_count: object,
) -> None:
    monkeypatch.setattr(
        cli,
        "run_walk_forward_validation_experiment",
        lambda rows, *, evaluation_window, in_sample_size, out_of_sample_size, step_size, config=None: {
            "metadata": {"window_count": window_count},
            "windows": [],
            "robustness_summary": {
                "out_of_sample_scorecard": {"total_return": 0.0, "trade_count": 0},
                "performance_dispersion": {"positive_window_ratio": 0.0},
            },
            "parameter_stability": {"parameter_stability_score": 0.0},
        },
        raising=False,
    )
    config = _minimal_cli_config(tmp_path, experiment_kind="walk_forward_validation")
    config = replace(
        config,
        experiment_params=ExperimentParams(
            evaluation_window="3d",
            walk_forward=WalkForwardConfig(in_sample_size=1, out_of_sample_size=1, step_size=1),
        ),
    )

    with pytest.raises(ValueError, match="experiment.metadata.window_count must be a non-negative integer"):
        cli._walk_forward_validation_outputs(config, _sample_dataset_rows())


def test_regime_scorecard_rejects_non_object_metadata_payload() -> None:
    with pytest.raises(ValueError, match="experiment.metadata must be an object"):
        reporting.render_regime_scorecard(
            experiment_name="regime_dispersion",
            experiment={
                "metadata": [],
                "by_regime": {"bull": {"forward_return_by_window": {"3d": 0.01}}},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_regime_scorecard_rejects_non_object_report_metadata() -> None:
    with pytest.raises(ValueError, match="metadata must be an object"):
        reporting.render_regime_scorecard(
            experiment_name="regime_dispersion",
            experiment={
                "metadata": {"snapshot_count": 2},
                "by_regime": {"bull": {"forward_return_by_window": {"3d": 0.01}}},
            },
            metadata=[("dataset_root", "dataset")],  # type: ignore[arg-type]
        )


def test_regime_scorecard_rejects_padded_experiment_name() -> None:
    with pytest.raises(ValueError, match="experiment_name must be a canonical string"):
        reporting.render_regime_scorecard(
            experiment_name=" regime_dispersion ",
            experiment={
                "metadata": {"snapshot_count": 2},
                "by_regime": {"bull": {"forward_return_by_window": {"3d": 0.01}}},
            },
            metadata={"dataset_root": "dataset"},
        )


@pytest.mark.parametrize("field", ("dataset_root", "baseline_name", "variant_name"))
def test_regime_scorecard_rejects_padded_report_metadata_identifiers(field: str) -> None:
    with pytest.raises(ValueError, match=rf"metadata\.{field} must be a canonical string"):
        reporting.render_regime_scorecard(
            experiment_name="regime_dispersion",
            experiment={
                "metadata": {"snapshot_count": 2},
                "by_regime": {"bull": {"forward_return_by_window": {"3d": 0.01}}},
            },
            metadata={field: " padded "},
        )


def test_regime_scorecard_rejects_malformed_sample_period_shape() -> None:
    with pytest.raises(ValueError, match=r"metadata\.sample_period\.end must be a canonical string"):
        reporting.render_regime_scorecard(
            experiment_name="regime_dispersion",
            experiment={
                "metadata": {"snapshot_count": 2},
                "by_regime": {"bull": {"forward_return_by_window": {"3d": 0.01}}},
            },
            metadata={
                "dataset_root": "dataset",
                "sample_period": {"start": "2026-03-10T00:00:00Z", "end": " 2026-03-11T00:00:00Z "},
            },
        )


def test_regime_scorecard_rejects_non_object_forward_return_window() -> None:
    with pytest.raises(ValueError, match="by_regime.bull.forward_return_by_window must be an object"):
        reporting.render_regime_scorecard(
            experiment_name="regime_dispersion",
            experiment={
                "metadata": {"snapshot_count": 2},
                "by_regime": {"bull": {"forward_return_by_window": []}},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_regime_scorecard_rejects_non_object_regime_payload() -> None:
    with pytest.raises(ValueError, match="by_regime.bull must be an object"):
        reporting.render_regime_scorecard(
            experiment_name="regime_dispersion",
            experiment={"metadata": {"snapshot_count": 2}, "by_regime": {"bull": []}},
            metadata={"dataset_root": "dataset"},
        )


def test_regime_scorecard_rejects_non_object_by_regime() -> None:
    with pytest.raises(ValueError, match="by_regime must be an object"):
        reporting.render_regime_scorecard(
            experiment_name="regime_dispersion",
            experiment={"metadata": {"snapshot_count": 2}, "by_regime": []},
            metadata={"dataset_root": "dataset"},
        )


def test_regime_scorecard_rejects_non_object_duration_stats() -> None:
    with pytest.raises(ValueError, match="duration_stats must be an object"):
        reporting.render_regime_scorecard(
            experiment_name="regime_dispersion",
            experiment={
                "metadata": {"snapshot_count": 2},
                "by_regime": {
                    "bull": {"forward_return_by_window": {"3d": 0.01}},
                    "bear": {"forward_return_by_window": {"3d": -0.01}},
                },
                "duration_stats": [],
            },
            metadata={"dataset_root": "dataset"},
        )


def test_regime_scorecard_rejects_non_string_regime_label() -> None:
    with pytest.raises(ValueError, match="by_regime keys must be a canonical string"):
        reporting.render_regime_scorecard(
            experiment_name="regime_dispersion",
            experiment={
                "metadata": {"snapshot_count": 2},
                "by_regime": {1: {"forward_return_by_window": {"3d": 0.01}}},
            },
            metadata={"dataset_root": "dataset"},
        )


def test_regime_scorecard_rejects_duplicate_regime_labels() -> None:
    with pytest.raises(ValueError, match="by_regime keys must be unique"):
        reporting.render_regime_scorecard(
            experiment_name="regime_dispersion",
            experiment={
                "metadata": {"snapshot_count": 2},
                "by_regime": _DuplicateKeyMapping(
                    [
                        ("bull", {"forward_return_by_window": {"3d": 0.01}}),
                        ("bull", {"forward_return_by_window": {"3d": -0.01}}),
                    ]
                ),
            },
            metadata={"dataset_root": "dataset"},
        )


def test_regime_scorecard_rejects_invalid_forward_return_metric() -> None:
    with pytest.raises(ValueError, match="by_regime.bull.forward_return_by_window.3d must be a finite number"):
        reporting.render_regime_scorecard(
            experiment_name="regime_dispersion",
            experiment={
                "metadata": {"snapshot_count": 2},
                "by_regime": {
                    "bull": {"forward_return_by_window": {"3d": True}},
                    "bear": {"forward_return_by_window": {"3d": -0.01}},
                },
            },
            metadata={"dataset_root": "dataset"},
        )


def test_regime_scorecard_rejects_string_forward_return_metric() -> None:
    with pytest.raises(ValueError, match="by_regime.bull.forward_return_by_window.3d must be a finite number"):
        reporting.render_regime_scorecard(
            experiment_name="regime_dispersion",
            experiment={
                "metadata": {"snapshot_count": 2},
                "by_regime": {
                    "bull": {"forward_return_by_window": {"3d": "0.01"}},
                    "bear": {"forward_return_by_window": {"3d": -0.01}},
                },
            },
            metadata={"dataset_root": "dataset"},
        )


def test_regime_scorecard_rejects_invalid_non_primary_forward_return_metric() -> None:
    with pytest.raises(ValueError, match="by_regime.bull.forward_return_by_window.7d must be a finite number"):
        reporting.render_regime_scorecard(
            experiment_name="regime_dispersion",
            experiment={
                "metadata": {"snapshot_count": 2},
                "by_regime": {
                    "bull": {"forward_return_by_window": {"3d": 0.01, "7d": "0.02"}},
                    "bear": {"forward_return_by_window": {"3d": -0.01}},
                },
            },
            metadata={"dataset_root": "dataset"},
        )


@pytest.mark.parametrize("max_duration_bars", [True, "2", 2.0, float("nan"), float("inf"), -1])
def test_regime_scorecard_rejects_malformed_present_max_duration_bars(max_duration_bars: object) -> None:
    with pytest.raises(
        ValueError,
        match=r"duration_stats\.bull\.max_duration_bars must be a non-negative integer",
    ):
        reporting.render_regime_scorecard(
            experiment_name="regime_dispersion",
            experiment={
                "metadata": {"snapshot_count": 2},
                "by_regime": {
                    "bull": {"forward_return_by_window": {"3d": 0.01}},
                    "bear": {"forward_return_by_window": {"3d": -0.01}},
                },
                "duration_stats": {"bull": {"max_duration_bars": max_duration_bars}},
            },
            metadata={"dataset_root": "dataset"},
        )


def sample_baseline_result() -> BaselineReplayResult:
    return BaselineReplayResult(
        portfolio_summary=PortfolioScorecardRow(
            experiment_name="current_system__auditable_baseline",
            total_return=0.12,
            max_drawdown=-0.08,
            sharpe=1.4,
            sortino=1.8,
            calmar=1.5,
            turnover=0.42,
            trade_count=4,
        ),
        trade_ledger=(
            TradeLedgerRow(
                symbol="BTCUSDT",
                market_type="spot",
                base_asset="BTC",
                side="long",
                status="accepted",
                entry_timestamp=_ts("2026-03-10T00:00:00Z"),
                exit_timestamp=_ts("2026-03-11T00:00:00Z"),
                entry_price=100.0,
                exit_price=110.0,
                qty=10.0,
                position_notional=1_000.0,
                holding_hours=24.0,
                gross_pnl=100.0,
                net_pnl=90.0,
                gross_return_pct=0.10,
                net_return_pct=0.09,
                fee_paid=1.0,
                slippage_paid=4.0,
                funding_paid=0.0,
                entry_reference_timeframe="15m",
                entry_reference_price=100.0,
                gate_timeframes=("daily", "4h", "1h"),
                trigger_timeframes=("30m", "15m"),
                execution_price_source="ohlcv_next_open",
                fill_model="next_bar_ohlcv",
                fill_quality="evidence_backed",
                execution_timeframe="1m",
                execution_lag_bars=1,
                maker_status="filled",
                queue_ahead_initial=2.0,
                queue_ahead_remaining=0.0,
                maker_wait_seconds=1.0,
                maker_reasons=("queue_depleted",),
                requested_quantity=10.0,
                filled_quantity=10.0,
                filled_notional=1_000.0,
                unfilled_quantity=0.0,
                depth_levels_consumed=2,
                execution_impact_bps=4.5,
                slippage_bps=10.0,
                mark_price=100.25,
                mark_price_timestamp=_ts("2026-03-10T00:00:00Z"),
                mark_price_age_seconds=0,
                funding_rate=0.0004,
                funding_timestamp=_ts("2026-03-10T00:00:00Z"),
                funding_age_seconds=0,
                open_interest_usdt=120_000_000.0,
                open_interest_timestamp=_ts("2026-03-10T00:00:00Z"),
                open_interest_age_seconds=0,
            ),
            TradeLedgerRow(
                symbol="BTCUSDTPERP",
                market_type="futures",
                base_asset="BTC",
                side="long",
                status="resized",
                entry_timestamp=_ts("2026-03-10T00:00:00Z"),
                exit_timestamp=_ts("2026-03-11T00:00:00Z"),
                entry_price=100.0,
                exit_price=108.0,
                qty=5.0,
                position_notional=500.0,
                holding_hours=24.0,
                gross_pnl=40.0,
                net_pnl=33.0,
                gross_return_pct=0.08,
                net_return_pct=0.066,
                fee_paid=0.5,
                slippage_paid=2.0,
                funding_paid=4.5,
            ),
            TradeLedgerRow(
                symbol="ETHUSDT",
                market_type="spot",
                base_asset="ETH",
                side="long",
                status="accepted",
                entry_timestamp=_ts("2027-01-10T00:00:00Z"),
                exit_timestamp=_ts("2027-01-11T00:00:00Z"),
                entry_price=1_000.0,
                exit_price=1_050.0,
                qty=1.0,
                position_notional=1_000.0,
                holding_hours=24.0,
                gross_pnl=50.0,
                net_pnl=45.0,
                gross_return_pct=0.05,
                net_return_pct=0.045,
                fee_paid=1.0,
                slippage_paid=4.0,
                funding_paid=0.0,
            ),
            TradeLedgerRow(
                symbol="SOLUSDTPERP",
                market_type="futures",
                base_asset="SOL",
                side="short",
                status="accepted",
                entry_timestamp=_ts("2027-01-10T00:00:00Z"),
                exit_timestamp=_ts("2027-01-11T00:00:00Z"),
                entry_price=50.0,
                exit_price=45.0,
                qty=20.0,
                position_notional=1_000.0,
                holding_hours=24.0,
                gross_pnl=100.0,
                net_pnl=88.0,
                gross_return_pct=0.10,
                net_return_pct=0.088,
                fee_paid=0.5,
                slippage_paid=3.0,
                funding_paid=8.5,
            ),
        ),
        rejection_ledger=(
            PortfolioDecisionLedgerRow(
                symbol="BTCUSDTPERP",
                market_type="futures",
                base_asset="BTC",
                status="rejected",
                reasons=("base_asset_same_direction_crowding",),
                final_risk_budget=0.0,
                position_notional=0.0,
                qty=0.0,
            ),
            PortfolioDecisionLedgerRow(
                symbol="DOGEUSDT",
                market_type="spot",
                base_asset="DOGE",
                status="rejected",
                reasons=("open_risk_limit_reached",),
                final_risk_budget=0.0,
                position_notional=0.0,
                qty=0.0,
            ),
            PortfolioDecisionLedgerRow(
                symbol="ADAUSDTPERP",
                market_type="futures",
                base_asset="ADA",
                status="rejected",
                reasons=("capital_usage_limit_reached",),
                final_risk_budget=0.0,
                position_notional=0.0,
                qty=0.0,
            ),
        ),
        cost_breakdown={"fees": 3.0, "slippage": 13.0, "funding": 13.0},
        gross_period_returns=(0.08, 0.04),
        net_period_returns=(0.07, 0.04672897196261682),
    )


def test_full_market_trade_postmortem_exposes_execution_source_and_quality() -> None:
    markdown = cli._render_trade_postmortem_markdown(
        [
            {
                "entry_timestamp": "2026-03-10T00:00:00+00:00",
                "symbol": "BTCUSDT",
                "side": "long",
                "engine": "trend",
                "setup_type": "TREND_PULLBACK",
                "score": 0.95,
                "entry_price": 100.1,
                "exit_price": 110.0,
                "gross_pnl": 99.0,
                "net_pnl": 95.0,
                "mfe_pct": 0.12,
                "mae_pct": 0.01,
                "exit_reason": "fixed_horizon",
                "fill_model": "taker_orderbook",
                "execution_price_source": "best_ask",
                "execution_timeframe": "",
                "execution_lag_bars": 0,
                "fill_quality": "evidence_backed",
                "maker_status": "filled",
                "queue_ahead_initial": 2.0,
                "queue_ahead_remaining": 0.0,
                "maker_wait_seconds": 1.0,
                "maker_reasons": ["queue_depleted"],
                "filled_quantity": 10.0,
                "unfilled_quantity": 0.0,
                "depth_levels_consumed": 2,
                "execution_impact_bps": 4.5,
            }
        ]
    )

    assert "exec_source" in markdown
    assert "best_ask" in markdown
    assert "taker_orderbook" in markdown
    assert "evidence_backed" in markdown
    assert "filled_qty" in markdown
    assert "unfilled_qty" in markdown
    assert "maker_status" in markdown
    assert "maker_wait" in markdown
    assert "depth_levels" in markdown
    assert "impact_bps" in markdown


def test_full_market_trade_postmortem_rejects_numeric_string_score() -> None:
    with pytest.raises(ValueError, match=r"trades\[0\]\.score must be a finite number"):
        cli._render_trade_postmortem_markdown(
            [
                {
                    "entry_timestamp": "2026-03-10T00:00:00+00:00",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "engine": "trend",
                    "setup_type": "TREND_PULLBACK",
                    "score": "0.95",
                    "entry_price": 100.1,
                    "exit_price": 110.0,
                    "gross_pnl": 99.0,
                    "net_pnl": 95.0,
                    "mfe_pct": 0.12,
                    "mae_pct": 0.01,
                    "exit_reason": "fixed_horizon",
                    "fill_model": "taker_orderbook",
                    "execution_price_source": "best_ask",
                    "execution_lag_bars": 0,
                    "fill_quality": "evidence_backed",
                }
            ]
        )


@pytest.mark.parametrize("reduce_only", ["false", 0, 1])
def test_full_market_trade_postmortem_rejects_malformed_present_reduce_only(reduce_only: object) -> None:
    with pytest.raises(ValueError, match=r"trades\[0\]\.reduce_only must be a strict boolean"):
        cli._render_trade_postmortem_markdown(
            [
                {
                    "entry_timestamp": "2026-03-10T00:00:00+00:00",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "engine": "trend",
                    "setup_type": "TREND_PULLBACK",
                    "score": 0.95,
                    "entry_price": 100.1,
                    "exit_price": 110.0,
                    "gross_pnl": 99.0,
                    "net_pnl": 95.0,
                    "mfe_pct": 0.12,
                    "mae_pct": 0.01,
                    "exit_reason": "fixed_horizon",
                    "fill_model": "taker_orderbook",
                    "execution_price_source": "best_ask",
                    "execution_lag_bars": 0,
                    "fill_quality": "evidence_backed",
                    "reduce_only": reduce_only,
                }
            ]
        )


@pytest.mark.parametrize("post_only", ["false", 0, 1, object()])
def test_full_market_trade_postmortem_rejects_malformed_present_post_only(post_only: object) -> None:
    with pytest.raises(ValueError, match=r"trades\[0\]\.post_only must be a strict boolean"):
        cli._render_trade_postmortem_markdown(
            [
                {
                    "entry_timestamp": "2026-03-10T00:00:00+00:00",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "engine": "trend",
                    "setup_type": "TREND_PULLBACK",
                    "score": 0.95,
                    "entry_price": 100.1,
                    "exit_price": 110.0,
                    "gross_pnl": 99.0,
                    "net_pnl": 95.0,
                    "mfe_pct": 0.12,
                    "mae_pct": 0.01,
                    "exit_reason": "fixed_horizon",
                    "fill_model": "maker_post_only_queue",
                    "execution_price_source": "no_crossing_evidence",
                    "execution_lag_bars": 0,
                    "fill_quality": "evidence_backed",
                    "post_only": post_only,
                }
            ]
        )


def test_full_market_trade_postmortem_rejects_numeric_string_entry_price() -> None:
    with pytest.raises(ValueError, match=r"trades\[0\]\.entry_price must be a finite number"):
        cli._render_trade_postmortem_markdown(
            [
                {
                    "entry_timestamp": "2026-03-10T00:00:00+00:00",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "engine": "trend",
                    "setup_type": "TREND_PULLBACK",
                    "score": 0.95,
                    "entry_price": "100.1",
                    "exit_price": 110.0,
                    "gross_pnl": 99.0,
                    "net_pnl": 95.0,
                    "mfe_pct": 0.12,
                    "mae_pct": 0.01,
                    "exit_reason": "fixed_horizon",
                    "fill_model": "taker_orderbook",
                    "execution_price_source": "best_ask",
                    "execution_lag_bars": 0,
                    "fill_quality": "evidence_backed",
                }
            ]
        )


@pytest.mark.parametrize("exit_price", ["110.0", True])
def test_full_market_trade_postmortem_rejects_coerced_exit_price(exit_price: object) -> None:
    with pytest.raises(ValueError, match=r"trades\[0\]\.exit_price must be a finite number"):
        cli._render_trade_postmortem_markdown(
            [
                {
                    "entry_timestamp": "2026-03-10T00:00:00+00:00",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "engine": "trend",
                    "setup_type": "TREND_PULLBACK",
                    "score": 0.95,
                    "entry_price": 100.1,
                    "exit_price": exit_price,
                    "gross_pnl": 99.0,
                    "net_pnl": 95.0,
                    "mfe_pct": 0.12,
                    "mae_pct": 0.01,
                    "exit_reason": "fixed_horizon",
                    "fill_model": "taker_orderbook",
                    "execution_price_source": "best_ask",
                    "execution_lag_bars": 0,
                    "fill_quality": "evidence_backed",
                }
            ]
        )


@pytest.mark.parametrize("gross_pnl", ["99.0", True])
def test_full_market_trade_postmortem_rejects_coerced_gross_pnl(gross_pnl: object) -> None:
    with pytest.raises(ValueError, match=r"trades\[0\]\.gross_pnl must be a finite number"):
        cli._render_trade_postmortem_markdown(
            [
                {
                    "entry_timestamp": "2026-03-10T00:00:00+00:00",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "engine": "trend",
                    "setup_type": "TREND_PULLBACK",
                    "score": 0.95,
                    "entry_price": 100.1,
                    "exit_price": 110.0,
                    "gross_pnl": gross_pnl,
                    "net_pnl": 95.0,
                    "mfe_pct": 0.12,
                    "mae_pct": 0.01,
                    "exit_reason": "fixed_horizon",
                    "fill_model": "taker_orderbook",
                    "execution_price_source": "best_ask",
                    "execution_lag_bars": 0,
                    "fill_quality": "evidence_backed",
                }
            ]
        )


@pytest.mark.parametrize("net_pnl", ["95.0", True])
def test_full_market_trade_postmortem_rejects_coerced_net_pnl(net_pnl: object) -> None:
    with pytest.raises(ValueError, match=r"trades\[0\]\.net_pnl must be a finite number"):
        cli._render_trade_postmortem_markdown(
            [
                {
                    "entry_timestamp": "2026-03-10T00:00:00+00:00",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "engine": "trend",
                    "setup_type": "TREND_PULLBACK",
                    "score": 0.95,
                    "entry_price": 100.1,
                    "exit_price": 110.0,
                    "gross_pnl": 99.0,
                    "net_pnl": net_pnl,
                    "mfe_pct": 0.12,
                    "mae_pct": 0.01,
                    "exit_reason": "fixed_horizon",
                    "fill_model": "taker_orderbook",
                    "execution_price_source": "best_ask",
                    "execution_lag_bars": 0,
                    "fill_quality": "evidence_backed",
                }
            ]
        )


@pytest.mark.parametrize("mfe_pct", ["0.12", True])
def test_full_market_trade_postmortem_rejects_coerced_mfe_pct(mfe_pct: object) -> None:
    with pytest.raises(ValueError, match=r"trades\[0\]\.mfe_pct must be a finite number"):
        cli._render_trade_postmortem_markdown(
            [
                {
                    "entry_timestamp": "2026-03-10T00:00:00+00:00",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "engine": "trend",
                    "setup_type": "TREND_PULLBACK",
                    "score": 0.95,
                    "entry_price": 100.1,
                    "exit_price": 110.0,
                    "gross_pnl": 99.0,
                    "net_pnl": 95.0,
                    "mfe_pct": mfe_pct,
                    "mae_pct": 0.01,
                    "exit_reason": "fixed_horizon",
                    "fill_model": "taker_orderbook",
                    "execution_price_source": "best_ask",
                    "execution_lag_bars": 0,
                    "fill_quality": "evidence_backed",
                }
            ]
        )


@pytest.mark.parametrize("mae_pct", ["0.01", True])
def test_full_market_trade_postmortem_rejects_coerced_mae_pct(mae_pct: object) -> None:
    with pytest.raises(ValueError, match=r"trades\[0\]\.mae_pct must be a finite number"):
        cli._render_trade_postmortem_markdown(
            [
                {
                    "entry_timestamp": "2026-03-10T00:00:00+00:00",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "engine": "trend",
                    "setup_type": "TREND_PULLBACK",
                    "score": 0.95,
                    "entry_price": 100.1,
                    "exit_price": 110.0,
                    "gross_pnl": 99.0,
                    "net_pnl": 95.0,
                    "mfe_pct": 0.12,
                    "mae_pct": mae_pct,
                    "exit_reason": "fixed_horizon",
                    "fill_model": "taker_orderbook",
                    "execution_price_source": "best_ask",
                    "execution_lag_bars": 0,
                    "fill_quality": "evidence_backed",
                }
            ]
        )


def test_full_market_report_rejects_invalid_cost_breakdown_fields() -> None:
    result = sample_baseline_result()
    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=result.trade_ledger,
        rejection_ledger=result.rejection_ledger,
        cost_breakdown=[("fees", 1.0)],  # type: ignore[arg-type]
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match="cost_breakdown must be an object"):
        reporting.render_full_market_baseline_report(bad_result)

    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=result.trade_ledger,
        rejection_ledger=result.rejection_ledger,
        cost_breakdown={True: 1.0},  # type: ignore[dict-item]
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match="cost_breakdown key must be a canonical string"):
        reporting.render_full_market_baseline_report(bad_result)

    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=result.trade_ledger,
        rejection_ledger=result.rejection_ledger,
        cost_breakdown={"fees": True},  # type: ignore[dict-item]
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match="cost_breakdown.fees must be a finite number"):
        reporting.render_full_market_baseline_report(bad_result)


def test_full_market_report_rejects_string_cost_breakdown_value() -> None:
    result = sample_baseline_result()
    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=result.trade_ledger,
        rejection_ledger=result.rejection_ledger,
        cost_breakdown={"fees": "3.0"},  # type: ignore[dict-item]
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match="cost_breakdown.fees must be a finite number"):
        reporting.render_full_market_baseline_report(bad_result)


def test_full_market_report_rejects_string_cost_drag_return_metric() -> None:
    result = sample_baseline_result()
    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=result.trade_ledger,
        rejection_ledger=result.rejection_ledger,
        cost_breakdown=result.cost_breakdown,
        gross_period_returns=("0.08", result.gross_period_returns[1]),  # type: ignore[arg-type]
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match=r"gross_period_returns\[0\] must be a finite number"):
        reporting.render_full_market_baseline_report(bad_result)


def test_full_market_report_exposes_canonical_trade_ledger_payload() -> None:
    report = reporting.render_full_market_baseline_report(sample_baseline_result())

    assert {
        key: report["trades"][0][key]
        for key in (
            "symbol",
            "market_type",
            "base_asset",
            "side",
            "status",
            "engine",
            "setup_type",
            "entry_timestamp",
            "exit_timestamp",
        )
    } == {
        "symbol": "BTCUSDT",
        "market_type": "spot",
        "base_asset": "BTC",
        "side": "long",
        "status": "accepted",
        "engine": "",
        "setup_type": "",
        "entry_timestamp": "2026-03-10T00:00:00+00:00",
        "exit_timestamp": "2026-03-11T00:00:00+00:00",
    }


@pytest.mark.parametrize("symbol", [True, "", " BTCUSDT ", _StringSubclass("BTCUSDT")])
def test_full_market_report_rejects_noncanonical_trade_symbol(symbol: object) -> None:
    result = sample_baseline_result()
    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=(replace(result.trade_ledger[0], symbol=symbol),),  # type: ignore[arg-type]
        rejection_ledger=result.rejection_ledger,
        cost_breakdown=result.cost_breakdown,
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match=r"trades\[0\]\.symbol must be a canonical string"):
        reporting.render_full_market_baseline_report(bad_result)


@pytest.mark.parametrize("market_type", [True, "", " spot ", _StringSubclass("spot")])
def test_full_market_report_rejects_noncanonical_trade_market_type(market_type: object) -> None:
    result = sample_baseline_result()
    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=(replace(result.trade_ledger[0], market_type=market_type),),  # type: ignore[arg-type]
        rejection_ledger=result.rejection_ledger,
        cost_breakdown=result.cost_breakdown,
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match=r"trades\[0\]\.market_type must be a canonical string"):
        reporting.render_full_market_baseline_report(bad_result)


@pytest.mark.parametrize("base_asset", [True, "", " BTC ", _StringSubclass("BTC")])
def test_full_market_report_rejects_noncanonical_trade_base_asset(base_asset: object) -> None:
    result = sample_baseline_result()
    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=(replace(result.trade_ledger[0], base_asset=base_asset),),  # type: ignore[arg-type]
        rejection_ledger=result.rejection_ledger,
        cost_breakdown=result.cost_breakdown,
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match=r"trades\[0\]\.base_asset must be a canonical string"):
        reporting.render_full_market_baseline_report(bad_result)


@pytest.mark.parametrize("side", [True, "", " long ", _StringSubclass("long")])
def test_full_market_report_rejects_noncanonical_trade_side(side: object) -> None:
    result = sample_baseline_result()
    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=(replace(result.trade_ledger[0], side=side),),  # type: ignore[arg-type]
        rejection_ledger=result.rejection_ledger,
        cost_breakdown=result.cost_breakdown,
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match=r"trades\[0\]\.side must be a canonical string"):
        reporting.render_full_market_baseline_report(bad_result)


@pytest.mark.parametrize("status", [True, "", " accepted ", _StringSubclass("accepted")])
def test_full_market_report_rejects_noncanonical_trade_status(status: object) -> None:
    result = sample_baseline_result()
    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=(replace(result.trade_ledger[0], status=status),),  # type: ignore[arg-type]
        rejection_ledger=result.rejection_ledger,
        cost_breakdown=result.cost_breakdown,
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match=r"trades\[0\]\.status must be a canonical string"):
        reporting.render_full_market_baseline_report(bad_result)


@pytest.mark.parametrize("engine", [True, " trend ", _StringSubclass("trend")])
def test_full_market_report_rejects_noncanonical_trade_engine(engine: object) -> None:
    result = sample_baseline_result()
    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=(replace(result.trade_ledger[0], engine=engine),),  # type: ignore[arg-type]
        rejection_ledger=result.rejection_ledger,
        cost_breakdown=result.cost_breakdown,
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match=r"trades\[0\]\.engine must be a canonical string"):
        reporting.render_full_market_baseline_report(bad_result)


@pytest.mark.parametrize("setup_type", [" TREND_PULLBACK ", True, _StringSubclass("TREND_PULLBACK")])
def test_full_market_report_rejects_invalid_trade_setup_type(setup_type: object) -> None:
    result = sample_baseline_result()
    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=(replace(result.trade_ledger[0], setup_type=setup_type),),  # type: ignore[arg-type]
        rejection_ledger=result.rejection_ledger,
        cost_breakdown=result.cost_breakdown,
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match=r"trades\[0\]\.setup_type must be a canonical string"):
        reporting.render_full_market_baseline_report(bad_result)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("entry_price", True),
        ("entry_price", "100.0"),
        ("exit_price", float("nan")),
        ("qty", True),
        ("position_notional", float("inf")),
        ("gross_pnl", True),
        ("net_pnl", float("-inf")),
        ("fee_paid", True),
        ("slippage_paid", "4.0"),
        ("funding_paid", float("nan")),
    ],
)
def test_full_market_report_rejects_nonfinite_or_coerced_trade_evidence_numbers(
    field: str,
    value: object,
) -> None:
    result = sample_baseline_result()
    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=(replace(result.trade_ledger[0], **{field: value}),),  # type: ignore[arg-type]
        rejection_ledger=result.rejection_ledger,
        cost_breakdown=result.cost_breakdown,
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match=rf"trades\[0\]\.{field} must be a finite number"):
        reporting.render_full_market_baseline_report(bad_result)


def test_full_market_report_breakdowns_aggregate_from_trade_ledger_with_deterministic_keys() -> None:
    result = sample_baseline_result()
    first = replace(result.trade_ledger[0], engine="trend", setup_type="TREND_PULLBACK")
    second = replace(
        result.trade_ledger[1],
        market_type="spot",
        engine="mean_reversion",
        setup_type="RANGE_REVERSION",
        gross_pnl=7.0,
        net_pnl=3.0,
        fee_paid=1.25,
        slippage_paid=0.5,
        funding_paid=0.75,
    )
    third = replace(
        result.trade_ledger[2],
        market_type="futures",
        engine="trend",
        setup_type="TREND_PULLBACK",
        gross_pnl=-2.0,
        net_pnl=-5.0,
        fee_paid=0.25,
        slippage_paid=0.75,
        funding_paid=2.0,
    )
    focused_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=(first, second, third),
        rejection_ledger=result.rejection_ledger,
        cost_breakdown=result.cost_breakdown,
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    report = reporting.render_full_market_baseline_report(focused_result)

    assert report["breakdowns"]["by_market"] == [
        {
            "market_type": "futures",
            "trade_count": 1,
            "gross_pnl": -2.0,
            "net_pnl": -5.0,
            "fees": 0.25,
            "slippage": 0.75,
            "funding": 2.0,
        },
        {
            "market_type": "spot",
            "trade_count": 2,
            "gross_pnl": 107.0,
            "net_pnl": 93.0,
            "fees": 2.25,
            "slippage": 4.5,
            "funding": 0.75,
        },
    ]
    assert report["breakdowns"]["by_symbol"] == [
        {
            "symbol": "BTCUSDT",
            "trade_count": 1,
            "gross_pnl": 100.0,
            "net_pnl": 90.0,
            "fees": 1.0,
            "slippage": 4.0,
            "funding": 0.0,
        },
        {
            "symbol": "BTCUSDTPERP",
            "trade_count": 1,
            "gross_pnl": 7.0,
            "net_pnl": 3.0,
            "fees": 1.25,
            "slippage": 0.5,
            "funding": 0.75,
        },
        {
            "symbol": "ETHUSDT",
            "trade_count": 1,
            "gross_pnl": -2.0,
            "net_pnl": -5.0,
            "fees": 0.25,
            "slippage": 0.75,
            "funding": 2.0,
        },
    ]
    assert report["breakdowns"]["by_setup_type"] == [
        {
            "setup_type": "RANGE_REVERSION",
            "trade_count": 1,
            "gross_pnl": 7.0,
            "net_pnl": 3.0,
            "fees": 1.25,
            "slippage": 0.5,
            "funding": 0.75,
        },
        {
            "setup_type": "TREND_PULLBACK",
            "trade_count": 2,
            "gross_pnl": 98.0,
            "net_pnl": 85.0,
            "fees": 1.25,
            "slippage": 4.75,
            "funding": 2.0,
        },
    ]


def test_full_market_report_rejects_non_string_rejection_reason() -> None:
    result = sample_baseline_result()
    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=result.trade_ledger,
        rejection_ledger=(replace(result.rejection_ledger[0], reasons=(True,)),),  # type: ignore[arg-type]
        cost_breakdown=result.cost_breakdown,
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match=r"rejections\[0\]\.reasons\[0\] must be a canonical string"):
        reporting.render_full_market_baseline_report(bad_result)


def test_full_market_report_rejects_list_rejection_reasons_container() -> None:
    result = sample_baseline_result()
    bad_result = BaselineReplayResult(
        portfolio_summary=result.portfolio_summary,
        trade_ledger=result.trade_ledger,
        rejection_ledger=(replace(result.rejection_ledger[0], reasons=["open_risk_limit_reached"]),),  # type: ignore[arg-type]
        cost_breakdown=result.cost_breakdown,
        gross_period_returns=result.gross_period_returns,
        net_period_returns=result.net_period_returns,
    )

    with pytest.raises(ValueError, match=r"rejections\[0\]\.reasons must be a tuple"):
        reporting.render_full_market_baseline_report(bad_result)


def test_full_market_report_exposes_depth_and_maker_fill_fields() -> None:
    report = reporting.render_full_market_baseline_report(sample_baseline_result())

    trade = report["trades"][0]
    assert trade["requested_quantity"] == pytest.approx(10.0)
    assert trade["filled_quantity"] == pytest.approx(10.0)
    assert trade["filled_notional"] == pytest.approx(1_000.0)
    assert trade["unfilled_quantity"] == pytest.approx(0.0)
    assert trade["depth_levels_consumed"] == 2
    assert trade["execution_impact_bps"] == pytest.approx(4.5)
    assert trade["slippage_bps"] == pytest.approx(10.0)
    assert trade["maker_status"] == "filled"
    assert trade["queue_ahead_initial"] == pytest.approx(2.0)
    assert trade["queue_ahead_remaining"] == pytest.approx(0.0)
    assert trade["maker_wait_seconds"] == pytest.approx(1.0)
    assert trade["maker_reasons"] == ["queue_depleted"]


@pytest.mark.parametrize("slippage_bps", [True, "10.0", float("nan"), float("inf"), -1.0])
def test_full_market_report_rejects_malformed_present_slippage_bps(slippage_bps: object) -> None:
    result = sample_baseline_result()
    bad_result = replace(
        result,
        trade_ledger=(replace(result.trade_ledger[0], slippage_bps=slippage_bps),),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match=r"trades\[0\]\.slippage_bps must be a non-negative finite number"):
        reporting.render_full_market_baseline_report(bad_result)


@pytest.mark.parametrize("maker_status", [123, "", " filled ", "unknown_status"])
def test_full_market_report_rejects_malformed_present_maker_status(maker_status: object) -> None:
    result = sample_baseline_result()
    bad_result = replace(
        result,
        trade_ledger=(
            replace(result.trade_ledger[0], maker_status=maker_status),
        ),
    )

    with pytest.raises(ValueError, match=r"trades\[0\]\.maker_status must be a supported canonical string"):
        reporting.render_full_market_baseline_report(bad_result)


def test_full_market_report_and_postmortem_expose_futures_context_fields() -> None:
    report = reporting.render_full_market_baseline_report(sample_baseline_result())

    trade = report["trades"][0]
    assert trade["mark_price"] == pytest.approx(100.25)
    assert trade["mark_price_timestamp"] == "2026-03-10T00:00:00+00:00"
    assert trade["mark_price_age_seconds"] == 0
    assert trade["funding_rate"] == pytest.approx(0.0004)
    assert trade["funding_timestamp"] == "2026-03-10T00:00:00+00:00"
    assert trade["funding_age_seconds"] == 0
    assert trade["open_interest_usdt"] == pytest.approx(120_000_000.0)
    assert trade["open_interest_timestamp"] == "2026-03-10T00:00:00+00:00"
    assert trade["open_interest_age_seconds"] == 0

    markdown = cli._render_trade_postmortem_markdown([trade])
    assert "mark_price" in markdown
    assert "funding_rate" in markdown
    assert "open_interest" in markdown


def test_render_backtest_evaluation_report_labels_is_oos_regime_and_cost_stress() -> None:
    rows = [
        DatasetSnapshotRow(
            timestamp=_ts("2026-03-10T00:00:00Z"),
            run_id="row-001",
            market={"symbols": {"BTCUSDT": {"daily": {"close": 110.0, "ema_50": 100.0, "return_pct_7d": 0.06, "atr_pct": 0.02}}}},
            derivatives=[],
        ),
        DatasetSnapshotRow(
            timestamp=_ts("2026-03-11T00:00:00Z"),
            run_id="row-002",
            market={"symbols": {"BTCUSDT": {"daily": {"close": 108.0, "ema_50": 100.0, "return_pct_7d": 0.05, "atr_pct": 0.02}}}},
            derivatives=[],
        ),
        DatasetSnapshotRow(
            timestamp=_ts("2026-03-12T00:00:00Z"),
            run_id="row-003",
            market={"symbols": {"BTCUSDT": {"daily": {"close": 90.0, "ema_50": 100.0, "return_pct_7d": -0.06, "atr_pct": 0.07}}}},
            derivatives=[],
        ),
    ]
    trade = sample_baseline_result().trade_ledger[0]
    evaluation = build_evaluation_report(
        rows=rows,
        trade_ledger=(trade,),
        train_size=2,
        test_size=1,
        cost_scenarios=(CostStressScenario(name="fees_2x", fee_multiplier=2.0),),
    )

    report = reporting.render_backtest_evaluation_report(
        experiment_name="full_market_baseline",
        evaluation=evaluation,
        metadata={"baseline_name": "current_system", "variant_name": "auditable_baseline"},
    )

    assert report["summary"]["metadata"]["evaluation_layer"] == "walk_forward_oos_regime_cost_stress"
    assert report["walk_forward"]["windows"][0]["splits"]["in_sample"]["label"] == "IS"
    assert report["walk_forward"]["windows"][0]["splits"]["out_of_sample"]["label"] == "OOS"
    assert report["regimes"]["buckets"]
    assert report["cost_stress"]["scenarios"][0]["label"] == "cost_stress:fees_2x"
    assert report["summary"]["cost_stress_scenarios"] == ["fees_2x"]


def _write_fixture_bundle(dataset_root: Path, *, timestamp: str, run_id: str) -> None:
    bundle_dir = dataset_root / f"{timestamp.replace(':', '-')}__{run_id}"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "metadata.json").write_text(
        json.dumps({"timestamp": timestamp, "run_id": run_id}),
        encoding="utf-8",
    )
    (bundle_dir / "market_context.json").write_text(
        json.dumps({"symbols": {}, "candidate_symbols": []}),
        encoding="utf-8",
    )
    (bundle_dir / "derivatives_snapshot.json").write_text(
        json.dumps({"rows": []}),
        encoding="utf-8",
    )
    (bundle_dir / "account_snapshot.json").write_text(
        json.dumps(
            {
                "equity": 100_000.0,
                "available_balance": 100_000.0,
                "futures_wallet_balance": 100_000.0,
                "open_positions": [],
            }
        ),
        encoding="utf-8",
    )


def _write_imported_public_strategy_bundle(
    dataset_root: Path,
    *,
    timestamp: str,
    run_id: str,
    momentum: float,
    forward_return: float,
) -> None:
    bundle_dir = dataset_root / f"{timestamp.replace(':', '-')}__{run_id}"
    bundle_dir.mkdir(parents=True)
    instrument_row = {
        "symbol": "BTCUSDT",
        "market_type": "futures",
        "base_asset": "BTC",
        "listing_timestamp": "2020-01-01T00:00:00Z",
        "quote_volume_usdt_24h": 1_500_000_000.0,
        "liquidity_tier": "high",
        "quantity_step": 0.001,
        "price_tick": 0.1,
        "has_complete_funding": True,
    }
    (bundle_dir / "metadata.json").write_text(
        json.dumps(
            {
                "timestamp": timestamp,
                "run_id": run_id,
                "schema_version": "phase1_import_bundle.v1",
                "forward_returns": {"3d": forward_return},
                "forward_drawdowns": {"3d": -0.02},
            }
        ),
        encoding="utf-8",
    )
    (bundle_dir / "market_context.json").write_text(
        json.dumps(
            {
                "as_of": timestamp,
                "schema_version": "imported_market_context.v1",
                "symbols": {
                    "BTCUSDT": {
                        "daily": {
                            "close": 100.0 + momentum,
                            "ema_50": 100.0,
                            "return_pct_7d": momentum,
                            "atr_pct": abs(momentum) + 0.01,
                        }
                    }
                },
                "instrument_rows": [instrument_row],
            }
        ),
        encoding="utf-8",
    )
    (bundle_dir / "derivatives_snapshot.json").write_text(
        json.dumps({"rows": [{"symbol": "BTCUSDT", "funding_rate": 0.0001, "basis_bps": 0.0}]}),
        encoding="utf-8",
    )
    (bundle_dir / "account_snapshot.json").write_text(
        json.dumps({"equity": 100_000.0, "available_balance": 100_000.0, "positions": []}),
        encoding="utf-8",
    )
    (bundle_dir / "instrument_snapshot.json").write_text(
        json.dumps({"as_of": timestamp, "schema_version": "imported_instrument_snapshot.v1", "rows": [instrument_row]}),
        encoding="utf-8",
    )


def _write_imported_public_strategy_dataset(dataset_root: Path) -> None:
    dataset_root.mkdir(parents=True)
    samples = [
        ("2025-01-01T00:00:00Z", "phase1-import-001", -0.04, -0.03),
        ("2025-01-02T00:00:00Z", "phase1-import-002", -0.01, -0.01),
        ("2025-01-03T00:00:00Z", "phase1-import-003", 0.02, 0.02),
        ("2025-01-04T00:00:00Z", "phase1-import-004", 0.05, 0.05),
    ]
    for timestamp, run_id, momentum, forward_return in samples:
        _write_imported_public_strategy_bundle(
            dataset_root,
            timestamp=timestamp,
            run_id=run_id,
            momentum=momentum,
            forward_return=forward_return,
        )
    (dataset_root / "import_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "phase1_imported_dataset_root.v1",
                "scope": "phase1_binance_futures",
                "archive_root": "/tmp/archive",
                "dataset_root": str(dataset_root),
                "snapshot_count": len(samples),
                "symbols": ["BTCUSDT"],
                "start_timestamp": samples[0][0],
                "end_timestamp": samples[-1][0],
                "bundle_dirs": [
                    str(dataset_root / f"{timestamp.replace(':', '-')}__{run_id}")
                    for timestamp, run_id, _momentum, _forward_return in samples
                ],
                "source": {"scope": "phase1_binance_futures", "symbols": ["BTCUSDT"]},
            }
        ),
        encoding="utf-8",
    )


def _sample_dataset_rows() -> list[DatasetSnapshotRow]:
    return [
        DatasetSnapshotRow(
            timestamp=_ts("2026-03-10T00:00:00Z"),
            run_id="row-001",
            market={"symbols": {}},
            derivatives=[],
            account={
                "equity": 100_000.0,
                "available_balance": 100_000.0,
                "futures_wallet_balance": 100_000.0,
                "open_positions": [],
            },
        ),
        DatasetSnapshotRow(
            timestamp=_ts("2026-03-12T00:00:00Z"),
            run_id="row-002",
            market={"symbols": {}},
            derivatives=[],
            account={
                "equity": 100_000.0,
                "available_balance": 100_000.0,
                "futures_wallet_balance": 100_000.0,
                "open_positions": [],
            },
        ),
    ]



def _write_experiment_fixture_config(tmp_path: Path, fixture_name: str) -> Path:
    fixture_path = Path("trading_system/tests/fixtures/backtest") / fixture_name
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    dataset_root = tmp_path / "dataset"
    dataset_root.mkdir(parents=True, exist_ok=True)
    raw["dataset_root"] = str(dataset_root)
    output_path = tmp_path / fixture_name
    output_path.write_text(json.dumps(raw), encoding="utf-8")
    return output_path



def sample_full_market_config(tmp_path: Path) -> Path:
    dataset_root = tmp_path / "dataset"
    _write_fixture_bundle(dataset_root, timestamp="2026-03-10T00:00:00Z", run_id="row-001")
    _write_fixture_bundle(dataset_root, timestamp="2026-03-11T00:00:00Z", run_id="row-002")

    config = BacktestConfig(
        dataset_root=dataset_root,
        experiment_kind="full_market_baseline",
        sample_windows=(
            SampleWindow(
                name="full_history",
                start=_ts("2026-03-10T00:00:00Z"),
                end=_ts("2026-03-11T00:00:00Z"),
            ),
        ),
        forward_return_windows=(),
        costs=BacktestCosts(
            fee_bps_by_market={"spot": 10.0, "futures": 5.0},
            slippage_bps_by_tier={"top": 2.0, "high": 8.0, "medium": 15.0, "low": 30.0},
            funding_mode="historical_series",
        ),
        baseline_name="current_system",
        variant_name="auditable_baseline",
        universe=UniverseFilterConfig(
            listing_age_days=30,
            min_quote_volume_usdt_24h={"spot": 1_000_000.0, "futures": 1_000_000.0},
            require_complete_funding=True,
        ),
        capital=CapitalModelConfig(
            model="shared_pool",
            initial_equity=100_000.0,
            risk_per_trade=0.02,
            max_open_risk=0.03,
        ),
    )

    config_path = tmp_path / "full_market_baseline.json"
    config_path.write_text(
        json.dumps(
            {
                "dataset_root": str(config.dataset_root),
                "experiment_kind": config.experiment_kind,
                "sample_windows": [
                    {
                        "name": config.sample_windows[0].name,
                        "start": "2026-03-10T00:00:00Z",
                        "end": "2026-03-11T00:00:00Z",
                    }
                ],
                "forward_return_windows": [],
                "costs": {
                    "fee_bps": config.costs.fee_bps_by_market,
                    "slippage_tiers": config.costs.slippage_bps_by_tier,
                    "funding_mode": config.costs.funding_mode,
                },
                "baseline_name": config.baseline_name,
                "variant_name": config.variant_name,
                "universe": {
                    "listing_age_days": config.universe.listing_age_days,
                    "min_quote_volume_usdt_24h": config.universe.min_quote_volume_usdt_24h,
                    "require_complete_funding": config.universe.require_complete_funding,
                },
                "capital": {
                    "model": config.capital.model,
                    "initial_equity": config.capital.initial_equity,
                    "risk_per_trade": config.capital.risk_per_trade,
                    "max_open_risk": config.capital.max_open_risk,
                },
            }
        ),
        encoding="utf-8",
    )
    return config_path


def test_render_full_market_baseline_report_contains_summary_breakdowns_and_audit_counts() -> None:
    renderer = getattr(reporting, "render_full_market_baseline_report", None)
    assert callable(renderer), "render_full_market_baseline_report is missing"

    report = renderer(sample_baseline_result())

    assert report["summary"]["total_return"] == pytest.approx(0.12)
    assert "by_market" in report["breakdowns"]
    assert "by_year" in report["breakdowns"]
    assert report["audit"]["rejection_count"] == 3
    first_trade = report["trades"][0]
    assert first_trade["entry_reference_timeframe"] == "15m"
    assert first_trade["entry_reference_price"] == pytest.approx(100.0)
    assert first_trade["gate_timeframes"] == ["daily", "4h", "1h"]
    assert first_trade["trigger_timeframes"] == ["30m", "15m"]
    assert first_trade["fill_model"] == "next_bar_ohlcv"
    assert first_trade["execution_price_source"] == "ohlcv_next_open"
    assert first_trade["fill_quality"] == "evidence_backed"
    assert first_trade["execution_timeframe"] == "1m"
    assert first_trade["execution_lag_bars"] == 1


def test_backtest_cli_writes_full_market_baseline_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "replay_full_market_baseline", lambda config: sample_baseline_result(), raising=False)

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(sample_full_market_config(tmp_path)),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "full_market_baseline__current_system__auditable_baseline"
    assert exit_code == 0
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "breakdowns.json").exists()
    assert (bundle_dir / "audit.json").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "breakdowns.json",
        "audit.json",
        "trades.json",
        "exit_path_replay.json",
        "trade_postmortem.md",
    ]
    exit_path = json.loads((bundle_dir / "exit_path_replay.json").read_text(encoding="utf-8"))["exit_path_replay"]
    assert exit_path["schema_version"] == "exit_path_replay_audit.v1"
    assert len(exit_path["trades"]) == len(sample_baseline_result().trade_ledger)
    trades = json.loads((bundle_dir / "trades.json").read_text(encoding="utf-8"))["trades"]
    assert trades and "exit_reason" in trades[0]
    assert trades[0]["entry_reference_timeframe"] == "15m"
    assert trades[0]["fill_model"] == "next_bar_ohlcv"
    assert trades[0]["fill_quality"] == "evidence_backed"
    assert trades[0]["execution_timeframe"] == "1m"
    postmortem = (bundle_dir / "trade_postmortem.md").read_text(encoding="utf-8")
    assert "逐单复盘" in postmortem
    assert "exec_tf" in postmortem
    assert "next_bar_ohlcv" in postmortem


def test_full_market_baseline_outputs_emit_exit_policy_experiment_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli, "replay_full_market_baseline", lambda config: sample_baseline_result(), raising=False)

    rows = _sample_dataset_rows()
    default_config = BacktestConfig(
        dataset_root=tmp_path / "dataset-default",
        experiment_kind="full_market_baseline",
        sample_windows=(),
        forward_return_windows=(),
        costs=BacktestCosts(
            fee_bps_by_market={"spot": 10.0, "futures": 5.0},
            slippage_bps_by_tier={"top": 2.0},
            funding_mode="historical_series",
        ),
        baseline_name="current_system",
        variant_name="auditable_baseline",
    )
    policy_config = BacktestConfig(
        dataset_root=tmp_path / "dataset-policy",
        experiment_kind="full_market_baseline",
        sample_windows=(),
        forward_return_windows=(),
        costs=default_config.costs,
        baseline_name="current_system",
        variant_name="exit_policy_variant",
        experiment_params=ExperimentParams(
            exit_policy=ExitPolicyParams(name="after_cost_breakeven_stop", after_cost_buffer_bps=2.0)
        ),
    )

    default_manifest, default_artifacts = cli._full_market_baseline_outputs(default_config, rows)
    policy_manifest, policy_artifacts = cli._full_market_baseline_outputs(policy_config, rows)

    assert "exit_policy_experiment.json" not in default_artifacts
    assert "exit_policy" not in default_artifacts["summary.json"]["metadata"]["experiment_params"]
    assert default_manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "breakdowns.json",
        "audit.json",
        "trades.json",
        "exit_path_replay.json",
        "trade_postmortem.md",
    ]

    assert policy_manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "breakdowns.json",
        "audit.json",
        "trades.json",
        "exit_path_replay.json",
        "trade_postmortem.md",
        "exit_policy_experiment.json",
    ]
    assert policy_artifacts["summary.json"]["metadata"]["experiment_params"]["exit_policy"] == {
        "name": "after_cost_breakeven_stop",
        "after_cost_buffer_bps": 2.0,
        "activation_minute": 0,
        "giveback_fraction": None,
        "giveback_min_bps": None,
        "no_breakeven_time_stop_minute": None,
    }
    exit_policy_artifact = policy_artifacts["exit_policy_experiment.json"]
    assert exit_policy_artifact["metadata"]["artifact_type"] == "opt_in_offline_diagnostic"
    assert exit_policy_artifact["summary"] == {
        "total_trades": 4,
        "evaluated_count": 4,
        "triggered_count": 0,
        "not_triggered_count": 0,
        "no_evidence_count": 4,
        "skipped_count": 0,
    }


def test_full_market_baseline_outputs_emit_setup_rewrite_experiment_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli, "replay_full_market_baseline", lambda config: sample_baseline_result(), raising=False)

    rows = _sample_dataset_rows()
    costs = BacktestCosts(
        fee_bps_by_market={"spot": 10.0, "futures": 5.0},
        slippage_bps_by_tier={"top": 2.0},
        funding_mode="historical_series",
    )
    default_config = BacktestConfig(
        dataset_root=tmp_path / "dataset-default",
        experiment_kind="full_market_baseline",
        sample_windows=(),
        forward_return_windows=(),
        costs=costs,
        baseline_name="current_system",
        variant_name="auditable_baseline",
    )
    setup_rewrite_config = BacktestConfig(
        dataset_root=tmp_path / "dataset-setup-rewrite",
        experiment_kind="full_market_baseline",
        sample_windows=(),
        forward_return_windows=(),
        costs=costs,
        baseline_name="current_system",
        variant_name="setup_rewrite_probe",
        experiment_params=ExperimentParams(
            setup_rewrite=SetupRewriteParams(
                rules=(
                    SetupRewriteRule(name="require_min_score", min_score=0.7),
                    SetupRewriteRule(name="require_after_cost_breakeven_evidence"),
                )
            )
        ),
    )

    default_manifest, default_artifacts = cli._full_market_baseline_outputs(default_config, rows)
    setup_manifest, setup_artifacts = cli._full_market_baseline_outputs(setup_rewrite_config, rows)

    assert "setup_rewrite_experiment.json" not in default_artifacts
    assert "setup_rewrite" not in default_artifacts["summary.json"]["metadata"]["experiment_params"]
    assert default_manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "breakdowns.json",
        "audit.json",
        "trades.json",
        "exit_path_replay.json",
        "trade_postmortem.md",
    ]

    assert setup_manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "breakdowns.json",
        "audit.json",
        "trades.json",
        "exit_path_replay.json",
        "trade_postmortem.md",
        "setup_rewrite_experiment.json",
    ]
    assert setup_artifacts["summary.json"]["metadata"]["experiment_params"]["setup_rewrite"] == {
        "rules": [
            {"name": "require_min_score", "min_score": 0.7},
            {"name": "require_after_cost_breakeven_evidence"},
        ]
    }
    assert "would_keep" not in setup_artifacts["trades.json"]["trades"][0]
    assert "evaluation_status" not in setup_artifacts["trades.json"]["trades"][0]

    artifact = setup_artifacts["setup_rewrite_experiment.json"]
    assert artifact["metadata"]["artifact_type"] == "opt_in_offline_diagnostic"
    assert artifact["metadata"]["changes_baseline_ledger"] is False
    assert artifact["summary"]["total_trades"] == 4


def test_backtest_cli_writes_rotation_suppression_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: _sample_dataset_rows(), raising=False)
    monkeypatch.setattr(
        cli,
        "run_rotation_suppression_experiment",
        lambda rows, *, evaluation_window, soft_score_floor: {
            "policies": {
                "current": {"bucket_level_pnl": 0.08, "trade_count": 2},
                "no_suppression": {"bucket_level_pnl": 0.12, "trade_count": 3},
                "soft_suppression": {"bucket_level_pnl": 0.1, "trade_count": 2},
            },
            "opportunity_kill_rate": 0.25,
            "avoid_loss_rate": 0.75,
            "rotation_comparison_rows": [{"symbol": "LINKUSDT", "current": "suppressed", "soft_suppression": "selected"}],
        },
        raising=False,
    )

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(_write_experiment_fixture_config(tmp_path, "rotation_suppression_config.json")),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "rotation_suppression__current_policy__soft_suppression"
    assert exit_code == 0
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "comparison_rows.json").exists()
    assert (bundle_dir / "scorecard.json").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "comparison_rows.json",
        "scorecard.json",
    ]
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["metadata"]["evaluation_window"] == "3d"
    assert scorecard["key_metrics"]["snapshot_count"] == 2
    assert scorecard["decision_summary"]["decision"] in {
        "keep_researching",
        "candidate_for_promotion",
        "reject",
    }



def test_backtest_cli_writes_allocator_friction_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: _sample_dataset_rows(), raising=False)
    monkeypatch.setattr(
        cli,
        "run_allocator_friction_experiment",
        lambda rows, *, evaluation_window: {
            "metadata": {"snapshot_count": 2, "variant_count": 3, "evaluation_window": evaluation_window},
            "variants": {
                "current_allocator": {
                    "allocation_summary": {"accepted_allocations": 2, "total_risk_budget": 0.02},
                    "frictions": {
                        "base": {"net_bucket_pnl": 0.03, "cost_drag": 0.004, "trade_count": 2},
                        "low": {"net_bucket_pnl": 0.031, "cost_drag": 0.003, "trade_count": 2},
                        "stressed": {"net_bucket_pnl": 0.02, "cost_drag": 0.008, "trade_count": 2},
                    },
                },
                "equal_weight_baseline": {
                    "allocation_summary": {"accepted_allocations": 3, "total_risk_budget": 0.03},
                    "frictions": {
                        "base": {"net_bucket_pnl": 0.04, "cost_drag": 0.005, "trade_count": 3},
                        "low": {"net_bucket_pnl": 0.041, "cost_drag": 0.004, "trade_count": 3},
                        "stressed": {"net_bucket_pnl": 0.025, "cost_drag": 0.01, "trade_count": 3},
                    },
                },
            },
            "comparison_rows": [{"allocator_variant": "current_allocator", "friction_scenario": "base", "net_bucket_pnl": 0.03}],
        },
        raising=False,
    )

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(_write_experiment_fixture_config(tmp_path, "allocator_friction_config.json")),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "allocator_friction__current_policy__allocator_fee_drag"
    assert exit_code == 0
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "comparison_rows.json").exists()
    assert (bundle_dir / "scorecard.json").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "comparison_rows.json",
        "scorecard.json",
    ]
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["metadata"]["evaluation_window"] == "3d"
    assert scorecard["key_metrics"]["snapshot_count"] == 2
    assert scorecard["decision_summary"]["decision"] in {
        "keep_researching",
        "candidate_for_promotion",
        "reject",
    }



def test_backtest_cli_writes_engine_filter_ablation_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: _sample_dataset_rows(), raising=False)
    monkeypatch.setattr(
        cli,
        "run_engine_filter_ablation_experiment",
        lambda rows, *, evaluation_window: {
            "metadata": {"snapshot_count": 2, "variant_count": 4, "evaluation_window": evaluation_window},
            "variants": {
                "trend_only": {"funnel": {"accepted_allocations": 2}, "performance": {"bucket_level_pnl": 0.02, "trade_count": 2}},
                "rotation_only": {"funnel": {"accepted_allocations": 1}, "performance": {"bucket_level_pnl": 0.01, "trade_count": 1}},
            },
        },
        raising=False,
    )

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(_write_experiment_fixture_config(tmp_path, "engine_filter_ablation_config.json")),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "engine_filter_ablation__current_policy__no_engine_filter"
    assert exit_code == 0
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "scorecard.json").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "scorecard.json",
    ]
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["metadata"]["evaluation_window"] == "1d"
    assert scorecard["key_metrics"]["snapshot_count"] == 2
    assert scorecard["decision_summary"]["decision"] in {
        "keep_researching",
        "candidate_for_promotion",
        "reject",
    }



def test_backtest_cli_filters_engine_filter_ablation_rows_to_sample_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: _sample_dataset_rows(), raising=False)
    captured: dict[str, object] = {}

    def _fake_engine_filter(rows, *, evaluation_window):
        captured["run_ids"] = [row.run_id for row in rows]
        captured["evaluation_window"] = evaluation_window
        return {
            "metadata": {"snapshot_count": len(rows), "variant_count": 1, "evaluation_window": evaluation_window},
            "variants": {
                "trend_only": {
                    "funnel": {"accepted_allocations": len(rows)},
                    "performance": {"bucket_level_pnl": 0.0, "trade_count": len(rows)},
                }
            },
        }

    monkeypatch.setattr(cli, "run_engine_filter_ablation_experiment", _fake_engine_filter, raising=False)

    config_path = _write_experiment_fixture_config(tmp_path, "engine_filter_ablation_config.json")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["sample_windows"] = [
        {
            "name": "train_only",
            "start": "2026-03-10T00:00:00Z",
            "end": "2026-03-10T00:00:00Z",
            "split": "in_sample",
        }
    ]
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "engine_filter_ablation__current_policy__no_engine_filter"
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    summary = json.loads((bundle_dir / "summary.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert captured["run_ids"] == ["row-001"]
    assert captured["evaluation_window"] == "1d"
    assert summary["metadata"]["snapshot_count"] == 1
    assert summary["metadata"]["sample_period"] == {
        "start": "2026-03-10T00:00:00+00:00",
        "end": "2026-03-10T00:00:00+00:00",
    }
    assert summary["metadata"]["window_counts"] == {"train_only": 1}
    assert scorecard["key_metrics"]["snapshot_count"] == 1



def test_backtest_cli_writes_long_gate_telemetry_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: _sample_dataset_rows(), raising=False)
    monkeypatch.setattr(
        cli,
        "run_long_gate_telemetry_experiment",
        lambda rows, *, evaluation_window: {
            "metadata": {"snapshot_count": 2, "engine_count": 2, "evaluation_window": evaluation_window},
            "engines": {
                "trend_long": {
                    "funnel": {"raw_candidates": 3, "accepted_allocations": 1},
                    "filter_counts": {"trend_filtered": 2, "selected": 1},
                    "performance": {"bucket_level_pnl": 0.01, "trade_count": 1},
                },
                "rotation_long": {
                    "funnel": {"raw_candidates": 0, "accepted_allocations": 0},
                    "filter_counts": {"overheat_filtered": 4, "selected": 0},
                    "performance": {"bucket_level_pnl": 0.0, "trade_count": 0},
                },
            },
            "symbol_breakdown": {
                "trend_long": {
                    "BTCUSDT": {
                        "snapshot_count": 1,
                        "funnel": {"raw_candidates": 1, "validated_candidates": 1, "allocation_decisions": 1, "accepted_allocations": 1},
                        "filter_counts": {"selected": 1},
                    }
                },
                "rotation_long": {},
            },
            "regime_breakdown": {
                "RISK_ON_TREND": {
                    "snapshot_count": 2,
                    "engines": {
                        "trend_long": {
                            "funnel": {"raw_candidates": 1, "validated_candidates": 1, "allocation_decisions": 1, "accepted_allocations": 1},
                            "filter_counts": {"selected": 1},
                            "performance": {"bucket_level_pnl": 0.01, "trade_count": 1},
                        },
                        "rotation_long": {
                            "funnel": {"raw_candidates": 0, "validated_candidates": 0, "allocation_decisions": 0, "accepted_allocations": 0},
                            "filter_counts": {"trend_filtered": 1, "selected": 0},
                            "performance": {"bucket_level_pnl": 0.0, "trade_count": 0},
                        },
                    },
                }
            },
            "snapshot_rows": [
                {
                    "timestamp": "2026-03-10T00:00:00+00:00",
                    "run_id": "row-001",
                    "regime_label": "RISK_ON_TREND",
                    "total_long_raw_candidates": 1,
                    "total_long_accepted_allocations": 1,
                    "engines": {},
                }
            ],
        },
        raising=False,
    )

    config_path = _write_experiment_fixture_config(tmp_path, "long_gate_telemetry_config.json")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["sample_windows"] = [
        {
            "name": "train",
            "start": "2026-03-10T00:00:00Z",
            "end": "2026-03-12T00:00:00Z",
            "split": "in_sample",
        }
    ]
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "long_gate_telemetry__current_policy__long_gate_diagnostic"
    assert exit_code == 0
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "snapshot_rows.json").exists()
    assert (bundle_dir / "symbol_breakdown.json").exists()
    assert (bundle_dir / "regime_breakdown.json").exists()
    assert (bundle_dir / "scorecard.json").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "snapshot_rows.json",
        "symbol_breakdown.json",
        "regime_breakdown.json",
        "scorecard.json",
    ]
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["metadata"]["evaluation_window"] == "3d"
    assert scorecard["key_metrics"]["snapshot_count"] == 2
    assert scorecard["decision_summary"]["decision"] == "keep_researching"
    assert scorecard["key_metrics"]["dominant_blocker_gate"] in {"trend_filtered", "overheat_filtered"}
    snapshot_rows = json.loads((bundle_dir / "snapshot_rows.json").read_text(encoding="utf-8"))
    assert snapshot_rows["rows"][0]["run_id"] == "row-001"
    symbol_breakdown = json.loads((bundle_dir / "symbol_breakdown.json").read_text(encoding="utf-8"))
    assert symbol_breakdown["engines"]["trend_long"]["BTCUSDT"]["funnel"]["accepted_allocations"] == 1
    regime_breakdown = json.loads((bundle_dir / "regime_breakdown.json").read_text(encoding="utf-8"))
    assert regime_breakdown["regimes"]["RISK_ON_TREND"]["engines"]["trend_long"]["funnel"]["accepted_allocations"] == 1



def test_render_long_gate_telemetry_prefers_specific_eligibility_blocker_in_scorecard() -> None:
    report = cli.render_long_gate_telemetry_report(
        experiment_name="long_gate_telemetry",
        metadata={"snapshot_count": 1, "evaluation_window": "3d"},
        experiment={
            "engines": {
                "trend_long": {
                    "funnel": {"raw_candidates": 0, "accepted_allocations": 0},
                    "filter_counts": {
                        "eligibility_filtered": 6,
                        "eligibility_liquidity_tier_filtered": 2,
                        "eligibility_pretrend_filtered": 1,
                        "eligibility_daily_return_filtered": 3,
                        "eligibility_h4_return_filtered": 1,
                        "selected": 0,
                    },
                    "performance": {"bucket_level_pnl": 0.0, "trade_count": 0},
                },
                "rotation_long": {
                    "funnel": {"raw_candidates": 0, "accepted_allocations": 0},
                    "filter_counts": {"trend_filtered": 2, "selected": 0},
                    "performance": {"bucket_level_pnl": 0.0, "trade_count": 0},
                },
            },
            "snapshot_rows": [],
        },
    )

    assert report["scorecard"]["key_metrics"]["dominant_blocker_engine"] == "trend_long"
    assert report["scorecard"]["key_metrics"]["dominant_blocker_gate"] == "eligibility_daily_return_filtered"


def test_render_long_gate_telemetry_rejects_non_string_engine_keys() -> None:
    with pytest.raises(ValueError, match="engine names must be a canonical string"):
        cli.render_long_gate_telemetry_report(
            experiment_name="long_gate_telemetry",
            metadata={"snapshot_count": 1, "evaluation_window": "3d"},
            experiment={
                "engines": {
                    True: {
                        "funnel": {"raw_candidates": 1, "accepted_allocations": 1},
                        "filter_counts": {"selected": 1},
                        "performance": {"bucket_level_pnl": 0.01, "trade_count": 1},
                    },
                },
                "snapshot_rows": [],
            },
        )


def test_render_long_gate_telemetry_rejects_non_object_filter_counts() -> None:
    with pytest.raises(ValueError, match="engines.trend_long.filter_counts must be an object"):
        cli.render_long_gate_telemetry_report(
            experiment_name="long_gate_telemetry",
            metadata={"snapshot_count": 1, "evaluation_window": "3d"},
            experiment={
                "engines": {
                    "trend_long": {
                        "funnel": {"raw_candidates": 0, "accepted_allocations": 0},
                        "filter_counts": [],
                        "performance": {"bucket_level_pnl": 0.0, "trade_count": 0},
                    },
                },
                "snapshot_rows": [],
            },
        )


def test_render_long_gate_telemetry_rejects_boolean_filter_count_metric() -> None:
    with pytest.raises(
        ValueError,
        match="engines.trend_long.filter_counts.trend_filtered must be a non-negative integer",
    ):
        cli.render_long_gate_telemetry_report(
            experiment_name="long_gate_telemetry",
            metadata={"snapshot_count": 1, "evaluation_window": "3d"},
            experiment={
                "engines": {
                    "trend_long": {
                        "funnel": {"raw_candidates": 0, "accepted_allocations": 0},
                        "filter_counts": {"trend_filtered": True, "selected": 0},
                        "performance": {"bucket_level_pnl": 0.0, "trade_count": 0},
                    },
                },
                "snapshot_rows": [],
            },
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("accepted_allocations", True),
        ("raw_candidates", "1"),
    ],
)
def test_render_long_gate_telemetry_rejects_malformed_funnel_counts(field: str, value: object) -> None:
    funnel = {"raw_candidates": 1, "accepted_allocations": 1}
    funnel[field] = value

    with pytest.raises(
        ValueError,
        match=rf"engines\.trend_long\.funnel\.{field} must be a non-negative integer",
    ):
        cli.render_long_gate_telemetry_report(
            experiment_name="long_gate_telemetry",
            metadata={"snapshot_count": 1, "evaluation_window": "3d"},
            experiment={
                "engines": {
                    "trend_long": {
                        "funnel": funnel,
                        "filter_counts": {"selected": 1},
                        "performance": {"bucket_level_pnl": 0.0, "trade_count": 0},
                    },
                },
                "snapshot_rows": [],
            },
        )


def test_render_long_gate_telemetry_rejects_tuple_snapshot_rows_payload() -> None:
    with pytest.raises(ValueError, match="snapshot_rows must be a list"):
        cli.render_long_gate_telemetry_report(
            experiment_name="long_gate_telemetry",
            metadata={"snapshot_count": 1, "evaluation_window": "3d"},
            experiment={
                "engines": {
                    "trend_long": {
                        "funnel": {"raw_candidates": 1, "accepted_allocations": 1},
                        "filter_counts": {"selected": 1},
                        "performance": {"bucket_level_pnl": 0.0, "trade_count": 0},
                    },
                },
                "snapshot_rows": ({"run_id": "row-001"},),
            },
        )



def _minimal_walk_forward_validation_experiment() -> dict[str, object]:
    return {
        "windows": [],
        "robustness_summary": {
            "out_of_sample_scorecard": {"total_return": 0.03, "trade_count": 1},
            "in_sample_scorecard": {"total_return": 0.04, "trade_count": 1},
            "performance_dispersion": {"positive_window_ratio": 1.0},
        },
        "parameter_stability": {"parameter_stability_score": 0.8},
    }


def test_walk_forward_validation_report_accepts_stability_scorecard_object_contracts() -> None:
    report = cli.render_walk_forward_validation_report(
        experiment_name="walk_forward_validation",
        metadata={"snapshot_count": 1, "window_count": 1},
        experiment=_minimal_walk_forward_validation_experiment(),
    )

    assert report["summary"]["parameter_stability"]["parameter_stability_score"] == pytest.approx(0.8)
    robustness_summary = report["summary"]["robustness_summary"]
    assert robustness_summary["performance_dispersion"]["positive_window_ratio"] == pytest.approx(1.0)
    assert robustness_summary["out_of_sample_scorecard"]["trade_count"] == 1
    assert robustness_summary["in_sample_scorecard"]["trade_count"] == 1


@pytest.mark.parametrize(
    ("path", "match"),
    [
        (("parameter_stability",), "parameter_stability must be an object"),
        (("robustness_summary", "performance_dispersion"), "performance_dispersion must be an object"),
        (("robustness_summary", "out_of_sample_scorecard"), "out_of_sample_scorecard must be an object"),
        (("robustness_summary", "in_sample_scorecard"), "in_sample_scorecard must be an object"),
    ],
)
def test_walk_forward_validation_report_rejects_non_object_stability_scorecard_contracts(
    path: tuple[str, ...],
    match: str,
) -> None:
    experiment = _minimal_walk_forward_validation_experiment()
    cursor: object = experiment
    for part in path[:-1]:
        cursor = cursor[part]  # type: ignore[index]
    cursor[path[-1]] = []  # type: ignore[index]

    with pytest.raises(ValueError, match=match):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment=experiment,
        )


def test_walk_forward_validation_report_rejects_invalid_scorecard_numerics() -> None:
    with pytest.raises(ValueError, match="out_of_sample_scorecard.total_return must be a finite number"):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment={
                "windows": [],
                "robustness_summary": {
                    "out_of_sample_scorecard": {"total_return": True},
                    "performance_dispersion": {"positive_window_ratio": 1.0},
                },
                "parameter_stability": {"parameter_stability_score": 0.8},
            },
        )


def test_walk_forward_validation_report_rejects_string_summary_scorecard_metric() -> None:
    with pytest.raises(ValueError, match="out_of_sample_scorecard.total_return must be a finite number"):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment={
                "windows": [],
                "robustness_summary": {
                    "out_of_sample_scorecard": {"total_return": "0.03"},
                    "performance_dispersion": {"positive_window_ratio": 1.0},
                },
                "parameter_stability": {"parameter_stability_score": 0.8},
            },
        )



def test_walk_forward_validation_report_rejects_invalid_metadata_counts() -> None:
    with pytest.raises(ValueError, match="metadata.snapshot_count must be a non-negative integer"):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": True, "window_count": 1},
            experiment={
                "windows": [],
                "robustness_summary": {
                    "out_of_sample_scorecard": {"total_return": 0.03},
                    "performance_dispersion": {"positive_window_ratio": 1.0},
                },
                "parameter_stability": {"parameter_stability_score": 0.8},
            },
        )



def test_walk_forward_validation_report_rejects_non_object_robustness_payloads() -> None:
    with pytest.raises(ValueError, match="robustness_summary must be an object"):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment={"windows": [], "robustness_summary": [], "parameter_stability": {"parameter_stability_score": 0.8}},
        )



def test_walk_forward_validation_report_rejects_invalid_windows_shape() -> None:
    with pytest.raises(ValueError, match="windows must be a list"):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment={
                "windows": "not-a-list",
                "robustness_summary": {
                    "out_of_sample_scorecard": {"total_return": 0.03},
                    "performance_dispersion": {"positive_window_ratio": 1.0},
                },
                "parameter_stability": {"parameter_stability_score": 0.8},
            },
        )


def test_walk_forward_validation_report_rejects_non_object_window_row() -> None:
    with pytest.raises(ValueError, match=r"windows\[0\] must be an object"):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment={
                "windows": [["window_index", 1]],
                "robustness_summary": {
                    "out_of_sample_scorecard": {"total_return": 0.03},
                    "performance_dispersion": {"positive_window_ratio": 1.0},
                },
                "parameter_stability": {"parameter_stability_score": 0.8},
            },
        )


def test_walk_forward_validation_report_rejects_boolean_window_index() -> None:
    with pytest.raises(ValueError, match=r"windows\[0\]\.window_index must be a non-negative integer"):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment={
                "windows": [
                    {
                        "window_index": True,
                        "out_of_sample": {
                            "scorecard": {"total_return": 0.03, "trade_count": 1},
                            "run_ids": ["row-002"],
                        },
                    }
                ],
                "robustness_summary": {
                    "out_of_sample_scorecard": {"total_return": 0.03},
                    "performance_dispersion": {"positive_window_ratio": 1.0},
                },
                "parameter_stability": {"parameter_stability_score": 0.8},
            },
        )


def test_walk_forward_validation_report_rejects_string_window_scorecard_metric() -> None:
    with pytest.raises(ValueError, match=r"windows\[0\]\.out_of_sample\.scorecard\.total_return must be a finite number"):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment={
                "windows": [
                    {
                        "window_index": 1,
                        "out_of_sample": {
                            "scorecard": {"total_return": "0.03", "trade_count": 1},
                            "run_ids": ["row-002"],
                        },
                    }
                ],
                "robustness_summary": {
                    "out_of_sample_scorecard": {"total_return": 0.03},
                    "performance_dispersion": {"positive_window_ratio": 1.0},
                },
                "parameter_stability": {"parameter_stability_score": 0.8},
            },
        )


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (
            ("windows", 0, "out_of_sample", "snapshot_count"),
            True,
            r"windows\[0\]\.out_of_sample\.snapshot_count must be a non-negative integer",
        ),
        (
            ("windows", 0, "out_of_sample", "snapshot_count"),
            "1",
            r"windows\[0\]\.out_of_sample\.snapshot_count must be a non-negative integer",
        ),
        (
            ("windows", 0, "out_of_sample", "snapshot_count"),
            1.5,
            r"windows\[0\]\.out_of_sample\.snapshot_count must be a non-negative integer",
        ),
        (
            ("windows", 0, "out_of_sample", "snapshot_count"),
            float("nan"),
            r"windows\[0\]\.out_of_sample\.snapshot_count must be a non-negative integer",
        ),
        (
            ("windows", 0, "out_of_sample", "snapshot_count"),
            float("inf"),
            r"windows\[0\]\.out_of_sample\.snapshot_count must be a non-negative integer",
        ),
        (
            ("windows", 0, "out_of_sample", "snapshot_count"),
            -1,
            r"windows\[0\]\.out_of_sample\.snapshot_count must be a non-negative integer",
        ),
        (
            ("windows", 0, "out_of_sample", "scorecard", "win_count"),
            "1",
            r"windows\[0\]\.out_of_sample\.scorecard\.win_count must be a non-negative integer",
        ),
        (
            ("windows", 0, "out_of_sample", "scorecard", "loss_count"),
            True,
            r"windows\[0\]\.out_of_sample\.scorecard\.loss_count must be a non-negative integer",
        ),
        (
            ("robustness_summary", "performance_dispersion", "window_count"),
            1.5,
            r"performance_dispersion\.window_count must be a non-negative integer",
        ),
        (
            ("robustness_summary", "performance_dispersion", "positive_window_count"),
            "1",
            r"performance_dispersion\.positive_window_count must be a non-negative integer",
        ),
        (
            ("robustness_summary", "worst_window", "window_index"),
            0,
            r"worst_window\.window_index must be a positive integer",
        ),
        (
            ("robustness_summary", "worst_window", "scorecard", "trade_count"),
            float("inf"),
            r"worst_window\.scorecard\.trade_count must be a non-negative integer",
        ),
    ],
)
def test_walk_forward_validation_report_rejects_malformed_present_count_domains(
    path: tuple[object, ...],
    value: object,
    match: str,
) -> None:
    experiment: dict[str, object] = {
        "windows": [
            {
                "window_index": 1,
                "out_of_sample": {
                    "snapshot_count": 1,
                    "scorecard": {"total_return": 0.03, "trade_count": 1, "win_count": 1, "loss_count": 0},
                    "run_ids": ["row-002"],
                },
            }
        ],
        "robustness_summary": {
            "out_of_sample_scorecard": {"total_return": 0.03, "trade_count": 1},
            "performance_dispersion": {
                "window_count": 1,
                "positive_window_count": 1,
                "positive_window_ratio": 1.0,
            },
            "worst_window": {
                "window_index": 1,
                "scorecard": {"total_return": 0.03, "trade_count": 1},
            },
        },
        "parameter_stability": {"parameter_stability_score": 0.8},
    }
    cursor: object = experiment
    for part in path[:-1]:
        cursor = cursor[part]  # type: ignore[index]
    cursor[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValueError, match=match):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment=experiment,
        )


@pytest.mark.parametrize(
    ("worst_window", "match"),
    [
        (["window-1"], "worst_window must be an object"),
        (
            {"window_index": 1, "scorecard": ["total_return", 0.03]},
            re.escape("worst_window.scorecard must be an object"),
        ),
    ],
)
def test_walk_forward_validation_report_rejects_malformed_worst_window_contracts(
    worst_window: object,
    match: str,
) -> None:
    experiment: dict[str, object] = {
        "windows": [
            {
                "window_index": 1,
                "out_of_sample": {
                    "snapshot_count": 1,
                    "scorecard": {"total_return": 0.03, "trade_count": 1},
                    "run_ids": ["row-002"],
                },
            }
        ],
        "robustness_summary": {
            "out_of_sample_scorecard": {"total_return": 0.03, "trade_count": 1},
            "performance_dispersion": {
                "window_count": 1,
                "positive_window_count": 1,
                "positive_window_ratio": 1.0,
            },
            "worst_window": worst_window,
        },
        "parameter_stability": {"parameter_stability_score": 0.8},
    }

    with pytest.raises(ValueError, match=match):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment=experiment,
        )


def test_walk_forward_validation_report_preserves_valid_worst_window_scorecard() -> None:
    report = cli.render_walk_forward_validation_report(
        experiment_name="walk_forward_validation",
        metadata={"snapshot_count": 1, "window_count": 1},
        experiment={
            "windows": [
                {
                    "window_index": 1,
                    "out_of_sample": {
                        "snapshot_count": 1,
                        "scorecard": {"total_return": 0.03, "trade_count": 1},
                        "run_ids": ["row-002"],
                    },
                }
            ],
            "robustness_summary": {
                "out_of_sample_scorecard": {"total_return": 0.03, "trade_count": 1},
                "performance_dispersion": {
                    "window_count": 1,
                    "positive_window_count": 1,
                    "positive_window_ratio": 1.0,
                },
                "worst_window": {
                    "window_index": 1,
                    "scorecard": {
                        "total_return": 0.03,
                        "trade_count": 1,
                        "win_count": 1,
                        "loss_count": 0,
                    },
                },
            },
            "parameter_stability": {"parameter_stability_score": 0.8},
        },
    )

    assert report["summary"]["robustness_summary"]["worst_window"] == {
        "window_index": 1,
        "scorecard": {
            "total_return": 0.03,
            "trade_count": 1,
            "win_count": 1,
            "loss_count": 0,
        },
    }


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (
            ("windows", 0, "out_of_sample", "scorecard", "duration_bars"),
            True,
            r"windows\[0\]\.out_of_sample\.scorecard\.duration_bars must be a positive integer",
        ),
        (
            ("windows", 0, "out_of_sample", "scorecard", "duration_bars"),
            "2",
            r"windows\[0\]\.out_of_sample\.scorecard\.duration_bars must be a positive integer",
        ),
        (
            ("windows", 0, "out_of_sample", "scorecard", "holding_bars"),
            1.5,
            r"windows\[0\]\.out_of_sample\.scorecard\.holding_bars must be a positive integer",
        ),
        (
            ("windows", 0, "out_of_sample", "scorecard", "bars_since_entry"),
            float("nan"),
            r"windows\[0\]\.out_of_sample\.scorecard\.bars_since_entry must be a positive integer",
        ),
        (
            ("windows", 0, "out_of_sample", "scorecard", "age_bars"),
            float("inf"),
            r"windows\[0\]\.out_of_sample\.scorecard\.age_bars must be a positive integer",
        ),
        (
            ("windows", 0, "out_of_sample", "scorecard", "lookback_bars"),
            -1,
            r"windows\[0\]\.out_of_sample\.scorecard\.lookback_bars must be a positive integer",
        ),
        (
            ("windows", 0, "out_of_sample", "scorecard", "window_span_bars"),
            0,
            r"windows\[0\]\.out_of_sample\.scorecard\.window_span_bars must be a positive integer",
        ),
        (
            ("robustness_summary", "performance_dispersion", "median_duration_bars"),
            "4",
            r"performance_dispersion\.median_duration_bars must be a positive integer",
        ),
        (
            ("robustness_summary", "worst_window", "scorecard", "max_duration_bars"),
            2.0,
            r"worst_window\.scorecard\.max_duration_bars must be a positive integer",
        ),
        (
            ("robustness_summary", "out_of_sample_scorecard", "min_duration_bars"),
            0,
            r"out_of_sample_scorecard\.min_duration_bars must be a positive integer",
        ),
    ],
)
def test_walk_forward_validation_report_rejects_malformed_present_duration_domains(
    path: tuple[object, ...],
    value: object,
    match: str,
) -> None:
    experiment: dict[str, object] = {
        "windows": [
            {
                "window_index": 1,
                "out_of_sample": {
                    "snapshot_count": 1,
                    "scorecard": {
                        "total_return": 0.03,
                        "trade_count": 1,
                        "duration_bars": 2,
                        "holding_bars": 2,
                        "bars_since_entry": 1,
                        "age_bars": 5,
                        "lookback_bars": 20,
                        "window_span_bars": 48,
                    },
                    "run_ids": ["row-002"],
                },
            }
        ],
        "robustness_summary": {
            "out_of_sample_scorecard": {"total_return": 0.03, "trade_count": 1, "min_duration_bars": 1},
            "performance_dispersion": {
                "window_count": 1,
                "positive_window_count": 1,
                "positive_window_ratio": 1.0,
                "median_duration_bars": 2,
            },
            "worst_window": {
                "window_index": 1,
                "scorecard": {"total_return": 0.03, "trade_count": 1, "max_duration_bars": 2},
            },
        },
        "parameter_stability": {"parameter_stability_score": 0.8},
    }
    cursor: object = experiment
    for part in path[:-1]:
        cursor = cursor[part]  # type: ignore[index]
    cursor[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValueError, match=match):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment=experiment,
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("win_rate", True, r"windows\[0\]\.out_of_sample\.scorecard\.win_rate must be a bounded ratio strict number"),
        ("win_rate", "0.55", r"windows\[0\]\.out_of_sample\.scorecard\.win_rate must be a bounded ratio strict number"),
        ("win_rate", float("nan"), r"windows\[0\]\.out_of_sample\.scorecard\.win_rate must be a bounded ratio strict number"),
        ("win_rate", float("inf"), r"windows\[0\]\.out_of_sample\.scorecard\.win_rate must be a bounded ratio strict number"),
        ("win_rate", -0.01, r"windows\[0\]\.out_of_sample\.scorecard\.win_rate must be a bounded ratio strict number"),
        ("win_rate", 1.01, r"windows\[0\]\.out_of_sample\.scorecard\.win_rate must be a bounded ratio strict number"),
        ("payoff_ratio", True, r"windows\[0\]\.out_of_sample\.scorecard\.payoff_ratio must be a non-negative finite strict number"),
        ("payoff_ratio", "1.5", r"windows\[0\]\.out_of_sample\.scorecard\.payoff_ratio must be a non-negative finite strict number"),
        ("payoff_ratio", float("nan"), r"windows\[0\]\.out_of_sample\.scorecard\.payoff_ratio must be a non-negative finite strict number"),
        ("payoff_ratio", float("inf"), r"windows\[0\]\.out_of_sample\.scorecard\.payoff_ratio must be a non-negative finite strict number"),
        ("payoff_ratio", -1.0, r"windows\[0\]\.out_of_sample\.scorecard\.payoff_ratio must be a non-negative finite strict number"),
    ],
)
def test_walk_forward_validation_report_rejects_malformed_ratio_domain_scorecard_values(
    field: str,
    value: object,
    match: str,
) -> None:
    scorecard = {
        "total_return": 0.03,
        "max_drawdown": -0.01,
        "sharpe": 1.2,
        "sortino": 1.4,
        "calmar": 2.0,
        "win_rate": 0.5,
        "payoff_ratio": 1.5,
        "expectancy": 0.01,
        "trade_count": 2,
    }
    scorecard[field] = value

    with pytest.raises(ValueError, match=match):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment={
                "windows": [
                    {
                        "window_index": 1,
                        "out_of_sample": {
                            "scorecard": scorecard,
                            "run_ids": ["row-002"],
                        },
                    }
                ],
                "robustness_summary": {
                    "out_of_sample_scorecard": {"total_return": 0.03},
                    "performance_dispersion": {"positive_window_ratio": 1.0},
                },
                "parameter_stability": {"parameter_stability_score": 0.8},
            },
        )


@pytest.mark.parametrize("positive_window_ratio", [True, "0.75", float("nan"), float("inf"), -0.01, 1.01])
def test_walk_forward_validation_report_rejects_malformed_performance_dispersion_ratio(
    positive_window_ratio: object,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"performance_dispersion\.positive_window_ratio must be a bounded ratio strict number",
    ):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment={
                "windows": [],
                "robustness_summary": {
                    "out_of_sample_scorecard": {"total_return": 0.03},
                    "performance_dispersion": {"positive_window_ratio": positive_window_ratio},
                },
                "parameter_stability": {"parameter_stability_score": 0.8},
            },
        )


def test_walk_forward_validation_report_preserves_zero_performance_dispersion_ratio() -> None:
    report = cli.render_walk_forward_validation_report(
        experiment_name="walk_forward_validation",
        metadata={"snapshot_count": 1, "window_count": 1},
        experiment={
            "windows": [],
            "robustness_summary": {
                "out_of_sample_scorecard": {"total_return": 0.0},
                "performance_dispersion": {"positive_window_ratio": 0.0},
            },
            "parameter_stability": {"parameter_stability_score": 0.8},
        },
    )

    assert (
        report["summary"]["robustness_summary"]["performance_dispersion"]["positive_window_ratio"]
        == pytest.approx(0.0)
    )


@pytest.mark.parametrize("parameter_stability_score", [True, "0.75", float("nan"), float("inf"), -0.01, 1.01])
def test_walk_forward_validation_report_rejects_malformed_parameter_stability_score(
    parameter_stability_score: object,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"parameter_stability\.parameter_stability_score must be a bounded ratio strict number",
    ):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment={
                "windows": [],
                "robustness_summary": {
                    "out_of_sample_scorecard": {"total_return": 0.03},
                    "performance_dispersion": {"positive_window_ratio": 1.0},
                },
                "parameter_stability": {"parameter_stability_score": parameter_stability_score},
            },
        )


def test_walk_forward_validation_report_preserves_zero_parameter_stability_score() -> None:
    report = cli.render_walk_forward_validation_report(
        experiment_name="walk_forward_validation",
        metadata={"snapshot_count": 1, "window_count": 1},
        experiment={
            "windows": [],
            "robustness_summary": {
                "out_of_sample_scorecard": {"total_return": 0.0},
                "performance_dispersion": {"positive_window_ratio": 0.0},
            },
            "parameter_stability": {"parameter_stability_score": 0.0},
        },
    )

    assert report["summary"]["parameter_stability"]["parameter_stability_score"] == pytest.approx(0.0)
    assert report["scorecard"]["key_metrics"]["parameter_stability_score"] == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("coverage_ratio", True, r"windows\[0\]\.out_of_sample\.scorecard\.coverage_ratio must be a bounded ratio strict number"),
        ("coverage_ratio", "0.90", r"windows\[0\]\.out_of_sample\.scorecard\.coverage_ratio must be a bounded ratio strict number"),
        ("coverage_ratio", float("nan"), r"windows\[0\]\.out_of_sample\.scorecard\.coverage_ratio must be a bounded ratio strict number"),
        ("coverage_ratio", float("inf"), r"windows\[0\]\.out_of_sample\.scorecard\.coverage_ratio must be a bounded ratio strict number"),
        ("coverage_ratio", -0.01, r"windows\[0\]\.out_of_sample\.scorecard\.coverage_ratio must be a bounded ratio strict number"),
        ("coverage_ratio", 1.01, r"windows\[0\]\.out_of_sample\.scorecard\.coverage_ratio must be a bounded ratio strict number"),
        ("data_coverage_pct", True, r"windows\[0\]\.out_of_sample\.scorecard\.data_coverage_pct must be a bounded percentage strict number"),
        ("data_coverage_pct", "90", r"windows\[0\]\.out_of_sample\.scorecard\.data_coverage_pct must be a bounded percentage strict number"),
        ("data_coverage_pct", 90.5, r"windows\[0\]\.out_of_sample\.scorecard\.data_coverage_pct must be a bounded percentage strict number"),
        ("data_coverage_pct", float("nan"), r"windows\[0\]\.out_of_sample\.scorecard\.data_coverage_pct must be a bounded percentage strict number"),
        ("data_coverage_pct", float("inf"), r"windows\[0\]\.out_of_sample\.scorecard\.data_coverage_pct must be a bounded percentage strict number"),
        ("data_coverage_pct", -0.01, r"windows\[0\]\.out_of_sample\.scorecard\.data_coverage_pct must be a bounded percentage strict number"),
        ("data_coverage_pct", 100.01, r"windows\[0\]\.out_of_sample\.scorecard\.data_coverage_pct must be a bounded percentage strict number"),
    ],
)
def test_walk_forward_validation_report_rejects_malformed_coverage_scorecard_values(
    field: str,
    value: object,
    match: str,
) -> None:
    scorecard = {
        "total_return": 0.03,
        "trade_count": 2,
        "coverage_ratio": 0.9,
        "data_coverage_pct": 90,
    }
    scorecard[field] = value

    with pytest.raises(ValueError, match=match):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment={
                "windows": [
                    {
                        "window_index": 1,
                        "out_of_sample": {
                            "scorecard": scorecard,
                            "run_ids": ["row-002"],
                        },
                    }
                ],
                "robustness_summary": {
                    "out_of_sample_scorecard": {"total_return": 0.03},
                    "performance_dispersion": {"positive_window_ratio": 1.0},
                },
                "parameter_stability": {"parameter_stability_score": 0.8},
            },
        )


@pytest.mark.parametrize(
    "field",
    [
        "coverage_pct",
        "sample_coverage_pct",
        "trade_coverage_pct",
        "signal_coverage_pct",
        "regime_coverage_pct",
        "fill_coverage_pct",
        "universe_coverage_pct",
        "benchmark_coverage_pct",
        "execution_coverage_pct",
    ],
)
@pytest.mark.parametrize("value", [True, "90", 90.5, float("nan"), float("inf"), -0.01, 100.01])
def test_walk_forward_validation_report_rejects_malformed_named_percentage_coverage_fields(
    field: str,
    value: object,
) -> None:
    scorecard = {
        "total_return": 0.03,
        "trade_count": 2,
        field: value,
    }

    with pytest.raises(
        ValueError,
        match=rf"windows\[0\]\.out_of_sample\.scorecard\.{field} must be a bounded percentage strict number",
    ):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment={
                "windows": [
                    {
                        "window_index": 1,
                        "out_of_sample": {
                            "scorecard": scorecard,
                            "run_ids": ["row-002"],
                        },
                    }
                ],
                "robustness_summary": {
                    "out_of_sample_scorecard": {"total_return": 0.03},
                    "performance_dispersion": {"positive_window_ratio": 1.0},
                },
                "parameter_stability": {"parameter_stability_score": 0.8},
            },
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("coverage_ratio", "0.90", r"out_of_sample_scorecard\.coverage_ratio must be a bounded ratio strict number"),
        ("coverage_ratio", 1.01, r"out_of_sample_scorecard\.coverage_ratio must be a bounded ratio strict number"),
        ("execution_coverage_pct", "90", r"out_of_sample_scorecard\.execution_coverage_pct must be a bounded percentage strict number"),
        ("execution_coverage_pct", 100.01, r"out_of_sample_scorecard\.execution_coverage_pct must be a bounded percentage strict number"),
    ],
)
def test_walk_forward_validation_report_rejects_malformed_summary_coverage_scorecard_values(
    field: str,
    value: object,
    match: str,
) -> None:
    out_of_sample_scorecard = {
        "total_return": 0.03,
        "trade_count": 2,
        field: value,
    }

    with pytest.raises(ValueError, match=match):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment={
                "windows": [],
                "robustness_summary": {
                    "out_of_sample_scorecard": out_of_sample_scorecard,
                    "performance_dispersion": {"positive_window_ratio": 1.0},
                },
                "parameter_stability": {"parameter_stability_score": 0.8},
            },
        )


def test_walk_forward_validation_report_preserves_valid_coverage_scorecard_values() -> None:
    report = cli.render_walk_forward_validation_report(
        experiment_name="walk_forward_validation",
        metadata={"snapshot_count": 1, "window_count": 1},
        experiment={
            "windows": [
                {
                    "window_index": 1,
                    "out_of_sample": {
                        "scorecard": {
                            "total_return": 0.03,
                            "trade_count": 2,
                            "coverage_ratio": 1.0,
                            "data_coverage_pct": 100,
                            "sample_coverage_pct": 75,
                            "trade_coverage_pct": 80,
                            "signal_coverage_pct": 85,
                            "regime_coverage_pct": 90,
                            "fill_coverage_pct": 95,
                            "universe_coverage_pct": 100,
                            "benchmark_coverage_pct": 0,
                            "execution_coverage_pct": 65,
                        },
                        "run_ids": ["row-002"],
                    },
                }
            ],
            "robustness_summary": {
                "out_of_sample_scorecard": {
                    "total_return": 0.03,
                    "trade_count": 2,
                    "coverage_ratio": 0.0,
                    "data_coverage_pct": 0,
                },
                "performance_dispersion": {"positive_window_ratio": 1.0},
            },
            "parameter_stability": {"parameter_stability_score": 0.8},
        },
    )

    window_scorecard = report["windows"]["rows"][0]["out_of_sample"]["scorecard"]
    assert window_scorecard["coverage_ratio"] == pytest.approx(1.0)
    assert window_scorecard["data_coverage_pct"] == 100
    assert window_scorecard["sample_coverage_pct"] == 75
    assert window_scorecard["trade_coverage_pct"] == 80
    assert window_scorecard["signal_coverage_pct"] == 85
    assert window_scorecard["regime_coverage_pct"] == 90
    assert window_scorecard["fill_coverage_pct"] == 95
    assert window_scorecard["universe_coverage_pct"] == 100
    assert window_scorecard["benchmark_coverage_pct"] == 0
    assert window_scorecard["execution_coverage_pct"] == 65
    summary_scorecard = report["summary"]["robustness_summary"]["out_of_sample_scorecard"]
    assert summary_scorecard["coverage_ratio"] == pytest.approx(0.0)
    assert summary_scorecard["data_coverage_pct"] == 0


def test_walk_forward_validation_report_rejects_blank_window_run_id() -> None:
    with pytest.raises(ValueError, match=r"windows\[0\]\.out_of_sample\.run_ids\[\] must be a canonical string"):
        cli.render_walk_forward_validation_report(
            experiment_name="walk_forward_validation",
            metadata={"snapshot_count": 1, "window_count": 1},
            experiment={
                "windows": [
                    {
                        "window_index": 1,
                        "out_of_sample": {
                            "scorecard": {"total_return": 0.03, "trade_count": 1},
                            "run_ids": ["row-002", " "],
                        },
                    }
                ],
                "robustness_summary": {
                    "out_of_sample_scorecard": {"total_return": 0.03},
                    "performance_dispersion": {"positive_window_ratio": 1.0},
                },
                "parameter_stability": {"parameter_stability_score": 0.8},
            },
        )



def test_engine_filter_ablation_report_rejects_invalid_metadata_counts() -> None:
    with pytest.raises(ValueError, match="metadata.snapshot_count must be a non-negative integer"):
        cli.render_engine_filter_ablation_report(
            experiment_name="engine_filter_ablation",
            metadata={"snapshot_count": True},
            experiment={
                "variants": {
                    "trend_only": {
                        "funnel": {"accepted_allocations": 1},
                        "performance": {"bucket_level_pnl": 0.01},
                    }
                }
            },
        )



def test_engine_filter_ablation_report_rejects_non_object_variants() -> None:
    with pytest.raises(ValueError, match="variants must be an object"):
        cli.render_engine_filter_ablation_report(
            experiment_name="engine_filter_ablation",
            metadata={"snapshot_count": 1},
            experiment={"variants": []},
        )



def test_engine_filter_ablation_report_rejects_non_object_variant_payloads() -> None:
    with pytest.raises(ValueError, match="variants.trend_only must be an object"):
        cli.render_engine_filter_ablation_report(
            experiment_name="engine_filter_ablation",
            metadata={"snapshot_count": 1},
            experiment={"variants": {"trend_only": []}},
        )



def test_engine_filter_ablation_report_rejects_boolean_variant_metrics() -> None:
    with pytest.raises(ValueError, match="variants.trend_only.metric must be a finite number"):
        cli.render_engine_filter_ablation_report(
            experiment_name="engine_filter_ablation",
            metadata={"snapshot_count": 1},
            experiment={
                "variants": {
                    "trend_only": {
                        "funnel": {"accepted_allocations": 1},
                        "performance": {"bucket_level_pnl": True},
                    }
                }
            },
        )



def test_engine_filter_ablation_report_rejects_boolean_accepted_allocations() -> None:
    with pytest.raises(ValueError, match="variants.trend_only.funnel.accepted_allocations must be a non-negative integer"):
        cli.render_engine_filter_ablation_report(
            experiment_name="engine_filter_ablation",
            metadata={"snapshot_count": 1},
            experiment={
                "variants": {
                    "trend_only": {
                        "funnel": {"accepted_allocations": True},
                        "performance": {"bucket_level_pnl": 0.01},
                    }
                }
            },
        )


def test_engine_filter_ablation_report_rejects_list_coerced_performance_payload() -> None:
    with pytest.raises(ValueError, match="variants.trend_only.performance must be an object"):
        cli.render_engine_filter_ablation_report(
            experiment_name="engine_filter_ablation",
            metadata={"snapshot_count": 1},
            experiment={
                "variants": {
                    "trend_only": {
                        "funnel": {"accepted_allocations": 1},
                        "performance": [("bucket_level_pnl", 0.01)],
                    }
                }
            },
        )


def _rotation_suppression_contract_experiment() -> dict[str, object]:
    return {
        "policies": {
            "current": {"bucket_level_pnl": 0.01},
            "soft_suppression": {"bucket_level_pnl": 0.02},
            "no_suppression": {"bucket_level_pnl": 0.0},
        },
        "opportunity_kill_rate": 0.1,
        "avoid_loss_rate": 0.2,
    }


def _allocator_friction_contract_experiment() -> dict[str, object]:
    return {
        "variants": {
            "current_allocator": {
                "frictions": {
                    "base": {"net_bucket_pnl": 0.01, "cost_drag": 0.005},
                    "stressed": {"net_bucket_pnl": 0.01},
                },
            },
            "low_friction": {
                "frictions": {
                    "base": {"net_bucket_pnl": 0.02, "cost_drag": 0.005},
                    "stressed": {"net_bucket_pnl": 0.01},
                },
            },
        }
    }


def test_rotation_suppression_report_rejects_non_object_policies() -> None:
    experiment = _rotation_suppression_contract_experiment()
    experiment["policies"] = [("current", {"bucket_level_pnl": 0.01})]

    with pytest.raises(ValueError, match="policies must be an object"):
        cli.render_rotation_suppression_report(
            experiment_name="rotation_suppression",
            metadata={"snapshot_count": 1},
            experiment=experiment,
        )


def test_allocator_friction_report_rejects_non_canonical_variant_name() -> None:
    experiment = _allocator_friction_contract_experiment()
    experiment["variants"] = {
        "current_allocator": {
            "frictions": {
                "base": {"net_bucket_pnl": 0.01, "cost_drag": 0.005},
                "stressed": {"net_bucket_pnl": 0.01},
            },
        },
        " low_friction": {
            "frictions": {
                "base": {"net_bucket_pnl": 0.02, "cost_drag": 0.005},
                "stressed": {"net_bucket_pnl": 0.01},
            },
        },
    }

    with pytest.raises(ValueError, match="variant names must be canonical strings"):
        cli.render_allocator_friction_report(
            experiment_name="allocator_friction",
            metadata={"snapshot_count": 1},
            experiment=experiment,
        )


def test_allocator_friction_report_rejects_non_object_current_allocator_frictions() -> None:
    experiment = _allocator_friction_contract_experiment()
    variants = experiment["variants"]
    assert isinstance(variants, dict)
    current_allocator = variants["current_allocator"]
    assert isinstance(current_allocator, dict)
    current_allocator["frictions"] = [("base", {"net_bucket_pnl": 0.01, "cost_drag": 0.005})]

    with pytest.raises(ValueError, match="variants.current_allocator.frictions must be an object"):
        cli.render_allocator_friction_report(
            experiment_name="allocator_friction",
            metadata={"snapshot_count": 1},
            experiment=experiment,
        )


def test_allocator_friction_report_rejects_non_object_current_allocator_base_frictions() -> None:
    experiment = _allocator_friction_contract_experiment()
    variants = experiment["variants"]
    assert isinstance(variants, dict)
    current_allocator = variants["current_allocator"]
    assert isinstance(current_allocator, dict)
    frictions = current_allocator["frictions"]
    assert isinstance(frictions, dict)
    frictions["base"] = [("net_bucket_pnl", 0.01), ("cost_drag", 0.005)]

    with pytest.raises(ValueError, match="variants.current_allocator.frictions.base must be an object"):
        cli.render_allocator_friction_report(
            experiment_name="allocator_friction",
            metadata={"snapshot_count": 1},
            experiment=experiment,
        )



def test_rotation_suppression_report_rejects_boolean_policy_pnl() -> None:
    with pytest.raises(ValueError, match="policies.current.bucket_level_pnl must be a finite number"):
        cli.render_rotation_suppression_report(
            experiment_name="rotation_suppression",
            metadata={"snapshot_count": 1},
            experiment={
                "policies": {
                    "current": {"bucket_level_pnl": True},
                    "soft_suppression": {"bucket_level_pnl": 0.02},
                    "no_suppression": {"bucket_level_pnl": 0.0},
                },
                "opportunity_kill_rate": 0.1,
                "avoid_loss_rate": 0.2,
            },
        )


def test_rotation_suppression_report_rejects_string_policy_pnl() -> None:
    with pytest.raises(ValueError, match="policies.soft_suppression.bucket_level_pnl must be a finite number"):
        cli.render_rotation_suppression_report(
            experiment_name="rotation_suppression",
            metadata={"snapshot_count": 1},
            experiment={
                "policies": {
                    "current": {"bucket_level_pnl": 0.01},
                    "soft_suppression": {"bucket_level_pnl": "0.02"},
                    "no_suppression": {"bucket_level_pnl": 0.0},
                },
                "opportunity_kill_rate": 0.1,
                "avoid_loss_rate": 0.2,
            },
        )


def test_rotation_suppression_report_rejects_missing_current_policy() -> None:
    with pytest.raises(ValueError, match="policies.current must be an object"):
        cli.render_rotation_suppression_report(
            experiment_name="rotation_suppression",
            metadata={"snapshot_count": 1},
            experiment={
                "policies": {
                    "soft_suppression": {"bucket_level_pnl": 0.02},
                    "no_suppression": {"bucket_level_pnl": 0.0},
                },
                "opportunity_kill_rate": 0.1,
                "avoid_loss_rate": 0.2,
            },
        )


def test_rotation_suppression_report_rejects_non_string_policy_key() -> None:
    with pytest.raises(ValueError, match="policies keys must be a canonical string"):
        cli.render_rotation_suppression_report(
            experiment_name="rotation_suppression",
            metadata={"snapshot_count": 1},
            experiment={
                "policies": {
                    "current": {"bucket_level_pnl": 0.01},
                    "soft_suppression": {"bucket_level_pnl": 0.02},
                    "no_suppression": {"bucket_level_pnl": 0.0},
                    1: {"bucket_level_pnl": 0.03},
                },
                "opportunity_kill_rate": 0.1,
                "avoid_loss_rate": 0.2,
            },
        )


def test_rotation_suppression_report_rejects_string_opportunity_kill_rate() -> None:
    with pytest.raises(ValueError, match="opportunity_kill_rate must be a finite number"):
        cli.render_rotation_suppression_report(
            experiment_name="rotation_suppression",
            metadata={"snapshot_count": 1},
            experiment={
                "policies": {
                    "current": {"bucket_level_pnl": 0.01},
                    "soft_suppression": {"bucket_level_pnl": 0.02},
                    "no_suppression": {"bucket_level_pnl": 0.0},
                },
                "opportunity_kill_rate": "0.1",
                "avoid_loss_rate": 0.2,
            },
        )


def test_rotation_suppression_report_rejects_non_list_comparison_rows() -> None:
    with pytest.raises(ValueError, match="rotation_comparison_rows must be a list"):
        cli.render_rotation_suppression_report(
            experiment_name="rotation_suppression",
            metadata={"snapshot_count": 1},
            experiment={
                "policies": {
                    "current": {"bucket_level_pnl": 0.01},
                    "soft_suppression": {"bucket_level_pnl": 0.02},
                    "no_suppression": {"bucket_level_pnl": 0.0},
                },
                "opportunity_kill_rate": 0.1,
                "avoid_loss_rate": 0.2,
                "rotation_comparison_rows": "accepted",
            },
        )


def test_rotation_suppression_report_rejects_non_object_comparison_row() -> None:
    with pytest.raises(ValueError, match=r"rotation_comparison_rows\[0\] must be an object"):
        cli.render_rotation_suppression_report(
            experiment_name="rotation_suppression",
            metadata={"snapshot_count": 1},
            experiment={
                "policies": {
                    "current": {"bucket_level_pnl": 0.01},
                    "soft_suppression": {"bucket_level_pnl": 0.02},
                    "no_suppression": {"bucket_level_pnl": 0.0},
                },
                "opportunity_kill_rate": 0.1,
                "avoid_loss_rate": 0.2,
                "rotation_comparison_rows": ["accepted"],
            },
        )


def test_allocator_friction_report_rejects_boolean_current_base_cost_drag() -> None:
    with pytest.raises(ValueError, match="variants.current_allocator.frictions.base.cost_drag must be a finite number"):
        cli.render_allocator_friction_report(
            experiment_name="allocator_friction",
            metadata={"snapshot_count": 1},
            experiment={
                "variants": {
                    "current_allocator": {
                        "frictions": {
                            "base": {"net_bucket_pnl": 0.01, "cost_drag": True},
                            "stressed": {"net_bucket_pnl": 0.01},
                        },
                    },
                    "low_friction": {
                        "frictions": {
                            "base": {"net_bucket_pnl": 0.02, "cost_drag": 0.005},
                            "stressed": {"net_bucket_pnl": 0.01},
                        },
                    },
                }
            },
        )


def test_allocator_friction_report_rejects_string_current_base_cost_drag() -> None:
    with pytest.raises(ValueError, match="variants.current_allocator.frictions.base.cost_drag must be a finite number"):
        cli.render_allocator_friction_report(
            experiment_name="allocator_friction",
            metadata={"snapshot_count": 1},
            experiment={
                "variants": {
                    "current_allocator": {
                        "frictions": {
                            "base": {"net_bucket_pnl": 0.01, "cost_drag": "0.005"},
                            "stressed": {"net_bucket_pnl": 0.01},
                        },
                    },
                    "low_friction": {
                        "frictions": {
                            "base": {"net_bucket_pnl": 0.02, "cost_drag": 0.005},
                            "stressed": {"net_bucket_pnl": 0.01},
                        },
                    },
                }
            },
        )


def test_allocator_friction_report_rejects_boolean_best_stressed_net_bucket_pnl() -> None:
    with pytest.raises(
        ValueError,
        match="variants.low_friction.frictions.stressed.net_bucket_pnl must be a finite number",
    ):
        cli.render_allocator_friction_report(
            experiment_name="allocator_friction",
            metadata={"snapshot_count": 1},
            experiment={
                "variants": {
                    "current_allocator": {
                        "frictions": {
                            "base": {"net_bucket_pnl": 0.01, "cost_drag": 0.005},
                            "stressed": {"net_bucket_pnl": 0.01},
                        },
                    },
                    "low_friction": {
                        "frictions": {
                            "base": {"net_bucket_pnl": 0.02, "cost_drag": 0.005},
                            "stressed": {"net_bucket_pnl": True},
                        },
                    },
                }
            },
        )


def test_allocator_friction_report_rejects_string_best_stressed_net_bucket_pnl() -> None:
    with pytest.raises(
        ValueError,
        match="variants.low_friction.frictions.stressed.net_bucket_pnl must be a finite number",
    ):
        cli.render_allocator_friction_report(
            experiment_name="allocator_friction",
            metadata={"snapshot_count": 1},
            experiment={
                "variants": {
                    "current_allocator": {
                        "frictions": {
                            "base": {"net_bucket_pnl": 0.01, "cost_drag": 0.005},
                            "stressed": {"net_bucket_pnl": 0.01},
                        },
                    },
                    "low_friction": {
                        "frictions": {
                            "base": {"net_bucket_pnl": 0.02, "cost_drag": 0.005},
                            "stressed": {"net_bucket_pnl": "0.01"},
                        },
                    },
                }
            },
        )


def test_allocator_friction_report_rejects_string_best_base_net_bucket_pnl() -> None:
    with pytest.raises(
        ValueError,
        match="variants.low_friction.frictions.base.net_bucket_pnl must be a finite number",
    ):
        cli.render_allocator_friction_report(
            experiment_name="allocator_friction",
            metadata={"snapshot_count": 1},
            experiment={
                "variants": {
                    "current_allocator": {
                        "frictions": {
                            "base": {"net_bucket_pnl": 0.01, "cost_drag": 0.005},
                            "stressed": {"net_bucket_pnl": 0.01},
                        },
                    },
                    "low_friction": {
                        "frictions": {
                            "base": {"net_bucket_pnl": "0.02", "cost_drag": 0.005},
                            "stressed": {"net_bucket_pnl": 0.01},
                        },
                    },
                }
            },
        )


def test_allocator_friction_report_rejects_missing_variant_base_frictions() -> None:
    with pytest.raises(
        ValueError,
        match="variants.low_friction.frictions.base must be an object",
    ):
        cli.render_allocator_friction_report(
            experiment_name="allocator_friction",
            metadata={"snapshot_count": 1},
            experiment={
                "variants": {
                    "current_allocator": {
                        "frictions": {
                            "base": {"net_bucket_pnl": 0.01, "cost_drag": 0.005},
                            "stressed": {"net_bucket_pnl": 0.01},
                        },
                    },
                    "low_friction": {
                        "frictions": {
                            "stressed": {"net_bucket_pnl": 0.01},
                        },
                    },
                }
            },
        )


def test_allocator_friction_report_rejects_list_coerced_variants_map() -> None:
    with pytest.raises(ValueError, match="variants must be an object"):
        cli.render_allocator_friction_report(
            experiment_name="allocator_friction",
            metadata={"snapshot_count": 1},
            experiment={
                "variants": [
                    (
                        "current_allocator",
                        {
                            "frictions": {
                                "base": {"net_bucket_pnl": 0.01, "cost_drag": 0.005},
                                "stressed": {"net_bucket_pnl": 0.01},
                            },
                        },
                    ),
                    (
                        "low_friction",
                        {
                            "frictions": {
                                "base": {"net_bucket_pnl": 0.02, "cost_drag": 0.005},
                                "stressed": {"net_bucket_pnl": 0.01},
                            },
                        },
                    ),
                ]
            },
        )


def test_allocator_friction_report_rejects_non_list_comparison_rows() -> None:
    with pytest.raises(ValueError, match="comparison_rows must be a list"):
        cli.render_allocator_friction_report(
            experiment_name="allocator_friction",
            metadata={"snapshot_count": 1},
            experiment={
                "variants": {
                    "current_allocator": {
                        "frictions": {
                            "base": {"net_bucket_pnl": 0.01, "cost_drag": 0.005},
                            "stressed": {"net_bucket_pnl": 0.01},
                        },
                    }
                },
                "comparison_rows": "accepted",
            },
        )


def test_allocator_friction_report_rejects_non_object_comparison_row() -> None:
    with pytest.raises(ValueError, match=r"comparison_rows\[0\] must be an object"):
        cli.render_allocator_friction_report(
            experiment_name="allocator_friction",
            metadata={"snapshot_count": 1},
            experiment={
                "variants": {
                    "current_allocator": {
                        "frictions": {
                            "base": {"net_bucket_pnl": 0.01, "cost_drag": 0.005},
                            "stressed": {"net_bucket_pnl": 0.01},
                        },
                    }
                },
                "comparison_rows": ["accepted"],
            },
        )


def test_backtest_cli_writes_walk_forward_validation_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: _sample_dataset_rows(), raising=False)
    monkeypatch.setattr(
        cli,
        "run_walk_forward_validation_experiment",
        lambda rows, *, evaluation_window, in_sample_size, out_of_sample_size, step_size, config=None: {
            "metadata": {
                "snapshot_count": 2,
                "window_count": 1,
                "evaluation_window": evaluation_window,
                "in_sample_size": in_sample_size,
                "out_of_sample_size": out_of_sample_size,
                "step_size": step_size,
            },
            "windows": [
                {
                    "window_index": 1,
                    "out_of_sample": {
                        "scorecard": {"total_return": 0.03, "trade_count": 1},
                        "run_ids": ["row-002"],
                    },
                }
            ],
            "robustness_summary": {
                "out_of_sample_scorecard": {"total_return": 0.03, "trade_count": 1},
                "performance_dispersion": {"positive_window_ratio": 1.0},
            },
            "parameter_stability": {"parameter_stability_score": 0.8},
        },
        raising=False,
    )

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(_write_experiment_fixture_config(tmp_path, "walk_forward_validation_config.json")),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "walk_forward_validation__current_policy__rolling_walk_forward"
    assert exit_code == 0
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "windows.json").exists()
    assert (bundle_dir / "scorecard.json").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "windows.json",
        "scorecard.json",
    ]
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["metadata"]["evaluation_window"] == "3d"
    assert scorecard["key_metrics"]["snapshot_count"] == 2
    assert scorecard["decision_summary"]["decision"] in {
        "keep_researching",
        "candidate_for_promotion",
        "reject",
    }

@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("supported_factor_count", True),
        ("unsupported_factor_count", "1"),
        ("effective_factor_count", 1.5),
        ("data_gap_count", -1),
        ("evaluated_factor_count", float("nan")),
    ],
)
def test_public_strategy_factor_report_rejects_invalid_summary_counts(field: str, value: object) -> None:
    summary = {
        "supported_factor_count": 2,
        "unsupported_factor_count": 1,
        "data_gap_count": 1,
        "evaluated_factor_count": 2,
        "effective_factor_count": 1,
    }
    summary[field] = value

    with pytest.raises(ValueError, match=rf"summary\.{field}"):
        reporting.render_public_strategy_factor_report(
            experiment_name="public_strategy_factors",
            experiment={"summary": summary, "factors": []},
            metadata={"snapshot_count": 8},
        )


def test_public_strategy_factor_report_rejects_invalid_effectiveness_sample_counts() -> None:
    with pytest.raises(ValueError, match="effectiveness.sample_count must be a non-negative integer"):
        reporting.render_public_strategy_factor_report(
            experiment_name="public_strategy_factors",
            experiment={
                "summary": {
                    "supported_factor_count": 1,
                    "unsupported_factor_count": 0,
                    "data_gap_count": 0,
                    "evaluated_factor_count": 1,
                    "effective_factor_count": 1,
                },
                "factors": [
                    {
                        "source_strategy_family": "momentum",
                        "factor_name": "momentum_3d",
                        "supported": True,
                        "effectiveness": {
                            "effectiveness_status": "promising_research",
                            "sample_count": True,
                            "minimum_sample_count": 1,
                            "information_coefficient": 0.3,
                            "top_minus_bottom_forward_return": 0.02,
                            "top_bucket_hit_rate": 0.6,
                        },
                    }
                ],
            },
            metadata={"snapshot_count": 1},
        )


def test_public_strategy_factor_report_rejects_invalid_effectiveness_numeric_metrics() -> None:
    with pytest.raises(ValueError, match="effectiveness.information_coefficient must be a finite number"):
        reporting.render_public_strategy_factor_report(
            experiment_name="public_strategy_factors",
            experiment={
                "summary": {
                    "supported_factor_count": 1,
                    "unsupported_factor_count": 0,
                    "data_gap_count": 0,
                    "evaluated_factor_count": 1,
                    "effective_factor_count": 1,
                },
                "factors": [
                    {
                        "source_strategy_family": "momentum",
                        "factor_name": "momentum_3d",
                        "supported": True,
                        "effectiveness": {
                            "effectiveness_status": "promising_research",
                            "sample_count": 2,
                            "minimum_sample_count": 1,
                            "information_coefficient": True,
                            "top_minus_bottom_forward_return": 0.02,
                            "top_bucket_hit_rate": 0.6,
                        },
                    }
                ],
            },
            metadata={"snapshot_count": 1},
        )


def test_public_strategy_factor_report_rejects_string_effectiveness_numeric_metrics() -> None:
    with pytest.raises(ValueError, match="effectiveness.information_coefficient must be a finite number"):
        reporting.render_public_strategy_factor_report(
            experiment_name="public_strategy_factors",
            experiment={
                "summary": {
                    "supported_factor_count": 1,
                    "unsupported_factor_count": 0,
                    "data_gap_count": 0,
                    "evaluated_factor_count": 1,
                    "effective_factor_count": 1,
                },
                "factors": [
                    {
                        "source_strategy_family": "momentum",
                        "factor_name": "momentum_3d",
                        "supported": True,
                        "effectiveness": {
                            "effectiveness_status": "promising_research",
                            "sample_count": 2,
                            "minimum_sample_count": 1,
                            "information_coefficient": "0.3",
                            "top_minus_bottom_forward_return": 0.02,
                            "top_bucket_hit_rate": 0.6,
                        },
                    }
                ],
            },
            metadata={"snapshot_count": 1},
        )


def test_public_strategy_factor_report_rejects_non_string_factor_keys() -> None:
    with pytest.raises(ValueError, match=r"factors\[0\] key must be a string"):
        reporting.render_public_strategy_factor_report(
            experiment_name="public_strategy_factors",
            experiment={
                "summary": {
                    "supported_factor_count": 1,
                    "unsupported_factor_count": 0,
                    "data_gap_count": 0,
                    "evaluated_factor_count": 1,
                    "effective_factor_count": 1,
                },
                "factors": [{1: "bad", "effectiveness": {}}],
            },
            metadata={"snapshot_count": 1},
        )


def test_public_strategy_factor_report_rejects_list_of_pairs_factor_payload() -> None:
    with pytest.raises(ValueError, match=r"factors\[0\] must be an object"):
        reporting.render_public_strategy_factor_report(
            experiment_name="public_strategy_factors",
            experiment={
                "summary": {
                    "supported_factor_count": 1,
                    "unsupported_factor_count": 0,
                    "data_gap_count": 0,
                    "evaluated_factor_count": 1,
                    "effective_factor_count": 1,
                },
                "factors": [[("factor_name", "momentum_3d"), ("effectiveness", {})]],
            },
            metadata={"snapshot_count": 1},
        )


def test_public_strategy_factor_report_rejects_non_string_effectiveness_keys() -> None:
    with pytest.raises(ValueError, match=r"factors\[0\]\.effectiveness key must be a string"):
        reporting.render_public_strategy_factor_report(
            experiment_name="public_strategy_factors",
            experiment={
                "summary": {
                    "supported_factor_count": 1,
                    "unsupported_factor_count": 0,
                    "data_gap_count": 0,
                    "evaluated_factor_count": 1,
                    "effective_factor_count": 1,
                },
                "factors": [
                    {
                        "source_strategy_family": "momentum",
                        "factor_name": "momentum_3d",
                        "supported": True,
                        "effectiveness": {1: "bad"},
                    }
                ],
            },
            metadata={"snapshot_count": 1},
        )


def test_public_strategy_factor_report_rejects_list_of_pairs_summary_payload() -> None:
    with pytest.raises(ValueError, match="summary must be an object"):
        reporting.render_public_strategy_factor_report(
            experiment_name="public_strategy_factors",
            experiment={
                "summary": [
                    ("supported_factor_count", 1),
                    ("unsupported_factor_count", 0),
                    ("data_gap_count", 0),
                    ("evaluated_factor_count", 1),
                    ("effective_factor_count", 1),
                ],
                "factors": [],
            },
            metadata={"snapshot_count": 1},
        )


def test_public_strategy_factor_report_rejects_invalid_effectiveness_snapshot_fallback() -> None:
    with pytest.raises(ValueError, match="metadata.snapshot_count must be a non-negative integer"):
        reporting.render_public_strategy_factor_report(
            experiment_name="public_strategy_factors",
            experiment={
                "summary": {
                    "supported_factor_count": 0,
                    "unsupported_factor_count": 0,
                    "data_gap_count": 0,
                    "evaluated_factor_count": 0,
                    "effective_factor_count": 0,
                },
                "factors": [],
            },
            metadata={"snapshot_count": True},
        )


def test_public_strategy_factor_report_surfaces_effectiveness_counts() -> None:
    report = reporting.render_public_strategy_factor_report(
        experiment_name="public_strategy_factors",
        experiment={
            "summary": {
                "supported_factor_count": 2,
                "unsupported_factor_count": 1,
                "data_gap_count": 1,
                "evaluated_factor_count": 2,
                "effective_factor_count": 1,
            },
            "factors": [
                {
                    "source_strategy_family": "momentum",
                    "factor_name": "momentum_3d",
                    "supported": True,
                    "effectiveness": {"effectiveness_status": "promising_research", "information_coefficient": 0.9},
                }
            ],
        },
        metadata={
            "snapshot_count": 8,
            "evaluation_window": "3d",
            "baseline_name": "public_strategy_scan",
            "variant_name": "factor_catalog_v1",
        },
    )

    assert report["scorecard"]["key_metrics"]["evaluated_factor_count"] == 2
    assert report["scorecard"]["key_metrics"]["effective_factor_count"] == 1
    assert report["scorecard"]["decision_summary"]["decision"] == "keep_researching"


def test_backtest_cli_writes_public_strategy_factor_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rows = [
        DatasetSnapshotRow(
            timestamp=_ts("2026-03-10T00:00:00Z"),
            run_id="row-001",
            market={"symbols": {"BTCUSDT": {"close": 100.0}}},
            derivatives=[{"symbol": "BTCUSDT", "funding_rate": 0.0001}],
            account={"equity": 100_000.0},
            forward_returns={"3d": 0.04},
            forward_drawdowns={"3d": -0.03},
        ),
        DatasetSnapshotRow(
            timestamp=_ts("2026-03-12T00:00:00Z"),
            run_id="row-002",
            market={"symbols": {"ETHUSDT": {"close": 110.0}}},
            derivatives=[],
            account={"equity": 100_000.0},
            forward_returns={"3d": -0.02},
            forward_drawdowns={"3d": -0.05},
        ),
    ]
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: rows, raising=False)

    exit_code = cli.main([
        "run",
        "--config",
        str(_write_experiment_fixture_config(tmp_path, "public_strategy_factors_config.json")),
        "--output-dir",
        str(tmp_path / "out"),
    ])

    bundle_dir = tmp_path / "out" / "public_strategy_factors__public_strategy_scan__factor_catalog_v1"
    assert exit_code == 0
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "factor_catalog.json").exists()
    assert (bundle_dir / "scorecard.json").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "factor_catalog.json",
        "scorecard.json",
    ]
    catalog = json.loads((bundle_dir / "factor_catalog.json").read_text(encoding="utf-8"))
    factors = {item["factor_name"]: item for item in catalog["factors"]}
    assert factors["momentum_3d"]["source_strategy_family"] == "momentum"
    assert factors["momentum_3d"]["supported"] is True
    assert factors["funding_basis"]["supported"] is False
    assert factors["funding_basis"]["unsupported_reason"] == "insufficient_funding_or_basis_fields"

    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["metadata"]["evaluation_window"] == "3d"
    assert scorecard["key_metrics"]["snapshot_count"] == 2
    assert scorecard["key_metrics"]["supported_factor_count"] >= 2
    assert scorecard["decision_summary"]["decision"] == "keep_researching"


def test_public_strategy_factor_cli_surfaces_flat_tiny_sample_effectiveness_fields(
    fixture_dir: Path, tmp_path: Path
) -> None:
    exit_code = cli.main(
        [
            "run",
            "--config",
            str(fixture_dir / "backtest" / "public_strategy_factors_config.json"),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "public_strategy_factors__public_strategy_scan__factor_catalog_v1"
    assert exit_code == 0
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "scorecard.json").exists()
    assert (bundle_dir / "factor_catalog.json").exists()

    summary = json.loads((bundle_dir / "summary.json").read_text(encoding="utf-8"))
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    catalog = json.loads((bundle_dir / "factor_catalog.json").read_text(encoding="utf-8"))

    assert summary["sample_count"] == 3
    assert summary["minimum_sample_count"] == 30
    assert summary["effective_factor_count"] == 0
    assert summary["decision"] == "keep_researching"
    assert scorecard["decision_summary"]["decision"] == "keep_researching"

    evaluated_tiny_sample_factors = [
        factor
        for factor in catalog["factors"]
        if factor["supported"] and factor["sample_count"] == 3
    ]
    assert evaluated_tiny_sample_factors
    for factor in evaluated_tiny_sample_factors:
        assert factor["effectiveness_status"] == "insufficient_sample"
        assert factor["sample_count"] == 3
        assert (
            "information_coefficient" in factor or "rank_correlation" in factor
        )
        assert "top_minus_bottom_forward_return" in factor
        assert "top_bucket_hit_rate" in factor


def test_public_strategy_factor_cli_generates_config_for_imported_dataset_root(tmp_path: Path) -> None:
    dataset_root = tmp_path / "imported_dataset"
    config_path = tmp_path / "public_strategy_factors_real_history.json"
    output_dir = tmp_path / "out"
    _write_imported_public_strategy_dataset(dataset_root)

    generate_exit_code = cli.main(
        [
            "write-public-strategy-factors-config",
            "--dataset-root",
            str(dataset_root),
            "--output-config",
            str(config_path),
            "--minimum-effectiveness-sample-count",
            "4",
        ]
    )
    run_exit_code = cli.main(["run", "--config", str(config_path), "--output-dir", str(output_dir)])

    bundle_dir = output_dir / "public_strategy_factors__public_strategy_scan__factor_catalog_v1"
    assert generate_exit_code == 0
    assert run_exit_code == 0
    generated = json.loads(config_path.read_text(encoding="utf-8"))
    assert generated["dataset_root"] == str(dataset_root)
    assert generated["sample_windows"] == [
        {
            "name": "imported_history",
            "start": "2025-01-01T00:00:00Z",
            "end": "2025-01-04T00:00:00Z",
            "split": "in_sample",
        }
    ]

    summary = json.loads((bundle_dir / "summary.json").read_text(encoding="utf-8"))
    catalog = json.loads((bundle_dir / "factor_catalog.json").read_text(encoding="utf-8"))
    assert summary["metadata"]["snapshot_count"] == 4
    assert summary["metadata"]["imported_dataset"]["import_manifest"]["manifest_snapshot_count"] == 4
    assert summary["metadata"]["minimum_effectiveness_sample_count"] == 4
    assert catalog["metadata"]["imported_dataset"]["dataset_root_type"] == "imported_archive"
    momentum = next(item for item in catalog["factors"] if item["factor_name"] == "momentum_3d")
    assert momentum["effectiveness"]["sample_count"] == 4
    assert momentum["effectiveness"]["minimum_sample_count"] == 4
    assert momentum["effectiveness"]["effectiveness_status"] == "promising_research"


def test_full_market_baseline_runbook_documents_required_inputs_outputs_and_limitations() -> None:
    runbook = Path("trading_system/docs/BACKTEST_RUNBOOK.md").read_text(encoding="utf-8")

    assert "instrument_snapshot.json" in runbook
    assert "full_market_baseline" in runbook
    assert "listing_age_days" in runbook
    assert "min_quote_volume_usdt_24h" in runbook
    assert "risk_per_trade" in runbook
    assert "max_open_risk" in runbook
    assert "manifest.json" in runbook
    assert "summary.json" in runbook
    assert "breakdowns.json" in runbook
    assert "audit.json" in runbook
    assert "rejection_reasons" in runbook
    assert "parameter search" in runbook
    assert "walk-forward" in runbook
    assert "order-book simulation" in runbook



def test_backtest_docs_cover_run_compare_gate_and_roadmap() -> None:
    runbook = Path("trading_system/docs/BACKTEST_RUNBOOK.md").read_text(encoding="utf-8")
    promotion_gate = Path("trading_system/docs/BACKTEST_PROMOTION_GATE.md").read_text(encoding="utf-8")
    roadmap = Path("trading_system/docs/BACKTEST_ROADMAP.md").read_text(encoding="utf-8")

    assert "python -m trading_system.app.backtest.cli run" in runbook
    assert "python -m trading_system.app.backtest.cli compare" in runbook
    assert "promotion_gate.json" in runbook
    assert "decision_summary.json" in runbook
    assert "scorecard.json" in runbook
    assert "什么时候允许进入“改交易程序”阶段" in runbook or "什么时候允许改交易程序" in runbook
    assert "必须继续研究，不准改源码" in runbook or "继续研究，不进入主线" in runbook
    assert "rotation_suppression" in runbook
    assert "allocator_friction" in runbook
    assert "engine_filter_ablation" in runbook
    assert "walk_forward_validation" in runbook

    assert '"decision": "hold"' in promotion_gate
    assert '"has_runtime_observability_plan": false' in promotion_gate
    assert '"has_rollback_plan": false' in promotion_gate
    assert '"why": ["missing out-of-sample evidence"]' in promotion_gate

    assert "已实现 experiment" in roadmap
    assert "已接入 CLI" in roadmap
    assert "已接入 gate" in roadmap
    assert "rotation suppression" in roadmap
    assert "allocator" in roadmap
    assert "walk-forward" in roadmap

def test_render_llm_trend_breakout_report_builds_summary_candidate_rows_and_scorecard() -> None:
    experiment = {
        "summary": {
            "snapshot_count": 2,
            "technical_candidate_count": 2,
            "accepted_candidate_count": 1,
            "rejected_candidate_count": 1,
            "acceptance_rate": 0.5,
            "rejection_reasons": {"high_event_risk": 1},
        },
        "candidate_rows": [
            {"symbol": "SOLUSDT", "decision": "accepted", "reasons": ["llm_filter_passed"]},
            {"symbol": "BTCUSDT", "decision": "rejected", "reasons": ["high_event_risk"]},
        ],
    }

    report = reporting.render_llm_trend_breakout_report(
        experiment_name="llm_trend_breakout",
        experiment=experiment,
        metadata={
            "dataset_root": "sample_dataset",
            "baseline_name": "trend-breakout",
            "variant_name": "llm-filtered",
            "evaluation_window": "1d",
            "snapshot_count": 2,
        },
    )

    assert set(report) == {"summary", "candidate_rows", "scorecard"}
    assert report["summary"]["summary"]["accepted_candidate_count"] == 1
    assert report["candidate_rows"]["rows"] == experiment["candidate_rows"]
    assert report["scorecard"]["key_metrics"] == {
        "snapshot_count": 2,
        "technical_candidate_count": 2,
        "accepted_candidate_count": 1,
        "rejected_candidate_count": 1,
        "acceptance_rate": 0.5,
    }
    assert report["scorecard"]["decision_summary"]["decision"] == "keep_researching"


def test_render_llm_trend_breakout_report_rejects_invalid_candidate_rows_shape() -> None:
    with pytest.raises(ValueError, match="candidate_rows must be a list"):
        reporting.render_llm_trend_breakout_report(
            experiment_name="llm_trend_breakout",
            experiment={
                "summary": {
                    "technical_candidate_count": 2,
                    "accepted_candidate_count": 1,
                    "rejected_candidate_count": 1,
                    "acceptance_rate": 0.5,
                },
                "candidate_rows": "not-a-list",
            },
            metadata={"snapshot_count": 2},
        )


def test_render_llm_trend_breakout_report_rejects_string_candidate_row_score() -> None:
    with pytest.raises(ValueError, match=r"candidate_rows\[0\]\.final_score must be a finite number"):
        reporting.render_llm_trend_breakout_report(
            experiment_name="llm_trend_breakout",
            experiment={
                "summary": {
                    "technical_candidate_count": 1,
                    "accepted_candidate_count": 1,
                    "rejected_candidate_count": 0,
                    "acceptance_rate": 1.0,
                },
                "candidate_rows": [
                    {
                        "symbol": "SOLUSDT",
                        "decision": "accepted",
                        "reasons": ["llm_filter_passed"],
                        "final_score": "0.9",
                    }
                ],
            },
            metadata={"snapshot_count": 1},
        )


def test_render_llm_trend_breakout_report_rejects_invalid_metadata_snapshot_count() -> None:
    with pytest.raises(ValueError, match="metadata.snapshot_count must be a non-negative integer"):
        reporting.render_llm_trend_breakout_report(
            experiment_name="llm_trend_breakout",
            experiment={
                "summary": {
                    "technical_candidate_count": 2,
                    "accepted_candidate_count": 1,
                    "rejected_candidate_count": 1,
                    "acceptance_rate": 0.5,
                },
                "candidate_rows": [],
            },
            metadata={"snapshot_count": True},
        )


def test_render_llm_trend_breakout_report_rejects_non_object_promotion_metadata() -> None:
    with pytest.raises(ValueError, match="promotion_metadata must be an object"):
        reporting.render_llm_trend_breakout_report(
            experiment_name="llm_trend_breakout",
            experiment={
                "summary": {
                    "technical_candidate_count": 2,
                    "accepted_candidate_count": 1,
                    "rejected_candidate_count": 1,
                    "acceptance_rate": 0.5,
                },
                "candidate_rows": [],
            },
            metadata={"snapshot_count": 2, "promotion_metadata": True},
        )


def test_render_llm_trend_breakout_report_rejects_pseudo_object_promotion_metadata() -> None:
    class PromotionMetadataMapping(Mapping[str, object]):
        def __iter__(self) -> Iterator[str]:
            return iter(("runtime_fields",))

        def __len__(self) -> int:
            return 1

        def __getitem__(self, key: str) -> object:
            if key == "runtime_fields":
                return ["candidate_rows.final_score"]
            raise KeyError(key)

    with pytest.raises(ValueError, match="promotion_metadata must be an object"):
        reporting.render_llm_trend_breakout_report(
            experiment_name="llm_trend_breakout",
            experiment={
                "summary": {
                    "technical_candidate_count": 2,
                    "accepted_candidate_count": 1,
                    "rejected_candidate_count": 1,
                    "acceptance_rate": 0.5,
                },
                "candidate_rows": [],
            },
            metadata={"snapshot_count": 2, "promotion_metadata": PromotionMetadataMapping()},
        )


def test_render_llm_trend_breakout_report_rejects_invalid_promotion_rollback_fields() -> None:
    with pytest.raises(ValueError, match="promotion_metadata.rollback_target must be a canonical string"):
        reporting.render_llm_trend_breakout_report(
            experiment_name="llm_trend_breakout",
            experiment={
                "summary": {
                    "technical_candidate_count": 2,
                    "accepted_candidate_count": 1,
                    "rejected_candidate_count": 1,
                    "acceptance_rate": 0.5,
                },
                "candidate_rows": [],
            },
            metadata={"snapshot_count": 2, "promotion_metadata": {"rollback_target": True}},
        )


def test_render_llm_trend_breakout_report_rejects_invalid_promotion_runtime_fields() -> None:
    with pytest.raises(ValueError, match="promotion_metadata.runtime_fields\\[\\] must be a canonical string"):
        reporting.render_llm_trend_breakout_report(
            experiment_name="llm_trend_breakout",
            experiment={
                "summary": {
                    "technical_candidate_count": 2,
                    "accepted_candidate_count": 1,
                    "rejected_candidate_count": 1,
                    "acceptance_rate": 0.5,
                },
                "candidate_rows": [],
            },
            metadata={"snapshot_count": 2, "promotion_metadata": {"runtime_fields": [True]}},
        )


def test_render_llm_trend_breakout_report_rejects_promotion_runtime_fields_list_subclass() -> None:
    class RuntimeFields(list[str]):
        pass

    with pytest.raises(ValueError, match="promotion_metadata.runtime_fields must be a list"):
        reporting.render_llm_trend_breakout_report(
            experiment_name="llm_trend_breakout",
            experiment={
                "summary": {
                    "technical_candidate_count": 2,
                    "accepted_candidate_count": 1,
                    "rejected_candidate_count": 1,
                    "acceptance_rate": 0.5,
                },
                "candidate_rows": [],
            },
            metadata={
                "snapshot_count": 2,
                "promotion_metadata": {"runtime_fields": RuntimeFields(["candidate_rows.final_score"])},
            },
        )


@pytest.mark.parametrize("runtime_fields", ["regime", ["regime"]])
def test_render_llm_trend_breakout_report_rejects_malformed_promotion_metadata_runtime_fields(
    runtime_fields: object,
) -> None:
    with pytest.raises(ValueError, match="promotion_metadata.runtime_fields must be a tuple"):
        reporting.render_llm_trend_breakout_report(
            experiment_name="llm_trend_breakout",
            experiment={
                "summary": {
                    "technical_candidate_count": 2,
                    "accepted_candidate_count": 1,
                    "rejected_candidate_count": 1,
                    "acceptance_rate": 0.5,
                },
                "candidate_rows": [],
            },
            metadata={
                "snapshot_count": 2,
                "promotion_metadata": PromotionMetadata(runtime_fields=runtime_fields),  # type: ignore[arg-type]
            },
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("accepted_candidate_count", True),
        ("accepted_candidate_count", float("nan")),
        ("accepted_candidate_count", float("inf")),
        ("acceptance_rate", True),
        ("acceptance_rate", float("nan")),
        ("acceptance_rate", float("inf")),
    ],
)
def test_render_llm_trend_breakout_report_rejects_invalid_present_summary_numerics(
    field: str,
    value: object,
) -> None:
    summary = {
        "technical_candidate_count": 2,
        "accepted_candidate_count": 1,
        "rejected_candidate_count": 1,
        "acceptance_rate": 0.5,
    }
    summary[field] = value

    with pytest.raises(ValueError, match=rf"summary\.{field}"):
        reporting.render_llm_trend_breakout_report(
            experiment_name="llm_trend_breakout",
            experiment={"summary": summary, "candidate_rows": []},
            metadata={"snapshot_count": 2},
        )


def test_render_llm_trend_breakout_report_rejects_string_acceptance_rate() -> None:
    with pytest.raises(ValueError, match="summary.acceptance_rate must be a finite number"):
        reporting.render_llm_trend_breakout_report(
            experiment_name="llm_trend_breakout",
            experiment={
                "summary": {
                    "technical_candidate_count": 2,
                    "accepted_candidate_count": 1,
                    "rejected_candidate_count": 1,
                    "acceptance_rate": "0.5",
                },
                "candidate_rows": [],
            },
            metadata={"snapshot_count": 2},
        )


def test_render_llm_trend_breakout_report_rejects_list_of_pairs_summary_payload() -> None:
    with pytest.raises(ValueError, match="summary must be an object"):
        reporting.render_llm_trend_breakout_report(
            experiment_name="llm_trend_breakout",
            experiment={
                "summary": [
                    ("technical_candidate_count", 2),
                    ("accepted_candidate_count", 1),
                    ("rejected_candidate_count", 1),
                    ("acceptance_rate", 0.5),
                ],
                "candidate_rows": [],
            },
            metadata={"snapshot_count": 2},
        )


def test_render_llm_trend_breakout_report_rejects_non_object_rejection_reasons() -> None:
    with pytest.raises(ValueError, match="summary.rejection_reasons must be an object"):
        reporting.render_llm_trend_breakout_report(
            experiment_name="llm_trend_breakout",
            experiment={
                "summary": {
                    "technical_candidate_count": 2,
                    "accepted_candidate_count": 1,
                    "rejected_candidate_count": 1,
                    "acceptance_rate": 0.5,
                    "rejection_reasons": [("llm_low_confidence", 1)],
                },
                "candidate_rows": [],
            },
            metadata={"snapshot_count": 2},
        )


def test_backtest_cli_writes_llm_trend_breakout_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: _sample_dataset_rows(), raising=False)
    captured: dict[str, object] = {}

    def _fake_llm_runner(rows, *, params):
        captured["run_ids"] = [row.run_id for row in rows]
        captured["params"] = params
        return {
            "summary": {
                "snapshot_count": len(rows),
                "technical_candidate_count": 2,
                "accepted_candidate_count": 1,
                "rejected_candidate_count": 1,
                "acceptance_rate": 0.5,
                "rejection_reasons": {"missing_llm_label": 1},
            },
            "candidate_rows": [
                {
                    "timestamp": "2026-03-10T00:00:00+00:00",
                    "symbol": "SOLUSDT",
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "technical_score": 0.8,
                    "sentiment_score": 0.1,
                    "final_score": 0.9,
                    "decision": "accepted",
                    "reasons": ["llm_filter_passed"],
                    "event_risk": "low",
                    "fomo_risk": "low",
                    "label_confidence": 0.8,
                }
            ],
        }

    monkeypatch.setattr(cli, "run_llm_trend_breakout_experiment", _fake_llm_runner, raising=False)
    config_path = _write_experiment_fixture_config(tmp_path, "llm_trend_breakout_config.json")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["sample_windows"] = [
        {
            "name": "train_only",
            "start": "2026-03-10T00:00:00Z",
            "end": "2026-03-10T00:00:00Z",
            "split": "in_sample",
        }
    ]
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "llm_trend_breakout__trend-breakout__llm-filtered"
    assert exit_code == 0
    assert captured["run_ids"] == ["row-001"]
    assert captured["params"].llm_label_path == str(config_path.parent / "llm_labels/sample_labels.json")
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == ["manifest.json", "summary.json", "candidate_rows.json", "scorecard.json"]
    summary = json.loads((bundle_dir / "summary.json").read_text(encoding="utf-8"))
    candidate_rows = json.loads((bundle_dir / "candidate_rows.json").read_text(encoding="utf-8"))
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert summary["metadata"]["snapshot_count"] == 1
    assert candidate_rows["rows"][0]["symbol"] == "SOLUSDT"
    assert scorecard["metadata"]["evaluation_window"] == "1d"
    assert scorecard["decision_summary"]["decision"] in {
        "keep_researching",
        "candidate_for_promotion",
        "reject",
    }
