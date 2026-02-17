"""Tests for decomposition build modes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pact.decomposer import DecompositionResult, run_decomposition
from pact.schemas import DecompositionTree


class TestUnaryMode:
    """build_mode='unary' forces single component without LLM."""

    @pytest.mark.asyncio
    async def test_unary_returns_single_component(self):
        """Unary mode returns exactly one component."""
        agent = MagicMock()
        # Agent should NOT be called in unary mode
        agent.assess = AsyncMock(side_effect=RuntimeError("Should not be called"))

        result = await run_decomposition(
            agent, "Build a calculator", build_mode="unary",
        )

        assert isinstance(result, DecompositionResult)
        assert len(result.tree.nodes) == 1
        assert result.decisions == []

    @pytest.mark.asyncio
    async def test_unary_root_id(self):
        """Unary mode root ID is 'root'."""
        agent = MagicMock()
        agent.assess = AsyncMock(side_effect=RuntimeError("Should not be called"))

        result = await run_decomposition(
            agent, "Build a calculator", build_mode="unary",
        )
        assert result.tree.root_id == "root"

    @pytest.mark.asyncio
    async def test_unary_node_description_is_full_task(self):
        """Unary mode uses the full task as the component description."""
        agent = MagicMock()
        agent.assess = AsyncMock(side_effect=RuntimeError("Should not be called"))

        task = "Build a comprehensive REST API with authentication, caching, and rate limiting"
        result = await run_decomposition(agent, task, build_mode="unary")

        root = result.tree.nodes["root"]
        assert root.description == task

    @pytest.mark.asyncio
    async def test_unary_node_depth_is_zero(self):
        """Unary mode creates a node at depth 0."""
        agent = MagicMock()
        agent.assess = AsyncMock(side_effect=RuntimeError("Should not be called"))

        result = await run_decomposition(
            agent, "Simple task", build_mode="unary",
        )
        assert result.tree.nodes["root"].depth == 0

    @pytest.mark.asyncio
    async def test_unary_no_children(self):
        """Unary mode creates a leaf node (no children)."""
        agent = MagicMock()
        agent.assess = AsyncMock(side_effect=RuntimeError("Should not be called"))

        result = await run_decomposition(
            agent, "Simple task", build_mode="unary",
        )
        assert result.tree.nodes["root"].children == []

    @pytest.mark.asyncio
    async def test_unary_skips_agent_call(self):
        """Agent is never invoked in unary mode."""
        agent = MagicMock()
        agent.assess = AsyncMock()

        await run_decomposition(
            agent, "Simple task", build_mode="unary",
        )

        agent.assess.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unary_with_interview(self):
        """Unary mode works even when interview results are provided."""
        from pact.schemas import InterviewResult

        agent = MagicMock()
        agent.assess = AsyncMock(side_effect=RuntimeError("Should not be called"))

        interview = InterviewResult(
            risks=["Risk 1"],
            questions=["Q1"],
            assumptions=["A1"],
        )
        result = await run_decomposition(
            agent, "Simple task",
            interview=interview,
            build_mode="unary",
        )
        assert len(result.tree.nodes) == 1


class TestAutoMode:
    """build_mode='auto' passes through to LLM."""

    @pytest.mark.asyncio
    async def test_auto_calls_agent(self):
        """Auto mode invokes the agent."""
        from pydantic import BaseModel

        class FakeResponse(BaseModel):
            components: list[dict] = []
            decisions: list[dict] = []
            is_trivial: bool = True

        agent = MagicMock()
        agent.assess = AsyncMock(return_value=(
            FakeResponse(
                components=[{"id": "root", "name": "Main", "description": "Test"}],
                is_trivial=True,
            ),
            "research",
            "plan",
        ))

        result = await run_decomposition(
            agent, "Simple task", build_mode="auto",
        )

        agent.assess.assert_awaited_once()
        assert len(result.tree.nodes) >= 1


class TestHierarchyMode:
    """build_mode='hierarchy' always decomposes."""

    @pytest.mark.asyncio
    async def test_hierarchy_calls_agent(self):
        """Hierarchy mode invokes the agent."""
        from pydantic import BaseModel

        class FakeResponse(BaseModel):
            components: list[dict] = []
            decisions: list[dict] = []
            is_trivial: bool = False

        agent = MagicMock()
        agent.assess = AsyncMock(return_value=(
            FakeResponse(
                components=[
                    {"id": "root", "name": "Root", "description": "Top", "children": ["a"]},
                    {"id": "a", "name": "A", "description": "Component A"},
                ],
                is_trivial=False,
            ),
            "research",
            "plan",
        ))

        result = await run_decomposition(
            agent, "Complex task", build_mode="hierarchy",
        )

        agent.assess.assert_awaited_once()
        assert len(result.tree.nodes) >= 1


class TestDefaultMode:
    """Default build_mode is 'auto'."""

    @pytest.mark.asyncio
    async def test_default_is_auto(self):
        """When no build_mode specified, uses auto (calls agent)."""
        from pydantic import BaseModel

        class FakeResponse(BaseModel):
            components: list[dict] = []
            decisions: list[dict] = []
            is_trivial: bool = True

        agent = MagicMock()
        agent.assess = AsyncMock(return_value=(
            FakeResponse(
                components=[{"id": "root", "name": "Main", "description": "Test"}],
                is_trivial=True,
            ),
            "research",
            "plan",
        ))

        result = await run_decomposition(agent, "Simple task")

        agent.assess.assert_awaited_once()


class TestImprovedPrompt:
    """Decomposition prompt improvements."""

    @pytest.mark.asyncio
    async def test_complexity_hint_in_prompt(self):
        """Prompt includes word count complexity hint."""
        from pydantic import BaseModel

        class FakeResponse(BaseModel):
            components: list[dict] = []
            decisions: list[dict] = []
            is_trivial: bool = True

        captured_prompt = None

        async def capture_assess(model_cls, prompt, system):
            nonlocal captured_prompt
            captured_prompt = prompt
            return (
                FakeResponse(
                    components=[{"id": "root", "name": "Main", "description": "Test"}],
                    is_trivial=True,
                ),
                "research",
                "plan",
            )

        agent = MagicMock()
        agent.assess = capture_assess

        task = "Build a simple calculator with add and subtract"
        await run_decomposition(agent, task, build_mode="auto")

        assert captured_prompt is not None
        assert "Complexity hint" in captured_prompt
        # 8 words in task
        assert "~8 words" in captured_prompt

    @pytest.mark.asyncio
    async def test_short_task_gets_single_concern_hint(self):
        """Short tasks (<200 words) get single-concern hint."""
        from pydantic import BaseModel

        class FakeResponse(BaseModel):
            components: list[dict] = []
            decisions: list[dict] = []
            is_trivial: bool = True

        captured_prompt = None

        async def capture_assess(model_cls, prompt, system):
            nonlocal captured_prompt
            captured_prompt = prompt
            return (
                FakeResponse(
                    components=[{"id": "root", "name": "Main", "description": "Test"}],
                    is_trivial=True,
                ),
                "research",
                "plan",
            )

        agent = MagicMock()
        agent.assess = capture_assess

        task = "Build a calculator"
        await run_decomposition(agent, task, build_mode="auto")

        assert "single-concern" in captured_prompt
