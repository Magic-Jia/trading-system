from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts" / "audit_worker_commit.py"


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
    assert payload["changed_files"]
    assert payload["status"] == "ok"
    assert payload["audit_kind"] == "worker_commit_preflight"
    assert payload["final_merge_proof"] is False
    assert payload["controller_next_steps"] == [
        "inspect changed_files",
        "run verification_plan.commands in controller workspace",
        "only integrate after controller verification passes",
    ]
    assert isinstance(payload["worktree_dirty"], bool)
    assert payload["verification_plan"]["commands"][-1] == "git diff --check HEAD"


def test_audit_worker_commit_rejects_dev_status_file() -> None:
    result = run_audit("--changed-file", "memory/dev-status.md")

    assert result.returncode == 2
    assert "memory/dev-status.md" in result.stderr


def test_audit_worker_commit_rejects_empty_input() -> None:
    result = run_audit()

    assert result.returncode == 2
    assert "no changed files" in result.stderr
