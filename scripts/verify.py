#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path


TEST = "python3 -m pytest -q"
DIFF_CHECK = "git --no-pager diff --check HEAD"
SANITIZED_ENV_REMOVED_PREFIXES = ["TRADING_"]

SUITES: dict[str, list[str]] = {
    "evidence-chain": [
        "trading_system/tests/test_backtest_live_readiness.py",
        "trading_system/tests/test_runtime_safety_evidence.py",
        "trading_system/tests/test_runtime_incident_bundle_schema.py",
        "trading_system/tests/test_backtest_microstructure_evidence.py",
        "trading_system/tests/test_backtest_derivatives_risk.py",
        "trading_system/tests/test_backtest_l2_event_replay.py",
        "trading_system/tests/test_backtest_validation_evidence.py",
        "trading_system/tests/test_execution_calibration_evidence.py",
        "trading_system/tests/test_generate_execution_calibration_records.py",
        "trading_system/tests/test_backtest_promotion_evidence_bundle.py",
        "trading_system/tests/test_rolling_simulated_live_evidence_bundle.py",
        "trading_system/tests/test_simulated_live_evidence_window.py",
        "trading_system/tests/test_real_local_simulated_live_evidence_chain.py",
        "trading_system/tests/test_simulated_live_artifact_inventory.py",
        "trading_system/tests/test_market_coverage_reports.py",
        "trading_system/tests/test_promotion_gate_decision.py",
        "trading_system/tests/test_longitudinal_promotion_decision_archive.py",
        "trading_system/tests/test_replay_simulated_live_evidence.py",
        "trading_system/tests/test_simulated_live_cadence_runner.py",
        "trading_system/tests/test_execution_stream_producers.py",
        "trading_system/tests/test_backtest_setup_rewrite_experiment.py",
        "trading_system/tests/test_backtest_promotion.py",
        "trading_system/tests/test_scheduled_live_sim_generation.py",
        "trading_system/tests/test_longitudinal_live_sim_trend_report.py",
        "trading_system/tests/test_main_v2_cycle.py",
    ],
    "runtime-main": [
        "trading_system/tests/test_main_v2_cycle.py",
        "trading_system/tests/test_backtest_live_readiness.py",
        "trading_system/tests/test_backtest_promotion.py",
    ],
    "universe": [
        "trading_system/tests/test_universe_liquidity_filter.py",
        "trading_system/tests/test_universe_builder.py",
        "trading_system/tests/test_backtest_universe.py",
        "trading_system/tests/test_main_v2_cycle.py",
    ],
    "portfolio": [
        "trading_system/tests/test_allocator.py",
        "trading_system/tests/test_exit_policy.py",
        "trading_system/tests/test_exposure.py",
        "trading_system/tests/test_lifecycle_v2.py",
        "trading_system/tests/test_management_execution.py",
        "trading_system/tests/test_target_management_state.py",
        "trading_system/tests/test_main_v2_cycle.py",
    ],
    "backtest-core": [
        "trading_system/tests/test_backtest_engine.py",
        "trading_system/tests/test_backtest_portfolio.py",
        "trading_system/tests/test_backtest_costs.py",
        "trading_system/tests/test_backtest_dataset.py",
        "trading_system/tests/test_backtest_exit_policies.py",
        "trading_system/tests/test_backtest_reporting.py",
        "trading_system/tests/test_backtest_promotion.py",
        "trading_system/tests/test_backtest_execution_sim.py",
        "trading_system/tests/test_backtest_l2_event_replay.py",
        "trading_system/tests/test_backtest_evaluation.py",
        "trading_system/tests/test_backtest_metrics.py",
        "trading_system/tests/test_backtest_ablation_experiments.py",
        "trading_system/tests/test_backtest_regime_experiments.py",
        "trading_system/tests/test_backtest_exit_policy_experiment.py",
        "trading_system/tests/test_main_v2_cycle.py",
    ],
    "archive-data": [
        "trading_system/tests/test_backtest_archive_importer.py",
        "trading_system/tests/test_backtest_archive_dataset_importer.py",
        "trading_system/tests/test_backtest_archive_raw_market.py",
        "trading_system/tests/test_backtest_archive_runtime_bundle.py",
        "trading_system/tests/test_backtest_archive_binance_execution_downloader.py",
        "trading_system/tests/test_backtest_archive_capture.py",
        "trading_system/tests/test_backtest_raw_market_fetch.py",
    ],
    "runtime-support": [
        "trading_system/tests/test_run_cycle.py",
        "trading_system/tests/test_executor.py",
        "trading_system/tests/test_testnet_preview.py",
        "trading_system/tests/test_reporting.py",
        "trading_system/tests/test_runtime_paths.py",
        "trading_system/tests/test_main_v2_cycle.py",
    ],
    "paper-optimization": [
        "trading_system/tests/test_paper_optimization_collector.py",
        "trading_system/tests/test_paper_optimization_metrics.py",
        "trading_system/tests/test_paper_optimization_outcomes.py",
        "trading_system/tests/test_paper_optimization_promotion.py",
        "trading_system/tests/test_paper_optimization_recommendations.py",
        "trading_system/tests/test_paper_optimization_reporting.py",
        "trading_system/tests/test_paper_optimization_validation.py",
    ],
    "app-smoke": [
        "trading_system/tests/test_main_v2_cycle.py",
        "trading_system/tests/test_run_cycle.py",
        "trading_system/tests/test_executor.py",
        "trading_system/tests/test_reporting.py",
        "trading_system/tests/test_validator.py",
    ],
    "workflow-meta": [
        "trading_system/tests/test_development_workflow.py",
        "trading_system/tests/test_development_workflow_docs.py",
        "trading_system/tests/test_development_workflow_impact_map.py",
        "trading_system/tests/test_development_workflow_worker_audit.py",
    ],
    "full": [],
}

