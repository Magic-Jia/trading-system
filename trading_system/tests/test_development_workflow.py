from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "scripts" / "verify.py"
SANITIZED_VERIFY = ROOT / "scripts" / "trading_system_sanitized_verify.sh"

SUITE_INVENTORY_JSON_KEYS = {"inventory_fingerprint", "inventory_kind", "inventory_version", "plan_version", "suites"}
VERIFICATION_PLAN_JSON_KEYS = {
    "changed",
    "command_argv",
    "commands",
    "explicit_tests",
    "full",
    "full_checkpoint_reason",
    "plan_fingerprint",
    "plan_kind",
    "plan_version",
    "sanitized_env",
    "sanitized_env_removed_prefixes",
    "strict_changed_verification",
    "suites",
    "tests",
}
CI_PLAN_JSON_KEYS = {
    "command_argv",
    "commands",
    "entrypoint",
    "plan_fingerprint",
    "plan_kind",
    "plan_version",
    "strict_changed_verification",
    "suites",
}
NIGHTLY_PLAN_JSON_KEYS = {
    "clean_env",
    "command_argv",
    "commands",
    "entrypoint",
    "plan_fingerprint",
    "plan_kind",
    "plan_version",
    "sanitized_env_removed_prefixes",
    "suites",
    "unset_env",
}
SANITIZED_VERIFY_CONTRACT_JSON_KEYS = {
    "command_argv",
    "contract_kind",
    "contract_version",
    "entrypoint",
    "python",
    "unset_env",
}


