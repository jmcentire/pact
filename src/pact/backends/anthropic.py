"""Anthropic backend — direct API calls with tool_choice schema enforcement.

Reused from swarm with import path adaptation.

Newer models (Claude Opus 5.5, Sonnet 5.5, Fable 5.1) reject forced tool
use with a 400. The backend starts with a forced tool_choice and, on that
400, switches the instance to tool_choice "auto" plus a system-prompt
instruction naming the tool. Strict tool use is not an option here: several
Pact schemas have dict[str, X] fields, and strict mode only accepts
additionalProperties: false. Pydantic validation and the correction retry
loop keep the output schema-valid in both modes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from pact.budget import BudgetExceeded, BudgetTracker

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_MODEL_MAX_TOKENS: dict[str, int] = {
    "claude-opus-4-6": 32768,
    "claude-sonnet-4-5-20250929": 64000,
    "claude-haiku-4-5-20251001": 8192,
    "claude-opus-5-5": 128000,
}
_DEFAULT_MAX_TOKENS_CAP = 32768


_TOOL_CHOICE_UNSUPPORTED_MARKER = "tool_choice"


class AnthropicBackend:
    """Backend using the Anthropic API with tool_choice for structured extraction."""

    # Flipped to False per instance the first time the model rejects a
    # forced tool_choice; reset by set_model().
    _forced_tool_choice: bool = True

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
        self._forced_tool_choice = True

    def _max_tokens_cap(self) -> int:
        return _MODEL_MAX_TOKENS.get(self._model, _DEFAULT_MAX_TOKENS_CAP)

    async def assess(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        max_tokens: int = 32768,
    ) -> tuple[T, int, int]:
        """Call LLM with schema enforcement via tool_choice.

        On validation failure, feeds the error back to the LLM as a
        correction prompt so it can fix its output.
        """
        total_in = 0
        total_out = 0
        cap = self._max_tokens_cap()
        current_max = min(max_tokens, cap)
        last_error: ValidationError | None = None
        missed_tool_call = False

        for attempt in range(3):
            # On retry, augment prompt with the correction for the last failure
            effective_prompt = prompt
            if last_error is not None:
                correction = self._format_validation_correction(last_error)
                effective_prompt = f"{prompt}\n\n{correction}"
                logger.info("Retrying %s with validation feedback (attempt %d)",
                            schema.__name__, attempt + 1)
            elif missed_tool_call:
                effective_prompt = (
                    f"{prompt}\n\n{self._missed_tool_call_correction(schema.__name__)}"
                )
                logger.info("Retrying %s after response without a tool call (attempt %d)",
                            schema.__name__, attempt + 1)

            raw_input, stop_reason, in_tok, out_tok = await self._call_llm(
                schema, effective_prompt, system, current_max,
            )
            total_in += in_tok
            total_out += out_tok

            if stop_reason == "refusal":
                raise RuntimeError(f"Model refused to produce {schema.__name__}")

            if stop_reason == "max_tokens" and attempt < 2:
                new_max = min(current_max * 2, cap)
                if new_max > current_max:
                    current_max = new_max
                    continue

            if raw_input is None:
                # Under tool_choice "auto" the model can answer in text
                # without calling the tool.
                if attempt < 2:
                    missed_tool_call = True
                    last_error = None
                    continue
                raise RuntimeError(
                    f"No tool_use block found for {schema.__name__}"
                )
            missed_tool_call = False

            raw_input = self._coerce_fields(raw_input)

            try:
                parsed = schema.model_validate(raw_input)
                return parsed, total_in, total_out
            except ValidationError as e:
                last_error = e
                if attempt < 2:
                    new_max = min(current_max * 2, cap)
                    if new_max > current_max:
                        current_max = new_max
                    continue
                raise

        raise RuntimeError(f"Failed to get valid {schema.__name__} after 3 attempts")

    @staticmethod
    def _missed_tool_call_correction(tool_name: str) -> str:
        return (
            "IMPORTANT: Your previous response did not call the "
            f"`{tool_name}` tool. Respond by calling `{tool_name}` exactly "
            "once with your complete answer as its input."
        )

    @staticmethod
    def _tool_instruction(tool_name: str) -> str:
        return (
            f"Deliver your answer by calling the `{tool_name}` tool exactly "
            "once, with the complete result as its input. Do not answer in "
            "plain text."
        )

    @staticmethod
    def _format_validation_correction(error: ValidationError) -> str:
        """Format a Pydantic ValidationError into a clear correction prompt.

        Tells the LLM exactly what fields failed validation and what types
        are expected, so it can fix its output on the next attempt.
        """
        lines = [
            "IMPORTANT: Your previous response failed schema validation. "
            "Please fix the following errors and try again:\n"
        ]
        for err in error.errors():
            loc = " -> ".join(str(p) for p in err["loc"])
            msg = err["msg"]
            typ = err["type"]
            inp = err.get("input")
            inp_preview = repr(inp)[:120] if inp is not None else "N/A"
            lines.append(
                f"  - Field '{loc}': {msg} (error type: {typ}). "
                f"You provided: {inp_preview}"
            )
        lines.append(
            "\nEnsure all fields match the required types exactly. "
            "Lists must be JSON arrays (not strings). "
            "Dictionaries must be JSON objects (not strings). "
            "String fields must be plain strings (not objects)."
        )
        return "\n".join(lines)

    @classmethod
    def _coerce_fields(cls, data):
        """Recursively parse string fields that look like JSON.

        LLMs sometimes return structured fields (lists, dicts) as JSON
        strings instead of native objects.  This walks the entire tree
        so nested structures are also fixed.
        """
        if isinstance(data, dict):
            return {k: cls._coerce_fields(v) for k, v in data.items()}
        if isinstance(data, list):
            return [cls._coerce_fields(item) for item in data]
        if isinstance(data, str):
            stripped = data.strip()
            if stripped and stripped[0] in ("[", "{"):
                try:
                    parsed = json.loads(stripped)
                    return cls._coerce_fields(parsed)
                except (json.JSONDecodeError, ValueError):
                    pass
                # Common LLM JSON malformations: trailing commas, control chars
                repaired = cls._repair_json(stripped)
                if repaired is not None:
                    return cls._coerce_fields(repaired)
        return data

    @staticmethod
    def _repair_json(s: str):
        """Attempt common repairs on malformed JSON from LLMs."""
        # Remove trailing commas before ] or }
        fixed = re.sub(r",\s*([}\]])", r"\1", s)
        # Replace unescaped control characters (tabs, newlines inside strings)
        # by walking through and escaping bare control chars
        try:
            parsed = json.loads(fixed)
            return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        # Try replacing single quotes with double quotes (only outer-level)
        try:
            parsed = json.loads(fixed.replace("'", '"'))
            return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return None

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
        # Optional register normalization via Transmogrifier
        try:
            from transmogrifier.core import Transmogrifier
            from transmogrifier.system_prompts import inject_system_prompt
            _transmog = Transmogrifier()
            result = _transmog.translate(prompt, model=self._model)
            prompt = result.output_text
            if result.system_prompt:
                system = inject_system_prompt(system, result.system_prompt)
        except ImportError:
            pass
        except Exception:
            pass

        return await self._stream_tool_call(
            tool_name, tool_schema, tool_description,
            system, prompt, max_tokens, stall_timeout,
        )

    async def _stream_tool_call(
        self,
        tool_name: str,
        tool_schema: dict,
        tool_description: str,
        system: str | list[dict],
        user_content: str | list[dict],
        max_tokens: int,
        stall_timeout: float,
    ):
        """Stream one tool-extraction request and return the final message.

        Sends a forced tool_choice until the model rejects it, then retries
        with tool_choice "auto" and a system instruction naming the tool.
        Raises asyncio.TimeoutError if no event arrives within stall_timeout.
        """
        import anthropic

        tool = {
            "name": tool_name,
            "description": tool_description,
            "input_schema": tool_schema,
        }
        # Request-local: a concurrent request may flip the instance flag
        # while this one is in flight.
        forced = self._forced_tool_choice
        while True:
            if forced:
                request_system = system
                tool_choice = {"type": "tool", "name": tool_name}
            else:
                instruction = self._tool_instruction(tool_name)
                if isinstance(system, str):
                    request_system = f"{system}\n\n{instruction}"
                else:
                    request_system = [*system, {"type": "text", "text": instruction}]
                tool_choice = {"type": "auto", "disable_parallel_tool_use": True}

            try:
                async with self._client.messages.stream(
                    model=self._model,
                    max_tokens=max_tokens,
                    system=request_system,
                    messages=[{"role": "user", "content": user_content}],
                    tools=[tool],
                    tool_choice=tool_choice,
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
            except anthropic.BadRequestError as exc:
                if not forced or _TOOL_CHOICE_UNSUPPORTED_MARKER not in str(exc):
                    raise
                if self._forced_tool_choice:
                    logger.info(
                        "%s rejected forced tool_choice; using tool_choice auto",
                        self._model,
                    )
                self._forced_tool_choice = False
                forced = False

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
        # Optional register normalization via Transmogrifier
        try:
            from transmogrifier.core import Transmogrifier
            from transmogrifier.system_prompts import inject_system_prompt
            _transmog = Transmogrifier()
            result = _transmog.translate(prompt, model=self._model)
            prompt = result.output_text
            if result.system_prompt:
                system = inject_system_prompt(system, result.system_prompt)
        except ImportError:
            pass
        except Exception:
            pass

        tool_name = schema.__name__
        tool_schema = schema.model_json_schema()
        tool_schema.pop("title", None)

        system_blocks = self._build_system_blocks(system, cache=True)
        user_content = self._build_user_blocks(cache_prefix, prompt)

        try:
            message = await self._stream_tool_call(
                tool_name, tool_schema, schema.__doc__ or f"Extract {tool_name}",
                system_blocks, user_content, max_tokens, stall_timeout,
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

        On validation failure, feeds the error back to the LLM as a
        correction prompt so it can fix its output.
        """
        total_in = 0
        total_out = 0
        cap = self._max_tokens_cap()
        current_max = min(max_tokens, cap)
        last_error: ValidationError | None = None
        missed_tool_call = False

        for attempt in range(3):
            effective_prompt = prompt
            if last_error is not None:
                correction = self._format_validation_correction(last_error)
                effective_prompt = f"{prompt}\n\n{correction}"
                logger.info("Retrying %s with validation feedback (attempt %d)",
                            schema.__name__, attempt + 1)
            elif missed_tool_call:
                effective_prompt = (
                    f"{prompt}\n\n{self._missed_tool_call_correction(schema.__name__)}"
                )
                logger.info("Retrying %s after response without a tool call (attempt %d)",
                            schema.__name__, attempt + 1)

            raw_input, stop_reason, in_tok, out_tok = await self._call_llm_cached(
                schema, effective_prompt, system, cache_prefix, current_max,
            )
            total_in += in_tok
            total_out += out_tok

            if stop_reason == "refusal":
                raise RuntimeError(f"Model refused to produce {schema.__name__}")

            if stop_reason == "max_tokens" and attempt < 2:
                new_max = min(current_max * 2, cap)
                if new_max > current_max:
                    current_max = new_max
                    continue

            if raw_input is None:
                # Under tool_choice "auto" the model can answer in text
                # without calling the tool.
                if attempt < 2:
                    missed_tool_call = True
                    last_error = None
                    continue
                raise RuntimeError(
                    f"No tool_use block found for {schema.__name__}"
                )
            missed_tool_call = False

            raw_input = self._coerce_fields(raw_input)

            try:
                parsed = schema.model_validate(raw_input)
                return parsed, total_in, total_out
            except ValidationError as e:
                last_error = e
                if attempt < 2:
                    new_max = min(current_max * 2, cap)
                    if new_max > current_max:
                        current_max = new_max
                    continue
                raise

        raise RuntimeError(f"Failed to get valid {schema.__name__} after 3 attempts")

    async def close(self) -> None:
        await self._client.close()