FORBIDDEN_CHANGED_FILES = {"memory/dev-status.md"}


IMPACT_RULES: tuple[tuple[str, list[str]], ...] = (
    ("trading_system/app/main.py", SUITES["runtime-main"]),
    ("trading_system/app/universe/", SUITES["universe"]),
    ("trading_system/app/portfolio/", SUITES["portfolio"]),
    ("trading_system/app/runtime/runtime_safety_evidence.py", SUITES["evidence-chain"]),
    ("trading_system/app/runtime/ledger_reconciliation.py", SUITES["evidence-chain"]),
    ("trading_system/tests/test_ledger_reconciliation.py", ["trading_system/tests/test_ledger_reconciliation.py"]),
    ("trading_system/app/runtime/", SUITES["runtime-support"]),
    ("trading_system/app/paper_optimization/", SUITES["paper-optimization"]),
    ("trading_system/run_cycle.py", SUITES["runtime-support"]),
    ("trading_system/app/backtest/archive/", SUITES["archive-data"]),
    ("trading_system/app/backtest/live_readiness.py", SUITES["evidence-chain"]),
    (
        "trading_system/scheduled_live_sim_generation.py",
        ["trading_system/tests/test_scheduled_live_sim_generation.py"],
    ),
    (
        "trading_system/app/reporting/promotion_readiness_scorecard.py",
        [
            "trading_system/tests/test_promotion_readiness_scorecard.py",
            "trading_system/tests/test_daily_quality_gate_report.py",
        ],
    ),
    (
        "trading_system/app/reporting/promotion_readiness_scorecard_trend.py",
        [
            "trading_system/tests/test_promotion_readiness_scorecard_trend.py",
            "trading_system/tests/test_scheduled_live_sim_generation.py",
        ],
    ),
    (
        "trading_system/generate_execution_calibration_records.py",
        [
            "trading_system/tests/test_generate_execution_calibration_records.py",
            "trading_system/tests/test_execution_calibration_evidence.py",
            "trading_system/tests/test_scheduled_live_sim_generation.py",
        ],
    ),
    ("trading_system/app/execution/calibration.py", ["trading_system/tests/test_execution_calibration_evidence.py"]),
    ("deploy/cron/trading-system-paper-cron.sh", ["trading_system/tests/test_scheduled_live_sim_generation.py"]),
    (
        "trading_system/app/backtest/dataset.py",
        [
            *SUITES["backtest-core"],
            *[test for test in SUITES["archive-data"] if test not in SUITES["backtest-core"]],
        ],
    ),
    ("trading_system/app/backtest/microstructure_evidence.py", SUITES["evidence-chain"]),
    ("trading_system/app/backtest/derivatives_risk.py", SUITES["evidence-chain"]),
    ("trading_system/app/backtest/validation_evidence.py", SUITES["evidence-chain"]),
    ("trading_system/app/backtest/promotion_evidence_bundle.py", SUITES["evidence-chain"]),
    (
        "trading_system/app/reporting/rolling_simulated_live_evidence_bundle.py",
        SUITES["evidence-chain"],
    ),
    (
        "trading_system/app/reporting/simulated_live_evidence_window.py",
        SUITES["evidence-chain"],
    ),
    (
        "trading_system/app/reporting/real_local_simulated_live_evidence_chain.py",
        SUITES["evidence-chain"],
    ),
    (
        "trading_system/app/reporting/simulated_live_artifact_inventory.py",
        SUITES["evidence-chain"],
    ),
    (
        "trading_system/app/reporting/execution_stream_producers.py",
        [
            "trading_system/tests/test_execution_stream_producers.py",
            "trading_system/tests/test_rolling_simulated_live_evidence_bundle.py",
            "trading_system/tests/test_simulated_live_artifact_inventory.py",
        ],
    ),
    (
        "trading_system/app/reporting/market_coverage.py",
        [
            "trading_system/tests/test_market_coverage_reports.py",
            "trading_system/tests/test_rolling_simulated_live_evidence_bundle.py",
            "trading_system/tests/test_simulated_live_artifact_inventory.py",
        ],
    ),
    (
        "trading_system/app/reporting/promotion_gate_decision.py",
        [
            "trading_system/tests/test_promotion_gate_decision.py",
            "trading_system/tests/test_simulated_live_evidence_window.py",
            "trading_system/tests/test_promotion_readiness_scorecard_trend.py",
            "trading_system/tests/test_execution_calibration_evidence.py",
        ],
    ),
    (
        "trading_system/app/reporting/longitudinal_promotion_decision_archive.py",
        ["trading_system/tests/test_longitudinal_promotion_decision_archive.py"],
    ),
    (
        "trading_system/app/reporting/replay_simulated_live_evidence.py",
        SUITES["evidence-chain"],
    ),
    (
        "trading_system/generate_rolling_simulated_live_evidence_bundle.py",
        ["trading_system/tests/test_rolling_simulated_live_evidence_bundle.py"],
    ),
    (
        "trading_system/generate_simulated_live_evidence_window.py",
        ["trading_system/tests/test_simulated_live_evidence_window.py"],
    ),
    (
        "trading_system/generate_real_local_simulated_live_evidence_chain.py",
        ["trading_system/tests/test_real_local_simulated_live_evidence_chain.py"],
    ),
    (
        "trading_system/generate_simulated_live_artifact_inventory.py",
        ["trading_system/tests/test_simulated_live_artifact_inventory.py"],
    ),
    (
        "trading_system/generate_execution_race_evidence.py",
        ["trading_system/tests/test_execution_stream_producers.py"],
    ),
    (
        "trading_system/generate_l2_longitudinal_replay_calibration.py",
        ["trading_system/tests/test_execution_stream_producers.py"],
    ),
    (
        "trading_system/generate_cross_source_parity_report.py",
        ["trading_system/tests/test_market_coverage_reports.py"],
    ),
    (
        "trading_system/generate_venue_rulebook_catalog_freshness.py",
        ["trading_system/tests/test_market_coverage_reports.py"],
    ),
    (
        "trading_system/generate_promotion_gate_decision.py",
        ["trading_system/tests/test_promotion_gate_decision.py"],
    ),
    (
        "trading_system/generate_longitudinal_promotion_decision_archive.py",
        ["trading_system/tests/test_longitudinal_promotion_decision_archive.py"],
    ),
    (
        "trading_system/generate_replay_simulated_live_evidence_bundle.py",
        ["trading_system/tests/test_replay_simulated_live_evidence.py"],
    ),
    (
        "trading_system/generate_simulated_live_cadence_runner.py",
        ["trading_system/tests/test_simulated_live_cadence_runner.py"],
    ),
    ("trading_system/app/backtest/promotion.py", [
        "trading_system/tests/test_backtest_promotion.py",
        "trading_system/tests/test_backtest_live_readiness.py",
    ]),
    ("trading_system/tests/fixtures/backtest/full_market_baseline_dataset/", SUITES["backtest-core"]),
    ("trading_system/app/backtest/", SUITES["backtest-core"]),
    (
        "trading_system/tests/test_longitudinal_live_sim_trend_report.py",
        [
            "trading_system/tests/test_longitudinal_live_sim_trend_report.py",
            "trading_system/tests/test_scheduled_live_sim_generation.py",
        ],
    ),
    (
        "trading_system/tests/test_promotion_readiness_scorecard.py",
        [
            "trading_system/tests/test_promotion_readiness_scorecard.py",
            "trading_system/tests/test_scheduled_live_sim_generation.py",
        ],
    ),
    (
        "trading_system/tests/test_promotion_readiness_scorecard_trend.py",
        [
            "trading_system/tests/test_promotion_readiness_scorecard_trend.py",
            "trading_system/tests/test_scheduled_live_sim_generation.py",
        ],
    ),
    (
        "trading_system/tests/test_promotion_gate_decision.py",
        [
            "trading_system/tests/test_promotion_gate_decision.py",
            "trading_system/tests/test_simulated_live_evidence_window.py",
            "trading_system/tests/test_promotion_readiness_scorecard_trend.py",
            "trading_system/tests/test_execution_calibration_evidence.py",
        ],
    ),
    (
        "trading_system/tests/test_simulated_live_cadence_runner.py",
        ["trading_system/tests/test_simulated_live_cadence_runner.py"],
    ),
    (
        "trading_system/tests/test_longitudinal_promotion_decision_archive.py",
        ["trading_system/tests/test_longitudinal_promotion_decision_archive.py"],
    ),
    ("scripts/audit_worker_commit.py", SUITES["workflow-meta"]),
    ("scripts/ci_verify.py", SUITES["workflow-meta"]),
    ("scripts/nightly_verify.py", SUITES["workflow-meta"]),
    ("scripts/trading_system_sanitized_verify.sh", SUITES["workflow-meta"]),
    ("scripts/verify.py", SUITES["workflow-meta"]),
    ("trading_system/tests/test_development_workflow", SUITES["workflow-meta"]),
    ("AGENTS.md", SUITES["workflow-meta"]),
    ("README.md", SUITES["workflow-meta"]),
    ("trading_system/README.md", SUITES["workflow-meta"]),
    ("trading_system/app/README.md", SUITES["workflow-meta"]),
    ("docs/development-workflow.md", SUITES["workflow-meta"]),
    ("docs/plans/", SUITES["workflow-meta"]),
    ("templates/", SUITES["workflow-meta"]),
    ("trading_system/app/", SUITES["app-smoke"]),
)