def run_verify(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def run_sanitized_verify(
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = None
    if env is not None:
        import os

        merged_env = os.environ.copy()
        merged_env.update(env)
    return subprocess.run(
        [str(SANITIZED_VERIFY), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=merged_env,
        check=False,
    )


def expected_plan_fingerprint(payload: dict[str, object]) -> str:
    payload_without_fingerprint = dict(payload)
    payload_without_fingerprint.pop("plan_fingerprint")
    canonical = json.dumps(payload_without_fingerprint, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def expected_inventory_fingerprint(payload: dict[str, object]) -> str:
    payload_without_fingerprint = dict(payload)
    payload_without_fingerprint.pop("inventory_fingerprint")
    canonical = json.dumps(payload_without_fingerprint, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verify_dry_run_maps_main_changes_to_runtime_regression() -> None:
    result = run_verify("--dry-run", "--changed", "trading_system/app/main.py")

    assert result.returncode == 0, result.stderr
    assert "python3 -m pytest -q" in result.stdout
    assert "trading_system/tests/test_main_v2_cycle.py" in result.stdout
    assert "trading_system/tests/test_backtest_live_readiness.py" in result.stdout
    assert "trading_system/tests/test_backtest_promotion.py" in result.stdout
    assert "git --no-pager diff --check HEAD" in result.stdout


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
    assert "python3 -m pytest -q" in result.stdout
    assert "git --no-pager diff --check HEAD" in result.stdout


def test_verify_maps_docs_and_templates_to_workflow_doc_tests() -> None:
    result = run_verify("--dry-run", "--changed", "docs/development-workflow.md", "--changed", "templates/codex-worker-prompt.md")

    assert result.returncode == 0, result.stderr
    assert "trading_system/tests/test_development_workflow_docs.py" in result.stdout


def test_verify_maps_plan_docs_to_workflow_meta_suite() -> None:
    result = run_verify("--dry-run", "--changed", "docs/plans/2026-05-12-industry-best-correctness-closure.md")

    assert result.returncode == 0, result.stderr
    assert "trading_system/tests/test_development_workflow.py" in result.stdout
    assert "trading_system/tests/test_development_workflow_worker_audit.py" in result.stdout


def test_verify_maps_agent_rules_to_workflow_meta_suite() -> None:
    result = run_verify("--dry-run", "--changed", "AGENTS.md")

    assert result.returncode == 0, result.stderr
    assert "trading_system/tests/test_development_workflow.py" in result.stdout
    assert "trading_system/tests/test_development_workflow_docs.py" in result.stdout
    assert "trading_system/tests/test_development_workflow_worker_audit.py" in result.stdout


def test_verify_maps_worker_audit_script_to_worker_audit_tests() -> None:
    result = run_verify("--dry-run", "--changed", "scripts/audit_worker_commit.py")

    assert result.returncode == 0, result.stderr
    assert "trading_system/tests/test_development_workflow_worker_audit.py" in result.stdout


def test_verify_maps_paper_optimization_changes_to_owning_regression() -> None:
    result = run_verify("--dry-run", "--changed", "trading_system/app/paper_optimization/collector.py")

    assert result.returncode == 0, result.stderr
    assert "trading_system/tests/test_paper_optimization_collector.py" in result.stdout
    assert "trading_system/tests/test_paper_optimization_recommendations.py" in result.stdout


def test_verify_maps_backtest_tail_tests_to_backtest_core_regression() -> None:
    for changed in (
        "trading_system/tests/test_backtest_ablation_experiments.py",
        "trading_system/tests/test_backtest_execution_sim.py",
        "trading_system/tests/test_backtest_exit_policy_experiment.py",
    ):
        result = run_verify("--dry-run", "--changed", changed)

        assert result.returncode == 0, result.stderr
        assert "trading_system/tests/test_backtest_ablation_experiments.py" in result.stdout
        assert "trading_system/tests/test_backtest_execution_sim.py" in result.stdout
        assert "trading_system/tests/test_backtest_exit_policy_experiment.py" in result.stdout
        assert "trading_system/tests/test_backtest_evaluation.py" in result.stdout


def test_verify_maps_archive_runtime_bundle_tests_to_archive_data_regression() -> None:
    result = run_verify("--dry-run", "--changed", "trading_system/tests/test_backtest_archive_runtime_bundle.py")

    assert result.returncode == 0, result.stderr
    assert "trading_system/tests/test_backtest_archive_runtime_bundle.py" in result.stdout
    assert "trading_system/tests/test_backtest_archive_raw_market.py" in result.stdout


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
    assert "paper-optimization" in result.stdout
    assert "full" in result.stdout


def test_verify_lists_available_suites_as_json() -> None:
    result = run_verify("--list-suites", "--json")

    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout)
    assert set(payload) == SUITE_INVENTORY_JSON_KEYS
    assert payload["plan_version"] == 1
    assert payload["inventory_version"] == 1
    assert payload["inventory_kind"] == "suite_inventory"
    assert len(payload["inventory_fingerprint"]) == 64
    assert payload["inventory_fingerprint"] == expected_inventory_fingerprint(payload)
    repeat = run_verify("--list-suites", "--json")
    assert repeat.returncode == 0, repeat.stderr
    assert json.loads(repeat.stdout)["inventory_fingerprint"] == payload["inventory_fingerprint"]
    assert payload["suites"]["workflow-meta"]["count"] == 4
    assert "trading_system/tests/test_development_workflow.py" in payload["suites"]["workflow-meta"]["tests"]
    for suite_name, suite in payload["suites"].items():
        assert set(suite) == {"count", "tests"}
        assert len(suite["tests"]) == len(set(suite["tests"])), suite_name
        if suite_name != "full":
            assert suite["count"] == len(suite["tests"])
        for test_path in suite["tests"]:
            assert (ROOT / test_path).exists(), test_path
    assert payload["suites"]["full"] == {"count": "full pytest suite", "tests": []}


def test_verify_json_dry_run_emits_machine_readable_plan() -> None:
    result = run_verify("--dry-run", "--json", "--changed", "trading_system/app/main.py")

    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout)
    assert set(payload) == VERIFICATION_PLAN_JSON_KEYS
    assert payload["plan_version"] == 1
    assert payload["plan_kind"] == "verification_plan"
    assert len(payload["plan_fingerprint"]) == 64
    assert all(char in "0123456789abcdef" for char in payload["plan_fingerprint"])
    assert payload["plan_fingerprint"] == expected_plan_fingerprint(payload)
    alternate = run_verify("--dry-run", "--json", "--changed", "trading_system/app/universe/liquidity_filter.py")
    assert alternate.returncode == 0, alternate.stderr
    alternate_payload = json.loads(alternate.stdout)
    assert alternate_payload["plan_fingerprint"] != payload["plan_fingerprint"]
    repeat = run_verify("--dry-run", "--json", "--changed", "trading_system/app/main.py")
    assert repeat.returncode == 0, repeat.stderr
    repeat_payload = json.loads(repeat.stdout)
    assert repeat_payload["plan_fingerprint"] == payload["plan_fingerprint"]
    assert payload["suites"] == []
    assert payload["changed"] == ["trading_system/app/main.py"]
    assert payload["strict_changed_verification"] is False
    assert "trading_system/tests/test_main_v2_cycle.py" in payload["tests"]
    assert payload["commands"][-1] == "git --no-pager diff --check HEAD"
    assert payload["command_argv"][-1] == ["git", "--no-pager", "diff", "--check", "HEAD"]
    assert ["python3", "-m", "pytest", "-q", "trading_system/tests/test_main_v2_cycle.py"] == payload["command_argv"][0][:5]
    assert payload["commands"][0].split() == payload["command_argv"][0]


def test_verify_json_dry_run_reports_strict_changed_verification() -> None:
    result = run_verify(
        "--dry-run",
        "--json",
        "--strict-auto-changed",
        "--changed",
        "trading_system/app/main.py",
    )

    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout)
    assert payload["strict_changed_verification"] is True


def test_verify_requires_full_after_slice_threshold() -> None:
    result = run_verify("--dry-run", "--json", "--require-full-after", "3", "--slice-count", "3", "--changed", "trading_system/app/main.py")

    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout)
    assert payload["full"] is True
    assert payload["commands"][0] == "python3 -m pytest -q"


def test_verify_full_plan_exposes_sanitized_trading_env_contract() -> None:
    result = run_verify("--dry-run", "--json", "--suite", "full")

    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout)
    assert payload["full"] is True
    assert payload["sanitized_env"] is True
    assert payload["sanitized_env_removed_prefixes"] == ["TRADING_"]
    assert payload["command_argv"][0] == ["python3", "-m", "pytest", "-q"]
    assert payload["command_argv"][-1] == ["git", "--no-pager", "diff", "--check", "HEAD"]


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


