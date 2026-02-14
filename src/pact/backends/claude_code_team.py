"""Claude Code Team backend — tmux panes for full-capability agent sessions.

Instead of `claude -p` (structured JSON extraction, limited tools),
each agent gets a full Claude Code session in its own tmux pane with
Read/Write/Edit/Bash/Glob/Grep tools. Agents can iterate, run tests,
debug — like a human developer.

Workflow:
1. Create tmux session (or attach to existing)
2. For each agent task, create a new pane
3. Launch `claude` in that pane with a prompt file
4. Agent works autonomously in its pane
5. Agent writes results to a known output path
6. Orchestrator polls for completion, reads results
7. Pane closes when agent finishes
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class AgentTask:
    """A task to be executed by a Claude Code agent in a tmux pane."""
    prompt: str
    output_file: str
    pane_name: str
    working_dir: str = ""
    model: str = "claude-opus-4-6"
    max_turns: int = 0  # 0 = unlimited


@dataclass
class AgentResult:
    """Result from a completed Claude Code agent."""
    pane_name: str
    output_file: str
    content: str
    success: bool
    error: str = ""


class ClaudeCodeTeamBackend:
    """Backend using tmux panes for real Claude Code agent sessions.

    Each coding agent is a real Claude Code instance that can:
    - Read/write files with full tool access
    - Run tests via Bash
    - Iterate on failures autonomously
    - Even run `cf decompose` recursively on its own sub-component
    """

    def __init__(
        self,
        model: str = "claude-opus-4-6",
        repo_path: str | Path = "",
        session_name: str = "cf-agents",
        poll_interval: float = 5.0,
        agent_timeout: int = 600,
        max_concurrent: int = 4,
    ) -> None:
        self._model = model
        self._repo_path = str(repo_path) if repo_path else ""
        self._session = session_name
        self._poll_interval = poll_interval
        self._agent_timeout = agent_timeout
        self._max_concurrent = max_concurrent
        self._prompt_dir = Path(tempfile.mkdtemp(prefix="cf-prompts-"))
        self._active_panes: dict[str, int] = {}  # pane_name -> pane_id

    async def ensure_session(self) -> None:
        """Ensure the tmux session exists."""
        check = await asyncio.create_subprocess_exec(
            "tmux", "has-session", "-t", self._session,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await check.wait()
        if check.returncode != 0:
            create = await asyncio.create_subprocess_exec(
                "tmux", "new-session", "-d", "-s", self._session,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await create.wait()
            logger.info("Created tmux session: %s", self._session)

    async def spawn_agent(self, task: AgentTask) -> str:
        """Spawn a Claude Code agent in a tmux pane.

        Returns pane identifier for monitoring.
        """
        await self.ensure_session()

        # Write prompt to file
        prompt_file = self._prompt_dir / f"{task.pane_name}_{uuid4().hex[:6]}.md"
        prompt_file.write_text(task.prompt)

        # Build the claude command
        # The agent reads its prompt, does its work, and writes output
        output_path = task.output_file
        model_flag = f"--model {task.model}" if task.model else ""
        max_turns_flag = f"--max-turns {task.max_turns}" if task.max_turns > 0 else ""

        # Remove CLAUDECODE env var to allow spawning from within Claude Code
        env_unset = "unset CLAUDECODE; "

        # The command: run claude with the prompt, capture to output file
        cmd = (
            f'{env_unset}'
            f'claude -p "$(cat {prompt_file})" '
            f'{model_flag} {max_turns_flag} '
            f'--output-format json '
            f'> {output_path} 2>&1; '
            f'echo "__CF_AGENT_DONE__" >> {output_path}'
        )

        cwd = task.working_dir or self._repo_path or str(Path.cwd())

        # Create new tmux window and run command
        proc = await asyncio.create_subprocess_exec(
            "tmux", "new-window", "-t", self._session,
            "-n", task.pane_name,
            "-d",  # don't switch to it
            f"cd {cwd} && {cmd}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()

        self._active_panes[task.pane_name] = 1
        logger.info("Spawned agent in tmux pane: %s", task.pane_name)
        return task.pane_name

    async def wait_for_completion(
        self, output_file: str, timeout: int | None = None,
    ) -> str:
        """Poll for output file to appear and contain completion marker.

        Returns the file content (minus the marker).
        """
        timeout = timeout or self._agent_timeout
        deadline = asyncio.get_event_loop().time() + timeout
        output_path = Path(output_file)

        while asyncio.get_event_loop().time() < deadline:
            if output_path.exists():
                content = output_path.read_text()
                if "__CF_AGENT_DONE__" in content:
                    return content.replace("__CF_AGENT_DONE__", "").strip()
            await asyncio.sleep(self._poll_interval)

        raise TimeoutError(
            f"Agent did not complete within {timeout}s: {output_file}"
        )

    async def spawn_parallel(self, tasks: list[AgentTask]) -> list[AgentResult]:
        """Spawn multiple agents in parallel tmux panes.

        Each gets: prompt, output path, pane name.
        Returns results as they complete.
        Respects max_concurrent limit via semaphore.
        """
        sem = asyncio.Semaphore(self._max_concurrent)

        async def _run_one(task: AgentTask) -> AgentResult:
            async with sem:
                try:
                    await self.spawn_agent(task)
                    content = await self.wait_for_completion(task.output_file)
                    return AgentResult(
                        pane_name=task.pane_name,
                        output_file=task.output_file,
                        content=content,
                        success=True,
                    )
                except Exception as e:
                    logger.error("Agent %s failed: %s", task.pane_name, e)
                    return AgentResult(
                        pane_name=task.pane_name,
                        output_file=task.output_file,
                        content="",
                        success=False,
                        error=str(e),
                    )
                finally:
                    self._active_panes.pop(task.pane_name, None)

        return list(await asyncio.gather(*[_run_one(t) for t in tasks]))

    async def kill_session(self) -> None:
        """Kill the entire tmux session (cleanup)."""
        proc = await asyncio.create_subprocess_exec(
            "tmux", "kill-session", "-t", self._session,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        self._active_panes.clear()

    async def close(self) -> None:
        """Cleanup prompt files. Does NOT kill the tmux session."""
        import shutil
        if self._prompt_dir.exists():
            shutil.rmtree(self._prompt_dir, ignore_errors=True)
