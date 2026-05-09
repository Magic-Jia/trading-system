#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
    "full": [],
}

IMPACT_RULES: tuple[tuple[str, list[str]], ...] = (
    ("trading_system/app/main.py", SUITES["runtime-main"]),
    ("trading_system/app/universe/", SUITES["universe"]),
    ("trading_system/app/backtest/live_readiness.py", SUITES["evidence-chain"]),
    ("trading_system/app/runtime/runtime_safety_evidence.py", SUITES["evidence-chain"]),
    ("trading_system/app/backtest/microstructure_evidence.py", SUITES["evidence-chain"]),
    ("trading_system/app/backtest/validation_evidence.py", SUITES["evidence-chain"]),
    ("trading_system/app/backtest/promotion_evidence_bundle.py", SUITES["evidence-chain"]),
    ("trading_system/app/backtest/promotion.py", [
        "trading_system/tests/test_backtest_promotion.py",
        "trading_system/tests/test_backtest_live_readiness.py",
    ]),
    ("scripts/verify.py", ["trading_system/tests/test_development_workflow.py"]),
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


def git_changed_paths() -> list[str]:
    completed = subprocess.run(
        "git diff --name-only HEAD",
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git diff --name-only HEAD failed")
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def build_commands(*, suites: list[str], changed: list[str]) -> list[str]:
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
    commands: list[str] = []
    if full:
        commands.append(TEST)
    elif tests:
        commands.append(f"{TEST} {' '.join(unique(tests))}")
    else:
        commands.append(f"{TEST} trading_system/tests/test_development_workflow.py")
    commands.append(DIFF_CHECK)
    return commands


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
    parser.add_argument("--auto-changed", action="store_true", help="include paths from git diff --name-only HEAD")
    parser.add_argument("--dry-run", action="store_true", help="print commands without executing")
    args = parser.parse_args(argv)

    changed = list(args.changed)
    if args.auto_changed:
        try:
            changed.extend(git_changed_paths())
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    try:
        commands = build_commands(suites=args.suite, changed=changed)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.dry_run:
        print("\n".join(commands))
        return 0
    return run_commands(commands)


if __name__ == "__main__":
    raise SystemExit(main())
