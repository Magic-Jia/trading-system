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


def test_execution_calibration_module_has_calibration_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/app/execution/calibration.py"])

    assert "trading_system/tests/test_execution_calibration_evidence.py" in tests


def test_replay_simulated_live_evidence_has_evidence_chain_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/app/reporting/replay_simulated_live_evidence.py"])

    assert "trading_system/tests/test_replay_simulated_live_evidence.py" in tests
    assert "trading_system/tests/test_simulated_live_evidence_window.py" in tests


def test_replay_simulated_live_generator_has_replay_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/generate_replay_simulated_live_evidence_bundle.py"])

    assert "trading_system/tests/test_replay_simulated_live_evidence.py" in tests


def test_longitudinal_live_sim_trend_report_test_file_has_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed([
        "trading_system/tests/test_longitudinal_live_sim_trend_report.py"
    ])

    assert "trading_system/tests/test_longitudinal_live_sim_trend_report.py" in tests
    assert "trading_system/tests/test_scheduled_live_sim_generation.py" in tests


def test_promotion_readiness_scorecard_test_file_has_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed([
        "trading_system/tests/test_promotion_readiness_scorecard.py"
    ])

    assert "trading_system/tests/test_promotion_readiness_scorecard.py" in tests
    assert "trading_system/tests/test_scheduled_live_sim_generation.py" in tests


def test_promotion_readiness_scorecard_trend_test_file_has_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed([
        "trading_system/tests/test_promotion_readiness_scorecard_trend.py"
    ])

    assert "trading_system/tests/test_promotion_readiness_scorecard_trend.py" in tests
    assert "trading_system/tests/test_scheduled_live_sim_generation.py" in tests


def test_promotion_readiness_evidence_test_file_has_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed([
        "trading_system/tests/test_generate_promotion_readiness_evidence.py"
    ])

    assert "trading_system/tests/test_generate_promotion_readiness_evidence.py" in tests
    assert "trading_system/tests/test_promotion_readiness_scorecard.py" in tests


def test_promotion_gate_decision_test_file_has_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed([
        "trading_system/tests/test_promotion_gate_decision.py"
    ])

    assert "trading_system/tests/test_promotion_gate_decision.py" in tests
    assert "trading_system/tests/test_simulated_live_evidence_window.py" in tests
    assert "trading_system/tests/test_promotion_readiness_scorecard_trend.py" in tests
    assert "trading_system/tests/test_execution_calibration_evidence.py" in tests


def test_bootstrap_live_sim_generation_inputs_has_impact_mapping() -> None:
    verify = load_verify_module()

    implementation_tests = verify.tests_for_changed(["trading_system/bootstrap_live_sim_generation_inputs.py"])
    test_file_tests = verify.tests_for_changed(["trading_system/tests/test_bootstrap_live_sim_generation_inputs.py"])

    for tests in (implementation_tests, test_file_tests):
        assert "trading_system/tests/test_bootstrap_live_sim_generation_inputs.py" in tests
        assert "trading_system/tests/test_scheduled_live_sim_generation.py" in tests
        assert "trading_system/tests/test_simulated_live_cadence_runner.py" in tests


def test_promotion_readiness_scorecard_has_reporting_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/app/reporting/promotion_readiness_scorecard.py"])

    assert "trading_system/tests/test_promotion_readiness_scorecard.py" in tests
    assert "trading_system/tests/test_daily_quality_gate_report.py" in tests


def test_promotion_readiness_scorecard_trend_has_reporting_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/app/reporting/promotion_readiness_scorecard_trend.py"])

    assert "trading_system/tests/test_promotion_readiness_scorecard_trend.py" in tests
    assert "trading_system/tests/test_scheduled_live_sim_generation.py" in tests


def test_promotion_readiness_evidence_generator_has_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/generate_promotion_readiness_evidence.py"])

    assert "trading_system/tests/test_generate_promotion_readiness_evidence.py" in tests
    assert "trading_system/tests/test_promotion_readiness_scorecard.py" in tests
    assert "trading_system/tests/test_simulated_live_cadence_runner.py" in tests


