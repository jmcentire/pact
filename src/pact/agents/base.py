"""LLM service wrapper — delegates to a Backend for actual LLM calls.

Reuses the Backend protocol from swarm. Thin wrapper adding budget tracking.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from pact.budget import BudgetTracker

T = TypeVar("T", bound=BaseModel)


class AgentBase:
    """Base class for all LLM agents. Wraps structured extraction + budget."""

    def __init__(
        self,
        budget: BudgetTracker,
        model: str = "claude-opus-4-6",
        backend: str = "anthropic",
    ) -> None:
        from pact.backends import create_backend
        self._backend = create_backend(backend, budget, model)
        self._budget = budget
        self._model = model

    def set_model(self, model: str) -> None:
        self._model = model
        self._backend.set_model(model)

    def set_backend(self, backend_name: str, repo_path: object = None) -> None:
        from pact.backends import create_backend
        self._backend = create_backend(
            backend_name, self._budget, self._model, repo_path=repo_path,
        )

    async def assess(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        max_tokens: int = 32768,
    ) -> tuple[T, int, int]:
        """Call LLM with schema enforcement. Returns (result, input_tokens, output_tokens)."""
        return await self._backend.assess(schema, prompt, system, max_tokens)

    async def assess_cached(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        cache_prefix: str = "",
        max_tokens: int = 32768,
    ) -> tuple[T, int, int]:
        """Call LLM with optional prompt caching.

        If the backend supports assess_with_cache and cache_prefix is non-empty,
        uses the cached path. Otherwise falls back to regular assess().
        """
        if cache_prefix and hasattr(self._backend, 'assess_with_cache'):
            return await self._backend.assess_with_cache(
                schema, prompt, system,
                cache_prefix=cache_prefix,
                max_tokens=max_tokens,
            )
        # Backend doesn't support caching — prepend cache_prefix to prompt
        # so the context is not lost (critical for handoff briefs)
        if cache_prefix:
            prompt = f"{cache_prefix}\n\n{prompt}"
        return await self._backend.assess(schema, prompt, system, max_tokens)

    def with_learnings(self, learnings: list[dict]) -> str:
        """Format learnings as context string for prompts."""
        if not learnings:
            return ""
        lines = ["Learnings from previous runs:"]
        for entry in learnings[-10:]:
            lines.append(f"  - [{entry.get('category', '')}] {entry.get('lesson', '')}")
        return "\n".join(lines)

    async def close(self) -> None:
        await self._backend.close()
