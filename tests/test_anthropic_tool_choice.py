"""Tests for AnthropicBackend's tool_choice fallback.

Claude Opus 5.5 (and Sonnet 5.5, Fable 5.1) return a 400 for a forced
tool_choice. The backend switches to tool_choice "auto" plus a system
instruction and retries when a response comes back without a tool call.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anthropic
import pytest
from pydantic import BaseModel

from pact.backends.anthropic import AnthropicBackend
from pact.budget import BudgetTracker


class SimpleSchema(BaseModel):
    """Simple test schema."""
    name: str
    value: int


def _make_backend() -> AnthropicBackend:
    backend = AnthropicBackend.__new__(AnthropicBackend)
    backend._model = "claude-opus-5-5"
    budget = BudgetTracker(per_project_cap=100.0)
    budget.set_model_pricing("claude-opus-5-5")
    backend._budget = budget
    backend._client = MagicMock()
    return backend


def _bad_request(message: str) -> anthropic.BadRequestError:
    response = MagicMock(status_code=400, headers={})
    return anthropic.BadRequestError(message, response=response, body=None)


class _FakeStream:
    def __init__(self, message):
        self._message = message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def get_final_message(self):
        return self._message


def _tool_message(tool_name: str, tool_input: dict):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name=tool_name, input=tool_input)],
        stop_reason="tool_use",
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


def _stream_factory(outcomes: list):
    """Return a messages.stream stand-in that raises or yields each outcome in turn."""
    outcomes = list(outcomes)

    def stream(**kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeStream(outcome)

    return MagicMock(side_effect=stream)


_TOOL_CHOICE_400 = _bad_request(
    'tool_choice: type "tool" and "any" are not supported for this model.'
)


class TestForcedToolChoiceFallback:
    async def test_forced_rejection_retries_with_auto(self):
        backend = _make_backend()
        backend._client.messages.stream = _stream_factory([
            _TOOL_CHOICE_400,
            _tool_message("SimpleSchema", {"name": "a", "value": 1}),
        ])

        result, _, _ = await backend.assess(SimpleSchema, "prompt", "system prompt")

        assert result.value == 1
        calls = backend._client.messages.stream.call_args_list
        assert calls[0].kwargs["tool_choice"] == {"type": "tool", "name": "SimpleSchema"}
        assert calls[1].kwargs["tool_choice"]["type"] == "auto"
        assert calls[1].kwargs["system"].startswith("system prompt")
        assert "`SimpleSchema` tool" in calls[1].kwargs["system"]

    async def test_auto_mode_persists_for_later_calls(self):
        backend = _make_backend()
        backend._client.messages.stream = _stream_factory([
            _TOOL_CHOICE_400,
            _tool_message("SimpleSchema", {"name": "a", "value": 1}),
            _tool_message("SimpleSchema", {"name": "b", "value": 2}),
        ])

        await backend.assess(SimpleSchema, "prompt", "system")
        await backend.assess(SimpleSchema, "prompt", "system")

        calls = backend._client.messages.stream.call_args_list
        assert len(calls) == 3
        assert calls[2].kwargs["tool_choice"]["type"] == "auto"

    async def test_set_model_restores_forced_tool_choice(self):
        backend = _make_backend()
        backend._forced_tool_choice = False
        backend.set_model("claude-opus-4-8")
        assert backend._forced_tool_choice is True

    async def test_cached_path_appends_instruction_block(self):
        backend = _make_backend()
        backend._client.messages.stream = _stream_factory([
            _TOOL_CHOICE_400,
            _tool_message("SimpleSchema", {"name": "a", "value": 1}),
        ])

        await backend.assess_with_cache(
            SimpleSchema, "prompt", "system prompt", cache_prefix="x" * 400,
        )

        system = backend._client.messages.stream.call_args_list[1].kwargs["system"]
        assert system[0]["text"] == "system prompt"
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert "`SimpleSchema` tool" in system[-1]["text"]

    async def test_other_bad_request_is_raised(self):
        backend = _make_backend()
        backend._client.messages.stream = _stream_factory([
            _bad_request("max_tokens: too large"),
        ])

        with pytest.raises(anthropic.BadRequestError):
            await backend.assess(SimpleSchema, "prompt", "system")
        assert backend._forced_tool_choice is True

    async def test_concurrent_forced_rejections_both_fall_back(self):
        # Both requests go out forced. The first 400 flips the instance to
        # auto before the second 400 lands; the second must still retry.
        backend = _make_backend()
        both_sent = asyncio.Event()
        forced_sent = 0

        class _RejectingStream(_FakeStream):
            async def __aenter__(self):
                nonlocal forced_sent
                forced_sent += 1
                if forced_sent == 2:
                    both_sent.set()
                await both_sent.wait()
                raise _TOOL_CHOICE_400

        def stream(**kwargs):
            if kwargs["tool_choice"]["type"] == "tool":
                return _RejectingStream(None)
            return _FakeStream(_tool_message("SimpleSchema", {"name": "a", "value": 1}))

        backend._client.messages.stream = MagicMock(side_effect=stream)

        results = await asyncio.gather(
            backend.assess(SimpleSchema, "prompt", "system"),
            backend.assess(SimpleSchema, "prompt", "system"),
        )

        assert [r.value for r, _, _ in results] == [1, 1]
        assert forced_sent == 2
        assert backend._forced_tool_choice is False


class TestMissedToolCall:
    async def test_retries_with_correction_when_no_tool_call(self):
        backend = _make_backend()
        backend._call_llm = AsyncMock(side_effect=[
            (None, "end_turn", 100, 50),
            ({"name": "a", "value": 1}, "tool_use", 100, 50),
        ])

        result, in_tok, _ = await backend.assess(SimpleSchema, "prompt", "system")

        assert result.value == 1
        assert in_tok == 200
        retry_prompt = backend._call_llm.call_args_list[1][0][1]
        assert retry_prompt.startswith("prompt")
        assert "did not call the `SimpleSchema` tool" in retry_prompt

    async def test_cached_retries_with_correction_when_no_tool_call(self):
        backend = _make_backend()
        backend._call_llm_cached = AsyncMock(side_effect=[
            (None, "end_turn", 100, 50),
            ({"name": "a", "value": 1}, "tool_use", 100, 50),
        ])

        result, _, _ = await backend.assess_with_cache(SimpleSchema, "prompt", "system")

        assert result.value == 1
        assert "did not call" in backend._call_llm_cached.call_args_list[1][0][1]

    async def test_refusal_raises_without_retry(self):
        backend = _make_backend()
        backend._call_llm = AsyncMock(return_value=(None, "refusal", 100, 0))

        with pytest.raises(RuntimeError, match="refused"):
            await backend.assess(SimpleSchema, "prompt", "system")
        assert backend._call_llm.call_count == 1
