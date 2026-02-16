"""OpenAI backend — structured output via tool_choice + strict mode.

Also compatible with DeepSeek, Together AI, Groq, and any provider
implementing the OpenAI API spec via base_url override.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from pact.budget import BudgetExceeded, BudgetTracker

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class OpenAIBackend:
    """Backend using the OpenAI API with tool_choice for structured extraction."""

    def __init__(
        self,
        budget: BudgetTracker,
        model: str = "gpt-4o",
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required. Install with: pip install openai"
            ) from exc

        resolved_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not resolved_key:
            raise ValueError("OPENAI_API_KEY environment variable is required.")

        kwargs: dict = {
            "api_key": resolved_key,
            "max_retries": 3,
            "timeout": 600.0,
        }
        if base_url:
            kwargs["base_url"] = base_url

        self._client = AsyncOpenAI(**kwargs)
        self._model = model
        self._budget = budget

    def set_model(self, model: str) -> None:
        self._model = model

    # Model-specific output token limits
    _MAX_OUTPUT_TOKENS: dict[str, int] = {
        "gpt-4o": 16384,
        "gpt-4o-mini": 16384,
        "gpt-4-turbo": 4096,
    }
    _DEFAULT_MAX_OUTPUT = 16384

    async def assess(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        max_tokens: int = 32768,
    ) -> tuple[T, int, int]:
        """Call LLM with schema enforcement via tool_choice."""
        # Cap max_tokens to model's limit
        model_max = self._MAX_OUTPUT_TOKENS.get(self._model, self._DEFAULT_MAX_OUTPUT)
        max_tokens = min(max_tokens, model_max)

        tool_name = schema.__name__
        tool_schema = schema.model_json_schema()
        tool_schema.pop("title", None)

        # Check if schema is compatible with strict mode
        use_strict = _is_strict_compatible(tool_schema)
        if use_strict:
            _prepare_strict_schema(tool_schema)

        # For non-strict schemas (free-form dicts), use json_object mode
        # instead of tool_choice since GPT-4o ignores non-strict tool schemas
        if use_strict:
            return await self._assess_tool_choice(
                schema, tool_name, tool_schema, prompt, system, max_tokens,
            )
        else:
            return await self._assess_json_mode(
                schema, tool_name, tool_schema, prompt, system, max_tokens,
            )

    async def _assess_tool_choice(
        self,
        schema: type[T],
        tool_name: str,
        tool_schema: dict,
        prompt: str,
        system: str,
        max_tokens: int,
    ) -> tuple[T, int, int]:
        """Structured output via tool_choice with strict mode."""
        for attempt in range(3):
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    tools=[{
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "description": schema.__doc__ or f"Extract {tool_name}",
                            "parameters": tool_schema,
                            "strict": True,
                        },
                    }],
                    tool_choice={
                        "type": "function",
                        "function": {"name": tool_name},
                    },
                )
            except Exception as e:
                if attempt < 2:
                    logger.warning("OpenAI API error (attempt %d): %s", attempt + 1, e)
                    continue
                raise

            usage = response.usage
            in_tok = usage.prompt_tokens if usage else 0
            out_tok = usage.completion_tokens if usage else 0

            if not self._budget.record_tokens(in_tok, out_tok):
                raise BudgetExceeded(f"Budget exceeded after {in_tok}+{out_tok} tokens")

            message = response.choices[0].message
            if not message.tool_calls:
                if attempt < 2:
                    continue
                raise RuntimeError(f"No tool call returned for {tool_name}")

            tool_call = message.tool_calls[0]
            try:
                raw = json.loads(tool_call.function.arguments)
                parsed = schema.model_validate(raw)
                return parsed, in_tok, out_tok
            except (json.JSONDecodeError, ValidationError) as e:
                if attempt < 2:
                    logger.warning("Parse error (attempt %d): %s", attempt + 1, e)
                    continue
                raise

        raise RuntimeError(f"Failed to get valid {tool_name} after 3 attempts")

    async def _assess_json_mode(
        self,
        schema: type[T],
        tool_name: str,
        tool_schema: dict,
        prompt: str,
        system: str,
        max_tokens: int,
    ) -> tuple[T, int, int]:
        """Structured output via json_object response format for non-strict schemas."""
        schema_instruction = (
            f"\n\nRespond with a JSON object matching this schema:\n"
            f"```json\n{json.dumps(tool_schema, indent=2)}\n```\n"
            f"Return ONLY valid JSON, no markdown fences or explanation."
        )

        for attempt in range(3):
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt + schema_instruction},
                    ],
                    response_format={"type": "json_object"},
                )
            except Exception as e:
                if attempt < 2:
                    logger.warning("OpenAI API error (attempt %d): %s", attempt + 1, e)
                    continue
                raise

            usage = response.usage
            in_tok = usage.prompt_tokens if usage else 0
            out_tok = usage.completion_tokens if usage else 0

            if not self._budget.record_tokens(in_tok, out_tok):
                raise BudgetExceeded(f"Budget exceeded after {in_tok}+{out_tok} tokens")

            content = response.choices[0].message.content or ""
            try:
                raw = json.loads(content)
                parsed = schema.model_validate(raw)
                return parsed, in_tok, out_tok
            except (json.JSONDecodeError, ValidationError) as e:
                if attempt < 2:
                    logger.warning("JSON parse error (attempt %d): %s", attempt + 1, e)
                    continue
                raise

        raise RuntimeError(f"Failed to get valid {tool_name} after 3 attempts")

    async def close(self) -> None:
        await self._client.close()


def _is_strict_compatible(schema: dict) -> bool:
    """Check if a JSON schema is compatible with OpenAI strict mode.

    Strict mode can't handle free-form objects (additionalProperties with
    a type schema, e.g., dict[str, str]). Detect these and fall back.
    """
    if isinstance(schema, dict):
        # Free-form dict: additionalProperties is a type schema, not just true/false
        ap = schema.get("additionalProperties")
        if isinstance(ap, dict):
            return False
        for value in schema.values():
            if isinstance(value, dict):
                if not _is_strict_compatible(value):
                    return False
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and not _is_strict_compatible(item):
                        return False
    return True


def _prepare_strict_schema(schema: dict) -> None:
    """Recursively prepare a JSON schema for OpenAI strict mode.

    Strict mode requires:
    - additionalProperties: false on all objects
    - ALL properties listed in 'required' (not just non-default ones)
    - Optional fields use anyOf with null type instead of being absent from required
    """
    if schema.get("type") == "object" and "properties" in schema:
        schema["additionalProperties"] = False
        # Strict mode: ALL properties must be in required
        schema["required"] = list(schema["properties"].keys())

        # Convert optional fields (those with defaults) to accept null
        for prop_name, prop_schema in schema["properties"].items():
            if isinstance(prop_schema, dict) and "default" in prop_schema:
                _make_nullable(prop_schema)

    # Handle $defs
    for defn in schema.get("$defs", {}).values():
        _prepare_strict_schema(defn)

    # Recurse into properties
    for prop in schema.get("properties", {}).values():
        if isinstance(prop, dict):
            _prepare_strict_schema(prop)
            # Handle array items
            if "items" in prop and isinstance(prop["items"], dict):
                _prepare_strict_schema(prop["items"])
            # Handle anyOf/oneOf variants
            for variant_key in ("anyOf", "oneOf"):
                for variant in prop.get(variant_key, []):
                    if isinstance(variant, dict):
                        _prepare_strict_schema(variant)


def _make_nullable(prop: dict) -> None:
    """Make a property schema accept null values for strict mode compatibility."""
    if "anyOf" in prop:
        # Already has anyOf — add null type if not present
        null_types = [v for v in prop["anyOf"] if v == {"type": "null"}]
        if not null_types:
            prop["anyOf"].append({"type": "null"})
    elif "type" in prop:
        current_type = prop["type"]
        if isinstance(current_type, list):
            if "null" not in current_type:
                current_type.append("null")
        elif current_type != "null":
            # Convert type to anyOf with null
            prop_copy = {k: v for k, v in prop.items() if k != "default"}
            prop.clear()
            prop["anyOf"] = [prop_copy, {"type": "null"}]
            # Restore default
            if "default" in prop_copy:
                prop["default"] = prop_copy.pop("default")
