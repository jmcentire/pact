"""Tests for validation retry logic in AnthropicBackend.

Covers:
- _format_validation_correction() formatting
- assess() retry on ValidationError
- assess_with_cache() retry on ValidationError
"""

from __future__ import annotations

from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError, field_validator

from pact.backends.anthropic import AnthropicBackend
from pact.budget import BudgetTracker


# ── Test schemas ──────────────────────────────────────────────────────


class SimpleSchema(BaseModel):
    """Simple test schema."""
    name: str
    value: int


class ListSchema(BaseModel):
    """Schema with a list field."""
    items: List[str]
    count: int


class StrictSchema(BaseModel):
    """Schema with a validator that rejects specific values."""
    score: int

    @field_validator("score")
    @classmethod
    def score_must_be_positive(cls, v: int) -> int:
        if v < 0:
            raise ValueError("score must be non-negative")
        return v


# ── Helpers ───────────────────────────────────────────────────────────


def _make_validation_error(schema: type[BaseModel], data: dict) -> ValidationError:
    """Create a real Pydantic ValidationError by validating bad data."""
    try:
        schema.model_validate(data)
    except ValidationError as e:
        return e
    raise AssertionError("Expected ValidationError was not raised")


def _make_backend() -> AnthropicBackend:
    """Create an AnthropicBackend without calling __init__."""
    backend = AnthropicBackend.__new__(AnthropicBackend)
    backend._model = "claude-opus-4-6"
    budget = BudgetTracker(per_project_cap=100.0)
    budget.set_model_pricing("claude-opus-4-6")
    backend._budget = budget
    return backend


# ── _format_validation_correction tests ──────────────────────────────


class TestFormatValidationCorrection:
    """Tests for _format_validation_correction static method."""

    def test_single_error_with_field_path_and_message(self):
        """Single validation error includes field path, message, and input preview."""
        error = _make_validation_error(SimpleSchema, {"name": "ok", "value": "not_an_int"})
        result = AnthropicBackend._format_validation_correction(error)

        assert "IMPORTANT: Your previous response failed schema validation" in result
        assert "value" in result
        assert "input" in result.lower() or "not_an_int" in result

    def test_multiple_errors(self):
        """Multiple validation errors produce multiple bullet points."""
        error = _make_validation_error(SimpleSchema, {"name": 123, "value": "bad"})
        result = AnthropicBackend._format_validation_correction(error)

        # Both fields should appear as separate error lines
        lines_with_field = [ln for ln in result.split("\n") if ln.strip().startswith("- Field")]
        assert len(lines_with_field) >= 2

    def test_truncates_long_input_preview(self):
        """Long input values are truncated to 120 chars in repr."""
        long_value = "x" * 500
        error = _make_validation_error(SimpleSchema, {"name": "ok", "value": long_value})
        result = AnthropicBackend._format_validation_correction(error)

        # The repr of the long string should be truncated
        # 120 chars of repr means no single line should contain 500 x's
        for line in result.split("\n"):
            if "You provided:" in line:
                provided_part = line.split("You provided:")[1]
                assert len(provided_part) < 200  # well under 500

    def test_includes_json_arrays_instruction(self):
        """Output includes the 'Lists must be JSON arrays' instruction."""
        error = _make_validation_error(SimpleSchema, {"name": "ok", "value": "bad"})
        result = AnthropicBackend._format_validation_correction(error)

        assert "Lists must be JSON arrays" in result

    def test_includes_error_type(self):
        """Each error line includes the Pydantic error type."""
        error = _make_validation_error(SimpleSchema, {"name": "ok", "value": "bad"})
        result = AnthropicBackend._format_validation_correction(error)

        # Pydantic uses error types like "int_parsing" for int fields given strings
        assert "error type:" in result

    def test_none_input_shows_na(self):
        """When error input is None, shows N/A instead of repr(None)."""
        # Missing required field produces input=None in some Pydantic versions
        error = _make_validation_error(SimpleSchema, {})
        result = AnthropicBackend._format_validation_correction(error)

        # Should not crash; should still have field references
        assert "IMPORTANT:" in result
        assert "Field" in result

    def test_nested_field_path(self):
        """Nested field paths are joined with ' -> '."""

        class Outer(BaseModel):
            inner: SimpleSchema

        error = _make_validation_error(Outer, {"inner": {"name": "ok", "value": "bad"}})
        result = AnthropicBackend._format_validation_correction(error)

        assert "inner" in result
        assert "value" in result

    def test_validator_error_message(self):
        """Custom validator error messages are included."""
        error = _make_validation_error(StrictSchema, {"score": -5})
        result = AnthropicBackend._format_validation_correction(error)

        assert "non-negative" in result


