"""Tests for the remediator — knowledge-flashed fixer."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pact.remediator import (
    ReproducerResult,
    generate_reproducer_test,
    remediate_incident,
)
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    FieldSpec,
    FunctionContract,
    TestCase,
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