def test_verify_strict_auto_changed_accepts_agent_rule_changes() -> None:
    result = run_verify("--dry-run", "--strict-auto-changed", "--changed", "AGENTS.md")

    assert result.returncode == 0, result.stderr
    assert "trading_system/tests/test_development_workflow_docs.py" in result.stdout
    assert "git --no-pager diff --check HEAD" in result.stdout


def test_verify_strict_auto_changed_rejects_unmapped_paths() -> None:
    result = run_verify("--dry-run", "--strict-auto-changed", "--changed", "UNKNOWN_UNMAPPED_FILE.txt")

    assert result.returncode == 2
    assert "no impacted verification tests" in result.stderr


def test_verify_strict_auto_changed_rejects_forbidden_memory_noise() -> None:
    result = run_verify("--dry-run", "--strict-auto-changed", "--changed", "memory/dev-status.md")

    assert result.returncode == 2
    assert "forbidden changed file" in result.stderr
    assert "memory/dev-status.md" in result.stderr


def test_verify_rejects_blank_changed_path() -> None:
    result = run_verify("--dry-run", "--changed", "")

    assert result.returncode == 2
    assert "changed path must be non-empty" in result.stderr


def test_verify_rejects_duplicate_changed_path() -> None:
    result = run_verify("--dry-run", "--changed", "AGENTS.md", "--changed", "AGENTS.md")

    assert result.returncode == 2
    assert "duplicate changed path" in result.stderr


def test_verify_rejects_blank_suite() -> None:
    result = run_verify("--dry-run", "--suite", "")

    assert result.returncode == 2
    assert "suite must be non-empty" in result.stderr


def test_verify_rejects_duplicate_suite() -> None:
    result = run_verify("--dry-run", "--suite", "workflow-meta", "--suite", "workflow-meta")

    assert result.returncode == 2
    assert "duplicate suite" in result.stderr


def test_verify_rejects_blank_explicit_test() -> None:
    result = run_verify("--dry-run", "--test", "")

    assert result.returncode == 2
    assert "explicit test must be non-empty" in result.stderr


def test_verify_rejects_duplicate_explicit_test() -> None:
    result = run_verify(
        "--dry-run",
        "--test",
        "trading_system/tests/test_development_workflow.py",
        "--test",
        "trading_system/tests/test_development_workflow.py",
    )

    assert result.returncode == 2
    assert "duplicate explicit test" in result.stderr


def test_verify_rejects_negative_require_full_after() -> None:
    result = run_verify("--dry-run", "--require-full-after", "-1")

    assert result.returncode == 2
    assert "require-full-after must be non-negative" in result.stderr