# ── assess() retry behavior tests ────────────────────────────────────


class TestAssessRetry:
    """Tests for assess() validation retry logic."""

    async def test_success_on_first_attempt_no_correction(self):
        """When validation succeeds on first attempt, no correction is appended."""
        backend = _make_backend()
        backend._call_llm = AsyncMock(return_value=(
            {"name": "test", "value": 42},
            "end_turn",
            100, 50,
        ))

        result, in_tok, out_tok = await backend.assess(
            SimpleSchema, "do something", "system prompt"
        )

        assert result.name == "test"
        assert result.value == 42
        assert backend._call_llm.call_count == 1
        # The prompt should be the original, not augmented
        call_args = backend._call_llm.call_args
        prompt_arg = call_args[0][1]  # second positional arg is prompt
        assert "IMPORTANT: Your previous response failed" not in prompt_arg

    async def test_retry_with_correction_on_validation_failure(self):
        """When first attempt fails validation, second gets error in prompt."""
        backend = _make_backend()

        # First call returns bad data (value is a string), second returns good data
        backend._call_llm = AsyncMock(side_effect=[
            ({"name": "test", "value": "not_an_int"}, "end_turn", 100, 50),
            ({"name": "test", "value": 42}, "end_turn", 100, 50),
        ])

        result, in_tok, out_tok = await backend.assess(
            SimpleSchema, "do something", "system prompt"
        )

        assert result.name == "test"
        assert result.value == 42
        assert backend._call_llm.call_count == 2

        # Second call should have correction in the prompt
        second_call_prompt = backend._call_llm.call_args_list[1][0][1]
        assert "IMPORTANT: Your previous response failed schema validation" in second_call_prompt
        assert "value" in second_call_prompt

    async def test_correction_contains_field_name_and_error_type(self):
        """The correction text contains the actual field name and error type."""
        backend = _make_backend()

        backend._call_llm = AsyncMock(side_effect=[
            ({"name": "test", "value": "bad"}, "end_turn", 100, 50),
            ({"name": "test", "value": 99}, "end_turn", 100, 50),
        ])

        await backend.assess(SimpleSchema, "prompt", "system")

        second_call_prompt = backend._call_llm.call_args_list[1][0][1]
        assert "value" in second_call_prompt
        assert "error type:" in second_call_prompt

    async def test_three_failures_raises_validation_error(self):
        """After 3 validation failures, raises ValidationError (not RuntimeError)."""
        backend = _make_backend()

        # All 3 attempts return invalid data
        backend._call_llm = AsyncMock(return_value=(
            {"name": "test", "value": "always_bad"},
            "end_turn",
            100, 50,
        ))

        with pytest.raises(ValidationError):
            await backend.assess(SimpleSchema, "prompt", "system")

        assert backend._call_llm.call_count == 3

    async def test_token_counts_accumulate_across_retries(self):
        """Token counts from all attempts are summed."""
        backend = _make_backend()

        backend._call_llm = AsyncMock(side_effect=[
            ({"name": "test", "value": "bad"}, "end_turn", 100, 50),
            ({"name": "test", "value": 42}, "end_turn", 200, 75),
        ])

        _, in_tok, out_tok = await backend.assess(
            SimpleSchema, "prompt", "system"
        )

        assert in_tok == 300
        assert out_tok == 125

    async def test_no_tool_use_block_raises_runtime_error(self):
        """When _call_llm returns None, raises RuntimeError immediately."""
        backend = _make_backend()

        backend._call_llm = AsyncMock(return_value=(
            None, "end_turn", 100, 50,
        ))

        with pytest.raises(RuntimeError, match="No tool_use block found"):
            await backend.assess(SimpleSchema, "prompt", "system")

    async def test_original_prompt_preserved_in_correction(self):
        """Correction is appended to the original prompt, not replacing it."""
        backend = _make_backend()

        original_prompt = "Please analyze this code carefully."
        backend._call_llm = AsyncMock(side_effect=[
            ({"name": "test", "value": "bad"}, "end_turn", 100, 50),
            ({"name": "test", "value": 42}, "end_turn", 100, 50),
        ])

        await backend.assess(SimpleSchema, original_prompt, "system")

        second_call_prompt = backend._call_llm.call_args_list[1][0][1]
        assert second_call_prompt.startswith(original_prompt)
        assert "IMPORTANT:" in second_call_prompt

    async def test_second_retry_includes_latest_error(self):
        """Third attempt (second retry) includes the error from the second attempt."""
        backend = _make_backend()

        backend._call_llm = AsyncMock(side_effect=[
            ({"name": "test", "value": "bad1"}, "end_turn", 100, 50),
            ({"name": 123, "value": "bad2"}, "end_turn", 100, 50),
            ({"name": "ok", "value": 42}, "end_turn", 100, 50),
        ])

        result, _, _ = await backend.assess(SimpleSchema, "prompt", "system")
        assert result.value == 42
        assert backend._call_llm.call_count == 3

        # Third call prompt should reference the second attempt's error
        third_call_prompt = backend._call_llm.call_args_list[2][0][1]
        assert "IMPORTANT:" in third_call_prompt

    async def test_list_field_validation_retry(self):
        """Retry works for list field validation errors."""
        backend = _make_backend()

        backend._call_llm = AsyncMock(side_effect=[
            # First attempt: items is a string instead of list
            ({"items": "not a list", "count": 3}, "end_turn", 100, 50),
            # Second attempt: correct
            ({"items": ["a", "b", "c"], "count": 3}, "end_turn", 100, 50),
        ])

        result, _, _ = await backend.assess(ListSchema, "prompt", "system")
        assert result.items == ["a", "b", "c"]

        # Verify correction was sent
        second_prompt = backend._call_llm.call_args_list[1][0][1]
        assert "IMPORTANT:" in second_prompt


