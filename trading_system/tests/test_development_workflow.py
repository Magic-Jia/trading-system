from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "scripts" / "verify.py"


def run_verify(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_verify_dry_run_maps_main_changes_to_runtime_regression() -> None:
    result = run_verify("--dry-run", "--changed", "trading_system/app/main.py")

    assert result.returncode == 0, result.stderr
    assert "python3 -m pytest -q" in result.stdout
    assert "trading_system/tests/test_main_v2_cycle.py" in result.stdout
    assert "trading_system/tests/test_backtest_live_readiness.py" in result.stdout
    assert "trading_system/tests/test_backtest_promotion.py" in result.stdout
    assert "git diff --check HEAD" in result.stdout


def test_verify_dry_run_maps_universe_changes_to_universe_regression() -> None:
    result = run_verify("--dry-run", "--changed", "trading_system/app/universe/liquidity_filter.py")

    assert result.returncode == 0, result.stderr
    assert "trading_system/tests/test_universe_liquidity_filter.py" in result.stdout
    assert "trading_system/tests/test_universe_builder.py" in result.stdout
    assert "trading_system/tests/test_backtest_universe.py" in result.stdout
    assert "trading_system/tests/test_main_v2_cycle.py" in result.stdout


def test_verify_dry_run_fixed_evidence_chain_is_explicit_and_sorted() -> None:
    result = run_verify("--dry-run", "--suite", "evidence-chain")

    assert result.returncode == 0, result.stderr
    stdout = result.stdout
    assert "trading_system/tests/test_backtest_live_readiness.py" in stdout
    assert "trading_system/tests/test_runtime_safety_evidence.py" in stdout
    assert "trading_system/tests/test_backtest_microstructure_evidence.py" in stdout
    assert "trading_system/tests/test_backtest_validation_evidence.py" in stdout
    assert "trading_system/tests/test_execution_calibration_evidence.py" in stdout
    assert "trading_system/tests/test_backtest_promotion_evidence_bundle.py" in stdout
    assert "trading_system/tests/test_backtest_setup_rewrite_experiment.py" in stdout
    assert "trading_system/tests/test_backtest_promotion.py" in stdout
    assert "trading_system/tests/test_main_v2_cycle.py" in stdout


def test_verify_rejects_unknown_suite() -> None:
    result = run_verify("--dry-run", "--suite", "not-a-suite")

    assert result.returncode == 2
    assert "unknown suite" in result.stderr


def test_verify_auto_detects_git_changed_paths() -> None:
    result = run_verify("--dry-run", "--auto-changed")

    assert result.returncode == 0, result.stderr
    assert "trading_system/tests/test_development_workflow.py" in result.stdout


def test_verify_maps_docs_and_templates_to_workflow_doc_tests() -> None:
    result = run_verify("--dry-run", "--changed", "docs/development-workflow.md", "--changed", "templates/codex-worker-prompt.md")

    assert result.returncode == 0, result.stderr
    assert "trading_system/tests/test_development_workflow_docs.py" in result.stdout


def test_verify_auto_changed_includes_untracked_files() -> None:
    marker = ROOT / "templates" / ".verify-untracked-marker.md"
    marker.parent.mkdir(exist_ok=True)
    marker.write_text("temporary untracked marker\n")
    try:
        result = run_verify("--dry-run", "--auto-changed")
    finally:
        marker.unlink(missing_ok=True)

    assert result.returncode == 0, result.stderr
    assert "trading_system/tests/test_development_workflow_docs.py" in result.stdout
