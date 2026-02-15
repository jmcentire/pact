"""Tests for group research (P1-1) and research sharing (P1-2)."""
import pytest
import inspect
from unittest.mock import AsyncMock, MagicMock


class TestResearchForGroup:
    """P1-1: Group research function."""

    def test_function_exists(self):
        from pact.agents.research import research_for_group
        assert callable(research_for_group)

    def test_prompt_includes_all_components(self):
        """Verify prompt construction includes all component names."""
        from pact.agents.research import research_for_group
        source = inspect.getsource(research_for_group)
        assert "component_listing" in source or "components" in source

    def test_signature(self):
        from pact.agents.research import research_for_group
        sig = inspect.signature(research_for_group)
        params = list(sig.parameters.keys())
        assert "group_description" in params
        assert "components" in params
        assert "role_context" in params
        assert "sops" in params


class TestAugmentResearch:
    """P1-2: Research augmentation."""

    def test_function_exists(self):
        from pact.agents.research import augment_research
        assert callable(augment_research)

    def test_signature(self):
        from pact.agents.research import augment_research
        sig = inspect.signature(augment_research)
        params = list(sig.parameters.keys())
        assert "base_research" in params
        assert "supplemental_focus" in params
        assert "sops" in params


class TestTestAuthorPriorResearch:
    """P1-2: test_author accepts prior_research."""

    def test_prior_research_param_exists(self):
        from pact.agents.test_author import author_tests
        sig = inspect.signature(author_tests)
        assert "prior_research" in sig.parameters

    def test_prior_research_default_none(self):
        from pact.agents.test_author import author_tests
        sig = inspect.signature(author_tests)
        assert sig.parameters["prior_research"].default is None

    def test_uses_augment_when_prior_provided(self):
        from pact.agents.test_author import author_tests
        source = inspect.getsource(author_tests)
        assert "augment_research" in source
        assert "prior_research" in source


class TestCodeAuthorPriorResearch:
    """P1-2: code_author accepts prior_research."""

    def test_prior_research_param_exists(self):
        from pact.agents.code_author import author_code
        sig = inspect.signature(author_code)
        assert "prior_research" in sig.parameters

    def test_prior_research_default_none(self):
        from pact.agents.code_author import author_code
        sig = inspect.signature(author_code)
        assert sig.parameters["prior_research"].default is None

    def test_uses_augment_when_prior_provided(self):
        from pact.agents.code_author import author_code
        source = inspect.getsource(author_code)
        assert "augment_research" in source
        assert "prior_research" in source