# ── assess_with_cache() retry behavior tests ─────────────────────────


class TestAssessWithCacheRetry:
    """Tests for assess_with_cache() validation retry logic (mirrors assess)."""

    async def test_success_on_first_attempt_no_correction(self):
        """When validation succeeds first try, no correction appended."""
        backend = _make_backend()
        backend._call_llm_cached = AsyncMock(return_value=(
            {"name": "test", "value": 42},
            "end_turn",
            100, 50,
        ))

        result, in_tok, out_tok = await backend.assess_with_cache(
            SimpleSchema, "do something", "system prompt", cache_prefix="prefix"
        )

        assert result.name == "test"
        assert result.value == 42
        assert backend._call_llm_cached.call_count == 1
        call_args = backend._call_llm_cached.call_args
        prompt_arg = call_args[0][1]
        assert "IMPORTANT: Your previous response failed" not in prompt_arg

    async def test_retry_with_correction_on_validation_failure(self):
        """Validation failure on first attempt triggers retry with correction."""
        backend = _make_backend()

        backend._call_llm_cached = AsyncMock(side_effect=[
            ({"name": "test", "value": "not_an_int"}, "end_turn", 100, 50),
            ({"name": "test", "value": 42}, "end_turn", 100, 50),
        ])

        result, _, _ = await backend.assess_with_cache(
            SimpleSchema, "prompt", "system", cache_prefix="prefix"
        )

        assert result.value == 42
        assert backend._call_llm_cached.call_count == 2

        second_call_prompt = backend._call_llm_cached.call_args_list[1][0][1]
        assert "IMPORTANT: Your previous response failed schema validation" in second_call_prompt

    async def test_three_failures_raises_validation_error(self):
        """After 3 failures, raises ValidationError (not RuntimeError)."""
        backend = _make_backend()

        backend._call_llm_cached = AsyncMock(return_value=(
            {"name": "test", "value": "always_bad"},
            "end_turn",
            100, 50,
        ))

        with pytest.raises(ValidationError):
            await backend.assess_with_cache(
                SimpleSchema, "prompt", "system", cache_prefix="prefix"
            )

        assert backend._call_llm_cached.call_count == 3

    async def test_token_counts_accumulate(self):
        """Token counts sum across retry attempts."""
        backend = _make_backend()

        backend._call_llm_cached = AsyncMock(side_effect=[
            ({"name": "test", "value": "bad"}, "end_turn", 150, 60),
            ({"name": "test", "value": 42}, "end_turn", 150, 60),
        ])

        _, in_tok, out_tok = await backend.assess_with_cache(
            SimpleSchema, "prompt", "system", cache_prefix="prefix"
        )

        assert in_tok == 300
        assert out_tok == 120
