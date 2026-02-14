"""Tests for IntegrationContext gatherer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pact.human.context import (
    ExternalContext,
    IntegrationContext,
    check_for_human_response,
    gather_context,
)


class TestIntegrationContext:
    def test_format_for_prompt_empty(self):
        ctx = IntegrationContext()
        assert ctx.format_for_prompt() == ""

    def test_format_for_prompt_with_items(self):
        ctx = IntegrationContext(items=[
            ExternalContext(
                source="linear",
                kind="issue_comment",
                content="Looks good, proceed",
                author="Alice",
                reference="PROJ-1",
            ),
            ExternalContext(
                source="github",
                kind="pr_review",
                content="[APPROVED] Ship it",
                author="Bob",
                reference="PR #42",
            ),
        ])
        result = ctx.format_for_prompt()
        assert "## EXTERNAL CONTEXT" in result
        assert "linear/issue_comment" in result
        assert "Alice" in result
        assert "Looks good, proceed" in result
        assert "github/pr_review" in result
        assert "Bob" in result

    def test_format_for_prompt_truncation(self):
        ctx = IntegrationContext(items=[
            ExternalContext(
                source="linear",
                kind="issue_comment",
                content="A" * 500,
                author="User",
            )
            for _ in range(20)
        ])
        result = ctx.format_for_prompt(max_chars=200)
        assert "truncated" in result
        assert len(result) <= 300  # Some margin for the truncation message

    def test_format_for_prompt_no_truncation_when_fits(self):
        ctx = IntegrationContext(items=[
            ExternalContext(
                source="slack",
                kind="thread_reply",
                content="Short",
                author="U",
            ),
        ])
        result = ctx.format_for_prompt(max_chars=4000)
        assert "truncated" not in result


class TestGatherContext:
    @pytest.mark.asyncio
    async def test_gather_context_no_integrations(self):
        """EventBus with no configured integrations returns empty context."""
        bus = MagicMock()
        bus.linear = MagicMock()
        bus.linear.configured = False
        bus.slack = MagicMock()
        bus.slack.read_configured = False
        bus.git = MagicMock()
        bus.git._repo_path = None
        bus._linear_issue_map = {}
        bus._slack_channel = ""
        bus._slack_thread_ts = ""

        ctx = await gather_context(bus, component_id="comp_a")
        assert ctx.items == []

    @pytest.mark.asyncio
    async def test_gather_context_with_linear_comments(self):
        """Gather context with Linear comments on a tracked issue."""
        bus = MagicMock()
        bus.linear = AsyncMock()
        bus.linear.configured = True
        bus.linear.get_issue_comments = AsyncMock(return_value=[
            {"body": "Please add error handling", "createdAt": "2025-01-01", "userName": "PM"},
        ])
        bus._linear_issue_map = {"comp_a": "issue_123"}
        bus.slack = MagicMock()
        bus.slack.read_configured = False
        bus._slack_channel = ""
        bus._slack_thread_ts = ""
        bus.git = MagicMock()
        bus.git._repo_path = None

        ctx = await gather_context(bus, component_id="comp_a")
        assert len(ctx.items) == 1
        assert ctx.items[0].source == "linear"
        assert ctx.items[0].kind == "issue_comment"
        assert "error handling" in ctx.items[0].content
        assert ctx.items[0].author == "PM"

    @pytest.mark.asyncio
    async def test_gather_context_with_slack_replies(self):
        """Gather context with Slack thread replies."""
        bus = MagicMock()
        bus.linear = MagicMock()
        bus.linear.configured = False
        bus._linear_issue_map = {}
        bus.slack = AsyncMock()
        bus.slack.read_configured = True
        bus.slack.get_thread_replies = AsyncMock(return_value=[
            {"user": "U001", "text": "Use redis for caching", "ts": "123.456"},
        ])
        bus._slack_channel = "C123"
        bus._slack_thread_ts = "100.000"
        bus.git = MagicMock()
        bus.git._repo_path = None

        ctx = await gather_context(bus)
        assert len(ctx.items) == 1
        assert ctx.items[0].source == "slack"
        assert ctx.items[0].kind == "thread_reply"

    @pytest.mark.asyncio
    async def test_gather_context_exception_swallowed(self):
        """Exceptions from integrations don't propagate."""
        bus = MagicMock()
        bus.linear = AsyncMock()
        bus.linear.configured = True
        bus.linear.get_issue_comments = AsyncMock(side_effect=RuntimeError("boom"))
        bus._linear_issue_map = {"comp_a": "issue_123"}
        bus.slack = MagicMock()
        bus.slack.read_configured = False
        bus._slack_channel = ""
        bus._slack_thread_ts = ""
        bus.git = MagicMock()
        bus.git._repo_path = None

        ctx = await gather_context(bus, component_id="comp_a")
        assert ctx.items == []  # Exception swallowed


class TestCheckForHumanResponse:
    @pytest.mark.asyncio
    async def test_check_for_human_response_none(self):
        """No response when nothing is configured."""
        bus = MagicMock()
        bus.linear = MagicMock()
        bus.linear.configured = False
        bus._linear_issue_map = {}
        bus.slack = MagicMock()
        bus.slack.read_configured = False
        bus._slack_channel = ""
        bus._slack_thread_ts = ""

        result = await check_for_human_response(bus, component_id="comp_a")
        assert result is None

    @pytest.mark.asyncio
    async def test_check_for_human_response_found_linear(self):
        """Human response found in Linear comments."""
        bus = MagicMock()
        bus.linear = AsyncMock()
        bus.linear.configured = True
        bus.linear.get_issue_comments = AsyncMock(return_value=[
            {"body": "Yes, proceed with option B", "createdAt": "2025-01-01", "userName": "PM"},
        ])
        bus._linear_issue_map = {"comp_a": "issue_123"}
        bus.slack = MagicMock()
        bus.slack.read_configured = False
        bus._slack_channel = ""
        bus._slack_thread_ts = ""

        result = await check_for_human_response(bus, component_id="comp_a")
        assert result == "Yes, proceed with option B"

    @pytest.mark.asyncio
    async def test_check_for_human_response_skips_pact_comments(self):
        """Bot comments starting with [pact] are skipped."""
        bus = MagicMock()
        bus.linear = AsyncMock()
        bus.linear.configured = True
        bus.linear.get_issue_comments = AsyncMock(return_value=[
            {"body": "[pact] Component complete", "createdAt": "2025-01-01", "userName": "bot"},
        ])
        bus._linear_issue_map = {"comp_a": "issue_123"}
        bus.slack = MagicMock()
        bus.slack.read_configured = False
        bus._slack_channel = ""
        bus._slack_thread_ts = ""

        result = await check_for_human_response(bus, component_id="comp_a")
        assert result is None

    @pytest.mark.asyncio
    async def test_check_for_human_response_found_slack(self):
        """Human response found in Slack thread."""
        bus = MagicMock()
        bus.linear = MagicMock()
        bus.linear.configured = False
        bus._linear_issue_map = {}
        bus.slack = AsyncMock()
        bus.slack.read_configured = True
        bus.slack.get_thread_replies = AsyncMock(return_value=[
            {"user": "U001", "text": "Approved!", "ts": "123.456"},
        ])
        bus._slack_channel = "C123"
        bus._slack_thread_ts = "100.000"

        result = await check_for_human_response(bus)
        assert result == "Approved!"
