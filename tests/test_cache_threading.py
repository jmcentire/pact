"""Tests for cache_prefix threading through agent pipeline (P0-2)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from pact.agents.base import AgentBase
from pact.budget import BudgetTracker


class TestAgentBaseAssessCached:
    """Test assess_cached method on AgentBase."""

    def test_assess_cached_exists(self):
        assert hasattr(AgentBase, 'assess_cached')

    @pytest.mark.asyncio
    async def test_assess_cached_delegates_to_cache_when_available(self):
        """When backend has assess_with_cache and prefix is non-empty, use it."""
        budget = BudgetTracker()
        agent = AgentBase.__new__(AgentBase)
        agent._budget = budget

        mock_backend = AsyncMock()
        mock_backend.assess_with_cache = AsyncMock(return_value=("result", 100, 50))
        agent._backend = mock_backend

        from pydantic import BaseModel
        class TestSchema(BaseModel):
            value: str = ""

        result = await agent.assess_cached(
            TestSchema, "prompt", "system", cache_prefix="cached stuff",
        )
        mock_backend.assess_with_cache.assert_called_once()
        mock_backend.assess.assert_not_called()

    @pytest.mark.asyncio
    async def test_assess_cached_falls_back_without_prefix(self):
        """When cache_prefix is empty, use regular assess."""
        budget = BudgetTracker()
        agent = AgentBase.__new__(AgentBase)
        agent._budget = budget

        mock_backend = AsyncMock()
        mock_backend.assess = AsyncMock(return_value=("result", 100, 50))
        mock_backend.assess_with_cache = AsyncMock()
        agent._backend = mock_backend

        from pydantic import BaseModel
        class TestSchema(BaseModel):
            value: str = ""

        await agent.assess_cached(TestSchema, "prompt", "system", cache_prefix="")
        mock_backend.assess.assert_called_once()
        mock_backend.assess_with_cache.assert_not_called()

    @pytest.mark.asyncio
    async def test_assess_cached_falls_back_without_method(self):
        """When backend lacks assess_with_cache, use regular assess."""
        budget = BudgetTracker()
        agent = AgentBase.__new__(AgentBase)
        agent._budget = budget

        mock_backend = AsyncMock(spec=[])  # no assess_with_cache
        mock_backend.assess = AsyncMock(return_value=("result", 100, 50))
        agent._backend = mock_backend

        from pydantic import BaseModel
        class TestSchema(BaseModel):
            value: str = ""

        await agent.assess_cached(
            TestSchema, "prompt", "system", cache_prefix="cached stuff",
        )
        mock_backend.assess.assert_called_once()


class TestResearchCaching:
    """Test that research.py uses assess_cached with SOPs as prefix."""

    def test_research_phase_uses_assess_cached(self):
        """research_phase should call assess_cached."""
        import inspect
        from pact.agents.research import research_phase
        source = inspect.getsource(research_phase)
        assert "assess_cached" in source

    def test_plan_evaluate_uses_assess_cached(self):
        """plan_and_evaluate should call assess_cached."""
        import inspect
        from pact.agents.research import plan_and_evaluate
        source = inspect.getsource(plan_and_evaluate)
        assert "assess_cached" in source


class TestContractAuthorCaching:
    """Test that contract_author.py uses assess_cached."""

    def test_author_contract_uses_assess_cached(self):
        import inspect
        from pact.agents.contract_author import author_contract
        source = inspect.getsource(author_contract)
        assert "assess_cached" in source


class TestTestAuthorCaching:
    """Test that test_author.py uses assess_cached."""

    def test_author_tests_uses_assess_cached(self):
        import inspect
        from pact.agents.test_author import author_tests
        source = inspect.getsource(author_tests)
        assert "assess_cached" in source


class TestCodeAuthorCaching:
    """Test that code_author.py uses assess_cached."""

    def test_author_code_uses_assess_cached(self):
        import inspect
        from pact.agents.code_author import author_code
        source = inspect.getsource(author_code)
        assert "assess_cached" in source
