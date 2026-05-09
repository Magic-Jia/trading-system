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


def test_verify_maps_worker_audit_script_to_worker_audit_tests() -> None:
    result = run_verify("--dry-run", "--changed", "scripts/audit_worker_commit.py")

    assert result.returncode == 0, result.stderr
    assert "trading_system/tests/test_development_workflow_worker_audit.py" in result.stdout


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


def test_verify_rejects_missing_explicit_test_path_before_pytest() -> None:
    result = run_verify("--dry-run", "--test", "trading_system/tests/does_not_exist.py")

    assert result.returncode == 2
    assert "missing verification path" in result.stderr


def test_verify_lists_available_suites() -> None:
    result = run_verify("--list-suites")

    assert result.returncode == 0, result.stderr
    assert "evidence-chain" in result.stdout
    assert "runtime-main" in result.stdout
    assert "universe" in result.stdout
    assert "full" in result.stdout


def test_verify_json_dry_run_emits_machine_readable_plan() -> None:
    result = run_verify("--dry-run", "--json", "--changed", "trading_system/app/main.py")

    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout)
    assert payload["suites"] == []
    assert payload["changed"] == ["trading_system/app/main.py"]
    assert "trading_system/tests/test_main_v2_cycle.py" in payload["tests"]
    assert payload["commands"][-1] == "git diff --check HEAD"


def test_verify_requires_full_after_slice_threshold() -> None:
    result = run_verify("--dry-run", "--json", "--require-full-after", "3", "--slice-count", "3", "--changed", "trading_system/app/main.py")

    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout)
    assert payload["full"] is True
    assert payload["commands"][0] == "python3 -m pytest -q"


def test_verify_json_requires_dry_run() -> None:
    result = run_verify("--json", "--changed", "trading_system/app/main.py")

    assert result.returncode == 2
    assert "--json requires --dry-run" in result.stderr


def test_verify_exposes_workflow_meta_suite() -> None:
    result = run_verify("--list-suites")

    assert result.returncode == 0, result.stderr
    assert "workflow-meta" in result.stdout

    dry_run = run_verify("--dry-run", "--suite", "workflow-meta")
    assert dry_run.returncode == 0, dry_run.stderr
    assert "trading_system/tests/test_development_workflow.py" in dry_run.stdout
    assert "trading_system/tests/test_development_workflow_docs.py" in dry_run.stdout
    assert "trading_system/tests/test_development_workflow_impact_map.py" in dry_run.stdout
    assert "trading_system/tests/test_development_workflow_worker_audit.py" in dry_run.stdout


def test_verify_maps_workflow_tool_changes_to_workflow_meta_suite() -> None:
    result = run_verify("--dry-run", "--changed", "scripts/verify.py")

    assert result.returncode == 0, result.stderr
    assert "trading_system/tests/test_development_workflow.py" in result.stdout
    assert "trading_system/tests/test_development_workflow_docs.py" in result.stdout
    assert "trading_system/tests/test_development_workflow_impact_map.py" in result.stdout
    assert "trading_system/tests/test_development_workflow_worker_audit.py" in result.stdout


def test_verify_strict_auto_changed_rejects_unmapped_paths() -> None:
    result = run_verify("--dry-run", "--strict-auto-changed", "--changed", "UNKNOWN_UNMAPPED_FILE.txt")

    assert result.returncode == 2
    assert "no impacted verification tests" in result.stderr


def test_verify_strict_auto_changed_implies_auto_changed(tmp_path: Path) -> None:
    probe = ROOT / "UNTRACKED_STRICT_AUTO_CHANGED.txt"
    try:
        probe.write_text("temporary strict-auto-changed probe\n")
        result = run_verify("--dry-run", "--json", "--strict-auto-changed")
    finally:
        probe.unlink(missing_ok=True)

    assert result.returncode == 2
    assert "no impacted verification tests" in result.stderr


def test_ci_verify_entrypoint_runs_strict_workflow_and_evidence_chain() -> None:
    script = ROOT / "scripts" / "ci_verify.py"

    assert script.exists()
    text = script.read_text()
    assert "--strict-auto-changed" in text
    assert "--suite workflow-meta" in text
    assert "--suite evidence-chain" in text

    result = run_verify("--dry-run", "--changed", "scripts/ci_verify.py")
    assert result.returncode == 0, result.stderr
    assert "trading_system/tests/test_development_workflow.py" in result.stdout
    assert "trading_system/tests/test_development_workflow_docs.py" in result.stdout