def unique(items: list[str]) -> list[str]:
    return list(OrderedDict.fromkeys(items))


def validate_changed_paths(paths: list[str], *, label: str = "changed path") -> None:
    seen: set[str] = set()
    for path in paths:
        if not path:
            raise ValueError(f"{label} must be non-empty")
        if path in seen:
            raise ValueError(f"duplicate {label}: {path}")
        seen.add(path)


def validate_repeated_args(values: list[str], *, label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if not value:
            raise ValueError(f"{label} must be non-empty")
        if value in seen:
            raise ValueError(f"duplicate {label}: {value}")
        seen.add(value)


def validate_non_negative(value: int | None, *, label: str) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{label} must be non-negative")


def suites_for_test_path(path: str) -> list[str]:
    selected: list[str] = []
    for tests in SUITES.values():
        if path in tests:
            selected.extend(tests)
    return unique(selected)


def tests_for_changed(paths: list[str]) -> list[str]:
    selected: list[str] = []
    for changed in paths:
        selected.extend(suites_for_test_path(changed))
        for prefix, tests in IMPACT_RULES:
            if changed == prefix or changed.startswith(prefix):
                selected.extend(tests)
    return unique(selected)


def unmapped_changed_paths(paths: list[str]) -> list[str]:
    unmapped: list[str] = []
    for changed in paths:
        if suites_for_test_path(changed):
            continue
        if not any(changed == prefix or changed.startswith(prefix) for prefix, _tests in IMPACT_RULES):
            unmapped.append(changed)
    return unique(unmapped)


def _git_lines(command: list[str]) -> list[str]:
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"{' '.join(command)} failed")
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def git_changed_paths() -> list[str]:
    return unique(
        _git_lines(["git", "diff", "--name-only", "HEAD"])
        + _git_lines(["git", "ls-files", "--others", "--exclude-standard"])
    )


