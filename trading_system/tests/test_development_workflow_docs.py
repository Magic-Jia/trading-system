from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DOC = ROOT / "docs" / "development-workflow.md"
CODEX_TEMPLATE = ROOT / "templates" / "codex-worker-prompt.md"


def test_development_workflow_doc_records_controller_gates() -> None:
    text = WORKFLOW_DOC.read_text()

    assert "RED → GREEN" in text
    assert "scripts/verify.py" in text
    assert "Controller" in text
    assert "Codex summary is not final evidence" in text
    assert "memory/dev-status.md" in text


def test_codex_worker_template_forbids_side_effects_and_nested_agents() -> None:
    text = CODEX_TEMPLATE.read_text()

    assert "No real orders" in text
    assert "No testnet orders" in text
    assert "Do not run nested Codex" in text
    assert "model_provider=\"testvideo\"" in text
    assert "model_reasoning_effort=\"medium\"" in text
    assert "RED command" in text
    assert "GREEN command" in text
    assert "scripts/verify.py" in text


def test_workflow_doc_lists_every_non_full_suite() -> None:
    text = WORKFLOW_DOC.read_text()
    for suite in (
        "evidence-chain",
        "runtime-main",
        "universe",
        "portfolio",
        "backtest-core",
        "archive-data",
        "runtime-support",
        "app-smoke",
        "workflow-meta",
    ):
        assert f"--suite {suite}" in text


def test_workflow_doc_records_json_plan_and_full_checkpoint_policy() -> None:
    text = WORKFLOW_DOC.read_text()

    assert "--list-suites" in text
    assert "--list-suites --json" in text
    assert "inventory_kind" in text
    assert "inventory_version" in text
    assert "count" in text
    assert "tests" in text
    assert "--json" in text
    assert "--dry-run --json" in text
    assert "--json requires --dry-run" in text
    assert "scripts/ci_verify.py --json" in text
    assert "scripts/nightly_verify.py --json" in text
    assert "--require-full-after" in text
    assert "--strict-auto-changed" in text
    assert "scripts/ci_verify.py" in text
    assert "scripts/ci_verify.py --dry-run --json" in text
    assert "strict_changed_verification: true" in text
    assert "JSON payload includes `strict_changed_verification`" in text
    assert "plan_version" in text
    assert "plan_kind" in text
    assert "plan_kind: verification_plan" in text
    assert "plan_kind: ci_verification_plan" in text
    assert "plan_kind: nightly_verification_plan" in text
    assert "entrypoint JSON plans include `suites`" in text
    assert "plan_version: 1" in text
    assert "scripts/nightly_verify.py" in text
    assert "scripts/nightly_verify.py --dry-run --json" in text
    assert "clean_env: true" in text
    assert "forbidden changed file" in text
    assert "explicit --changed" in text
    assert "--slice-count" in text
    assert "full_checkpoint_reason" in text
    assert "exact top-level field set" in text
    assert "AGENTS.md" in text


def test_codex_worker_template_requires_json_plan_report() -> None:
    text = CODEX_TEMPLATE.read_text()

    assert "Verification plan JSON" in text
    assert "plan_kind" in text
    assert "inventory_version" in text
    assert "entrypoint JSON `suites`" in text
    assert "--dry-run --json" in text
    assert "scripts/ci_verify.py --dry-run --json" in text


def test_workflow_docs_and_codex_template_reference_worker_audit() -> None:
    doc = WORKFLOW_DOC.read_text()
    template = CODEX_TEMPLATE.read_text()

    assert "scripts/audit_worker_commit.py" in doc
    assert "scripts/audit_worker_commit.py" in template
    assert "final_merge_proof" in doc
    assert "audit_version" in doc
    assert "nested `verification_plan` includes `plan_kind: verification_plan`" in doc
    assert "controller_next_steps" in doc
    assert "worktree_dirty" in doc
    assert "worktree_dirty_paths" in doc
    assert "no impacted verification tests" in doc
