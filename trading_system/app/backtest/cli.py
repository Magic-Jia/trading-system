from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from ..reporting.promotion_gate_decision import write_promotion_gate_decision_report
from .archive.materialization import materialize_phase1_evidence_windows
from .config import load_backtest_config
from .dataset import load_dataset_root_metadata, load_historical_dataset, split_rows_by_windows
from .engine import replay_full_market_baseline
from .exit_policy_experiment import build_exit_policy_experiment, serialize_exit_policy
from .setup_rewrite_experiment import build_setup_rewrite_experiment, serialize_setup_rewrite
from .live_readiness import audit_exit_path_replay
from .experiments import (
    run_allocator_friction_experiment,
    run_engine_filter_ablation_experiment,
    run_long_gate_telemetry_experiment,
    run_public_strategy_factor_experiment,
    run_regime_predictive_power_experiment,
    run_rotation_suppression_experiment,
    run_walk_forward_validation_experiment,
)
from .promotion import compare_backtest_bundles
from .evidence_chain import OFFLINE_PROVENANCE, SCHEMA_VERSION, SOURCE_MODE, _generated_at
from .professional_reports import write_professional_backtest_evidence
from .llm_trend_breakout import run_llm_trend_breakout_experiment
from .reporting import (
    render_allocator_friction_report,
    render_engine_filter_ablation_report,
    render_full_market_baseline_report,
    render_long_gate_telemetry_report,
    render_llm_trend_breakout_report,
    render_public_strategy_factor_report,
    render_regime_scorecard,
    render_rotation_suppression_report,
    render_walk_forward_validation_report,
)
from .types import BacktestConfig, DatasetSnapshotRow, ExperimentParams


HandlerResult = tuple[dict[str, Any], dict[str, Any]]
Handler = Callable[[BacktestConfig, list[DatasetSnapshotRow]], HandlerResult]


def _bundle_name(config: BacktestConfig) -> str:
    return f"{config.experiment_kind}__{config.baseline_name}__{config.variant_name}"


def _sample_period(rows: list[DatasetSnapshotRow]) -> dict[str, str | None]:
    if not rows:
        return {"start": None, "end": None}
    return {
        "start": rows[0].timestamp.isoformat(),
        "end": rows[-1].timestamp.isoformat(),
    }


