#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path


TEST = "python3 -m pytest -q"
DIFF_CHECK = "git diff --check HEAD"

SUITES: dict[str, list[str]] = {
    "evidence-chain": [
        "trading_system/tests/test_backtest_live_readiness.py",
        "trading_system/tests/test_runtime_safety_evidence.py",
        "trading_system/tests/test_backtest_microstructure_evidence.py",
        "trading_system/tests/test_backtest_validation_evidence.py",
        "trading_system/tests/test_execution_calibration_evidence.py",
        "trading_system/tests/test_backtest_promotion_evidence_bundle.py",
        "trading_system/tests/test_backtest_setup_rewrite_experiment.py",
        "trading_system/tests/test_backtest_promotion.py",
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
        "trading_system/tests/test_target_management_state.py",
        "trading_system/tests/test_main_v2_cycle.py",
    ],
    "backtest-core": [
        "trading_system/tests/test_backtest_engine.py",
        "trading_system/tests/test_backtest_portfolio.py",
        "trading_system/tests/test_backtest_costs.py",
        "trading_system/tests/test_backtest_dataset.py",
        "trading_system/tests/test_backtest_reporting.py",
        "trading_system/tests/test_backtest_promotion.py",
        "trading_system/tests/test_main_v2_cycle.py",
    ],
    "archive-data": [
        "trading_system/tests/test_backtest_archive_importer.py",
        "trading_system/tests/test_backtest_archive_dataset_importer.py",
        "trading_system/tests/test_backtest_archive_raw_market.py",
        "trading_system/tests/test_backtest_archive_binance_execution_downloader.py",
        "trading_system/tests/test_backtest_archive_capture.py",
        "trading_system/tests/test_backtest_raw_market_fetch.py",
    ],
    "runtime-support": [
        "trading_system/tests/test_run_cycle.py",
        "trading_system/tests/test_executor.py",
        "trading_system/tests/test_reporting.py",
        "trading_system/tests/test_runtime_paths.py",
        "trading_system/tests/test_main_v2_cycle.py",
    ],
    "app-smoke": [
        "trading_system/tests/test_main_v2_cycle.py",
        "trading_system/tests/test_run_cycle.py",
        "trading_system/tests/test_executor.py",
        "trading_system/tests/test_reporting.py",
        "trading_system/tests/test_validator.py",
    ],
    "full": [],
}

IMPACT_RULES: tuple[tuple[str, list[str]], ...] = (
    ("trading_system/app/main.py", SUITES["runtime-main"]),
    ("trading_system/app/universe/", SUITES["universe"]),
    ("trading_system/app/portfolio/", SUITES["portfolio"]),
    ("trading_system/app/runtime/runtime_safety_evidence.py", SUITES["evidence-chain"]),
    ("trading_system/app/runtime/", SUITES["runtime-support"]),
    ("trading_system/app/backtest/archive/", SUITES["archive-data"]),
    ("trading_system/app/backtest/live_readiness.py", SUITES["evidence-chain"]),
    ("trading_system/app/backtest/microstructure_evidence.py", SUITES["evidence-chain"]),
    ("trading_system/app/backtest/validation_evidence.py", SUITES["evidence-chain"]),
    ("trading_system/app/backtest/promotion_evidence_bundle.py", SUITES["evidence-chain"]),
    ("trading_system/app/backtest/promotion.py", [
        "trading_system/tests/test_backtest_promotion.py",
        "trading_system/tests/test_backtest_live_readiness.py",
    ]),
    ("trading_system/app/backtest/", SUITES["backtest-core"]),
    ("scripts/verify.py", ["trading_system/tests/test_development_workflow.py"]),
    ("docs/development-workflow.md", ["trading_system/tests/test_development_workflow_docs.py"]),
    ("templates/", ["trading_system/tests/test_development_workflow_docs.py"]),
    ("trading_system/app/", SUITES["app-smoke"]),
)


def unique(items: list[str]) -> list[str]:
    return list(OrderedDict.fromkeys(items))


def tests_for_changed(paths: list[str]) -> list[str]:
    selected: list[str] = []
    for changed in paths:
        for prefix, tests in IMPACT_RULES:
            if changed == prefix or changed.startswith(prefix):
                selected.extend(tests)
    return unique(selected)


def _git_lines(command: str) -> list[str]:
    completed = subprocess.run(
        command,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"{command} failed")
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def git_changed_paths() -> list[str]:
    return unique(
        _git_lines("git diff --name-only HEAD")
        + _git_lines("git ls-files --others --exclude-standard")
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


def build_commands(*, suites: list[str], changed: list[str], explicit_tests: list[str] | None = None) -> list[str]:
    tests, full = build_tests(suites=suites, changed=changed, explicit_tests=explicit_tests)
    commands: list[str] = []
    if full:
        commands.append(TEST)
    elif tests:
        commands.append(f"{TEST} {' '.join(tests)}")
    else:
        commands.append(f"{TEST} trading_system/tests/test_development_workflow.py")
    commands.append(DIFF_CHECK)
    return commands


def validate_test_paths(commands: list[str]) -> None:
    for command in commands:
        if not command.startswith(TEST + " "):
            continue
        for token in command[len(TEST) :].strip().split():
            if token.startswith("-"):
                continue
            path = Path(token)
            if not path.exists():
                raise ValueError(f"missing verification path: {token}")


def run_commands(commands: list[str]) -> int:
    for command in commands:
        print(f"$ {command}", flush=True)
        completed = subprocess.run(command, shell=True, text=True)
        if completed.returncode != 0:
            return completed.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic trading-system verification entrypoint")
    parser.add_argument("--suite", action="append", default=[], help="fixed suite: evidence-chain, runtime-main, universe, full")
    parser.add_argument("--changed", action="append", default=[], help="changed repository path for impact-based tests")
    parser.add_argument("--test", action="append", default=[], help="explicit test path to include")
    parser.add_argument("--auto-changed", action="store_true", help="include paths from git diff --name-only HEAD")
    parser.add_argument("--dry-run", action="store_true", help="print commands without executing")
    parser.add_argument("--json", action="store_true", help="with --dry-run, emit the verification plan as JSON")
    parser.add_argument("--list-suites", action="store_true", help="list fixed verification suites")
    args = parser.parse_args(argv)

    if args.list_suites:
        for name, tests in SUITES.items():
            count = "full pytest suite" if name == "full" else f"{len(tests)} test paths"
            print(f"{name}: {count}")
        return 0

    changed = list(args.changed)
    if args.auto_changed:
        try:
            changed.extend(git_changed_paths())
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    try:
        commands = build_commands(suites=args.suite, changed=changed, explicit_tests=args.test)
        validate_test_paths(commands)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.dry_run:
        if args.json:
            tests, full = build_tests(suites=args.suite, changed=changed, explicit_tests=args.test)
            payload = {
                "suites": args.suite,
                "changed": changed,
                "explicit_tests": args.test,
                "full": full,
                "tests": tests,
                "commands": commands,
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("\n".join(commands))
        return 0
    return run_commands(commands)


if __name__ == "__main__":
    raise SystemExit(main())
