"""Git integration — branch and PR management.

Each component implementation becomes a branch (cf/<component_id>).
Integration becomes a PR composing children.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class GitManager:
    """Manages git branches and PRs for pact runs."""

    def __init__(self, repo_path: Path | None = None) -> None:
        self._repo_path = repo_path

    async def _run(self, *args: str) -> tuple[str, str, int]:
        """Run a git command and return (stdout, stderr, returncode)."""
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._repo_path) if self._repo_path else None,
        )
        stdout, stderr = await proc.communicate()
        return stdout.decode(), stderr.decode(), proc.returncode

    async def create_branch(self, branch_name: str) -> bool:
        """Create and checkout a new branch."""
        _, _, rc = await self._run("checkout", "-b", branch_name)
        return rc == 0

    async def checkout(self, branch_name: str) -> bool:
        """Checkout an existing branch."""
        _, _, rc = await self._run("checkout", branch_name)
        return rc == 0

    async def commit(self, message: str, files: list[str] | None = None) -> bool:
        """Stage files and commit."""
        if files:
            for f in files:
                await self._run("add", f)
        else:
            await self._run("add", "-A")

        _, _, rc = await self._run("commit", "-m", message)
        return rc == 0

    async def create_component_branch(self, component_id: str) -> str:
        """Create a branch for a component implementation."""
        branch = f"cf/{component_id}"
        success = await self.create_branch(branch)
        if success:
            logger.info("Created branch: %s", branch)
        return branch

    async def create_pr(
        self,
        title: str,
        body: str,
        base: str = "main",
        head: str = "",
    ) -> str:
        """Create a PR using gh CLI. Returns PR URL."""
        cmd = ["gh", "pr", "create", "--title", title, "--body", body, "--base", base]
        if head:
            cmd.extend(["--head", head])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._repo_path) if self._repo_path else None,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.error("PR creation failed: %s", stderr.decode())
            return ""

        return stdout.decode().strip()

    async def current_branch(self) -> str:
        """Get the current branch name."""
        stdout, _, _ = await self._run("branch", "--show-current")
        return stdout.strip()

    async def _run_gh(self, *args: str) -> tuple[str, str, int]:
        """Run a gh CLI command and return (stdout, stderr, returncode)."""
        proc = await asyncio.create_subprocess_exec(
            "gh", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._repo_path) if self._repo_path else None,
        )
        stdout, stderr = await proc.communicate()
        return stdout.decode(), stderr.decode(), proc.returncode

    async def get_pr_comments(self, pr_number: int) -> list[dict]:
        """Get comments on a PR. Returns [{author, body, createdAt}]."""
        if self._repo_path is None:
            return []

        stdout, _, rc = await self._run_gh(
            "pr", "view", str(pr_number), "--json", "comments",
        )
        if rc != 0:
            return []

        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return []

        return [
            {
                "author": (c.get("author") or {}).get("login", ""),
                "body": c.get("body", ""),
                "createdAt": c.get("createdAt", ""),
            }
            for c in data.get("comments", [])
        ]

    async def get_pr_reviews(self, pr_number: int) -> list[dict]:
        """Get reviews on a PR. Returns [{author, state, body}]."""
        if self._repo_path is None:
            return []

        stdout, _, rc = await self._run_gh(
            "pr", "view", str(pr_number), "--json", "reviews",
        )
        if rc != 0:
            return []

        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return []

        return [
            {
                "author": (r.get("author") or {}).get("login", ""),
                "state": r.get("state", ""),
                "body": r.get("body", ""),
            }
            for r in data.get("reviews", [])
        ]

    async def get_pr_status(self, pr_number: int) -> dict:
        """Get PR status. Returns {state, mergeable, reviewDecision}."""
        if self._repo_path is None:
            return {}

        stdout, _, rc = await self._run_gh(
            "pr", "view", str(pr_number),
            "--json", "state,mergeable,reviewDecision",
        )
        if rc != 0:
            return {}

        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return {}

        return {
            "state": data.get("state", ""),
            "mergeable": data.get("mergeable", ""),
            "reviewDecision": data.get("reviewDecision", ""),
        }

    async def add_pr_comment(self, pr_number: int, body: str) -> bool:
        """Add a comment to a PR. Returns success."""
        if self._repo_path is None:
            return False

        _, _, rc = await self._run_gh(
            "pr", "comment", str(pr_number), "--body", body,
        )
        return rc == 0

    async def get_open_prs(self, label: str = "pact") -> list[dict]:
        """Get open PRs with a label. Returns [{number, title, url, headRefName}]."""
        if self._repo_path is None:
            return []

        args = ["pr", "list", "--json", "number,title,url,headRefName", "--state", "open"]
        if label:
            args.extend(["--label", label])

        stdout, _, rc = await self._run_gh(*args)
        if rc != 0:
            return []

        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return []

        return [
            {
                "number": pr.get("number", 0),
                "title": pr.get("title", ""),
                "url": pr.get("url", ""),
                "headRefName": pr.get("headRefName", ""),
            }
            for pr in data
        ]
