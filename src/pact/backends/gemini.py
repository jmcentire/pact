"""Google Gemini backend — structured output via response_schema.

Uses the google-genai SDK with native Pydantic support.
"""

from __future__ import annotations

import logging
import os
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from pact.budget import BudgetExceeded, BudgetTracker

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class GeminiBackend:
    """Backend using the Google Gemini API with Pydantic structured output."""

    def __init__(
        self,
        budget: BudgetTracker,
        model: str = "gemini-2.5-flash",
        api_key: str | None = None,
    ) -> None:
        try:
            from google.genai import Client
        except ImportError as exc:
            raise ImportError(
                "The 'google-genai' package is required. Install with: pip install google-genai"
            ) from exc

        resolved_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not resolved_key:
            raise ValueError("GEMINI_API_KEY environment variable is required.")

        self._client = Client(api_key=resolved_key)
        self._model = model
        self._budget = budget

    def set_model(self, model: str) -> None:
        self._model = model

    async def assess(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        max_tokens: int = 32768,
    ) -> tuple[T, int, int]:
        """Call Gemini with Pydantic schema enforcement via response_schema."""
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            max_output_tokens=max_tokens,
        )

        for attempt in range(3):
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=config,
                )
            except Exception as e:
                if attempt < 2:
                    logger.warning("Gemini API error (attempt %d): %s", attempt + 1, e)
                    continue
                raise

            # Extract token counts
            usage = response.usage_metadata
            in_tok = usage.prompt_token_count if usage else 0
            out_tok = usage.candidates_token_count if usage else 0

            if not self._budget.record_tokens(in_tok, out_tok):
                raise BudgetExceeded(f"Budget exceeded after {in_tok}+{out_tok} tokens")

            # Try parsed response first (native Pydantic support)
            if response.parsed is not None:
                if isinstance(response.parsed, schema):
                    return response.parsed, in_tok, out_tok

            # Fall back to manual parsing from text
            text = response.text
            if not text:
                if attempt < 2:
                    continue
                raise RuntimeError(f"Empty response for {schema.__name__}")

            try:
                import json
                raw = json.loads(text)
                parsed = schema.model_validate(raw)
                return parsed, in_tok, out_tok
            except (ValueError, ValidationError) as e:
                if attempt < 2:
                    logger.warning("Parse error (attempt %d): %s", attempt + 1, e)
                    continue
                raise

        raise RuntimeError(f"Failed to get valid {schema.__name__} after 3 attempts")

    async def close(self) -> None:
        """Release resources. google-genai client doesn't require explicit close."""
        pass