def build_tests(*, suites: list[str], changed: list[str], explicit_tests: list[str] | None = None) -> tuple[list[str], bool]:
    tests: list[str] = []
    full = False
    for suite in suites:
        if suite not in SUITES:
            raise ValueError(f"unknown suite: {suite}")
        if suite == "full":
            full = True
        else:
            tests.extend(SUITES[suite])
    tests.extend(tests_for_changed(changed))
    tests.extend(explicit_tests or [])
    return unique(tests), full


def build_command_argv(*, suites: list[str], changed: list[str], explicit_tests: list[str] | None = None) -> list[list[str]]:
    tests, full = build_tests(suites=suites, changed=changed, explicit_tests=explicit_tests)
    commands: list[list[str]] = []
    if full:
        commands.append(["python3", "-m", "pytest", "-q"])
    elif tests:
        commands.append(["python3", "-m", "pytest", "-q", *tests])
    else:
        commands.append(["python3", "-m", "pytest", "-q", "trading_system/tests/test_development_workflow.py"])
    commands.append(DIFF_CHECK.split())
    return commands


def build_commands(*, suites: list[str], changed: list[str], explicit_tests: list[str] | None = None) -> list[str]:
    return [" ".join(argv) for argv in build_command_argv(suites=suites, changed=changed, explicit_tests=explicit_tests)]


