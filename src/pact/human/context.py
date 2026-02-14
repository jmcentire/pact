"""Integration context gatherer — collects external signals before agent phases.

Queries all configured integrations (Linear, Slack, GitHub) for comments,
reviews, and replies that are relevant to the current component/phase.
Returns structured context that can be injected into agent prompts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ExternalContext:
    """A single piece of external context from an integration."""
    source: str       # "linear", "slack", "github"
    kind: str         # "issue_comment", "pr_review", "thread_reply"
    content: str
    author: str = ""
    timestamp: str = ""
    reference: str = ""


@dataclass
class IntegrationContext:
    """Aggregated external context from all integrations."""
    items: list[ExternalContext] = field(default_factory=list)

    def format_for_prompt(self, max_chars: int = 4000) -> str:
        """Format context items for inclusion in an agent prompt."""
        if not self.items:
            return ""

        lines = ["## EXTERNAL CONTEXT", ""]
        total = len(lines[0]) + 1

        for item in self.items:
            author_str = f" ({item.author})" if item.author else ""
            ref_str = f" [{item.reference}]" if item.reference else ""
            line = f"- **[{item.source}/{item.kind}]**{author_str}{ref_str}: {item.content}"

            if total + len(line) + 1 > max_chars:
                lines.append("- *(truncated — more context available)*")
                break

            lines.append(line)
            total += len(line) + 1

        return "\n".join(lines)


async def gather_context(
    event_bus: object,
    component_id: str = "",
    phase: str = "",
) -> IntegrationContext:
    """Query all configured integrations and return aggregated context.

    Each query is wrapped in try/except so failures never block the pipeline.
    """
    ctx = IntegrationContext()

    # Linear: get comments on the component's issue
    try:
        linear = getattr(event_bus, "linear", None)
        issue_map = getattr(event_bus, "_linear_issue_map", {})
        if linear and linear.configured and component_id and component_id in issue_map:
            issue_id = issue_map[component_id]
            comments = await linear.get_issue_comments(issue_id)
            for c in comments:
                ctx.items.append(ExternalContext(
                    source="linear",
                    kind="issue_comment",
                    content=c.get("body", ""),
                    author=c.get("userName", ""),
                    timestamp=c.get("createdAt", ""),
                    reference=issue_id,
                ))
    except Exception as e:
        logger.debug("Failed to gather Linear context: %s", e)

    # Slack: get thread replies if tracking a thread
    try:
        slack = getattr(event_bus, "slack", None)
        slack_channel = getattr(event_bus, "_slack_channel", "")
        slack_thread_ts = getattr(event_bus, "_slack_thread_ts", "")
        if slack and slack.read_configured and slack_channel and slack_thread_ts:
            replies = await slack.get_thread_replies(
                slack_channel, slack_thread_ts,
            )
            for r in replies:
                ctx.items.append(ExternalContext(
                    source="slack",
                    kind="thread_reply",
                    content=r.get("text", ""),
                    author=r.get("user", ""),
                    timestamp=r.get("ts", ""),
                    reference=slack_thread_ts,
                ))
    except Exception as e:
        logger.debug("Failed to gather Slack context: %s", e)

    # GitHub: get PR comments/reviews if component has an open PR
    try:
        git = getattr(event_bus, "git", None)
        if git and git._repo_path is not None:
            prs = await git.get_open_prs(label="pact")
            for pr in prs:
                if component_id and component_id not in pr.get("headRefName", ""):
                    continue
                pr_num = pr.get("number", 0)
                if not pr_num:
                    continue

                comments = await git.get_pr_comments(pr_num)
                for c in comments:
                    ctx.items.append(ExternalContext(
                        source="github",
                        kind="pr_comment",
                        content=c.get("body", ""),
                        author=c.get("author", ""),
                        timestamp=c.get("createdAt", ""),
                        reference=f"PR #{pr_num}",
                    ))

                reviews = await git.get_pr_reviews(pr_num)
                for r in reviews:
                    if r.get("body"):
                        ctx.items.append(ExternalContext(
                            source="github",
                            kind="pr_review",
                            content=f"[{r.get('state', '')}] {r.get('body', '')}",
                            author=r.get("author", ""),
                            reference=f"PR #{pr_num}",
                        ))
    except Exception as e:
        logger.debug("Failed to gather GitHub context: %s", e)

    return ctx


async def check_for_human_response(
    event_bus: object,
    component_id: str = "",
) -> str | None:
    """Check integrations for human responses to pending questions.

    Returns the first response text found, or None.
    """
    # Check Linear comments
    try:
        linear = getattr(event_bus, "linear", None)
        issue_map = getattr(event_bus, "_linear_issue_map", {})
        if linear and linear.configured and component_id and component_id in issue_map:
            issue_id = issue_map[component_id]
            comments = await linear.get_issue_comments(issue_id)
            # Look for comments that aren't from the bot (no "[pact]" prefix)
            for c in comments:
                body = c.get("body", "")
                if body and not body.startswith("[pact]"):
                    return body
    except Exception as e:
        logger.debug("Failed to check Linear for human response: %s", e)

    # Check Slack thread replies
    try:
        slack = getattr(event_bus, "slack", None)
        slack_channel = getattr(event_bus, "_slack_channel", "")
        slack_thread_ts = getattr(event_bus, "_slack_thread_ts", "")
        if slack and slack.read_configured and slack_channel and slack_thread_ts:
            replies = await slack.get_thread_replies(
                slack_channel, slack_thread_ts,
            )
            if replies:
                return replies[-1].get("text", "")
    except Exception as e:
        logger.debug("Failed to check Slack for human response: %s", e)

    return None
