"""Tests for OpenAI backend."""

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


class TestOpenAIBackend:
    def test_create_without_key_raises(self):
        from pact.backends.openai import OpenAIBackend

        with patch.dict("os.environ", {}, clear=True):
            env = __import__("os").environ.copy()
            env.pop("OPENAI_API_KEY", None)
            with patch.dict("os.environ", env, clear=True):
                with pytest.raises(ValueError, match="OPENAI_API_KEY"):
                    OpenAIBackend(budget=BudgetTracker(), model="gpt-4o")

    def test_create_with_key(self):
        from pact.backends.openai import OpenAIBackend

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test123"}):
            backend = OpenAIBackend(budget=BudgetTracker(), model="gpt-4o")
            assert backend._model == "gpt-4o"

    def test_set_model(self):
        from pact.backends.openai import OpenAIBackend

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test123"}):
            backend = OpenAIBackend(budget=BudgetTracker(), model="gpt-4o")
            backend.set_model("gpt-4o-mini")
            assert backend._model == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_assess_mock(self):
        from pact.backends.openai import OpenAIBackend

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test123"}):
            budget = BudgetTracker(per_project_cap=100.0)
            budget.set_model_pricing("gpt-4o")
            backend = OpenAIBackend(budget=budget, model="gpt-4o")

            # Mock the OpenAI response
            mock_tool_call = MagicMock()
            mock_tool_call.function.arguments = json.dumps({"name": "test", "value": 42})

            mock_message = MagicMock()
            mock_message.tool_calls = [mock_tool_call]

            mock_choice = MagicMock()
            mock_choice.message = mock_message

            mock_usage = MagicMock()
            mock_usage.prompt_tokens = 100
            mock_usage.completion_tokens = 50

            mock_response = MagicMock()
            mock_response.choices = [mock_choice]
            mock_response.usage = mock_usage

            backend._client.chat.completions.create = AsyncMock(return_value=mock_response)

            result, in_tok, out_tok = await backend.assess(
                SampleSchema, "Extract info", "System prompt",
            )

            assert result.name == "test"
            assert result.value == 42
            assert in_tok == 100
            assert out_tok == 50

    @pytest.mark.asyncio
    async def test_assess_no_tool_call_retries(self):
        from pact.backends.openai import OpenAIBackend

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test123"}):
            budget = BudgetTracker(per_project_cap=100.0)
            budget.set_model_pricing("gpt-4o")
            backend = OpenAIBackend(budget=budget, model="gpt-4o")

            mock_message = MagicMock()
            mock_message.tool_calls = []

            mock_choice = MagicMock()
            mock_choice.message = mock_message

            mock_usage = MagicMock()
            mock_usage.prompt_tokens = 10
            mock_usage.completion_tokens = 5

            mock_response = MagicMock()
            mock_response.choices = [mock_choice]
            mock_response.usage = mock_usage

            backend._client.chat.completions.create = AsyncMock(return_value=mock_response)

            with pytest.raises(RuntimeError, match="No tool call"):
                await backend.assess(SampleSchema, "Extract", "System")


class TestPrepareStrictSchema:
    def test_adds_additional_properties(self):
        from pact.backends.openai import _prepare_strict_schema

        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        _prepare_strict_schema(schema)
        assert schema["additionalProperties"] is False
        assert "required" in schema
        assert set(schema["required"]) == {"name", "age"}

    def test_recurses_into_nested(self):
        from pact.backends.openai import _prepare_strict_schema

        schema = {
            "type": "object",
            "properties": {
                "inner": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                    },
                },
            },
        }
        _prepare_strict_schema(schema)
        assert schema["properties"]["inner"]["additionalProperties"] is False


class TestBackendFactory:
    def test_factory_openai(self):
        from pact.backends import create_backend

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test123"}):
            backend = create_backend("openai", BudgetTracker(), "gpt-4o")
            assert backend._model == "gpt-4o"

    def test_factory_unknown_raises(self):
        from pact.backends import create_backend

        with pytest.raises(ValueError, match="Unknown backend"):
            create_backend("nonexistent", BudgetTracker(), "model")