def _window_counts(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> dict[str, int]:
    return {
        name: len(window_rows)
        for name, window_rows in split_rows_by_windows(rows, config.sample_windows).items()
    }


def _rows_in_sample_windows(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> list[DatasetSnapshotRow]:
    if not config.sample_windows:
        return rows
    split = split_rows_by_windows(rows, config.sample_windows)
    selected: dict[tuple[str, str], DatasetSnapshotRow] = {}
    for window_rows in split.values():
        for row in window_rows:
            key = (row.timestamp.isoformat(), row.run_id)
            selected[key] = row
    return sorted(selected.values(), key=lambda row: (row.timestamp, row.run_id))


def _base_metadata(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "experiment_kind": config.experiment_kind,
        "dataset_root": str(config.dataset_root),
        "baseline_name": config.baseline_name,
        "variant_name": config.variant_name,
        "sample_period": _sample_period(rows),
        "window_counts": _window_counts(config, rows),
    }
    dataset_root_metadata = load_dataset_root_metadata(config.dataset_root)
    if dataset_root_metadata:
        metadata["imported_dataset"] = dataset_root_metadata
    if config.promotion_metadata is not None:
        metadata["promotion_metadata"] = {
            "runtime_fields": list(config.promotion_metadata.runtime_fields),
            "rollback_target": config.promotion_metadata.rollback_target,
            "rollback_trigger": config.promotion_metadata.rollback_trigger,
            "observation_window": config.promotion_metadata.observation_window,
        }
    params = config.experiment_params or ExperimentParams()
    metadata["experiment_params"] = {
        "disabled_engines": list(params.disabled_engines),
        "allowed_short_setup_types": list(params.allowed_short_setup_types),
        "quarantined_setup_types": list(params.quarantined_setup_types),
        "quarantined_short_setup_types": list(params.quarantined_short_setup_types),
    }
    serialized_exit_policy = serialize_exit_policy(params.exit_policy)
    if serialized_exit_policy is not None:
        metadata["experiment_params"]["exit_policy"] = serialized_exit_policy
    serialized_setup_rewrite = serialize_setup_rewrite(params.setup_rewrite)
    if serialized_setup_rewrite is not None:
        metadata["experiment_params"]["setup_rewrite"] = serialized_setup_rewrite
    return metadata


def _string_key_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    for key in value:
        if not isinstance(key, str):
            raise ValueError(f"{field_name} key must be a string")
    return dict(value)


def _safe_artifact_filename(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("artifact filename must be a safe relative filename")
    path = Path(value)
    if (
        not value.strip()
        or value != value.strip()
        or "\\" in value
        or path.is_absolute()
        or path.name != value
        or ".." in path.parts
    ):
        raise ValueError("artifact filename must be a safe relative filename")
    return value


def _experiment_metadata(experiment: Mapping[str, Any]) -> dict[str, Any]:
    if "metadata" not in experiment:
        return {}
    return _string_key_mapping(experiment["metadata"], field_name="experiment.metadata")


def _experiment_window_count(experiment: Mapping[str, Any]) -> int:
    metadata = _experiment_metadata(experiment)
    if "window_count" not in metadata:
        return 0
    window_count = metadata["window_count"]
    if isinstance(window_count, bool) or not isinstance(window_count, int) or window_count < 0:
        raise ValueError("experiment.metadata.window_count must be a non-negative integer")
    return window_count


def _strict_present_finite_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a finite number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{field_name} must be a finite number")
    return number


def _optional_postmortem_float(trade: Mapping[str, Any], field: str, *, index: int, default: float = 0.0) -> float:
    if field not in trade or trade[field] is None:
        return default
    return _strict_present_finite_float(trade[field], field_name=f"trades[{index}].{field}")


def _optional_postmortem_bool(trade: Mapping[str, Any], field: str, *, index: int) -> bool | None:
    if field not in trade or trade[field] is None:
        return None
    if not isinstance(trade[field], bool):
        raise ValueError(f"trades[{index}].{field} must be a strict boolean")
    return trade[field]


def _manifest(config: BacktestConfig, rows: list[DatasetSnapshotRow], artifacts: dict[str, dict[str, Any]], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    base = _string_key_mapping(metadata, field_name="metadata") if metadata is not None else _base_metadata(config, rows)
    artifact_filenames = [_safe_artifact_filename(name) for name in artifacts]
    return {
        **base,
        "bundle_name": _bundle_name(config),
        "snapshot_count": len(rows),
        "artifacts": ["manifest.json", *artifact_filenames],
    }


def _require_experiment_params(config: BacktestConfig) -> ExperimentParams:
    if config.experiment_params is None:
        raise ValueError(f"experiment_params are required for {config.experiment_kind}")
    return config.experiment_params


def _regime_research_outputs(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> HandlerResult:
    experiment = run_regime_predictive_power_experiment(rows)
    summary = dict(experiment)
    summary["metadata"] = {
        **_base_metadata(config, rows),
        **_experiment_metadata(experiment),
    }
    scorecard = render_regime_scorecard(
        experiment_name=config.experiment_kind,
        experiment=experiment,
        metadata=summary["metadata"],
    )
    artifacts = {"summary.json": summary, "scorecard.json": scorecard}
    return _manifest(config, rows, artifacts), artifacts


def _full_market_baseline_outputs(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> HandlerResult:
    result = replay_full_market_baseline(config)
    report = render_full_market_baseline_report(result)
    metadata = _base_metadata(config, rows)
    artifacts = {
        "summary.json": {"metadata": metadata, "summary": report["summary"]},
        "breakdowns.json": {"metadata": metadata, "breakdowns": report["breakdowns"]},
        "audit.json": {"metadata": metadata, "audit": report["audit"]},
        "trades.json": {"metadata": metadata, "trades": report["trades"]},
        "exit_path_replay.json": {"metadata": metadata, "exit_path_replay": audit_exit_path_replay(report["trades"])},
        "trade_postmortem.md": _render_trade_postmortem_markdown(report["trades"]),
    }
    exit_policy = config.experiment_params.exit_policy if config.experiment_params is not None else None
    if exit_policy is not None:
        artifacts["exit_policy_experiment.json"] = build_exit_policy_experiment(
            trades=report["trades"],
            policy=exit_policy,
            metadata=metadata,
        )
    setup_rewrite = config.experiment_params.setup_rewrite if config.experiment_params is not None else None
    if setup_rewrite is not None:
        artifacts["setup_rewrite_experiment.json"] = build_setup_rewrite_experiment(
            rows=_setup_rewrite_trade_rows(report["trades"]),
            setup_rewrite=setup_rewrite,
            metadata=metadata,
        )
    return _manifest(config, rows, artifacts, metadata), artifacts


def _setup_rewrite_trade_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trade in trades:
        row = dict(trade)
        if row.get("setup_type") == "":
            row["setup_type"] = None
        rows.append(row)
    return rows


def _rotation_suppression_outputs(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> HandlerResult:
    params = _require_experiment_params(config)
    evaluation_window = params.evaluation_window or "3d"
    soft_score_floor = float(params.soft_score_floor if params.soft_score_floor is not None else 0.72)
    experiment = run_rotation_suppression_experiment(
        rows,
        evaluation_window=evaluation_window,
        soft_score_floor=soft_score_floor,
    )
    metadata = {
        **_base_metadata(config, rows),
        "snapshot_count": len(rows),
        "evaluation_window": evaluation_window,
        "soft_score_floor": soft_score_floor,
    }
    report = render_rotation_suppression_report(
        experiment_name=config.experiment_kind,
        experiment=experiment,
        metadata=metadata,
    )
    artifacts = {
        "summary.json": report["summary"],
        "comparison_rows.json": report["comparison_rows"],
        "scorecard.json": report["scorecard"],
    }
    return _manifest(config, rows, artifacts, metadata), artifacts


def _allocator_friction_outputs(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> HandlerResult:
    params = _require_experiment_params(config)
    evaluation_window = params.evaluation_window or "3d"
    experiment = run_allocator_friction_experiment(rows, evaluation_window=evaluation_window)
    metadata = {
        **_base_metadata(config, rows),
        "snapshot_count": len(rows),
        "evaluation_window": evaluation_window,
    }
    report = render_allocator_friction_report(
        experiment_name=config.experiment_kind,
        experiment=experiment,
        metadata=metadata,
    )
    artifacts = {
        "summary.json": report["summary"],
        "comparison_rows.json": report["comparison_rows"],
        "scorecard.json": report["scorecard"],
    }
    return _manifest(config, rows, artifacts, metadata), artifacts


def _engine_filter_ablation_outputs(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> HandlerResult:
    params = _require_experiment_params(config)
    evaluation_window = params.evaluation_window or "3d"
    experiment = run_engine_filter_ablation_experiment(rows, evaluation_window=evaluation_window)
    metadata = {
        **_base_metadata(config, rows),
        "snapshot_count": len(rows),
        "evaluation_window": evaluation_window,
    }
    report = render_engine_filter_ablation_report(
        experiment_name=config.experiment_kind,
        experiment=experiment,
        metadata=metadata,
    )
    artifacts = {
        "summary.json": report["summary"],
        "scorecard.json": report["scorecard"],
    }
    return _manifest(config, rows, artifacts, metadata), artifacts


def _public_strategy_factors_outputs(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> HandlerResult:
    params = _require_experiment_params(config)
    evaluation_window = params.evaluation_window or "3d"
    experiment = run_public_strategy_factor_experiment(
        rows,
        evaluation_window=evaluation_window,
        strategy_families=params.public_strategy_families,
        minimum_effectiveness_sample_count=params.minimum_effectiveness_sample_count,
    )
    metadata = {
        **_base_metadata(config, rows),
        "snapshot_count": len(rows),
        "evaluation_window": evaluation_window,
        "strategy_families": list(params.public_strategy_families),
        "minimum_effectiveness_sample_count": params.minimum_effectiveness_sample_count,
    }
    report = render_public_strategy_factor_report(
        experiment_name=config.experiment_kind,
        experiment=experiment,
        metadata=metadata,
    )
    artifacts = {
        "summary.json": report["summary"],
        "factor_catalog.json": report["factor_catalog"],
        "scorecard.json": report["scorecard"],
    }
    return _manifest(config, rows, artifacts, metadata), artifacts


def _long_gate_telemetry_outputs(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> HandlerResult:
    params = _require_experiment_params(config)
    evaluation_window = params.evaluation_window or "3d"
    experiment = run_long_gate_telemetry_experiment(rows, evaluation_window=evaluation_window)
    metadata = {
        **_base_metadata(config, rows),
        "snapshot_count": len(rows),
        "evaluation_window": evaluation_window,
    }
    report = render_long_gate_telemetry_report(
        experiment_name=config.experiment_kind,
        experiment=experiment,
        metadata=metadata,
    )
    artifacts = {
        "summary.json": report["summary"],
        "snapshot_rows.json": report["snapshot_rows"],
        "symbol_breakdown.json": report["symbol_breakdown"],
        "regime_breakdown.json": report["regime_breakdown"],
        "scorecard.json": report["scorecard"],
    }
    return _manifest(config, rows, artifacts, metadata), artifacts


def _llm_trend_breakout_outputs(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> HandlerResult:
    params = _require_experiment_params(config)
    evaluation_window = params.evaluation_window or "1d"
    experiment = run_llm_trend_breakout_experiment(rows, params=params)
    metadata = {
        **_base_metadata(config, rows),
        "snapshot_count": len(rows),
        "evaluation_window": evaluation_window,
        "entry_profile": params.entry_profile,
        "symbols": list(params.symbols),
        "allowed_setup_types": list(params.allowed_setup_types),
        "quarantined_setup_types": list(params.quarantined_setup_types),
        "quarantined_short_setup_types": list(params.quarantined_short_setup_types),
        "minimum_final_score": params.minimum_final_score,
        "minimum_label_confidence": params.minimum_label_confidence,
        "require_llm_label": params.require_llm_label,
        "llm_label_path": params.llm_label_path,
    }
    report = render_llm_trend_breakout_report(
        experiment_name=config.experiment_kind,
        experiment=experiment,
        metadata=metadata,
    )
    artifacts = {
        "summary.json": report["summary"],
        "candidate_rows.json": report["candidate_rows"],
        "scorecard.json": report["scorecard"],
    }
    return _manifest(config, rows, artifacts, metadata), artifacts


def _walk_forward_validation_outputs(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> HandlerResult:
    params = _require_experiment_params(config)
    evaluation_window = params.evaluation_window or "3d"
    if params.walk_forward is None:
        raise ValueError("experiment_params.walk_forward is required for walk_forward_validation")
    experiment = run_walk_forward_validation_experiment(
        rows,
        evaluation_window=evaluation_window,
        in_sample_size=params.walk_forward.in_sample_size,
        out_of_sample_size=params.walk_forward.out_of_sample_size,
        step_size=params.walk_forward.step_size,
        config=config,
    )
    metadata = {
        **_base_metadata(config, rows),
        "snapshot_count": len(rows),
        "evaluation_window": evaluation_window,
        "window_count": _experiment_window_count(experiment),
        "in_sample_size": params.walk_forward.in_sample_size,
        "out_of_sample_size": params.walk_forward.out_of_sample_size,
        "step_size": params.walk_forward.step_size,
        "split_metadata": {
            "schema_version": "walk_forward_split_metadata.v1",
            "purge_bars": 0,
            "embargo_bars": 0,
            "timestamp_format": "datetime.isoformat",
            "boundary_policy": "train_end_before_test_start",
        },
    }
    report = render_walk_forward_validation_report(
        experiment_name=config.experiment_kind,
        experiment=experiment,
        metadata=metadata,
    )
    artifacts = {
        "summary.json": report["summary"],
        "windows.json": report["windows"],
        "scorecard.json": report["scorecard"],
    }
    return _manifest(config, rows, artifacts, metadata), artifacts


_EXPERIMENT_HANDLERS: dict[str, Handler] = {
    "regime_research": _regime_research_outputs,
    "full_market_baseline": _full_market_baseline_outputs,
    "rotation_suppression": _rotation_suppression_outputs,
    "allocator_friction": _allocator_friction_outputs,
    "engine_filter_ablation": _engine_filter_ablation_outputs,
    "public_strategy_factors": _public_strategy_factors_outputs,
    "long_gate_telemetry": _long_gate_telemetry_outputs,
    "llm_trend_breakout": _llm_trend_breakout_outputs,
    "walk_forward_validation": _walk_forward_validation_outputs,
}


def _render_trade_postmortem_markdown(trades: list[dict[str, Any]]) -> str:
    lines = [
        "# 逐单复盘",
        "",
        "| # | time | symbol | side | engine | setup | score | entry | exit | gross | net | MFE | MAE | exit_reason | fill_model | exec_source | exec_tf | lag_bars | fill_quality | maker_status | maker_wait | filled_qty | unfilled_qty | depth_levels | impact_bps | cost_coverage | mark_price | funding_rate | open_interest |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for index, trade in enumerate(trades, start=1):
        _optional_postmortem_bool(trade, "reduce_only", index=index - 1)
        _optional_postmortem_bool(trade, "post_only", index=index - 1)
        lines.append(
            "| {index} | {time} | {symbol} | {side} | {engine} | {setup} | {score:.4f} | {entry:.6g} | {exit:.6g} | {gross:.2f} | {net:.2f} | {mfe:.4%} | {mae:.4%} | {exit_reason} | {fill_model} | {exec_source} | {exec_tf} | {lag_bars} | {fill_quality} | {maker_status} | {maker_wait} | {filled_qty} | {unfilled_qty} | {depth_levels} | {impact_bps} | {coverage} | {mark_price} | {funding_rate} | {open_interest} |".format(
                index=index,
                time=trade.get("entry_timestamp", ""),
                symbol=trade.get("symbol", ""),
                side=trade.get("side", ""),
                engine=trade.get("engine", ""),
                setup=trade.get("setup_type", ""),
                score=_optional_postmortem_float(trade, "score", index=index - 1),
                entry=_optional_postmortem_float(trade, "entry_price", index=index - 1),
                exit=_optional_postmortem_float(trade, "exit_price", index=index - 1),
                gross=_optional_postmortem_float(trade, "gross_pnl", index=index - 1),
                net=_optional_postmortem_float(trade, "net_pnl", index=index - 1),
                mfe=_optional_postmortem_float(trade, "mfe_pct", index=index - 1),
                mae=_optional_postmortem_float(trade, "mae_pct", index=index - 1),
                exit_reason=trade.get("exit_reason", ""),
                fill_model=trade.get("fill_model", ""),
                exec_source=trade.get("execution_price_source", ""),
                exec_tf=trade.get("execution_timeframe", ""),
                lag_bars=int(trade.get("execution_lag_bars") or 0),
                fill_quality=trade.get("fill_quality", ""),
                maker_status=trade.get("maker_status", ""),
                maker_wait="" if trade.get("maker_wait_seconds") is None else f"{float(trade['maker_wait_seconds']):.2f}",
                filled_qty="" if trade.get("filled_quantity") is None else f"{float(trade['filled_quantity']):.8g}",
                unfilled_qty="" if trade.get("unfilled_quantity") is None else f"{float(trade['unfilled_quantity']):.8g}",
                depth_levels="" if trade.get("depth_levels_consumed") is None else int(trade["depth_levels_consumed"]),
                impact_bps="" if trade.get("execution_impact_bps") is None else f"{float(trade['execution_impact_bps']):.2f}",
                coverage="" if trade.get("cost_coverage_ratio") is None else f"{float(trade['cost_coverage_ratio']):.2f}",
                mark_price="" if trade.get("mark_price") is None else f"{float(trade['mark_price']):.6g}",
                funding_rate="" if trade.get("funding_rate") is None else f"{float(trade['funding_rate']):.8f}",
                open_interest="" if trade.get("open_interest_usdt") is None else f"{float(trade['open_interest_usdt']):.6g}",
            )
        )
    lines.append("")
    return "\n".join(lines)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_artifact(path: Path, payload: Any) -> None:
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
        return
    _write_json(path, payload)


def _run_command(args: argparse.Namespace) -> int:
    config = load_backtest_config(args.config)
    rows = load_historical_dataset(config.dataset_root)
    handler = _EXPERIMENT_HANDLERS.get(config.experiment_kind)
    if handler is None:
        supported = ", ".join(sorted(_EXPERIMENT_HANDLERS))
        raise ValueError(f"unsupported experiment_kind: {config.experiment_kind}; supported: {supported}")

    handler_rows = rows
    if config.experiment_kind != "full_market_baseline":
        handler_rows = _rows_in_sample_windows(config, rows)

    manifest, artifacts = handler(config, handler_rows)
    bundle_dir = Path(args.output_dir) / _bundle_name(config)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    _write_json(bundle_dir / "manifest.json", manifest)
    for filename, payload in artifacts.items():
        _write_artifact(bundle_dir / filename, payload)
    print(bundle_dir)
    return 0


def _public_strategy_factors_config_payload(
    *,
    dataset_root: Path,
    rows: list[DatasetSnapshotRow],
    minimum_effectiveness_sample_count: int,
) -> dict[str, Any]:
    start = rows[0].timestamp.isoformat().replace("+00:00", "Z")
    end = rows[-1].timestamp.isoformat().replace("+00:00", "Z")
    return {
        "dataset_root": str(dataset_root),
        "experiment_kind": "public_strategy_factors",
        "sample_windows": [
            {
                "name": "imported_history",
                "start": start,
                "end": end,
                "split": "in_sample",
            }
        ],
        "forward_return_windows": [
            {"name": "3d", "hours": 72},
        ],
        "costs": {
            "fee_bps": 4.0,
            "slippage_bps": 6.0,
            "funding_bps_per_day": 1.5,
        },
        "baseline_name": "public_strategy_scan",
        "variant_name": "factor_catalog_v1",
        "experiment_params": {
            "evaluation_window": "3d",
            "public_strategy_families": [
                "trend_following",
                "momentum",
                "mean_reversion",
                "volatility_breakout",
                "liquidity_volume",
                "funding_basis",
                "onchain_flow",
            ],
            "minimum_effectiveness_sample_count": minimum_effectiveness_sample_count,
        },
        "metadata": {
            "generated_by": "write-public-strategy-factors-config",
            "dataset_root_type": "imported_archive",
        },
    }


def _write_public_strategy_factors_config_command(args: argparse.Namespace) -> int:
    dataset_root = Path(args.dataset_root)
    dataset_root_metadata = load_dataset_root_metadata(dataset_root)
    if dataset_root_metadata.get("dataset_root_type") != "imported_archive":
        raise ValueError(f"dataset root is missing import_manifest.json: {dataset_root}")

    rows = load_historical_dataset(dataset_root)
    if not rows:
        raise ValueError(f"dataset root has no historical rows: {dataset_root}")

    output_config = Path(args.output_config)
    output_config.parent.mkdir(parents=True, exist_ok=True)
    payload = _public_strategy_factors_config_payload(
        dataset_root=dataset_root,
        rows=rows,
        minimum_effectiveness_sample_count=int(args.minimum_effectiveness_sample_count),
    )
    _write_json(output_config, payload)
    print(output_config)
    return 0


def _compare_command(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison = compare_backtest_bundles(
        baseline_bundle=Path(args.baseline_bundle),
        variant_bundle=Path(args.variant_bundle),
    )
    _write_json(output_dir / "promotion_gate.json", comparison["promotion_gate"])
    _write_json(output_dir / "decision_summary.json", comparison["decision_summary"])
    print(output_dir)
    return 0


def _write_professional_evidence_command(args: argparse.Namespace) -> int:
    outputs = write_professional_backtest_evidence(
        backtest_bundle_dir=Path(args.backtest_bundle_dir),
        walk_forward_bundle_dir=Path(args.walk_forward_bundle_dir),
        allocator_friction_bundle_dir=Path(args.allocator_friction_bundle_dir),
        output_dir=Path(args.output_dir),
        execution_calibration_summary_path=(
            Path(args.execution_calibration_summary_path)
            if args.execution_calibration_summary_path is not None
            else None
        ),
        execution_calibration_unavailable_path=(
            Path(args.execution_calibration_unavailable_path)
            if args.execution_calibration_unavailable_path is not None
            else None
        ),
        execution_sample_collection_health_path=(
            Path(args.execution_sample_collection_health_path)
            if args.execution_sample_collection_health_path is not None
            else None
        ),
        runtime_summary_path=Path(args.runtime_summary_path) if args.runtime_summary_path is not None else None,
        generated_at=args.generated_at,
    )
    print(outputs["evidence_chain_path"])
    return 0


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _diagnostic_reason_codes(message: str, preflight_reasons: list[str] | None = None) -> list[str]:
    reasons = ["pipeline_generation_failed"]
    if "lifecycle_status must be present" in message:
        _append_reason(reasons, "dataset_missing_lifecycle_status")
    if "margin_mode must be isolated or cross" in message:
        _append_reason(reasons, "dataset_missing_futures_context")
        _append_reason(reasons, "margin_liquidation_path_not_evaluable")
    for reason in preflight_reasons or []:
        _append_reason(reasons, reason)
    return reasons


def _dataset_root_from_config(config_path: Path) -> Path | None:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    dataset_root = payload.get("dataset_root") if isinstance(payload, Mapping) else None
    return Path(dataset_root) if isinstance(dataset_root, str) else None


def _append_example(examples: list[dict[str, str]], *, path: Path, symbol: str | None = None) -> None:
    if len(examples) >= 3:
        return
    example = {"path": str(path)}
    if symbol is not None:
        example["symbol"] = symbol
    if example not in examples:
        examples.append(example)


def _preflight_legacy_dataset_diagnostic(dataset_root: Path | None) -> dict[str, Any] | None:
    if dataset_root is None or not dataset_root.is_dir():
        return None
    reasons: list[str] = []
    missing_lifecycle_examples: list[dict[str, str]] = []
    missing_futures_examples: list[dict[str, str]] = []
    missing_lifecycle_snapshot_paths: set[str] = set()
    missing_futures_snapshot_paths: set[str] = set()
    missing_futures_symbols: set[str] = set()
    missing_lifecycle_row_count = 0
    snapshot_count = 0
    for snapshot_dir in sorted(path for path in dataset_root.iterdir() if path.is_dir()):
        instrument_path = snapshot_dir / "instrument_snapshot.json"
        market_context_path = snapshot_dir / "market_context.json"
        if not instrument_path.is_file():
            continue
        try:
            instrument_payload = json.loads(instrument_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        rows = instrument_payload.get("rows") if isinstance(instrument_payload, Mapping) else None
        if not isinstance(rows, list):
            continue
        snapshot_count += 1
        futures_symbols: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            symbol = row.get("symbol") if isinstance(row.get("symbol"), str) else None
            if "lifecycle_status" not in row:
                missing_lifecycle_row_count += 1
                missing_lifecycle_snapshot_paths.add(str(instrument_path))
                _append_reason(reasons, "dataset_missing_lifecycle_status")
                _append_example(missing_lifecycle_examples, path=instrument_path, symbol=symbol)
            if row.get("market_type") == "futures" and symbol is not None:
                futures_symbols.add(symbol)
        if not futures_symbols:
            continue
        try:
            market_context_payload = json.loads(market_context_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            for symbol in futures_symbols:
                missing_futures_symbols.add(symbol)
            missing_futures_snapshot_paths.add(str(market_context_path))
            _append_reason(reasons, "dataset_missing_futures_context")
            _append_reason(reasons, "margin_liquidation_path_not_evaluable")
            _append_example(missing_futures_examples, path=market_context_path, symbol=next(iter(sorted(futures_symbols))))
            continue
        symbols = market_context_payload.get("symbols") if isinstance(market_context_payload, Mapping) else None
        if not isinstance(symbols, Mapping):
            for symbol in futures_symbols:
                missing_futures_symbols.add(symbol)
            missing_futures_snapshot_paths.add(str(market_context_path))
            _append_reason(reasons, "dataset_missing_futures_context")
            _append_reason(reasons, "margin_liquidation_path_not_evaluable")
            _append_example(missing_futures_examples, path=market_context_path, symbol=next(iter(sorted(futures_symbols))))
            continue
        for symbol in futures_symbols:
            symbol_context = symbols.get(symbol)
            futures_context = symbol_context.get("futures_context") if isinstance(symbol_context, Mapping) else None
            if not isinstance(futures_context, Mapping):
                missing_futures_symbols.add(symbol)
                missing_futures_snapshot_paths.add(str(market_context_path))
                _append_reason(reasons, "dataset_missing_futures_context")
                _append_reason(reasons, "margin_liquidation_path_not_evaluable")
                _append_example(missing_futures_examples, path=market_context_path, symbol=symbol)
    return {
        "dataset_root": str(dataset_root),
        "snapshot_count": snapshot_count,
        "reason_codes": reasons,
        "missing_lifecycle_status": {
            "row_count": missing_lifecycle_row_count,
            "snapshot_count": len(missing_lifecycle_snapshot_paths),
            "examples": missing_lifecycle_examples,
        },
        "missing_futures_context": {
            "symbol_count": len(missing_futures_symbols),
            "snapshot_count": len(missing_futures_snapshot_paths),
            "examples": missing_futures_examples,
        },
    }


def _preflight_legacy_dataset_reason_codes(dataset_root: Path | None) -> list[str]:
    diagnostic = _preflight_legacy_dataset_diagnostic(dataset_root)
    if diagnostic is None:
        return []
    reason_codes = diagnostic.get("reason_codes")
    return list(reason_codes) if isinstance(reason_codes, list) else []


def _component_hold(generated_at: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "as_of": generated_at,
        "coverage_score": 0.0,
        "sample_count": 0,
        "status": "hold",
        "reason_codes": reasons,
    }


def _write_pipeline_generation_failure_diagnostic(
    *,
    output_dir: Path,
    error: Exception,
    generated_at: str | None,
    preflight_diagnostic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evaluated_at = _generated_at(generated_at)
    evidence_dir = output_dir / "professional_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_chain_path = evidence_dir / "backtest_evidence_chain.json"
    message = str(error)
    preflight_reason_codes = None
    if preflight_diagnostic is not None:
        reason_codes = preflight_diagnostic.get("reason_codes")
        preflight_reason_codes = list(reason_codes) if isinstance(reason_codes, list) else None
    reasons = _diagnostic_reason_codes(message, preflight_reasons=preflight_reason_codes)
    component_names = (
        "historical_backtest",
        "exit_path_replay",
        "walk_forward_oos",
        "cost_sensitivity",
        "execution_realism",
        "data_quality",
    )
    evidence_chain: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": evaluated_at,
        "source_mode": SOURCE_MODE,
        "backtest_bundle_dir": str(output_dir / "bundles"),
        "generation_failure": {
            "stage": "professional_evidence_pipeline_bundle_generation",
            "message": message,
            "reason_codes": reasons,
            **({"preflight": preflight_diagnostic} if preflight_diagnostic is not None else {}),
        },
        "sources": {},
        "missing_sources": ["backtest_bundle"],
        "malformed_sources": [],
        "provenance": {
            "source": OFFLINE_PROVENANCE,
            "source_mode": SOURCE_MODE,
            "side_effect_boundary": {
                "real_orders": "forbidden",
                "testnet_orders": "forbidden",
                "exchange_api_calls": "forbidden",
                "credential_use": "forbidden",
                "reads": "backtest_configs_and_dataset_only",
            },
        },
    }
    for name in component_names:
        evidence_chain[name] = _component_hold(evaluated_at, list(reasons))
    evidence_chain["summary"] = {
        "decision": "hold",
        "component_statuses": {name: "hold" for name in component_names},
        "reason_codes": reasons,
    }
    evidence_chain_path.write_text(json.dumps(evidence_chain, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "professional_evidence_pipeline.v1",
        "generated_at": evaluated_at,
        "decision": "hold",
        "bundles": {},
        "professional_evidence": {
            "evidence_chain_path": str(evidence_chain_path),
            "generation_failed": True,
            "generation_failure_reason_codes": reasons,
        },
    }
    manifest_path = output_dir / "professional_evidence_pipeline_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"evidence_chain": evidence_chain, "evidence_chain_path": str(evidence_chain_path), "manifest": manifest}


def _run_backtest_config_to_bundle(config_path: Path, output_dir: Path, *, expected_experiment_kind: str | None = None) -> Path:
    config = load_backtest_config(config_path)
    if expected_experiment_kind is not None and config.experiment_kind != expected_experiment_kind:
        raise ValueError(
            f"{config_path} experiment_kind must be {expected_experiment_kind}; loaded {config.experiment_kind}"
        )
    rows = load_historical_dataset(config.dataset_root)
    handler = _EXPERIMENT_HANDLERS.get(config.experiment_kind)
    if handler is None:
        supported = ", ".join(sorted(_EXPERIMENT_HANDLERS))
        raise ValueError(f"unsupported experiment_kind: {config.experiment_kind}; supported: {supported}")
    handler_rows = rows if config.experiment_kind == "full_market_baseline" else _rows_in_sample_windows(config, rows)
    manifest, artifacts = handler(config, handler_rows)
    bundle_dir = output_dir / _bundle_name(config)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    _write_json(bundle_dir / "manifest.json", manifest)
    for filename, payload in artifacts.items():
        _write_artifact(bundle_dir / filename, payload)
    return bundle_dir


def _run_professional_evidence_pipeline_command(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    bundles_dir = output_dir / "bundles"
    evidence_dir = output_dir / "professional_evidence"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    try:
        backtest_bundle_dir = _run_backtest_config_to_bundle(
            Path(args.backtest_config), bundles_dir, expected_experiment_kind="full_market_baseline"
        )
        walk_forward_bundle_dir = _run_backtest_config_to_bundle(
            Path(args.walk_forward_config), bundles_dir, expected_experiment_kind="walk_forward_validation"
        )
        allocator_friction_bundle_dir = _run_backtest_config_to_bundle(
            Path(args.allocator_friction_config), bundles_dir, expected_experiment_kind="allocator_friction"
        )
    except ValueError as exc:
        if "experiment_kind must be" in str(exc):
            raise
        _write_pipeline_generation_failure_diagnostic(
            output_dir=output_dir,
            error=exc,
            generated_at=args.generated_at,
            preflight_diagnostic=_preflight_legacy_dataset_diagnostic(
                _dataset_root_from_config(Path(args.backtest_config))
            ),
        )
        return 0
    outputs = write_professional_backtest_evidence(
        backtest_bundle_dir=backtest_bundle_dir,
        walk_forward_bundle_dir=walk_forward_bundle_dir,
        allocator_friction_bundle_dir=allocator_friction_bundle_dir,
        output_dir=evidence_dir,
        execution_calibration_summary_path=(
            Path(args.execution_calibration_summary_path)
            if args.execution_calibration_summary_path is not None
            else None
        ),
        execution_calibration_unavailable_path=(
            Path(args.execution_calibration_unavailable_path)
            if args.execution_calibration_unavailable_path is not None
            else None
        ),
        execution_sample_collection_health_path=(
            Path(args.execution_sample_collection_health_path)
            if args.execution_sample_collection_health_path is not None
            else None
        ),
        runtime_summary_path=Path(args.runtime_summary_path) if args.runtime_summary_path is not None else None,
        generated_at=args.generated_at,
    )
    evidence_chain = outputs["evidence_chain"]
    summary = evidence_chain.get("summary") if isinstance(evidence_chain, Mapping) else {}
    decision = summary.get("decision") if isinstance(summary, Mapping) else "hold"
    manifest = {
        "schema_version": "professional_evidence_pipeline.v1",
        "generated_at": outputs["evidence_chain"]["generated_at"],
        "decision": decision,
        "bundles": {
            "backtest": str(backtest_bundle_dir),
            "walk_forward": str(walk_forward_bundle_dir),
            "allocator_friction": str(allocator_friction_bundle_dir),
        },
        "professional_evidence": {
            "walk_forward_report_path": outputs["walk_forward_report_path"],
            "cost_sensitivity_report_path": outputs["cost_sensitivity_report_path"],
            "evidence_chain_path": outputs["evidence_chain_path"],
        },
    }
    if "execution_calibration_unavailable_path" in outputs:
        manifest["professional_evidence"]["execution_calibration_unavailable_path"] = outputs[
            "execution_calibration_unavailable_path"
        ]
    if "execution_calibration_summary_path" in outputs:
        manifest["professional_evidence"]["execution_calibration_summary_path"] = outputs[
            "execution_calibration_summary_path"
        ]
    if "execution_sample_collection_health_path" in outputs:
        manifest["professional_evidence"]["execution_sample_collection_health_path"] = outputs[
            "execution_sample_collection_health_path"
        ]
    if "runtime_summary_path" in outputs:
        manifest["professional_evidence"]["runtime_summary_path"] = outputs["runtime_summary_path"]
    if args.simulated_live_evidence_window is not None or args.promotion_readiness_scorecard_trend is not None:
        if args.simulated_live_evidence_window is None or args.promotion_readiness_scorecard_trend is None:
            print(
                "--simulated-live-evidence-window and --promotion-readiness-scorecard-trend must be provided together",
                file=sys.stderr,
            )
            return 1
        gate_path = output_dir / "promotion_gate_decision.json"
        gate_report = write_promotion_gate_decision_report(
            gate_path,
            simulated_live_evidence_window=Path(args.simulated_live_evidence_window),
            promotion_readiness_scorecard_trend=Path(args.promotion_readiness_scorecard_trend),
            calibration_artifacts=[Path(path) for path in args.calibration_artifact],
            professional_evidence_chain=Path(outputs["evidence_chain_path"]),
            generated_at=args.generated_at,
        )
        manifest["promotion_gate"] = {
            "decision_report_path": str(gate_path),
            "decision": gate_report["decision"],
            "blocking_reasons": gate_report["blocking_reasons"],
            "human_review_required": gate_report["human_review_required"],
            "simulated_live_evidence_window_path": str(args.simulated_live_evidence_window),
            "promotion_readiness_scorecard_trend_path": str(args.promotion_readiness_scorecard_trend),
            "calibration_artifact_paths": [str(path) for path in args.calibration_artifact],
            "professional_evidence_chain_path": outputs["evidence_chain_path"],
        }
    manifest_path = output_dir / "professional_evidence_pipeline_manifest.json"
    _write_json(manifest_path, manifest)
    print(manifest_path)
    return 0


def _historical_dataset_preflight_report(
    dataset_root: Path, *, generated_at: str | None = None
) -> dict[str, Any]:
    diagnostic = _preflight_legacy_dataset_diagnostic(dataset_root)
    if diagnostic is None:
        raise FileNotFoundError(f"dataset root not found: {dataset_root}")
    reason_codes = diagnostic.get("reason_codes")
    reasons = list(reason_codes) if isinstance(reason_codes, list) else []
    return {
        "schema_version": "historical_dataset_preflight.v1",
        "generated_at": _generated_at(generated_at),
        "decision": "hold" if reasons else "pass",
        **diagnostic,
    }


def _preflight_historical_dataset_command(args: argparse.Namespace) -> int:
    report = _historical_dataset_preflight_report(Path(args.dataset_root), generated_at=args.generated_at)
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output_path is not None:
        Path(args.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_path).write_text(output, encoding="utf-8")
        print(args.output_path)
    else:
        print(output, end="")
    return 0


def _historical_dataset_migration_plan_report(
    dataset_root: Path, *, generated_at: str | None = None
) -> dict[str, Any]:
    preflight = _historical_dataset_preflight_report(dataset_root, generated_at=generated_at)
    reason_codes = list(preflight["reason_codes"]) if isinstance(preflight.get("reason_codes"), list) else []
    missing_lifecycle = preflight["missing_lifecycle_status"]
    missing_futures = preflight["missing_futures_context"]
    return {
        "schema_version": "historical_dataset_migration_plan.v1",
        "generated_at": preflight["generated_at"],
        "decision": "hold" if reason_codes else "pass",
        "source_dataset_root": str(dataset_root),
        "reason_codes": reason_codes,
        "preflight_summary": {
            "schema_version": preflight["schema_version"],
            "decision": preflight["decision"],
            "snapshot_count": preflight["snapshot_count"],
            "missing_lifecycle_status": missing_lifecycle,
            "missing_futures_context": missing_futures,
        },
        "target_required_fields": [
            {
                "field": "lifecycle_status",
                "reason_code": "dataset_missing_lifecycle_status",
                "preflight_counts": {
                    "missing_row_count": missing_lifecycle["row_count"],
                    "missing_snapshot_count": missing_lifecycle["snapshot_count"],
                },
                "candidate_sources": [
                    "public_exchange_instrument_metadata",
                    "historical_exchange_listing_status_archive",
                    "importer_source_trace_with_timestamp",
                ],
                "requires_provenance": True,
                "not_derivable_fields": [],
                "non_fabrication_notes": [
                    "Do not default missing lifecycle_status to listed.",
                    "Backfill only from timestamped instrument metadata or reimport with source trace.",
                ],
                "recommended_action": "formal_reimport_or_provenance_backfill",
            },
            {
                "field": "futures_context",
                "reason_code": "dataset_missing_futures_context",
                "preflight_counts": {
                    "missing_symbol_count": missing_futures["symbol_count"],
                    "missing_snapshot_count": missing_futures["snapshot_count"],
                },
                "candidate_sources": [
                    "public_exchange_futures_market_metadata",
                    "historical_mark_price_funding_open_interest_archive",
                    "importer_source_trace_with_timestamp",
                ],
                "requires_provenance": True,
                "not_derivable_fields": [],
                "non_fabrication_notes": [
                    "Do not infer mark, funding, or open-interest context from spot candles.",
                    "Backfill only from public read-only futures metadata captured at the snapshot time.",
                ],
                "recommended_action": "fetch_public_exchange_metadata",
            },
            {
                "field": "margin_liquidation_path",
                "reason_code": "margin_liquidation_path_not_evaluable",
                "preflight_counts": {
                    "missing_futures_context_symbol_count": missing_futures["symbol_count"],
                    "affected_snapshot_count": missing_futures["snapshot_count"],
                },
                "candidate_sources": [
                    "account_policy_archive",
                    "execution_runtime_audit_log",
                    "broker_or_exchange_account_state_snapshot",
                    "margin_liquidation_path_evidence_bundle",
                ],
                "requires_provenance": True,
                "not_derivable_fields": [
                    "margin_mode",
                    "leverage",
                    "maintenance_tier",
                    "liquidation_price",
                ],
                "non_fabrication_notes": [
                    "Account and execution policy fields are not safely derivable from public market data.",
                    "Hold until legitimate account policy and liquidation-path evidence is attached.",
                ],
                "recommended_action": "attach_account_policy_or_hold",
            },
        ],
        "account_execution_policy_classification": {
            "public_market_data_derivable": False,
            "fields": ["margin_mode", "leverage", "maintenance_tier", "liquidation_price"],
            "notes": [
                "margin_mode can be account, policy, or runtime specific.",
                "leverage, maintenance tier, and liquidation context require source provenance.",
            ],
        },
        "operations": {
            "safe_next_steps": [
                "Keep the source dataset immutable while planning migration.",
                "Reimport or provenance-backfill lifecycle_status and futures_context from read-only historical sources.",
                "Attach account policy and liquidation-path evidence before enabling professional backtest gates.",
            ],
            "validation_commands": [
                "preflight-historical-dataset",
                "run-professional-evidence-pipeline",
            ],
            "validation_command_examples": [
                "preflight-historical-dataset --dataset-root <migrated_dataset_root> --output-path <preflight.json>",
                "run-professional-evidence-pipeline --backtest-config <backtest.json> --walk-forward-config <walk_forward.json> --allocator-friction-config <allocator.json> --output-dir <evidence_output_dir>",
            ],
        },
        "side_effect_boundary": {
            "dataset_mutation": "forbidden",
            "real_orders": "forbidden",
            "testnet_orders": "forbidden",
            "exchange_trading_endpoints": "forbidden",
            "credential_use": "forbidden",
            "allowed_reads": "local_dataset_files_only",
        },
    }


def _plan_historical_dataset_migration_command(args: argparse.Namespace) -> int:
    report = _historical_dataset_migration_plan_report(Path(args.dataset_root), generated_at=args.generated_at)
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output_path is not None:
        Path(args.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_path).write_text(output, encoding="utf-8")
        print(args.output_path)
    else:
        print(output, end="")
    return 0


def _materialize_evidence_windows_command(args: argparse.Namespace) -> int:
    symbols = tuple(str(value).strip().upper() for value in args.symbols.split(",") if str(value).strip()) if args.symbols else None
    windows_days = tuple(int(value.strip()) for value in args.windows_days.split(",") if value.strip())
    report = materialize_phase1_evidence_windows(
        args.archive_root,
        args.output_root,
        symbols=symbols,
        windows_days=windows_days,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic backtest research experiments.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run one backtest experiment config and write a result bundle.")
    run_parser.add_argument("--config", required=True, help="Path to a backtest config JSON file.")
    run_parser.add_argument("--output-dir", required=True, help="Directory where research bundles should be written.")
    run_parser.set_defaults(handler=_run_command)

    public_strategy_config_parser = subparsers.add_parser(
        "write-public-strategy-factors-config",
        help="Write a public_strategy_factors config for an imported/archive dataset root.",
    )
    public_strategy_config_parser.add_argument(
        "--dataset-root",
        required=True,
        help="Imported/archive dataset root containing import_manifest.json and bundle directories.",
    )
    public_strategy_config_parser.add_argument(
        "--output-config",
        required=True,
        help="Path where the generated public_strategy_factors config JSON should be written.",
    )
    public_strategy_config_parser.add_argument(
        "--minimum-effectiveness-sample-count",
        type=int,
        default=30,
        help="Minimum valid factor/forward-return pairs required before a factor can become promising_research.",
    )
    public_strategy_config_parser.set_defaults(handler=_write_public_strategy_factors_config_command)

    compare_parser = subparsers.add_parser("compare", help="Compare baseline and variant bundles and write promotion artifacts.")
    compare_parser.add_argument("--baseline-bundle", required=True, help="Path to the baseline bundle directory.")
    compare_parser.add_argument("--variant-bundle", required=True, help="Path to the variant bundle directory.")
    compare_parser.add_argument("--output-dir", required=True, help="Directory where promotion artifacts should be written.")
    compare_parser.set_defaults(handler=_compare_command)

    professional_evidence_parser = subparsers.add_parser(
        "write-professional-evidence",
        help="Generate professional WF/OOS, cost-sensitivity, and backtest evidence-chain reports from existing bundles.",
    )
    professional_evidence_parser.add_argument(
        "--backtest-bundle-dir",
        required=True,
        help="Full-market baseline/backtest bundle directory containing manifest, summary, audit, and exit-path replay artifacts.",
    )
    professional_evidence_parser.add_argument(
        "--walk-forward-bundle-dir",
        required=True,
        help="Walk-forward validation bundle directory containing summary, windows, and scorecard artifacts.",
    )
    professional_evidence_parser.add_argument(
        "--allocator-friction-bundle-dir",
        required=True,
        help="Allocator friction/cost-sensitivity bundle directory containing summary, comparison rows, and scorecard artifacts.",
    )
    professional_evidence_parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where walk_forward_oos_report.json, cost_sensitivity_report.json, and backtest_evidence_chain.json are written.",
    )
    professional_evidence_parser.add_argument(
        "--execution-calibration-summary-path",
        default=None,
        help="Optional passive_order_calibration_summary.json to include maker/taker execution realism metrics.",
    )
    professional_evidence_parser.add_argument(
        "--execution-calibration-unavailable-path",
        default=None,
        help="Optional calibration_records_unavailable.json marker to include as fail-closed execution realism evidence.",
    )
    professional_evidence_parser.add_argument(
        "--execution-sample-collection-health-path",
        default=None,
        help="Optional execution_sample_collection_health.json marker emitted by paper runtime run_cycle.",
    )
    professional_evidence_parser.add_argument(
        "--runtime-summary-path",
        default=None,
        help="Optional runtime latest.json whose execution_sample_collection_health_file should be consumed.",
    )
    professional_evidence_parser.add_argument(
        "--generated-at",
        default=None,
        help="Optional canonical UTC timestamp for deterministic generated_at fields.",
    )
    professional_evidence_parser.set_defaults(handler=_write_professional_evidence_command)

    professional_pipeline_parser = subparsers.add_parser(
        "run-professional-evidence-pipeline",
        help="Run backtest, walk-forward, allocator-friction configs and generate professional evidence in one command.",
    )
    professional_pipeline_parser.add_argument(
        "--backtest-config",
        required=True,
        help="Full-market baseline/backtest config JSON file.",
    )
    professional_pipeline_parser.add_argument(
        "--walk-forward-config",
        required=True,
        help="Walk-forward validation config JSON file.",
    )
    professional_pipeline_parser.add_argument(
        "--allocator-friction-config",
        required=True,
        help="Allocator friction/cost-sensitivity config JSON file.",
    )
    professional_pipeline_parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where bundles, professional evidence reports, and pipeline manifest are written.",
    )
    professional_pipeline_parser.add_argument(
        "--execution-calibration-summary-path",
        default=None,
        help="Optional passive_order_calibration_summary.json to include maker/taker execution realism metrics.",
    )
    professional_pipeline_parser.add_argument(
        "--execution-calibration-unavailable-path",
        default=None,
        help="Optional calibration_records_unavailable.json marker to include as fail-closed execution realism evidence.",
    )
    professional_pipeline_parser.add_argument(
        "--execution-sample-collection-health-path",
        default=None,
        help="Optional execution_sample_collection_health.json marker emitted by paper runtime run_cycle.",
    )
    professional_pipeline_parser.add_argument(
        "--runtime-summary-path",
        default=None,
        help="Optional runtime latest.json whose execution_sample_collection_health_file should be consumed.",
    )
    professional_pipeline_parser.add_argument(
        "--simulated-live-evidence-window",
        default=None,
        help="Optional simulated_live_evidence_window.json; when paired with trend, writes promotion_gate_decision.json.",
    )
    professional_pipeline_parser.add_argument(
        "--promotion-readiness-scorecard-trend",
        default=None,
        help="Optional promotion_readiness_scorecard_trend.json; when paired with evidence window, writes promotion_gate_decision.json.",
    )
    professional_pipeline_parser.add_argument(
        "--calibration-artifact",
        action="append",
        default=[],
        help="Optional calibration feedback/recommendation artifact for promotion gate; may be repeated.",
    )
    professional_pipeline_parser.add_argument(
        "--generated-at",
        default=None,
        help="Optional canonical UTC timestamp for deterministic generated_at fields.",
    )
    professional_pipeline_parser.set_defaults(handler=_run_professional_evidence_pipeline_command)

    preflight_parser = subparsers.add_parser(
        "preflight-historical-dataset",
        help="Write a standalone fail-closed preflight report for an imported historical dataset root.",
    )
    preflight_parser.add_argument(
        "--dataset-root",
        required=True,
        help="Imported historical dataset root containing per-snapshot instrument_snapshot.json and market_context.json files.",
    )
    preflight_parser.add_argument(
        "--output-path",
        default=None,
        help="Optional path where historical_dataset_preflight.v1 JSON should be written. Prints JSON to stdout when omitted.",
    )
    preflight_parser.add_argument(
        "--generated-at",
        default=None,
        help="Optional canonical UTC timestamp for deterministic generated_at fields.",
    )
    preflight_parser.set_defaults(handler=_preflight_historical_dataset_command)

    migration_plan_parser = subparsers.add_parser(
        "plan-historical-dataset-migration",
        help="Write a read-only migration/reimport planning report for a historical dataset root.",
    )
    migration_plan_parser.add_argument(
        "--dataset-root",
        required=True,
        help="Imported historical dataset root to inspect without mutation.",
    )
    migration_plan_parser.add_argument(
        "--output-path",
        default=None,
        help="Optional path where historical_dataset_migration_plan.v1 JSON should be written. Prints JSON to stdout when omitted.",
    )
    migration_plan_parser.add_argument(
        "--generated-at",
        default=None,
        help="Optional canonical UTC timestamp for deterministic generated_at fields.",
    )
    migration_plan_parser.set_defaults(handler=_plan_historical_dataset_migration_command)

    materialize_parser = subparsers.add_parser(
        "materialize-evidence-windows",
        help="Materialize 30/90/180 imported datasets from raw-market intraday/execution archives.",
    )
    materialize_parser.add_argument(
        "--archive-root",
        required=True,
        help="Archive root, raw-market root, or raw-market/binance/futures root to read.",
    )
    materialize_parser.add_argument(
        "--output-root",
        required=True,
        help="Directory where window dataset roots and coverage_report.json should be written.",
    )
    materialize_parser.add_argument(
        "--symbols",
        default=None,
        help="Optional comma-separated symbols to materialize, for example BTCUSDT,ETHUSDT,SOLUSDT.",
    )
    materialize_parser.add_argument(
        "--windows-days",
        default="30,90,180",
        help="Comma-separated day windows to materialize from the latest eligible archive timestamp.",
    )
    materialize_parser.set_defaults(handler=_materialize_evidence_windows_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    try:
        return int(handler(args))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
