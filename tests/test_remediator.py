"""Tests for the remediator — knowledge-flashed fixer."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pact.remediator import (
    ReproducerResult,
    build_narrative_debrief,
    generate_reproducer_test,
    remediate_incident,
)
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    FieldSpec,
    FunctionContract,
    PlanEvaluation,
    ResearchReport,
    TestCase,
    TestFailure,
    TestResults,
    TypeSpec,
)
from pact.schemas_monitoring import Incident, Signal


def _make_contract() -> ComponentContract:
    return ComponentContract(
        component_id="pricing",
        name="Pricing Engine",
        description="Calculates prices",
        functions=[
            FunctionContract(
                name="calculate_price",
                description="Calculate price",
                inputs=[FieldSpec(name="unit_id", type_ref="str")],
                output_type="float",
            ),
        ],
        types=[TypeSpec(name="PriceResult", kind="struct")],
    )


def _make_test_suite() -> ContractTestSuite:
    return ContractTestSuite(
        component_id="pricing",
        contract_version=1,
        test_cases=[
            TestCase(
                id="test_basic_price",
                description="Basic price calculation",
                function="calculate_price",
                category="happy_path",
            ),
        ],
        generated_code="def test_basic_price():\n    assert calculate_price('u1') > 0\n",
    )


def _make_incident() -> Incident:
    return Incident(
        id="inc_001",
        project_dir="/tmp/proj",
        component_id="pricing",
        signals=[Signal(
            source="log_file",
            raw_text="TypeError: NoneType has no attribute 'price'",
            timestamp=datetime.now().isoformat(),
        )],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )


class TestGenerateReproducerTest:
    @pytest.mark.asyncio
    async def test_generates_valid_python(self):
        agent = MagicMock()
        agent.assess = AsyncMock(return_value=(
            ReproducerResult(
                test_code=(
                    "def test_reproducer_none_price():\n"
                    "    result = calculate_price('invalid_unit')\n"
                    "    assert result is not None\n"
                ),
                test_name="test_reproducer_none_price",
                description="Reproduces NoneType error",
            ),
            200, 100,
        ))

        contract = _make_contract()
        test_suite = _make_test_suite()
        signal = Signal(
            source="log_file",
            raw_text="TypeError: NoneType has no attribute 'price'",
            timestamp="2024-01-01T00:00:00",
        )

        code = await generate_reproducer_test(agent, signal, contract, test_suite)
        assert "def test_reproducer" in code
        assert "calculate_price" in code

    @pytest.mark.asyncio
    async def test_handles_agent_failure(self):
        agent = MagicMock()
        agent.assess = AsyncMock(side_effect=Exception("API error"))

        signal = Signal(
            source="manual",
            raw_text="Some error",
            timestamp="2024-01-01T00:00:00",
        )

        code = await generate_reproducer_test(
            agent, signal, _make_contract(), _make_test_suite(),
        )
        # Should return a placeholder test
        assert "def test_reproducer" in code
        assert "assert False" in code


class TestRemediateIncident:
    @pytest.mark.asyncio
    async def test_success_path(self, tmp_path: Path):
        """Mock a successful remediation: agent generates code, tests pass."""
        incident = _make_incident()
        incident.project_dir = str(tmp_path)

        # Set up project structure
        project = MagicMock()
        project.load_contract.return_value = _make_contract()
        project.load_test_suite.return_value = _make_test_suite()
        project.load_all_contracts.return_value = {"pricing": _make_contract()}
        project.impl_src_dir.return_value = tmp_path / "src"
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)

        # Mock agent that returns code
        from pact.agents.code_author import ImplementationResult
        from pact.schemas import ResearchReport, PlanEvaluation

        agent = MagicMock()

        with patch("pact.agents.code_author.author_code") as mock_author, \
             patch("pact.remediator.generate_reproducer_test") as mock_repro, \
             patch("pact.test_harness.run_contract_tests") as mock_tests:

            mock_repro.return_value = "def test_repro(): pass\n"
            mock_author.return_value = ImplementationResult(
                files={"module.py": "# fixed code"},
                research=ResearchReport(task_summary="fix"),
                plan=PlanEvaluation(plan_summary="fix plan"),
            )
            mock_tests.return_value = TestResults(total=2, passed=2)

            success, summary = await remediate_incident(
                incident=incident,
                project=project,
                agent_or_factory=agent,
            )

        assert success is True
        assert "auto-fixed" in summary.lower() or "Auto-fixed" in summary

    @pytest.mark.asyncio
    async def test_failure_escalates(self, tmp_path: Path):
        """When tests keep failing, remediation returns False."""
        incident = _make_incident()
        incident.project_dir = str(tmp_path)

        project = MagicMock()
        project.load_contract.return_value = _make_contract()
        project.load_test_suite.return_value = _make_test_suite()
        project.load_all_contracts.return_value = {"pricing": _make_contract()}
        project.impl_src_dir.return_value = tmp_path / "src"
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)

        agent = MagicMock()

        from pact.agents.code_author import ImplementationResult
        from pact.schemas import ResearchReport, PlanEvaluation

        with patch("pact.agents.code_author.author_code") as mock_author, \
             patch("pact.remediator.generate_reproducer_test") as mock_repro, \
             patch("pact.test_harness.run_contract_tests") as mock_tests:

            mock_repro.return_value = "def test_repro(): assert False\n"
            mock_author.return_value = ImplementationResult(
                files={"module.py": "# still buggy"},
                research=ResearchReport(task_summary="fix"),
                plan=PlanEvaluation(plan_summary="fix plan"),
            )
            # Tests keep failing
            mock_tests.return_value = TestResults(total=2, passed=1, failed=1)

            success, summary = await remediate_incident(
                incident=incident,
                project=project,
                agent_or_factory=agent,
                max_attempts=2,
            )

        assert success is False
        assert "failed" in summary.lower()

    @pytest.mark.asyncio
    async def test_no_component_id(self, tmp_path: Path):
        """Incident without component ID should fail immediately."""
        incident = _make_incident()
        incident.component_id = ""

        success, summary = await remediate_incident(
            incident=incident,
            project=MagicMock(),
            agent_or_factory=MagicMock(),
        )
        assert success is False
        assert "No component" in summary

    @pytest.mark.asyncio
    async def test_no_contract(self, tmp_path: Path):
        """Missing contract should fail."""
        incident = _make_incident()

        project = MagicMock()
        project.load_contract.return_value = None

        success, summary = await remediate_incident(
            incident=incident,
            project=project,
            agent_or_factory=MagicMock(),
        )
        assert success is False
        assert "No contract" in summary

    @pytest.mark.asyncio
    async def test_callable_agent_factory(self, tmp_path: Path):
        """Test that a callable factory creates a fresh agent."""
        incident = _make_incident()
        incident.component_id = ""  # Will fail early, but we test factory

        factory = MagicMock(return_value=MagicMock())

        success, _ = await remediate_incident(
            incident=incident,
            project=MagicMock(),
            agent_or_factory=factory,
        )
        # Early failure due to no component_id, but factory should not be called
        # because component_id check happens first
        assert success is False

    @pytest.mark.asyncio
    async def test_forwards_prior_failures_on_retry(self, tmp_path: Path):
        """On retry, author_code should receive prior_failures."""
        incident = _make_incident()
        incident.project_dir = str(tmp_path)

        project = MagicMock()
        project.load_contract.return_value = _make_contract()
        project.load_test_suite.return_value = _make_test_suite()
        project.load_all_contracts.return_value = {"pricing": _make_contract()}
        project.impl_src_dir.return_value = tmp_path / "src"
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)

        agent = MagicMock()

        from pact.agents.code_author import ImplementationResult

        call_args_list = []

        with patch("pact.agents.code_author.author_code") as mock_author, \
             patch("pact.remediator.generate_reproducer_test") as mock_repro, \
             patch("pact.test_harness.run_contract_tests") as mock_tests:

            mock_repro.return_value = "def test_repro(): pass\n"
            mock_author.return_value = ImplementationResult(
                files={"module.py": "# code"},
                research=ResearchReport(task_summary="fix"),
                plan=PlanEvaluation(plan_summary="fix plan"),
            )

            # First attempt fails, second succeeds
            mock_tests.side_effect = [
                TestResults(
                    total=2, passed=1, failed=1,
                    failure_details=[
                        TestFailure(test_id="test_1", error_message="boom"),
                    ],
                ),
                TestResults(total=2, passed=2),
            ]

            success, _ = await remediate_incident(
                incident=incident,
                project=project,
                agent_or_factory=agent,
                max_attempts=2,
            )

            assert success is True
            assert mock_author.call_count == 2
            # Second call should have prior_failures
            second_call_kwargs = mock_author.call_args_list[1][1]
            assert second_call_kwargs.get("prior_failures") is not None
            assert len(second_call_kwargs["prior_failures"]) > 0

    @pytest.mark.asyncio
    async def test_forwards_test_results_on_retry(self, tmp_path: Path):
        """On retry, author_code should receive prior_test_results."""
        incident = _make_incident()
        incident.project_dir = str(tmp_path)

        project = MagicMock()
        project.load_contract.return_value = _make_contract()
        project.load_test_suite.return_value = _make_test_suite()
        project.load_all_contracts.return_value = {"pricing": _make_contract()}
        project.impl_src_dir.return_value = tmp_path / "src"
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)

        agent = MagicMock()

        from pact.agents.code_author import ImplementationResult

        with patch("pact.agents.code_author.author_code") as mock_author, \
             patch("pact.remediator.generate_reproducer_test") as mock_repro, \
             patch("pact.test_harness.run_contract_tests") as mock_tests:

            mock_repro.return_value = "def test_repro(): pass\n"
            mock_author.return_value = ImplementationResult(
                files={"module.py": "# code"},
                research=ResearchReport(task_summary="fix"),
                plan=PlanEvaluation(plan_summary="fix plan"),
            )
            failed_results = TestResults(total=2, passed=1, failed=1)
            mock_tests.side_effect = [
                failed_results,
                TestResults(total=2, passed=2),
            ]

            success, _ = await remediate_incident(
                incident=incident,
                project=project,
                agent_or_factory=agent,
                max_attempts=2,
            )

            assert success is True
            second_call_kwargs = mock_author.call_args_list[1][1]
            assert second_call_kwargs.get("prior_test_results") is not None

    @pytest.mark.asyncio
    async def test_enriches_context_on_retry(self, tmp_path: Path):
        """On attempt 2, external_context should be longer than attempt 1."""
        incident = _make_incident()
        incident.project_dir = str(tmp_path)

        project = MagicMock()
        project.load_contract.return_value = _make_contract()
        project.load_test_suite.return_value = _make_test_suite()
        project.load_all_contracts.return_value = {"pricing": _make_contract()}
        project.impl_src_dir.return_value = tmp_path / "src"
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)

        agent = MagicMock()

        from pact.agents.code_author import ImplementationResult

        with patch("pact.agents.code_author.author_code") as mock_author, \
             patch("pact.remediator.generate_reproducer_test") as mock_repro, \
             patch("pact.test_harness.run_contract_tests") as mock_tests:

            mock_repro.return_value = "def test_repro(): pass\n"
            mock_author.return_value = ImplementationResult(
                files={"module.py": "# code"},
                research=ResearchReport(task_summary="fix"),
                plan=PlanEvaluation(plan_summary="fix plan"),
            )
            mock_tests.side_effect = [
                TestResults(total=2, passed=1, failed=1),
                TestResults(total=2, passed=2),
            ]

            await remediate_incident(
                incident=incident,
                project=project,
                agent_or_factory=agent,
                max_attempts=2,
            )

            ctx1 = mock_author.call_args_list[0][1]["external_context"]
            ctx2 = mock_author.call_args_list[1][1]["external_context"]
            assert len(ctx2) > len(ctx1)


class TestBuildNarrativeDebrief:
    def _make_base_incident(self) -> Incident:
        return Incident(
            id="inc_test",
            project_dir="/tmp/proj",
            component_id="comp_a",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )

    def test_first_attempt_returns_base_context(self):
        base = "Some error context"
        result = build_narrative_debrief(
            attempt=1,
            incident=self._make_base_incident(),
            prior_failures=[],
            last_test_results=None,
            last_research=None,
            last_plan=None,
            base_error_context=base,
        )
        assert result == base

    def test_includes_prior_failures(self):
        failures = ["Test 'test_a': assertion error", "Test 'test_b': timeout"]
        result = build_narrative_debrief(
            attempt=2,
            incident=self._make_base_incident(),
            prior_failures=failures,
            last_test_results=None,
            last_research=None,
            last_plan=None,
            base_error_context="base",
        )
        assert "test_a" in result
        assert "test_b" in result

    def test_includes_test_failure_details(self):
        test_results = TestResults(
            total=2, passed=1, failed=1,
            failure_details=[
                TestFailure(test_id="test_price_calc", error_message="expected 10 got 0"),
            ],
        )
        result = build_narrative_debrief(
            attempt=2,
            incident=self._make_base_incident(),
            prior_failures=[],
            last_test_results=test_results,
            last_research=None,
            last_plan=None,
            base_error_context="base",
        )
        assert "test_price_calc" in result
        assert "expected 10 got 0" in result

    def test_includes_plan_summary(self):
        plan = PlanEvaluation(plan_summary="Refactor pricing module")
        result = build_narrative_debrief(
            attempt=2,
            incident=self._make_base_incident(),
            prior_failures=[],
            last_test_results=None,
            last_research=None,
            last_plan=plan,
            base_error_context="base",
        )
        assert "Refactor pricing module" in result

    def test_heroic_framing_present(self):
        result = build_narrative_debrief(
            attempt=2,
            incident=self._make_base_incident(),
            prior_failures=["something failed"],
            last_test_results=None,
            last_research=None,
            last_plan=None,
            base_error_context="base",
        )
        assert "senior engineer" in result
        assert "fundamentally different" in result

    def test_caps_failure_count(self):
        failures = [f"Test 'test_{i}': fail" for i in range(20)]
        result = build_narrative_debrief(
            attempt=2,
            incident=self._make_base_incident(),
            prior_failures=failures,
            last_test_results=None,
            last_research=None,
            last_plan=None,
            base_error_context="base",
        )
        # Should contain at most 10 failure lines in the debrief section
        assert "test_9" in result
        assert "10 more failures" in result