def validate_test_path_argv(commands: list[list[str]]) -> None:
    pytest_prefix = ["python3", "-m", "pytest", "-q"]
    for command in commands:
        if command[:4] != pytest_prefix:
            continue
        for token in command[4:]:
            if token.startswith("-"):
                continue
            path = Path(token)
            if not path.exists():
                raise ValueError(f"missing verification path: {token}")


def sanitized_env(env: dict[str, str] | None = None) -> dict[str, str]:
    cleaned = dict(os.environ if env is None else env)
    for key in list(cleaned):
        if any(key.startswith(prefix) for prefix in SANITIZED_ENV_REMOVED_PREFIXES):
            cleaned.pop(key, None)
    return cleaned


def run_command_argv(commands: list[list[str]], *, clean_trading_env: bool = False) -> int:
    env = sanitized_env() if clean_trading_env else None
    for command in commands:
        print(f"$ {' '.join(command)}", flush=True)
        kwargs: dict[str, object] = {"text": True, "shell": False}
        if env is not None:
            kwargs["env"] = env
        completed = subprocess.run(command, **kwargs)
        if completed.returncode != 0:
            return completed.returncode
    return 0


def canonical_fingerprint(payload: dict[str, object], *, excluded_field: str) -> str:
    canonical_payload = dict(payload)
    canonical_payload.pop(excluded_field, None)
    canonical = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_fingerprint(payload: dict[str, object]) -> str:
    return canonical_fingerprint(payload, excluded_field="plan_fingerprint")


