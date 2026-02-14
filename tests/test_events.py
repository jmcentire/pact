"""Tests for EventBus dispatch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pact.config import GlobalConfig, ProjectConfig
from pact.events import EventBus, PactEvent, _is_git_repo
from pact.schemas import TestResults


class TestPactEvent:
    def test_basic_event(self):
        event = PactEvent(kind="phase_start", project_name="test-project")
        assert event.kind == "phase_start"
        assert event.project_name == "test-project"
        assert event.detail == ""
        assert event.component_id == ""
        assert event.test_results is None

    def test_event_with_test_results(self):
        tr = TestResults(total=5, passed=3, failed=2)
        event = PactEvent(
            kind="component_failed",
            project_name="test",
            component_id="comp_a",
            test_results=tr,
        )
        assert event.test_results.passed == 3
        assert event.test_results.failed == 2


class TestIsGitRepo:
    def test_non_git_dir(self, tmp_path: Path):
        assert _is_git_repo(tmp_path) is False

    def test_nonexistent_dir(self, tmp_path: Path):
        assert _is_git_repo(tmp_path / "nonexistent") is False


class TestEventBus:
    @patch("pact.events._is_git_repo", return_value=False)
    def test_init_no_integrations(self, mock_git, tmp_path: Path):
        gc = GlobalConfig()
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        assert not bus.slack.configured
        assert not bus.linear.configured
        assert bus.git._repo_path is None

    @patch("pact.events._is_git_repo", return_value=False)
    def test_init_with_slack_webhook(self, mock_git, tmp_path: Path):
        gc = GlobalConfig(slack_webhook="https://hooks.slack.com/test")
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        assert bus.slack.configured

    @patch("pact.events._is_git_repo", return_value=False)
    def test_init_project_config_overrides_global(self, mock_git, tmp_path: Path):
        gc = GlobalConfig(slack_webhook="https://global.webhook")
        pc = ProjectConfig(slack_webhook="https://project.webhook")
        bus = EventBus(tmp_path, gc, pc)
        assert bus.slack._webhook_url == "https://project.webhook"

    @patch("pact.events._is_git_repo", return_value=False)
    def test_init_with_linear_key(self, mock_git, tmp_path: Path):
        gc = GlobalConfig(linear_api_key="lin_test_key")
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        assert bus.linear.configured

    @pytest.mark.asyncio
    @patch("pact.events._is_git_repo", return_value=False)
    async def test_emit_unknown_event_is_noop(self, mock_git, tmp_path: Path):
        gc = GlobalConfig()
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        # Should not raise
        await bus.emit(PactEvent(kind="unknown_event", project_name="test"))

    @pytest.mark.asyncio
    @patch("pact.events._is_git_repo", return_value=False)
    async def test_emit_phase_start_no_slack(self, mock_git, tmp_path: Path):
        gc = GlobalConfig()
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        bus.slack.notify = AsyncMock(return_value=False)
        await bus.emit(PactEvent(kind="phase_start", project_name="test", detail="implement"))
        # Not configured, so notify should not be called
        bus.slack.notify.assert_not_called()

    @pytest.mark.asyncio
    @patch("pact.events._is_git_repo", return_value=False)
    async def test_emit_phase_start_with_slack(self, mock_git, tmp_path: Path):
        gc = GlobalConfig(slack_webhook="https://hooks.slack.com/test")
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        bus.slack.notify = AsyncMock(return_value=True)
        await bus.emit(PactEvent(kind="phase_start", project_name="test", detail="implement"))
        bus.slack.notify.assert_called_once()

    @pytest.mark.asyncio
    @patch("pact.events._is_git_repo", return_value=False)
    async def test_emit_component_complete_with_slack(self, mock_git, tmp_path: Path):
        gc = GlobalConfig(slack_webhook="https://hooks.slack.com/test")
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        bus.slack.notify_component_complete = AsyncMock(return_value=True)
        tr = TestResults(total=5, passed=5)
        await bus.emit(PactEvent(
            kind="component_complete",
            project_name="test",
            component_id="comp_a",
            test_results=tr,
        ))
        bus.slack.notify_component_complete.assert_called_once_with(
            "test", "comp_a", "5/5",
        )

    @pytest.mark.asyncio
    @patch("pact.events._is_git_repo", return_value=False)
    async def test_emit_component_failed_with_slack(self, mock_git, tmp_path: Path):
        gc = GlobalConfig(slack_webhook="https://hooks.slack.com/test")
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        bus.slack.notify_component_failed = AsyncMock(return_value=True)
        await bus.emit(PactEvent(
            kind="component_failed",
            project_name="test",
            component_id="comp_a",
            detail="3/5 tests failed",
        ))
        bus.slack.notify_component_failed.assert_called_once_with(
            "test", "comp_a", "3/5 tests failed",
        )

    @pytest.mark.asyncio
    @patch("pact.events._is_git_repo", return_value=False)
    async def test_emit_human_needed_with_slack(self, mock_git, tmp_path: Path):
        gc = GlobalConfig(slack_webhook="https://hooks.slack.com/test")
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        bus.slack.notify_human_needed = AsyncMock(return_value=True)
        await bus.emit(PactEvent(
            kind="human_needed",
            project_name="test",
            detail="Interview questions pending",
        ))
        bus.slack.notify_human_needed.assert_called_once_with(
            "test", "Interview questions pending",
        )

    @pytest.mark.asyncio
    @patch("pact.events._is_git_repo", return_value=False)
    async def test_emit_handler_exception_is_swallowed(self, mock_git, tmp_path: Path):
        gc = GlobalConfig(slack_webhook="https://hooks.slack.com/test")
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        bus.slack.notify = AsyncMock(side_effect=RuntimeError("boom"))
        # Should not raise
        await bus.emit(PactEvent(kind="phase_start", project_name="test", detail="impl"))

    @pytest.mark.asyncio
    @patch("pact.events._is_git_repo", return_value=False)
    async def test_emit_budget_warning(self, mock_git, tmp_path: Path):
        gc = GlobalConfig(slack_webhook="https://hooks.slack.com/test")
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        bus.slack.notify = AsyncMock(return_value=True)
        await bus.emit(PactEvent(
            kind="budget_warning",
            project_name="test",
            detail="85% spent",
        ))
        bus.slack.notify.assert_called_once()
        call_args = bus.slack.notify.call_args[0][0]
        assert "budget warning" in call_args.lower() or "warning" in call_args.lower()

    @pytest.mark.asyncio
    @patch("pact.events._is_git_repo", return_value=False)
    async def test_component_complete_posts_linear_comment(self, mock_git, tmp_path: Path):
        gc = GlobalConfig(linear_api_key="lin_test")
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        bus._linear_issue_map = {"comp_a": "issue_123"}
        bus.linear.update_issue_status = AsyncMock(return_value=True)
        bus.linear.add_comment = AsyncMock(return_value=True)

        tr = TestResults(total=5, passed=5)
        await bus.emit(PactEvent(
            kind="component_complete",
            project_name="test",
            component_id="comp_a",
            test_results=tr,
        ))
        bus.linear.add_comment.assert_called_once()
        comment_body = bus.linear.add_comment.call_args[0][1]
        assert "[pact]" in comment_body
        assert "5/5" in comment_body

    @pytest.mark.asyncio
    @patch("pact.events._is_git_repo", return_value=False)
    async def test_component_failed_posts_linear_failure_comment(self, mock_git, tmp_path: Path):
        from pact.schemas import TestFailure
        gc = GlobalConfig(linear_api_key="lin_test")
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        bus._linear_issue_map = {"comp_a": "issue_123"}
        bus.linear.update_issue_status = AsyncMock(return_value=True)
        bus.linear.add_comment = AsyncMock(return_value=True)

        tr = TestResults(
            total=5, passed=3, failed=2,
            failure_details=[
                TestFailure(test_id="test_1", error_message="assertion failed"),
            ],
        )
        await bus.emit(PactEvent(
            kind="component_failed",
            project_name="test",
            component_id="comp_a",
            detail="2/5 tests failed",
            test_results=tr,
        ))
        bus.linear.add_comment.assert_called_once()
        comment_body = bus.linear.add_comment.call_args[0][1]
        assert "[pact]" in comment_body
        assert "failed" in comment_body.lower()

    @pytest.mark.asyncio
    @patch("pact.events._is_git_repo", return_value=False)
    async def test_human_needed_creates_slack_thread(self, mock_git, tmp_path: Path):
        gc = GlobalConfig(slack_bot_token="xoxb-test", slack_channel="C123")
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        bus.slack.post_message = AsyncMock(return_value={
            "ok": True, "ts": "123.456", "channel": "C123",
        })

        await bus.emit(PactEvent(
            kind="human_needed",
            project_name="test",
            detail="Interview pending",
        ))
        bus.slack.post_message.assert_called_once()
        assert bus._slack_thread_ts == "123.456"

    @pytest.mark.asyncio
    @patch("pact.events._is_git_repo", return_value=False)
    async def test_init_with_bot_token(self, mock_git, tmp_path: Path):
        gc = GlobalConfig(slack_bot_token="xoxb-test", slack_channel="C123")
        pc = ProjectConfig()
        bus = EventBus(tmp_path, gc, pc)
        assert bus.slack.read_configured is True
        assert bus._slack_channel == "C123"
        assert bus._slack_thread_ts == ""
