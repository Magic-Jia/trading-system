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
