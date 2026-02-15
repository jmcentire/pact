"""Anthropic backend — direct API calls with tool_choice schema enforcement.

Reused from swarm with import path adaptation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from pact.budget import BudgetExceeded, BudgetTracker

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_MODEL_MAX_TOKENS: dict[str, int] = {
    "claude-opus-4-6": 32768,
    "claude-sonnet-4-5-20250929": 64000,
    "claude-haiku-4-5-20251001": 8192,
}
_DEFAULT_MAX_TOKENS_CAP = 32768


class AnthropicBackend:
    """Backend using the Anthropic API with tool_choice for structured extraction."""

    def __init__(self, budget: BudgetTracker, model: str = "claude-opus-4-6") -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required. Install with: pip install -e '.[cli]'"
            ) from exc

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required.")

        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            max_retries=3,
            timeout=600.0,
        )
        self._model = model
        self._budget = budget

    def set_model(self, model: str) -> None:
        self._model = model

    def _max_tokens_cap(self) -> int:
        return _MODEL_MAX_TOKENS.get(self._model, _DEFAULT_MAX_TOKENS_CAP)

    async def assess(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        max_tokens: int = 32768,
    ) -> tuple[T, int, int]:
        """Call LLM with schema enforcement via tool_choice."""
        total_in = 0
        total_out = 0
        cap = self._max_tokens_cap()
        current_max = min(max_tokens, cap)

        for attempt in range(3):
            raw_input, stop_reason, in_tok, out_tok = await self._call_llm(
                schema, prompt, system, current_max,
            )
            total_in += in_tok
            total_out += out_tok

            if raw_input is None:
                raise RuntimeError(
                    f"No tool_use block found for {schema.__name__}"
                )

            if stop_reason == "max_tokens" and attempt < 2:
                new_max = min(current_max * 2, cap)
                if new_max > current_max:
                    current_max = new_max
                    continue

            raw_input = self._coerce_fields(raw_input)

            try:
                parsed = schema.model_validate(raw_input)
                return parsed, total_in, total_out
            except ValidationError as e:
                if attempt < 2:
                    new_max = min(current_max * 2, cap)
                    if new_max > current_max:
                        current_max = new_max
                    continue
                raise

        raise RuntimeError(f"Failed to get valid {schema.__name__} after 3 attempts")

    @staticmethod
    def _coerce_fields(data: dict) -> dict:
        if not isinstance(data, dict):
            return data
        coerced = {}
        for key, value in data.items():
            if isinstance(value, str) and value.startswith(("[", "{")):
                try:
                    coerced[key] = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    coerced[key] = value
            else:
                coerced[key] = value
        return coerced

    async def _call_llm(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        max_tokens: int,
        stall_timeout: float = 300.0,
    ) -> tuple[dict | None, str, int, int]:
        """Call LLM with streaming progress detection.

        Uses streaming so we can distinguish a stalled connection
        (no events for stall_timeout seconds) from a legitimately
        long generation that's actively producing tokens.
        """
        tool_name = schema.__name__
        tool_schema = schema.model_json_schema()
        tool_schema.pop("title", None)

        try:
            message = await self._stream_with_stall_detection(
                tool_name, tool_schema, schema.__doc__ or f"Extract {tool_name}",
                prompt, system, max_tokens, stall_timeout,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Anthropic API stalled (no progress for %.0fs) for %s",
                stall_timeout, tool_name,
            )
            raise RuntimeError(
                f"Anthropic API stalled (no progress for {stall_timeout:.0f}s) for {tool_name}"
            )

        in_tok = message.usage.input_tokens
        out_tok = message.usage.output_tokens
        stop_reason = message.stop_reason or ""

        if not self._budget.record_tokens(in_tok, out_tok):
            raise BudgetExceeded(f"Budget exceeded after {in_tok}+{out_tok} tokens")

        for block in message.content:
            if block.type == "tool_use" and block.name == tool_name:
                return block.input, stop_reason, in_tok, out_tok

        return None, stop_reason, in_tok, out_tok

    async def _stream_with_stall_detection(
        self,
        tool_name: str,
        tool_schema: dict,
        tool_description: str,
        prompt: str,
        system: str,
        max_tokens: int,
        stall_timeout: float,
    ):
        """Stream a response, raising TimeoutError if no event arrives within stall_timeout.

        Unlike a hard timeout on the full request, this only fires when the
        connection goes silent — a 10-minute generation that's actively
        streaming tokens will never trigger it.
        """
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            tools=[{
                "name": tool_name,
                "description": tool_description,
                "input_schema": tool_schema,
            }],
            tool_choice={"type": "tool", "name": tool_name},
        ) as stream:
            aiter = stream.__aiter__()
            while True:
                try:
                    await asyncio.wait_for(aiter.__anext__(), timeout=stall_timeout)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    raise asyncio.TimeoutError()

        return await stream.get_final_message()

    # ── Prompt caching helpers ──────────────────────────────────────────

    _CACHE_MIN_CHARS = 300
    _CACHE_MIN_SYSTEM_CHARS = 4  # system prompts are almost always reused

    def _build_system_blocks(self, system: str, *, cache: bool = False) -> list[dict]:
        """Convert system string to content block list.

        If *cache* is True and the text is long enough, the block gets
        ``cache_control: {"type": "ephemeral"}``.  Very short system
        strings (< 4 chars) skip cache_control since the overhead is
        not worthwhile.
        """
        block: dict = {"type": "text", "text": system}
        if cache and len(system) >= self._CACHE_MIN_SYSTEM_CHARS:
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    def _build_user_blocks(
        self, cache_prefix: str, prompt: str
    ) -> str | list[dict]:
        """Build user content: prefix block (optionally cached) + dynamic prompt.

        Returns a plain string when there is no prefix, avoiding unnecessary
        overhead.
        """
        if not cache_prefix:
            return prompt

        prefix_block: dict = {"type": "text", "text": cache_prefix}
        if len(cache_prefix) >= self._CACHE_MIN_CHARS:
            prefix_block["cache_control"] = {"type": "ephemeral"}

        prompt_block: dict = {"type": "text", "text": prompt}
        return [prefix_block, prompt_block]

    # ── Cached LLM call ──────────────────────────────────────────────

    async def _call_llm_cached(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        cache_prefix: str,
        max_tokens: int,
        stall_timeout: float = 300.0,
    ) -> tuple[dict | None, str, int, int]:
        """Like _call_llm but sends system + cache_prefix with cache_control."""
        tool_name = schema.__name__
        tool_schema = schema.model_json_schema()
        tool_schema.pop("title", None)

        system_blocks = self._build_system_blocks(system, cache=True)
        user_content = self._build_user_blocks(cache_prefix, prompt)

        try:
            async with self._client.messages.stream(
                model=self._model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=[{"role": "user", "content": user_content}],
                tools=[{
                    "name": tool_name,
                    "description": schema.__doc__ or f"Extract {tool_name}",
                    "input_schema": tool_schema,
                }],
                tool_choice={"type": "tool", "name": tool_name},
            ) as stream:
                aiter = stream.__aiter__()
                while True:
                    try:
                        await asyncio.wait_for(aiter.__anext__(), timeout=stall_timeout)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        raise asyncio.TimeoutError()

            message = await stream.get_final_message()
        except asyncio.TimeoutError:
            logger.error(
                "Anthropic API stalled (no progress for %.0fs) for %s",
                stall_timeout, tool_name,
            )
            raise RuntimeError(
                f"Anthropic API stalled (no progress for {stall_timeout:.0f}s) for {tool_name}"
            )

        in_tok = message.usage.input_tokens
        out_tok = message.usage.output_tokens
        stop_reason = message.stop_reason or ""

        # Record cache metrics
        cache_creation = getattr(message.usage, 'cache_creation_input_tokens', 0) or 0
        cache_read = getattr(message.usage, 'cache_read_input_tokens', 0) or 0
        if cache_creation or cache_read:
            self._budget.record_cache_tokens(cache_creation, cache_read)

        if not self._budget.record_tokens(in_tok, out_tok):
            raise BudgetExceeded(f"Budget exceeded after {in_tok}+{out_tok} tokens")

        for block in message.content:
            if block.type == "tool_use" and block.name == tool_name:
                return block.input, stop_reason, in_tok, out_tok

        return None, stop_reason, in_tok, out_tok

    # ── Public cached assess ─────────────────────────────────────────

    async def assess_with_cache(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        cache_prefix: str = "",
        max_tokens: int = 32768,
    ) -> tuple[T, int, int]:
        """Like assess() but marks system + cache_prefix for prompt caching.

        Args:
            system: System prompt -- always cached.
            cache_prefix: Static portion of user prompt (SOPs, contracts, etc.).
                          Sent as a separate content block with cache_control.
            prompt: Dynamic portion of user prompt (attempt-specific).
        """
        total_in = 0
        total_out = 0
        cap = self._max_tokens_cap()
        current_max = min(max_tokens, cap)

        for attempt in range(3):
            raw_input, stop_reason, in_tok, out_tok = await self._call_llm_cached(
                schema, prompt, system, cache_prefix, current_max,
            )
            total_in += in_tok
            total_out += out_tok

            if raw_input is None:
                raise RuntimeError(
                    f"No tool_use block found for {schema.__name__}"
                )

            if stop_reason == "max_tokens" and attempt < 2:
                new_max = min(current_max * 2, cap)
                if new_max > current_max:
                    current_max = new_max
                    continue

            raw_input = self._coerce_fields(raw_input)

            try:
                parsed = schema.model_validate(raw_input)
                return parsed, total_in, total_out
            except ValidationError as e:
                if attempt < 2:
                    new_max = min(current_max * 2, cap)
                    if new_max > current_max:
                        current_max = new_max
                    continue
                raise

        raise RuntimeError(f"Failed to get valid {schema.__name__} after 3 attempts")

    async def close(self) -> None:
        await self._client.close()
