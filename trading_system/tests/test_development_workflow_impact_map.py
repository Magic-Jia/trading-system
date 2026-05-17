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


def test_registered_suite_test_files_have_impact_mapping() -> None:
    verify = load_verify_module()
    registered_tests = sorted(
        {
            test_path
            for suite_tests in verify.SUITES.values()
            for test_path in suite_tests
        }
    )
    missing = [
        test_path
        for test_path in registered_tests
        if not verify.tests_for_changed([test_path])
    ]

    assert missing == []


def test_runtime_incident_bundle_contract_is_in_evidence_chain() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/tests/test_runtime_incident_bundle_schema.py"])

    assert "trading_system/tests/test_runtime_incident_bundle_schema.py" in tests
    assert "trading_system/tests/test_runtime_safety_evidence.py" in tests


def test_run_cycle_entrypoint_has_runtime_support_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/run_cycle.py"])

    assert "trading_system/tests/test_run_cycle.py" in tests
    assert "trading_system/tests/test_main_v2_cycle.py" in tests


def test_management_execution_test_file_has_portfolio_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/tests/test_management_execution.py"])

    assert "trading_system/tests/test_management_execution.py" in tests
    assert "trading_system/tests/test_target_management_state.py" in tests


def test_testnet_preview_file_has_runtime_support_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/tests/test_testnet_preview.py"])

    assert "trading_system/tests/test_testnet_preview.py" in tests
    assert "trading_system/tests/test_executor.py" in tests


def test_execution_calibration_generator_has_calibration_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/generate_execution_calibration_records.py"])

    assert "trading_system/tests/test_generate_execution_calibration_records.py" in tests
    assert "trading_system/tests/test_execution_calibration_evidence.py" in tests


def test_full_market_baseline_dataset_fixtures_have_backtest_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(
        [
            "trading_system/tests/fixtures/backtest/full_market_baseline_dataset/"
            "2026-03-10T00-00-00Z__row-001/instrument_snapshot.json"
        ]
    )

    assert "trading_system/tests/test_backtest_dataset.py" in tests
    assert "trading_system/tests/test_backtest_engine.py" in tests
