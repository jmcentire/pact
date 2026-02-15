"""Event bus — fire-and-forget notifications to integrations.

Each integration checks .configured and silently skips if not set up.
Integrations never block the pipeline.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from pact.config import GlobalConfig, ProjectConfig
from pact.schemas import TestResults

logger = logging.getLogger(__name__)


@dataclass
class PactEvent:
    """An event emitted during the pipeline."""
    kind: str  # "phase_start", "phase_complete", "component_complete", etc.
    project_name: str
    detail: str = ""
    component_id: str = ""
    test_results: TestResults | None = None


def _is_git_repo(path: Path) -> bool:
    """Check if path is inside a git repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


class EventBus:
    """Fire-and-forget notifications. Each integration checks .configured."""

    def __init__(
        self,
        project_dir: Path,
        global_config: GlobalConfig,
        project_config: ProjectConfig,
    ) -> None:
        from pact.human.slack import SlackNotifier
        from pact.human.linear import LinearClient
        from pact.human.git import GitManager

        # Resolve integration config: project > global > env var
        slack_webhook = project_config.slack_webhook or global_config.slack_webhook
        linear_api_key = project_config.linear_api_key or global_config.linear_api_key
        self._linear_team_id = project_config.linear_team_id or global_config.linear_team_id
        self._git_auto_commit = (
            project_config.git_auto_commit
            if project_config.git_auto_commit is not None
            else global_config.git_auto_commit
        )
        self._git_auto_branch = (
            project_config.git_auto_branch
            if project_config.git_auto_branch is not None
            else global_config.git_auto_branch
        )

        # Bidirectional config
        bot_token = project_config.slack_bot_token or global_config.slack_bot_token
        self._slack_channel = project_config.slack_channel or global_config.slack_channel
        self._slack_thread_ts = ""  # Set when we create a trackable thread

        self.slack = SlackNotifier(
            webhook_url=slack_webhook,
            bot_token=bot_token,
            channel=self._slack_channel,
        )
        self.linear = LinearClient(api_key=linear_api_key)
        self.git = GitManager(project_dir if _is_git_repo(project_dir) else None)
        self._project_dir = project_dir
        self._project_name = project_dir.name

        # Issue map for Linear: component_id -> linear_issue_id
        self._linear_issue_map: dict[str, str] = {}

    async def emit(self, event: PactEvent) -> None:
        """Dispatch to all configured integrations. Never raises."""
        handlers = {
            "phase_start": self._on_phase_start,
            "phase_complete": self._on_phase_complete,
            "component_complete": self._on_component_complete,
            "component_failed": self._on_component_failed,
            "human_needed": self._on_human_needed,
            "run_complete": self._on_run_complete,
            "budget_warning": self._on_budget_warning,
            "incident_detected": self._on_incident_detected,
            "incident_remediating": self._on_incident_remediating,
            "incident_resolved": self._on_incident_resolved,
            "incident_escalated": self._on_incident_escalated,
        }
        handler = handlers.get(event.kind)
        if handler:
            try:
                await handler(event)
            except Exception as e:
                logger.debug("EventBus handler error for %s: %s", event.kind, e)

    async def _on_phase_start(self, event: PactEvent) -> None:
        if self.slack.configured:
            await self.slack.notify(
                f":gear: *{event.project_name}* starting phase: {event.detail}"
            )

    async def _on_phase_complete(self, event: PactEvent) -> None:
        if self.slack.configured:
            if event.detail == "decompose":
                # Extract component count from event
                await self.slack.notify_decomposition_complete(
                    event.project_name,
                    int(event.component_id) if event.component_id.isdigit() else 0,
                )
            else:
                await self.slack.notify(
                    f":white_check_mark: *{event.project_name}* phase complete: {event.detail}"
                )

        # Linear: sync tree after decomposition
        if self.linear.configured and event.detail == "decompose" and self._linear_team_id:
            from pact.human.linear import sync_tree_to_linear
            tree_path = self._project_dir / ".pact" / "decomposition" / "tree.json"
            if tree_path.exists():
                self._linear_issue_map = await sync_tree_to_linear(
                    self.linear, tree_path, self._linear_team_id,
                )

        # Git: commit decomposition artifacts
        if self._git_auto_commit and self.git._repo_path:
            if event.detail == "decompose":
                await self.git.commit(
                    f"pact: decomposition complete for {event.project_name}"
                )

    async def _on_component_complete(self, event: PactEvent) -> None:
        test_str = ""
        if event.test_results:
            test_str = f"{event.test_results.passed}/{event.test_results.total}"

        if self.slack.configured:
            await self.slack.notify_component_complete(
                event.project_name, event.component_id, test_str,
            )

        # Linear: update issue to Done + post structured comment
        if self.linear.configured and event.component_id in self._linear_issue_map:
            issue_id = self._linear_issue_map[event.component_id]
            await self.linear.update_issue_status(issue_id, "Done")

            # Post structured comment with test results
            if event.test_results:
                tr = event.test_results
                comment = (
                    f"[pact] **Component complete** :white_check_mark:\n\n"
                    f"| Metric | Value |\n|---|---|\n"
                    f"| Tests passed | {tr.passed}/{tr.total} |\n"
                    f"| Tests failed | {tr.failed} |\n"
                    f"| Errors | {tr.errors} |\n"
                )
                await self.linear.add_comment(issue_id, comment)

        # Git: commit implementation
        if self._git_auto_commit and self.git._repo_path:
            await self.git.commit(
                f"pact: component {event.component_id} complete ({test_str})"
            )

    async def _on_component_failed(self, event: PactEvent) -> None:
        if self.slack.configured:
            await self.slack.notify_component_failed(
                event.project_name, event.component_id, event.detail,
            )

        # Linear: add failure comment + keep in progress
        if self.linear.configured and event.component_id in self._linear_issue_map:
            issue_id = self._linear_issue_map[event.component_id]
            await self.linear.update_issue_status(issue_id, "In Progress")

            # Post failure details
            failure_lines = [f"[pact] **Component failed** :x:\n\n{event.detail}\n"]
            if event.test_results and event.test_results.failure_details:
                failure_lines.append("**Failures:**")
                for fd in event.test_results.failure_details[:5]:
                    failure_lines.append(f"- `{fd.test_id}`: {fd.error_message[:200]}")
            await self.linear.add_comment(issue_id, "\n".join(failure_lines))

        # Git: post PR comment if component has an open PR
        if self.git._repo_path is not None:
            try:
                prs = await self.git.get_open_prs(label="pact")
                for pr in prs:
                    if event.component_id in pr.get("headRefName", ""):
                        await self.git.add_pr_comment(
                            pr["number"],
                            f"**pact:** Component `{event.component_id}` failed — {event.detail}",
                        )
                        break
            except Exception:
                pass

    async def _on_human_needed(self, event: PactEvent) -> None:
        # If bot token is configured, post a trackable thread message
        if self.slack.read_configured and self._slack_channel:
            result = await self.slack.post_message(
                self._slack_channel,
                f":raising_hand: *{event.project_name}* needs human intervention: {event.detail}",
            )
            if result.get("ok"):
                self._slack_thread_ts = result.get("ts", "")
        elif self.slack.configured:
            # Fall back to webhook
            await self.slack.notify_human_needed(
                event.project_name, event.detail,
            )

    async def _on_run_complete(self, event: PactEvent) -> None:
        if self.slack.configured:
            await self.slack.notify(
                f":checkered_flag: *{event.project_name}* pipeline complete: {event.detail}"
            )

        # Git: create PR with design doc
        if self._git_auto_commit and self.git._repo_path:
            design_path = self._project_dir / "design.md"
            if design_path.exists():
                await self.git.commit(
                    f"pact: run complete for {event.project_name}"
                )

    async def _on_budget_warning(self, event: PactEvent) -> None:
        if self.slack.configured:
            await self.slack.notify(
                f":warning: *{event.project_name}* budget warning: {event.detail}"
            )

    # ── Monitoring Event Handlers ────────────────────────────────────

    async def _on_incident_detected(self, event: PactEvent) -> None:
        if self.slack.configured:
            await self.slack.notify(
                f":rotating_light: *{event.project_name}* incident detected: {event.detail}"
            )
        if self.linear.configured and self._linear_team_id:
            try:
                await self.linear.create_issue(
                    self._linear_team_id,
                    f"[pact] Incident: {event.detail[:80]}",
                    body=(
                        f"**Incident detected by Pact monitoring**\n\n"
                        f"Component: {event.component_id or 'unknown'}\n"
                        f"Detail: {event.detail}"
                    ),
                )
            except Exception:
                pass

    async def _on_incident_remediating(self, event: PactEvent) -> None:
        if self.slack.configured:
            await self.slack.notify(
                f":wrench: *{event.project_name}* auto-remediating: {event.detail}"
            )

    async def _on_incident_resolved(self, event: PactEvent) -> None:
        if self.slack.configured:
            await self.slack.notify(
                f":white_check_mark: *{event.project_name}* incident resolved: {event.detail}"
            )

    async def _on_incident_escalated(self, event: PactEvent) -> None:
        if self.slack.configured:
            await self.slack.notify(
                f":sos: *{event.project_name}* incident escalated: {event.detail}"
            )
        if self.linear.configured and self._linear_team_id:
            try:
                await self.linear.create_issue(
                    self._linear_team_id,
                    f"[pact] ESCALATION: {event.detail[:80]}",
                    body=(
                        f"**Pact could not auto-fix this incident**\n\n"
                        f"Component: {event.component_id or 'unknown'}\n"
                        f"Detail: {event.detail}\n\n"
                        f"See diagnostic report in .pact/monitoring/reports/"
                    ),
                )
            except Exception:
                pass
