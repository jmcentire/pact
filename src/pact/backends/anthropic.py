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
    ) -> tuple[dict | None, str, int, int]:
        tool_name = schema.__name__
        tool_schema = schema.model_json_schema()
        tool_schema.pop("title", None)

        try:
            message = await asyncio.wait_for(
                self._client.messages.create(
                    model=self._model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                    tools=[{
                        "name": tool_name,
                        "description": schema.__doc__ or f"Extract {tool_name}",
                        "input_schema": tool_schema,
                    }],
                    tool_choice={"type": "tool", "name": tool_name},
                ),
                timeout=120.0,
            )
        except asyncio.TimeoutError:
            logger.error("Anthropic API call timed out after 120s for %s", tool_name)
            raise RuntimeError(f"Anthropic API call timed out after 120s for {tool_name}")

        in_tok = message.usage.input_tokens
        out_tok = message.usage.output_tokens
        stop_reason = message.stop_reason or ""

        if not self._budget.record_tokens(in_tok, out_tok):
            raise BudgetExceeded(f"Budget exceeded after {in_tok}+{out_tok} tokens")

        for block in message.content:
            if block.type == "tool_use" and block.name == tool_name:
                return block.input, stop_reason, in_tok, out_tok

        return None, stop_reason, in_tok, out_tok

    async def close(self) -> None:
        await self._client.close()