def test_verify_rejects_negative_slice_count() -> None:
    result = run_verify("--dry-run", "--slice-count", "-1")

    assert result.returncode == 2
    assert "slice-count must be non-negative" in result.stderr


def test_verify_strict_auto_changed_implies_auto_changed(tmp_path: Path) -> None:
    probe = ROOT / "UNTRACKED_STRICT_AUTO_CHANGED.txt"
    try:
        probe.write_text("temporary strict-auto-changed probe\n")
        result = run_verify("--dry-run", "--json", "--strict-auto-changed")
    finally:
        probe.unlink(missing_ok=True)

    assert result.returncode == 2
    assert (
        "UNTRACKED_STRICT_AUTO_CHANGED.txt" in result.stderr
        or "memory/dev-status.md" in result.stderr
    )


def test_verify_run_commands_uses_argv_without_shell(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("verify", VERIFY)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    calls: list[dict[str, object]] = []

    class Completed:
        returncode = 0

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return Completed()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.run_command_argv([["python3", "-m", "pytest", "-q"], ["git", "diff", "--check", "HEAD"]]) == 0
    assert calls == [
        {"command": ["python3", "-m", "pytest", "-q"], "text": True, "shell": False},
        {"command": ["git", "diff", "--check", "HEAD"], "text": True, "shell": False},
    ]


def test_verify_validates_test_paths_from_argv_not_display_commands() -> None:
    spec = importlib.util.spec_from_file_location("verify", VERIFY)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    module.validate_test_path_argv([
        ["python3", "-m", "pytest", "-q", "trading_system/tests/test_development_workflow.py"],
        ["git", "diff", "--check", "HEAD"],
    ])


def test_sanitized_verify_contract_uses_deterministic_python_and_unsets_trading_env() -> None:
    result = run_sanitized_verify("--dry-run", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == SANITIZED_VERIFY_CONTRACT_JSON_KEYS
    assert payload["contract_version"] == 1
    assert payload["contract_kind"] == "sanitized_verification_environment"
    assert payload["entrypoint"] == "trading_system_sanitized_verify"
    assert payload["python"] == "/home/cn/.hermes/hermes-agent/venv/bin/python"
    assert payload["command_argv"] == [
        "/home/cn/.hermes/hermes-agent/venv/bin/python",
        "scripts/verify.py",
    ]
    assert "TRADING_RUNTIME_ENV" in payload["unset_env"]
    assert "TRADING_ENTRY_PROFILE" in payload["unset_env"]
    assert "TRADING_BASE_DIR" in payload["unset_env"]


def test_sanitized_verify_clears_trading_env_but_preserves_non_trading_env() -> None:
    result = run_sanitized_verify(
        "--print-env",
        env={
            "TRADING_RUNTIME_ENV": "testnet",
            "TRADING_ENTRY_PROFILE": "live",
            "TRADING_BASE_DIR": "/tmp/real-trading-state",
            "TRADING_STATE_FILE": "/tmp/real-state.json",
            "SANITIZED_VERIFY_SENTINEL": "still-available",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "SANITIZED_VERIFY_SENTINEL": "still-available",
        "TRADING_BASE_DIR": None,
        "TRADING_ENTRY_PROFILE": None,
        "TRADING_RUNTIME_ENV": None,
        "TRADING_STATE_FILE": None,
    }


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


def test_ci_verify_fingerprint_excludes_existing_fingerprint_field() -> None:
    module = load_module("ci_verify", ROOT / "scripts" / "ci_verify.py")
    payload = {
        "plan_version": 1,
        "plan_kind": "ci_verification_plan",
        "entrypoint": "ci_verify",
        "commands": ["python3 scripts/verify.py --suite workflow-meta"],
        "command_argv": [["python3", "scripts/verify.py", "--suite", "workflow-meta"]],
        "suites": ["workflow-meta"],
        "strict_changed_verification": True,
    }

    digest = module.plan_fingerprint(payload)
    payload["plan_fingerprint"] = digest

    assert module.plan_fingerprint(payload) == digest


def test_ci_verify_dry_run_json_reports_commands() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ci_verify.py"), "--dry-run", "--json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout)
    assert set(payload) == CI_PLAN_JSON_KEYS
    assert payload["plan_version"] == 1
    assert payload["plan_kind"] == "ci_verification_plan"
    assert len(payload["plan_fingerprint"]) == 64
    assert payload["plan_fingerprint"] == expected_plan_fingerprint(payload)
    repeat = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ci_verify.py"), "--dry-run", "--json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert repeat.returncode == 0, repeat.stderr
    assert json.loads(repeat.stdout)["plan_fingerprint"] == payload["plan_fingerprint"]
    assert payload["entrypoint"] == "ci_verify"
    assert payload["strict_changed_verification"] is True
    assert payload["suites"] == ["workflow-meta", "evidence-chain"]
    assert payload["commands"] == [
        "python3 scripts/verify.py --dry-run --strict-auto-changed",
        "python3 scripts/verify.py --suite workflow-meta",
        "python3 scripts/verify.py --suite evidence-chain",
    ]
    assert payload["command_argv"] == [
        ["python3", "scripts/verify.py", "--dry-run", "--strict-auto-changed"],
        ["python3", "scripts/verify.py", "--suite", "workflow-meta"],
        ["python3", "scripts/verify.py", "--suite", "evidence-chain"],
    ]


def test_ci_verify_json_requires_dry_run() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ci_verify.py"), "--json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 2
    assert "--json requires --dry-run" in result.stderr


def test_ci_verify_text_dry_run_reports_strict_changed_verification() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ci_verify.py"), "--dry-run"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "plan_version: 1" in result.stdout
    assert "plan_kind: ci_verification_plan" in result.stdout
    assert "strict_changed_verification: true" in result.stdout
    assert "suites: workflow-meta,evidence-chain" in result.stdout
    assert "python3 scripts/verify.py --dry-run --strict-auto-changed" in result.stdout


def test_ci_verify_executes_plan_argv_without_shell(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("ci_verify", ROOT / "scripts" / "ci_verify.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    calls: list[dict[str, object]] = []

    class Completed:
        returncode = 0

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return Completed()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main([]) == 0
    assert calls == [
        {"command": ["python3", "scripts/verify.py", "--dry-run", "--strict-auto-changed"], "text": True, "shell": False},
        {"command": ["python3", "scripts/verify.py", "--suite", "workflow-meta"], "text": True, "shell": False},
        {"command": ["python3", "scripts/verify.py", "--suite", "evidence-chain"], "text": True, "shell": False},
    ]


def test_nightly_verify_entrypoint_runs_full_suite() -> None:
    script = ROOT / "scripts" / "nightly_verify.py"

    assert script.exists()
    text = script.read_text()
    assert "--suite full" in text
    assert "TRADING_RUNTIME_ENV" in text
    assert "TRADING_ENTRY_PROFILE" in text


def test_nightly_verify_fingerprint_excludes_existing_fingerprint_field() -> None:
    module = load_module("nightly_verify", ROOT / "scripts" / "nightly_verify.py")
    payload = {
        "plan_version": 1,
        "plan_kind": "nightly_verification_plan",
        "entrypoint": "nightly_verify",
        "clean_env": True,
        "commands": ["python3 scripts/verify.py --suite full"],
        "command_argv": [["python3", "scripts/verify.py", "--suite", "full"]],
        "suites": ["full"],
        "unset_env": ["TRADING_RUNTIME_ENV"],
    }

    digest = module.plan_fingerprint(payload)
    payload["plan_fingerprint"] = digest

    assert module.plan_fingerprint(payload) == digest


def test_nightly_verify_dry_run_json_reports_clean_env_full_command() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "nightly_verify.py"), "--dry-run", "--json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout)
    assert set(payload) == NIGHTLY_PLAN_JSON_KEYS
    assert payload["plan_version"] == 1
    assert payload["plan_kind"] == "nightly_verification_plan"
    assert len(payload["plan_fingerprint"]) == 64
    assert payload["plan_fingerprint"] == expected_plan_fingerprint(payload)
    repeat = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "nightly_verify.py"), "--dry-run", "--json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert repeat.returncode == 0, repeat.stderr
    assert json.loads(repeat.stdout)["plan_fingerprint"] == payload["plan_fingerprint"]
    assert payload["entrypoint"] == "nightly_verify"
    assert payload["clean_env"] is True
    assert payload["sanitized_env_removed_prefixes"] == ["TRADING_"]
    assert payload["suites"] == ["full"]
    assert payload["commands"] == ["python3 scripts/verify.py --suite full"]
    assert payload["command_argv"] == [["python3", "scripts/verify.py", "--suite", "full"]]
    assert "TRADING_RUNTIME_ENV" in payload["unset_env"]


def test_nightly_verify_json_requires_dry_run() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "nightly_verify.py"), "--json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 2
    assert "--json requires --dry-run" in result.stderr


def test_nightly_verify_text_dry_run_reports_clean_env() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "nightly_verify.py"), "--dry-run"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "plan_version: 1" in result.stdout
    assert "plan_kind: nightly_verification_plan" in result.stdout
    assert "clean_env: true" in result.stdout
    assert "suites: full" in result.stdout
    assert "TRADING_RUNTIME_ENV" in result.stdout
    assert "python3 scripts/verify.py --suite full" in result.stdout


