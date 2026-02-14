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

    async def close(self) -> None:
        await self._backend.close()