def inventory_fingerprint(payload: dict[str, object]) -> str:
    return canonical_fingerprint(payload, excluded_field="inventory_fingerprint")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic trading-system verification entrypoint")
    parser.add_argument("--suite", action="append", default=[], help="fixed suite: evidence-chain, runtime-main, universe, full")
    parser.add_argument("--changed", action="append", default=[], help="changed repository path for impact-based tests")
    parser.add_argument("--test", action="append", default=[], help="explicit test path to include")
    parser.add_argument("--auto-changed", action="store_true", help="include paths from git diff --name-only HEAD")
    parser.add_argument("--strict-auto-changed", action="store_true", help="fail if changed paths do not map to any tests and no explicit suite/test is selected")
    parser.add_argument("--dry-run", action="store_true", help="print commands without executing")
    parser.add_argument("--json", action="store_true", help="with --dry-run, emit the verification plan as JSON")
    parser.add_argument("--require-full-after", type=int, default=None, help="force full suite when --slice-count reaches this threshold")
    parser.add_argument("--slice-count", type=int, default=0, help="number of completed slices since the last full-suite checkpoint")
    parser.add_argument("--list-suites", action="store_true", help="list fixed verification suites")
    args = parser.parse_args(argv)

    if args.json and not args.dry_run and not args.list_suites:
        print("--json requires --dry-run", file=sys.stderr)
        return 2

    if args.list_suites:
        if args.json:
            payload = {
                "plan_version": 1,
                "inventory_version": 1,
                "inventory_kind": "suite_inventory",
                "suites": {
                    name: {
                        "count": "full pytest suite" if name == "full" else len(tests),
                        "tests": tests,
                    }
                    for name, tests in SUITES.items()
                },
            }
            payload["inventory_fingerprint"] = inventory_fingerprint(payload)
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for name, tests in SUITES.items():
                count = "full pytest suite" if name == "full" else f"{len(tests)} test paths"
                print(f"{name}: {count}")
        return 0

    changed = list(args.changed)
    if args.auto_changed or (args.strict_auto_changed and not changed):
        try:
            changed.extend(git_changed_paths())
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    try:
        validate_changed_paths(changed)
        validate_repeated_args(list(args.suite), label="suite")
        validate_repeated_args(list(args.test), label="explicit test")
        validate_non_negative(args.require_full_after, label="require-full-after")
        validate_non_negative(args.slice_count, label="slice-count")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    suites = list(args.suite)
    full_checkpoint_reason = None
    if args.require_full_after is not None and args.slice_count >= args.require_full_after:
        suites.append("full")
        full_checkpoint_reason = f"slice_count {args.slice_count} reached threshold {args.require_full_after}"

    try:
        tests, full = build_tests(suites=suites, changed=changed, explicit_tests=args.test)
        forbidden = [path for path in changed if path in FORBIDDEN_CHANGED_FILES]
        if args.strict_auto_changed and forbidden:
            print(f"forbidden changed file: {', '.join(forbidden)}", file=sys.stderr)
            return 2
        unmapped = unmapped_changed_paths(changed)
        if args.strict_auto_changed and changed and unmapped:
            print(f"no impacted verification tests for changed files: {', '.join(unmapped)}", file=sys.stderr)
            return 2
        commands = build_commands(suites=suites, changed=changed, explicit_tests=args.test)
        command_argv = build_command_argv(suites=suites, changed=changed, explicit_tests=args.test)
        validate_test_path_argv(command_argv)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.dry_run:
        if args.json:
            payload = {
                "plan_version": 1,
                "plan_kind": "verification_plan",
                "suites": suites,
                "changed": changed,
                "explicit_tests": args.test,
                "strict_changed_verification": bool(args.strict_auto_changed),
                "full": full,
                "full_checkpoint_reason": full_checkpoint_reason,
                "sanitized_env": full,
                "sanitized_env_removed_prefixes": SANITIZED_ENV_REMOVED_PREFIXES if full else [],
                "tests": tests,
                "commands": commands,
                "command_argv": command_argv,
            }
            payload["plan_fingerprint"] = plan_fingerprint(payload)
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("\n".join(commands))
        return 0
    return run_commands(commands, command_argv, clean_trading_env=full)


def run_commands(commands: list[str], command_argv: list[list[str]], *, clean_trading_env: bool = False) -> int:
    return run_command_argv(command_argv, clean_trading_env=clean_trading_env)


if __name__ == "__main__":
    raise SystemExit(main())
