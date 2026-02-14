"""Claude Code CLI backend — uses `claude` with tool access.

Reused from swarm with import path adaptation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from pact.budget import BudgetExceeded, BudgetTracker

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


class ClaudeCodeBackend:
    """Backend using the claude CLI with optional tool access."""

    def __init__(
        self,
        budget: BudgetTracker,
        model: str = "claude-opus-4-6",
        repo_path: Path | None = None,
        timeout: int = 300,
        max_retries: int = 2,
    ) -> None:
        self._model = model
        self._budget = budget
        self._repo_path = Path(repo_path) if repo_path else None
        self._timeout = timeout  # seconds per CLI invocation
        self._max_retries = max_retries

    def set_model(self, model: str) -> None:
        self._model = model

    def set_repo_path(self, path: Path) -> None:
        self._repo_path = path

    async def assess(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        max_tokens: int = 32768,
    ) -> tuple[T, int, int]:
        last_err: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return await self._assess_once(schema, prompt, system, max_tokens)
            except RuntimeError as exc:
                last_err = exc
                if "timed out" in str(exc) and attempt < self._max_retries - 1:
                    logger.warning(
                        "Attempt %d/%d timed out, retrying...",
                        attempt + 1, self._max_retries,
                    )
                    continue
                raise
        raise last_err  # type: ignore[misc]

    async def _assess_once(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        max_tokens: int = 32768,
    ) -> tuple[T, int, int]:
        schema_json = json.dumps(schema.model_json_schema(), indent=2)

        full_prompt = (
            f"{system}\n\n"
            f"You MUST respond with a JSON object matching this schema:\n"
            f"```json\n{schema_json}\n```\n\n"
        )

        if self._repo_path and self._repo_path.exists():
            full_prompt += (
                f"You have access to the codebase at: {self._repo_path}\n"
                f"Use your tools to explore before responding.\n\n"
            )

        full_prompt += (
            f"Task:\n{prompt}\n\n"
            f"Respond ONLY with the JSON object matching the schema above."
        )

        cmd = ["claude", "-p", full_prompt, "--output-format", "json"]
        cmd.extend(["--model", self._model])

        if self._repo_path and self._repo_path.exists():
            cmd.extend(["--allowedTools", "Read,Glob,Grep,Bash"])

        # Remove CLAUDECODE env var to allow spawning from within Claude Code
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._repo_path) if self._repo_path else None,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "claude CLI timed out after %ds (pid=%s), killing",
                self._timeout, proc.pid,
            )
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"claude CLI timed out after {self._timeout}s"
            )

        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI failed (exit {proc.returncode}): {stderr.decode()[:500]}"
            )

        raw = stdout.decode()

        try:
            cli_response = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse claude CLI output: {exc}") from exc

        result_text = cli_response.get("result", raw)
        if isinstance(result_text, str):
            data = self._extract_json(result_text)
        elif isinstance(result_text, dict):
            data = result_text
        else:
            raise RuntimeError(f"Unexpected result type: {type(result_text)}")

        in_tok = cli_response.get("input_tokens", len(full_prompt) // 4)
        out_tok = cli_response.get("output_tokens", len(raw) // 4)

        if not self._budget.record_tokens(in_tok, out_tok):
            raise BudgetExceeded(f"Budget exceeded after {in_tok}+{out_tok} tokens")

        return schema.model_validate(data), in_tok, out_tok

    @staticmethod
    def _extract_json(text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        json_match = re.search(r'```(?:json)?\s*\n({.*?})\s*\n```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        brace_start = text.find('{')
        if brace_start >= 0:
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[brace_start:i + 1])
                        except json.JSONDecodeError:
                            break

        raise RuntimeError(f"No valid JSON found: {text[:200]}")

    async def close(self) -> None:
        pass
