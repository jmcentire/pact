"""Backend protocol + factory — decouples agents from LLM provider.

Backend is a Protocol: any class implementing assess/set_model/close
can be used as an LLM backend. Factory creates backends by name.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Backend(Protocol):
    """Protocol for LLM backends — structured extraction + model switching."""

    async def assess(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        max_tokens: int = 32768,
    ) -> tuple[T, int, int]:
        """Call LLM with schema enforcement. Returns (result, input_tokens, output_tokens)."""
        ...

    def set_model(self, model: str) -> None:
        """Switch the active model."""
        ...

    async def close(self) -> None:
        """Release resources."""
        ...


def create_backend(
    name: str, budget: object, model: str, repo_path: object = None,
) -> Backend:
    """Factory: create a Backend by name."""
    if name == "anthropic":
        from pact.backends.anthropic import AnthropicBackend
        return AnthropicBackend(budget=budget, model=model)
    elif name == "claude_code":
        from pact.backends.claude_code import ClaudeCodeBackend
        return ClaudeCodeBackend(budget=budget, model=model, repo_path=repo_path)
    elif name == "claude_code_team":
        # Team backend is not a standard Backend (no assess method).
        # It's used directly by the scheduler for parallel tmux sessions.
        # Return a claude_code backend as the fallback for structured calls.
        from pact.backends.claude_code import ClaudeCodeBackend
        return ClaudeCodeBackend(budget=budget, model=model, repo_path=repo_path)
    else:
        raise ValueError(
            f"Unknown backend: {name}. "
            f"Available: anthropic, claude_code, claude_code_team"
        )