def test_real_local_simulated_live_evidence_chain_has_impact_mapping() -> None:
    verify = load_verify_module()

    reporting_tests = verify.tests_for_changed([
        "trading_system/app/reporting/real_local_simulated_live_evidence_chain.py"
    ])
    generator_tests = verify.tests_for_changed([
        "trading_system/generate_real_local_simulated_live_evidence_chain.py"
    ])

    assert "trading_system/tests/test_real_local_simulated_live_evidence_chain.py" in reporting_tests
    assert "trading_system/tests/test_real_local_simulated_live_evidence_chain.py" in generator_tests


def test_simulated_live_artifact_inventory_has_impact_mapping() -> None:
    verify = load_verify_module()

    reporting_tests = verify.tests_for_changed([
        "trading_system/app/reporting/simulated_live_artifact_inventory.py"
    ])
    generator_tests = verify.tests_for_changed([
        "trading_system/generate_simulated_live_artifact_inventory.py"
    ])

    assert "trading_system/tests/test_simulated_live_artifact_inventory.py" in reporting_tests
    assert "trading_system/tests/test_simulated_live_artifact_inventory.py" in generator_tests


def test_execution_stream_producers_have_impact_mapping() -> None:
    verify = load_verify_module()

    reporting_tests = verify.tests_for_changed([
        "trading_system/app/reporting/execution_stream_producers.py"
    ])
    race_generator_tests = verify.tests_for_changed([
        "trading_system/generate_execution_race_evidence.py"
    ])
    l2_generator_tests = verify.tests_for_changed([
        "trading_system/generate_l2_longitudinal_replay_calibration.py"
    ])

    assert "trading_system/tests/test_execution_stream_producers.py" in reporting_tests
    assert "trading_system/tests/test_rolling_simulated_live_evidence_bundle.py" in reporting_tests
    assert race_generator_tests == ["trading_system/tests/test_execution_stream_producers.py"]
    assert l2_generator_tests == ["trading_system/tests/test_execution_stream_producers.py"]


def test_promotion_gate_decision_has_reporting_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/app/reporting/promotion_gate_decision.py"])

    assert "trading_system/tests/test_promotion_gate_decision.py" in tests
    assert "trading_system/tests/test_simulated_live_evidence_window.py" in tests
    assert "trading_system/tests/test_promotion_readiness_scorecard_trend.py" in tests
    assert "trading_system/tests/test_execution_calibration_evidence.py" in tests


def test_promotion_gate_decision_generator_has_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/generate_promotion_gate_decision.py"])

    assert tests == ["trading_system/tests/test_promotion_gate_decision.py"]


def test_longitudinal_promotion_decision_archive_has_impact_mapping() -> None:
    verify = load_verify_module()

    reporting_tests = verify.tests_for_changed([
        "trading_system/app/reporting/longitudinal_promotion_decision_archive.py"
    ])
    generator_tests = verify.tests_for_changed([
        "trading_system/generate_longitudinal_promotion_decision_archive.py"
    ])
    test_file_tests = verify.tests_for_changed([
        "trading_system/tests/test_longitudinal_promotion_decision_archive.py"
    ])

    assert "trading_system/tests/test_longitudinal_promotion_decision_archive.py" in reporting_tests
    assert generator_tests == ["trading_system/tests/test_longitudinal_promotion_decision_archive.py"]
    assert "trading_system/tests/test_longitudinal_promotion_decision_archive.py" in test_file_tests


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


def test_professional_backtest_report_test_file_has_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/tests/test_backtest_professional_reports.py"])

    assert "trading_system/tests/test_backtest_professional_reports.py" in tests
    assert "trading_system/tests/test_backtest_evidence_chain.py" in tests


def test_backtest_cli_test_file_has_impact_mapping() -> None:
    verify = load_verify_module()

    tests = verify.tests_for_changed(["trading_system/tests/test_backtest_cli.py"])

    assert "trading_system/tests/test_backtest_cli.py" in tests
    assert "trading_system/tests/test_backtest_professional_reports.py" in tests
    assert "trading_system/tests/test_backtest_evidence_chain.py" in tests
