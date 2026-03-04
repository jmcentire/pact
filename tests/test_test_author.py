"""Tests for test author agent — including Goodhart test authoring."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from pact.agents.base import AgentBase
from pact.agents.test_author import author_goodhart_tests
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    FieldSpec,
    FunctionContract,
    TestCase,
)


def _make_contract() -> ComponentContract:
    return ComponentContract(
        component_id="calculator",
        name="Calculator",
        description="Basic arithmetic",
        functions=[
            FunctionContract(
                name="add",
                description="Add two numbers",
                inputs=[
                    FieldSpec(name="a", type_ref="float"),
                    FieldSpec(name="b", type_ref="float"),
                ],
                output_type="float",
                postconditions=["add(a, b) == add(b, a)"],
            ),
        ],
        invariants=["All operations must be deterministic"],
    )


def _make_visible_suite() -> ContractTestSuite:
    return ContractTestSuite(
        component_id="calculator",
        contract_version=1,
        test_cases=[
            TestCase(
                id="t1", description="add returns correct sum",
                function="add", category="happy_path",
            ),
            TestCase(
                id="t2", description="add with zero",
                function="add", category="edge_case",
            ),
        ],
        generated_code="def test_add(): assert add(2, 3) == 5",
    )


def _make_goodhart_response() -> ContractTestSuite:
    return ContractTestSuite(
        component_id="calculator",
        contract_version=1,
        test_cases=[
            TestCase(
                id="g1",
                description="the add function should be commutative for all numeric inputs",
                function="add",
                category="invariant",
            ),
            TestCase(
                id="g2",
                description="add should handle negative inputs without special-casing",
                function="add",
                category="edge_case",
            ),
        ],
        generated_code=(
            "def test_goodhart_commutative():\n"
            "    assert add(7, 3) == add(3, 7)\n\n"
            "def test_goodhart_negative():\n"
            "    assert add(-5, 3) == -2\n"
        ),
    )


class TestAuthorGoodhartTests:
    @pytest.mark.asyncio
    async def test_returns_valid_contract_test_suite(self):
        agent = AsyncMock(spec=AgentBase)
        agent.assess_cached = AsyncMock(
            return_value=(_make_goodhart_response(), 100, 200),
        )

        result = await author_goodhart_tests(
            agent, _make_contract(), _make_visible_suite(),
        )

        assert isinstance(result, ContractTestSuite)
        assert result.component_id == "calculator"
        assert result.contract_version == 1
        assert len(result.test_cases) == 2

    @pytest.mark.asyncio
    async def test_sets_component_id_and_version(self):
        """Ensure component_id and contract_version are forced from the contract."""
        response = _make_goodhart_response()
        response.component_id = "wrong_id"
        response.contract_version = 99

        agent = AsyncMock(spec=AgentBase)
        agent.assess_cached = AsyncMock(return_value=(response, 100, 200))

        result = await author_goodhart_tests(
            agent, _make_contract(), _make_visible_suite(),
        )

        assert result.component_id == "calculator"
        assert result.contract_version == 1

    @pytest.mark.asyncio
    async def test_prompt_includes_visible_test_info(self):
        """Goodhart author should see visible tests for gap analysis."""
        agent = AsyncMock(spec=AgentBase)
        agent.assess_cached = AsyncMock(
            return_value=(_make_goodhart_response(), 100, 200),
        )

        await author_goodhart_tests(
            agent, _make_contract(), _make_visible_suite(),
        )

        # Check the prompt passed to assess_cached
        call_args = agent.assess_cached.call_args
        prompt = call_args[0][1]  # Second positional arg is the prompt
        assert "add returns correct sum" in prompt  # visible test description
        assert "add with zero" in prompt

    @pytest.mark.asyncio
    async def test_does_not_call_research_or_plan(self):
        """Goodhart authoring must be a single LLM call — no research/plan phases."""
        agent = AsyncMock(spec=AgentBase)
        agent.assess_cached = AsyncMock(
            return_value=(_make_goodhart_response(), 100, 200),
        )

        with patch("pact.agents.test_author.research_phase") as mock_research, \
             patch("pact.agents.test_author.plan_and_evaluate") as mock_plan:

            await author_goodhart_tests(
                agent, _make_contract(), _make_visible_suite(),
            )

            mock_research.assert_not_called()
            mock_plan.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_llm_call(self):
        """Should make exactly one LLM call (assess_cached)."""
        agent = AsyncMock(spec=AgentBase)
        agent.assess_cached = AsyncMock(
            return_value=(_make_goodhart_response(), 100, 200),
        )

        await author_goodhart_tests(
            agent, _make_contract(), _make_visible_suite(),
        )

        assert agent.assess_cached.call_count == 1

    @pytest.mark.asyncio
    async def test_includes_dependency_mock_info(self):
        dep_contract = ComponentContract(
            component_id="logger",
            name="Logger",
            description="Logging",
            functions=[
                FunctionContract(
                    name="log", description="Log message",
                    inputs=[FieldSpec(name="msg", type_ref="str")],
                    output_type="None",
                ),
            ],
        )

        agent = AsyncMock(spec=AgentBase)
        agent.assess_cached = AsyncMock(
            return_value=(_make_goodhart_response(), 100, 200),
        )

        await author_goodhart_tests(
            agent, _make_contract(), _make_visible_suite(),
            dependency_contracts={"logger": dep_contract},
        )

        prompt = agent.assess_cached.call_args[0][1]
        assert "logger" in prompt.lower()
