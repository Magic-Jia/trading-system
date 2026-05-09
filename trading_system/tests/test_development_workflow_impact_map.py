from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERIFY_PATH = ROOT / "scripts" / "verify.py"


def load_verify_module():
    spec = importlib.util.spec_from_file_location("verify_workflow", VERIFY_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_core_app_python_files_have_impact_mapping() -> None:
    verify = load_verify_module()
    missing: list[str] = []
    for path in sorted((ROOT / "trading_system" / "app").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if "__pycache__" in relative or relative.endswith("/__init__.py"):
            continue
        if relative.startswith("trading_system/app/live/"):
            continue
        if not verify.tests_for_changed([relative]):
            missing.append(relative)

    assert missing == []


def test_workflow_scripts_have_impact_mapping() -> None:
    verify = load_verify_module()
    missing: list[str] = []
    for path in sorted((ROOT / "scripts").glob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if not verify.tests_for_changed([relative]):
            missing.append(relative)

    assert missing == []


def test_workflow_docs_and_templates_have_impact_mapping() -> None:
    verify = load_verify_module()
    required_paths = [ROOT / "docs" / "development-workflow.md"]
    required_paths.extend(sorted((ROOT / "templates").glob("*.md")))

    missing = [
        path.relative_to(ROOT).as_posix()
        for path in required_paths
        if not verify.tests_for_changed([path.relative_to(ROOT).as_posix()])
    ]

    assert missing == []


def test_agent_rule_files_have_impact_mapping() -> None:
    verify = load_verify_module()
    required_paths = [ROOT / "AGENTS.md"]
    required_paths.extend(ROOT.glob("CLAUDE.md"))
    required_paths.extend(ROOT.glob(".cursorrules"))

    missing = [
        path.relative_to(ROOT).as_posix()
        for path in required_paths
        if not verify.tests_for_changed([path.relative_to(ROOT).as_posix()])
    ]

    assert missing == []


def test_evidence_chain_test_files_have_impact_mapping() -> None:
    verify = load_verify_module()
    missing = [
        test_path
        for test_path in verify.SUITES["evidence-chain"]
        if not verify.tests_for_changed([test_path])
    ]

    assert missing == []
