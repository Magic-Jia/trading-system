from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from .config import load_backtest_config
from .dataset import load_historical_dataset, split_rows_by_windows
from .engine import replay_full_market_baseline
from .experiments import run_regime_predictive_power_experiment
from .reporting import render_full_market_baseline_report, render_regime_scorecard
from .types import BacktestConfig, DatasetSnapshotRow


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


def _base_metadata(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> dict[str, Any]:
    return {
        "experiment_kind": config.experiment_kind,
        "dataset_root": str(config.dataset_root),
        "baseline_name": config.baseline_name,
        "variant_name": config.variant_name,
        "sample_period": _sample_period(rows),
        "window_counts": _window_counts(config, rows),
    }


def _regime_research_outputs(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    experiment = run_regime_predictive_power_experiment(rows)
    summary = dict(experiment)
    summary["metadata"] = {
        **_base_metadata(config, rows),
        **dict(experiment.get("metadata", {})),
    }
    scorecard = render_regime_scorecard(
        experiment_name=config.experiment_kind,
        experiment=experiment,
        metadata=summary["metadata"],
    )
    manifest = {
        **_base_metadata(config, rows),
        "bundle_name": _bundle_name(config),
        "snapshot_count": len(rows),
        "artifacts": ["manifest.json", "summary.json", "scorecard.json"],
    }
    return manifest, {"summary.json": summary, "scorecard.json": scorecard}


def _full_market_baseline_outputs(config: BacktestConfig, rows: list[DatasetSnapshotRow]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    result = replay_full_market_baseline(config)
    report = render_full_market_baseline_report(result)
    metadata = _base_metadata(config, rows)
    artifacts = {
        "summary.json": {"metadata": metadata, "summary": report["summary"]},
        "breakdowns.json": {"metadata": metadata, "breakdowns": report["breakdowns"]},
        "audit.json": {"metadata": metadata, "audit": report["audit"]},
    }
    manifest = {
        **metadata,
        "bundle_name": _bundle_name(config),
        "snapshot_count": len(rows),
        "artifacts": ["manifest.json", *artifacts.keys()],
    }
    return manifest, artifacts


_EXPERIMENT_HANDLERS: dict[str, Callable[[BacktestConfig, list[DatasetSnapshotRow]], tuple[dict[str, Any], dict[str, dict[str, Any]]]]] = {
    "regime_research": _regime_research_outputs,
    "full_market_baseline": _full_market_baseline_outputs,
}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_command(args: argparse.Namespace) -> int:
    config = load_backtest_config(args.config)
    rows = load_historical_dataset(config.dataset_root)
    handler = _EXPERIMENT_HANDLERS.get(config.experiment_kind)
    if handler is None:
        supported = ", ".join(sorted(_EXPERIMENT_HANDLERS))
        raise ValueError(f"unsupported experiment_kind: {config.experiment_kind}; supported: {supported}")

    manifest, artifacts = handler(config, rows)
    bundle_dir = Path(args.output_dir) / _bundle_name(config)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    _write_json(bundle_dir / "manifest.json", manifest)
    for filename, payload in artifacts.items():
        _write_json(bundle_dir / filename, payload)
    print(bundle_dir)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic backtest research experiments.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run one backtest experiment config and write a result bundle.")
    run_parser.add_argument("--config", required=True, help="Path to a backtest config JSON file.")
    run_parser.add_argument("--output-dir", required=True, help="Directory where research bundles should be written.")
    run_parser.set_defaults(handler=_run_command)
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
