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
WORKER_AUDIT_VERIFICATION_PLAN_JSON_KEYS = {
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


def load_audit_module():
    spec = importlib.util.spec_from_file_location("audit_worker_commit", AUDIT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_audit(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AUDIT), *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def clone_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    clone = subprocess.run(
        ["git", "clone", "--quiet", str(ROOT), str(repo)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert clone.returncode == 0, clone.stderr
    return repo


def assert_worker_audit_contract(payload: dict[str, object]) -> None:
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
    assert payload["strict_changed_verification"] is True
    assert set(payload["verification_plan"]) == WORKER_AUDIT_VERIFICATION_PLAN_JSON_KEYS
    assert payload["verification_plan"]["plan_version"] == 1
    assert payload["verification_plan"]["plan_kind"] == "verification_plan"
    assert len(payload["verification_plan"]["plan_fingerprint"]) == 64
    assert payload["verification_plan"]["strict_changed_verification"] is True
    if payload["verification_plan"]["full"]:
        assert payload["verification_plan"]["sanitized_env"] is True
        assert payload["verification_plan"]["sanitized_env_removed_prefixes"] == ["TRADING_"]
    else:
        assert payload["verification_plan"]["sanitized_env"] is False
        assert payload["verification_plan"]["sanitized_env_removed_prefixes"] == []
    assert payload["verification_plan"]["commands"][-1] == "git --no-pager diff --check HEAD"
    assert payload["verification_plan"]["command_argv"][-1] == ["git", "--no-pager", "diff", "--check", "HEAD"]


def test_audit_worker_commit_outputs_json_for_head_in_clean_worktree(tmp_path: Path) -> None:
    repo = clone_repo(tmp_path)
    result = run_audit(repo, "--commit", "HEAD")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert_worker_audit_contract(payload)
    assert payload["worktree_dirty"] is False
    assert payload["worktree_dirty_paths"] == []


def test_audit_worker_commit_reports_dirty_worktree_paths(tmp_path: Path) -> None:
    repo = clone_repo(tmp_path)
    dirty_marker = repo / "trading_system" / "tests" / ".audit_worker_commit_dirty_marker.txt"
    dirty_marker.write_text("dirty\n", encoding="utf-8")
    try:
        result = run_audit(repo, "--commit", "HEAD")

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert_worker_audit_contract(payload)
        assert payload["worktree_dirty"] is True
        assert str(dirty_marker.relative_to(repo)) in payload["worktree_dirty_paths"]
    finally:
        dirty_marker.unlink(missing_ok=True)

    cleaned = run_audit(repo, "--commit", "HEAD")
    assert cleaned.returncode == 0, cleaned.stderr
    cleaned_payload = json.loads(cleaned.stdout)
    assert_worker_audit_contract(cleaned_payload)
    assert cleaned_payload["worktree_dirty"] is False
    assert cleaned_payload["worktree_dirty_paths"] == []


def test_audit_worker_commit_rejects_dev_status_file() -> None:
    result = run_audit(ROOT, "--changed-file", "memory/dev-status.md")

    assert result.returncode == 2
    assert "memory/dev-status.md" in result.stderr


def test_audit_worker_commit_rejects_empty_input() -> None:
    result = run_audit(ROOT)

    assert result.returncode == 2
    assert "no changed files" in result.stderr


def test_audit_worker_commit_rejects_blank_changed_file() -> None:
    result = run_audit(ROOT, "--changed-file", "")

    assert result.returncode == 2
    assert "changed file must be non-empty" in result.stderr


def test_audit_worker_commit_rejects_duplicate_changed_file() -> None:
    result = run_audit(ROOT, "--changed-file", "AGENTS.md", "--changed-file", "AGENTS.md")

    assert result.returncode == 2
    assert "duplicate changed file" in result.stderr


def test_audit_worker_commit_rejects_changed_files_without_impacted_tests() -> None:
    result = run_audit(ROOT, "--changed-file", "UNKNOWN_UNMAPPED_FILE.txt")

    assert result.returncode == 2
    assert "no impacted verification tests" in result.stderr


def test_audit_worker_commit_maps_agent_rules_to_workflow_meta() -> None:
    result = run_audit(ROOT, "--changed-file", "AGENTS.md")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "trading_system/tests/test_development_workflow_docs.py" in payload["verification_plan"]["tests"]
    assert "trading_system/tests/test_development_workflow_worker_audit.py" in payload["verification_plan"]["tests"]


def test_audit_worker_commit_maps_sanitized_verify_wrapper_to_workflow_meta() -> None:
    result = run_audit(ROOT, "--changed-file", "scripts/trading_system_sanitized_verify.sh")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "trading_system/tests/test_development_workflow.py" in payload["verification_plan"]["tests"]
    assert "trading_system/tests/test_development_workflow_worker_audit.py" in payload["verification_plan"]["tests"]


def test_audit_worker_commit_maps_readme_changes_to_workflow_meta() -> None:
    result = run_audit(ROOT, "--changed-file", "README.md")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "trading_system/tests/test_development_workflow_docs.py" in payload["verification_plan"]["tests"]
    assert "trading_system/tests/test_development_workflow.py" in payload["verification_plan"]["tests"]


def test_audit_worker_commit_rejects_partially_unmapped_changed_files() -> None:
    result = run_audit(
        ROOT,
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
