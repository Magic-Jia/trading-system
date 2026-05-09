from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts" / "audit_worker_commit.py"

WORKER_AUDIT_JSON_KEYS = {
    "audit_kind",
    "audit_version",
    "changed_files",
    "commit",
    "controller_next_steps",
    "final_merge_proof",
    "status",
    "strict_changed_verification",
    "verification_plan",
    "worktree_dirty",
    "worktree_dirty_paths",
}


def load_audit_module():
    spec = importlib.util.spec_from_file_location("audit_worker_commit", AUDIT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_audit(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AUDIT), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_audit_worker_commit_outputs_json_for_head() -> None:
    result = run_audit("--commit", "HEAD")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["commit"]
    assert set(payload) == WORKER_AUDIT_JSON_KEYS
    assert payload["changed_files"]
    assert payload["status"] == "ok"
    assert payload["audit_version"] == 1
    assert payload["audit_kind"] == "worker_commit_preflight"
    assert payload["final_merge_proof"] is False
    assert payload["controller_next_steps"] == [
        "inspect changed_files",
        "run verification_plan.commands in controller workspace",
        "only integrate after controller verification passes",
    ]
    assert payload["worktree_dirty"] is True
    assert "memory/dev-status.md" in payload["worktree_dirty_paths"]
    assert payload["strict_changed_verification"] is True
    assert payload["verification_plan"]["plan_version"] == 1
    assert payload["verification_plan"]["plan_kind"] == "verification_plan"
    assert payload["verification_plan"]["strict_changed_verification"] is True
    assert payload["verification_plan"]["commands"][-1] == "git diff --check HEAD"


def test_audit_worker_commit_rejects_dev_status_file() -> None:
    result = run_audit("--changed-file", "memory/dev-status.md")

    assert result.returncode == 2
    assert "memory/dev-status.md" in result.stderr


def test_audit_worker_commit_rejects_empty_input() -> None:
    result = run_audit()

    assert result.returncode == 2
    assert "no changed files" in result.stderr


def test_audit_worker_commit_rejects_changed_files_without_impacted_tests() -> None:
    result = run_audit("--changed-file", "UNKNOWN_UNMAPPED_FILE.txt")

    assert result.returncode == 2
    assert "no impacted verification tests" in result.stderr


def test_audit_worker_commit_maps_agent_rules_to_workflow_meta() -> None:
    result = run_audit("--changed-file", "AGENTS.md")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "trading_system/tests/test_development_workflow_docs.py" in payload["verification_plan"]["tests"]
    assert "trading_system/tests/test_development_workflow_worker_audit.py" in payload["verification_plan"]["tests"]


def test_audit_worker_commit_maps_readme_changes_to_workflow_meta() -> None:
    result = run_audit("--changed-file", "README.md")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "trading_system/tests/test_development_workflow_docs.py" in payload["verification_plan"]["tests"]
    assert "trading_system/tests/test_development_workflow.py" in payload["verification_plan"]["tests"]


def test_audit_worker_commit_rejects_partially_unmapped_changed_files() -> None:
    result = run_audit(
        "--changed-file",
        "README.md",
        "--changed-file",
        "UNKNOWN_UNMAPPED_FILE.txt",
    )

    assert result.returncode == 2
    assert "no impacted verification tests" in result.stderr
    assert "UNKNOWN_UNMAPPED_FILE.txt" in result.stderr


def test_parse_status_path_expands_renames() -> None:
    audit = load_audit_module()

    assert audit.parse_status_path("R  old/name.py -> new/name.py") == [
        "old/name.py",
        "new/name.py",
    ]
