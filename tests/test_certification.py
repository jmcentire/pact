from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from pact.certification import certify, verify_artifact_hashes
from pact.project import ProjectManager
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionNode,
    DecompositionTree,
    TestResults,
)


def _project_with_contract(tmp_path, *, create_impl: bool = True) -> ProjectManager:
    project = ProjectManager(tmp_path)
    cid = "comp_a"

    project.save_tree(
        DecompositionTree(
            root_id=cid,
            nodes={
                cid: DecompositionNode(
                    component_id=cid,
                    name="Comp A",
                    description="Test component",
                )
            },
        )
    )
    project.save_contract(
        ComponentContract(
            component_id=cid,
            name="Comp A",
            description="Test component",
        )
    )
    project.save_test_suite(
        ContractTestSuite(
            component_id=cid,
            contract_version=1,
            generated_code="def test_contract():\n    pass\n",
        )
    )
    project.save_goodhart_suite(
        ContractTestSuite(
            component_id=cid,
            contract_version=1,
            generated_code="def test_goodhart():\n    pass\n",
        )
    )
    if create_impl:
        project.impl_src_dir(cid).mkdir(parents=True, exist_ok=True)
    return project


def _passing_results() -> TestResults:
    return TestResults(total=1, passed=1, failed=0)


def _failing_results() -> TestResults:
    return TestResults(total=1, passed=0, failed=1)


def test_certify_fails_closed_when_emission_test_missing(tmp_path):
    project = _project_with_contract(tmp_path)
    runner = AsyncMock(return_value=_passing_results())

    with patch("pact.certification.run_contract_tests", runner):
        cert = asyncio.run(certify(project))

    assert cert.verdict == "fail"
    assert cert.summary == "Emission compliance test failures detected"
    assert cert.emission_results["comp_a"] == {
        "total": 0,
        "passed": 0,
        "failed": 1,
        "missing_test": True,
        "error": "Missing emission compliance test",
    }
    assert cert.emission_hashes["comp_a"] == ""
    assert runner.await_count == 2


def test_certify_summary_reports_visible_and_emission_failures(tmp_path):
    project = _project_with_contract(tmp_path)
    runner = AsyncMock(side_effect=[_failing_results(), _passing_results()])

    with patch("pact.certification.run_contract_tests", runner):
        cert = asyncio.run(certify(project))

    assert cert.verdict == "fail"
    assert cert.summary == "Emission compliance and visible test failures detected"
    assert cert.visible_results["comp_a"] == {"total": 1, "passed": 0, "failed": 1}
    assert cert.emission_results["comp_a"]["missing_test"] is True
    assert runner.await_count == 2


def test_certify_fails_closed_when_emission_impl_dir_missing(tmp_path):
    project = _project_with_contract(tmp_path, create_impl=False)
    project.save_emission_test("comp_a", "def test_emission():\n    pass\n")
    runner = AsyncMock(return_value=_passing_results())

    with patch("pact.certification.run_contract_tests", runner):
        cert = asyncio.run(certify(project))

    assert cert.verdict == "fail"
    assert cert.emission_results["comp_a"] == {
        "total": 0,
        "passed": 0,
        "failed": 1,
        "missing_implementation": True,
        "error": "Missing implementation directory",
    }
    assert runner.await_count == 0


def test_certify_runs_and_hashes_emission_tests(tmp_path):
    project = _project_with_contract(tmp_path)
    project.save_emission_test("comp_a", "def test_emission():\n    pass\n")
    runner = AsyncMock(return_value=_passing_results())

    with patch("pact.certification.run_contract_tests", runner):
        cert = asyncio.run(certify(project))

    assert cert.verdict == "pass"
    assert cert.summary == "All visible, Goodhart, and emission compliance tests pass"
    assert cert.emission_results["comp_a"] == {"total": 1, "passed": 1, "failed": 0}
    assert cert.emission_hashes["comp_a"]
    assert verify_artifact_hashes(cert, project) == []
    assert runner.await_count == 3


def test_verify_artifact_hashes_reports_missing_emission_sentinel(tmp_path):
    project = _project_with_contract(tmp_path)
    runner = AsyncMock(return_value=_passing_results())

    with patch("pact.certification.run_contract_tests", runner):
        cert = asyncio.run(certify(project))

    assert cert.emission_hashes["comp_a"] == ""
    assert verify_artifact_hashes(cert, project) == [
        "tests/comp_a/emission_test: missing at certification time"
    ]


def test_certify_runs_tests_under_configured_interpreter(tmp_path):
    project = _project_with_contract(tmp_path)
    project.save_emission_test("comp_a", "def test_emission():\n    pass\n")
    project.config_path.write_text(
        "environment:\n  python_path: /opt/venv/bin/python\n"
    )
    runner = AsyncMock(return_value=_passing_results())

    with patch("pact.certification.run_contract_tests", runner):
        asyncio.run(certify(project))

    assert runner.await_count == 3
    for call in runner.await_args_list:
        assert call.kwargs["environment"].python_path == "/opt/venv/bin/python"
