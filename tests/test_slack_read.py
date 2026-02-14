"""Tests for SlackNotifier read methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pact.human.slack import SlackNotifier


class TestReadConfigured:
    def test_read_configured_with_bot_token(self):
        s = SlackNotifier(bot_token="xoxb-test-token")
        assert s.read_configured is True

    def test_read_configured_without_bot_token(self):
        s = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        assert s.read_configured is False

    def test_read_configured_empty(self):
        s = SlackNotifier()
        assert s.read_configured is False

    def test_configured_with_bot_token_only(self):
        s = SlackNotifier(bot_token="xoxb-test")
        assert s.configured is True

    def test_configured_with_webhook_only(self):
        s = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        assert s.configured is True


class TestPostMessage:
    @pytest.mark.asyncio
    async def test_post_message_no_bot_token(self):
        s = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        result = await s.post_message("C123", "Hello")
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_post_message_mock(self):
        s = SlackNotifier(bot_token="xoxb-test-token")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "ok": True,
            "ts": "1234567890.123456",
            "channel": "C123",
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await s.post_message("C123", "Hello world")

        assert result["ok"] is True
        assert result["ts"] == "1234567890.123456"

        # Verify API call
        call_args = mock_client.post.call_args
        assert "chat.postMessage" in str(call_args)
        payload = call_args.kwargs.get("json", call_args[1].get("json", {}))
        assert payload["channel"] == "C123"
        assert payload["text"] == "Hello world"

    @pytest.mark.asyncio
    async def test_post_message_with_thread(self):
        s = SlackNotifier(bot_token="xoxb-test-token")

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "ts": "111.222", "channel": "C123"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await s.post_message("C123", "Reply", thread_ts="999.000")

        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json", call_args[1].get("json", {}))
        assert payload["thread_ts"] == "999.000"


class TestGetThreadReplies:
    @pytest.mark.asyncio
    async def test_get_thread_replies_no_bot_token(self):
        s = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        result = await s.get_thread_replies("C123", "999.000")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_thread_replies_mock(self):
        s = SlackNotifier(bot_token="xoxb-test-token")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "ok": True,
            "messages": [
                {"user": "U001", "text": "Parent message", "ts": "999.000"},
                {"user": "U002", "text": "First reply", "ts": "999.001"},
                {"user": "U003", "text": "Second reply", "ts": "999.002"},
            ],
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await s.get_thread_replies("C123", "999.000")

        # Should skip parent message
        assert len(result) == 2
        assert result[0]["text"] == "First reply"
        assert result[1]["user"] == "U003"

    @pytest.mark.asyncio
    async def test_get_thread_replies_since_filter(self):
        s = SlackNotifier(bot_token="xoxb-test-token")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "ok": True,
            "messages": [
                {"user": "U001", "text": "Parent", "ts": "100.000"},
                {"user": "U002", "text": "Old reply", "ts": "100.001"},
                {"user": "U003", "text": "New reply", "ts": "200.001"},
            ],
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await s.get_thread_replies("C123", "100.000", since_ts="150.000")

        assert len(result) == 1
        assert result[0]["text"] == "New reply"

    @pytest.mark.asyncio
    async def test_get_thread_replies_not_ok(self):
        s = SlackNotifier(bot_token="xoxb-test-token")

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": False, "error": "channel_not_found"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await s.get_thread_replies("C123", "999.000")

        assert result == []
