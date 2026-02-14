"""Tests for Gemini backend."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from pact.budget import BudgetTracker


class SampleSchema(BaseModel):
    """Test schema."""
    name: str
    value: int


def _make_mock_response(parsed=None, text="", prompt_tokens=100, output_tokens=50):
    """Create a mock Gemini response."""
    mock_usage = MagicMock()
    mock_usage.prompt_token_count = prompt_tokens
    mock_usage.candidates_token_count = output_tokens

    mock_response = MagicMock()
    mock_response.parsed = parsed
    mock_response.usage_metadata = mock_usage
    mock_response.text = text
    return mock_response


def _make_backend_with_mock(mock_response):
    """Create a GeminiBackend with a mocked client."""
    from pact.backends.gemini import GeminiBackend

    budget = BudgetTracker(per_project_cap=100.0)
    budget.set_model_pricing("gemini-2.5-flash")

    with patch.dict("os.environ", {"GEMINI_API_KEY": "AIza-test123"}):
        backend = GeminiBackend(budget=budget, model="gemini-2.5-flash")

    # Replace the client with a fully mocked one
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    backend._client = mock_client

    return backend


class TestGeminiBackend:
    def test_create_without_key_raises(self):
        from pact.backends.gemini import GeminiBackend

        with patch.dict("os.environ", {}, clear=True):
            env = __import__("os").environ.copy()
            env.pop("GEMINI_API_KEY", None)
            with patch.dict("os.environ", env, clear=True):
                with pytest.raises(ValueError, match="GEMINI_API_KEY"):
                    GeminiBackend(budget=BudgetTracker(), model="gemini-2.5-flash")

    def test_create_with_key(self):
        from pact.backends.gemini import GeminiBackend

        with patch.dict("os.environ", {"GEMINI_API_KEY": "AIza-test123"}):
            backend = GeminiBackend(budget=BudgetTracker(), model="gemini-2.5-flash")
            assert backend._model == "gemini-2.5-flash"

    def test_set_model(self):
        from pact.backends.gemini import GeminiBackend

        with patch.dict("os.environ", {"GEMINI_API_KEY": "AIza-test123"}):
            backend = GeminiBackend(budget=BudgetTracker(), model="gemini-2.5-flash")
            backend.set_model("gemini-2.5-pro")
            assert backend._model == "gemini-2.5-pro"

    @pytest.mark.asyncio
    async def test_assess_with_parsed_response(self):
        mock_response = _make_mock_response(
            parsed=SampleSchema(name="hello", value=7),
            text='{"name": "hello", "value": 7}',
            prompt_tokens=200,
            output_tokens=80,
        )
        backend = _make_backend_with_mock(mock_response)

        result, in_tok, out_tok = await backend.assess(
            SampleSchema, "Extract info", "System prompt",
        )

        assert result.name == "hello"
        assert result.value == 7
        assert in_tok == 200
        assert out_tok == 80

    @pytest.mark.asyncio
    async def test_assess_fallback_to_text_parsing(self):
        mock_response = _make_mock_response(
            parsed=None,
            text='{"name": "fallback", "value": 99}',
            prompt_tokens=150,
            output_tokens=60,
        )
        backend = _make_backend_with_mock(mock_response)

        result, in_tok, out_tok = await backend.assess(
            SampleSchema, "Extract", "System",
        )

        assert result.name == "fallback"
        assert result.value == 99

    @pytest.mark.asyncio
    async def test_assess_empty_response_retries(self):
        mock_response = _make_mock_response(
            parsed=None,
            text="",
            prompt_tokens=10,
            output_tokens=0,
        )
        backend = _make_backend_with_mock(mock_response)

        with pytest.raises(RuntimeError, match="Empty response"):
            await backend.assess(SampleSchema, "Extract", "System")

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        from pact.backends.gemini import GeminiBackend

        with patch.dict("os.environ", {"GEMINI_API_KEY": "AIza-test123"}):
            backend = GeminiBackend(budget=BudgetTracker(), model="gemini-2.5-flash")
            await backend.close()  # Should not raise


class TestBackendFactory:
    def test_factory_gemini(self):
        from pact.backends import create_backend

        with patch.dict("os.environ", {"GEMINI_API_KEY": "AIza-test123"}):
            backend = create_backend("gemini", BudgetTracker(), "gemini-2.5-flash")
            assert backend._model == "gemini-2.5-flash"


class TestPricingTable:
    def test_openai_models_in_pricing(self):
        from pact.budget import pricing_for_model

        inp, out = pricing_for_model("gpt-4o")
        assert inp == 2.50
        assert out == 10.00

    def test_gemini_models_in_pricing(self):
        from pact.budget import pricing_for_model

        inp, out = pricing_for_model("gemini-2.5-flash")
        assert inp == 0.15
        assert out == 0.60

    def test_gemini_pro_pricing(self):
        from pact.budget import pricing_for_model

        inp, out = pricing_for_model("gemini-2.5-pro")
        assert inp == 1.25
        assert out == 10.00