def test_nightly_verify_executes_plan_argv_without_shell(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("nightly_verify", ROOT / "scripts" / "nightly_verify.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    calls: list[dict[str, object]] = []

    class Completed:
        returncode = 0

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return Completed()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main([]) == 0
    assert len(calls) == 1
    assert calls[0]["command"] == ["python3", "scripts/verify.py", "--suite", "full"]
    assert calls[0]["text"] is True
    assert calls[0]["shell"] is False
    env = calls[0]["env"]
    assert isinstance(env, dict)
    assert "TRADING_RUNTIME_ENV" not in env


def test_nightly_verify_clears_all_trading_prefixed_env(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("nightly_verify", ROOT / "scripts" / "nightly_verify.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    monkeypatch.setenv("TRADING_RUNTIME_ENV", "live")
    monkeypatch.setenv("TRADING_LIVE_API_KEY", "real-key")
    monkeypatch.setenv("NON_TRADING_MARKER", "keep")
    calls: list[dict[str, object]] = []

    class Completed:
        returncode = 0

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return Completed()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main([]) == 0
    env = calls[0]["env"]
    assert isinstance(env, dict)
    assert "TRADING_RUNTIME_ENV" not in env
    assert "TRADING_LIVE_API_KEY" not in env
    assert env["NON_TRADING_MARKER"] == "keep"


def test_nightly_verify_writes_run_manifest(monkeypatch, tmp_path: Path) -> None:
    module = load_module("nightly_verify", ROOT / "scripts" / "nightly_verify.py")
    manifest_path = tmp_path / "nightly-manifest.json"

    class Completed:
        def __init__(self, returncode: int = 0, stdout: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    calls: list[dict[str, object]] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        if command == ["git", "rev-parse", "HEAD"]:
            return Completed(stdout="abc123\n")
        return Completed(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setenv("TRADING_RUNTIME_ENV", "prod")

    assert module.main(["--manifest-path", str(manifest_path)]) == 0

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["manifest_kind"] == "nightly_verification_run"
    assert payload["git_sha"] == "abc123"
    assert payload["python_executable"] == sys.executable
    assert payload["python_version"] == sys.version
    assert payload["sanitized_trading_env"] == {
        "TRADING_RUNTIME_ENV": {"present_before": True, "present_after": False},
        "TRADING_ENTRY_PROFILE": {"present_before": False, "present_after": False},
        "TRADING_EXECUTION_MODE": {"present_before": False, "present_after": False},
        "TRADING_BASE_DIR": {"present_before": False, "present_after": False},
        "TRADING_STATE_FILE": {"present_before": False, "present_after": False},
        "TRADING_ACCOUNT_SNAPSHOT_FILE": {"present_before": False, "present_after": False},
        "TRADING_MARKET_CONTEXT_FILE": {"present_before": False, "present_after": False},
        "TRADING_DERIVATIVES_SNAPSHOT_FILE": {"present_before": False, "present_after": False},
    }
    assert payload["sanitized_env_removed_prefixes"] == ["TRADING_"]
    assert payload["test_command"] == "python3 scripts/verify.py --suite full"
    assert payload["test_command_argv"] == ["python3", "scripts/verify.py", "--suite", "full"]
    assert payload["test_result_count"] is None
    assert payload["returncode"] == 0
    assert payload["diff_check_command"] == "git --no-pager diff --check HEAD"
    assert payload["started_at"].endswith("Z")
    assert payload["finished_at"].endswith("Z")
    assert calls[0]["command"] == ["python3", "scripts/verify.py", "--suite", "full"]
    assert calls[1]["command"] == ["git", "rev-parse", "HEAD"]
