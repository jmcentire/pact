"""Tests for LinearClient read methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pact.human.linear import LinearClient


class TestGetIssue:
    @pytest.mark.asyncio
    @patch.dict("os.environ", {}, clear=True)
    async def test_get_issue_unconfigured(self):
        client = LinearClient(api_key="")
        result = await client.get_issue("issue_123")
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_issue_mock(self):
        client = LinearClient(api_key="lin_test_key")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "issue": {
                    "id": "issue_123",
                    "title": "Test Issue",
                    "description": "A test",
                    "state": {"name": "In Progress"},
                    "comments": {
                        "nodes": [
                            {
                                "body": "Looking good",
                                "createdAt": "2025-01-01T00:00:00Z",
                                "user": {"name": "Alice"},
                            }
                        ]
                    },
                }
            }
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await client.get_issue("issue_123")

        assert result["id"] == "issue_123"
        assert result["title"] == "Test Issue"
        assert result["state"] == "In Progress"
        assert len(result["comments"]) == 1
        assert result["comments"][0]["userName"] == "Alice"


class TestGetIssueComments:
    @pytest.mark.asyncio
    @patch.dict("os.environ", {}, clear=True)
    async def test_get_issue_comments_unconfigured(self):
        client = LinearClient(api_key="")
        result = await client.get_issue_comments("issue_123")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_issue_comments_mock(self):
        client = LinearClient(api_key="lin_test_key")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "issue": {
                    "comments": {
                        "nodes": [
                            {
                                "body": "Comment 1",
                                "createdAt": "2025-01-01T00:00:00Z",
                                "user": {"name": "Bob"},
                            },
                            {
                                "body": "Comment 2",
                                "createdAt": "2025-01-02T00:00:00Z",
                                "user": {"name": "Carol"},
                            },
                        ]
                    }
                }
            }
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await client.get_issue_comments("issue_123")

        assert len(result) == 2
        assert result[0]["body"] == "Comment 1"
        assert result[1]["userName"] == "Carol"

        # Verify GraphQL query was called
        call_args = mock_client.post.call_args
        query = call_args.kwargs.get("json", call_args[1].get("json", {})).get("query", "")
        assert "GetComments" in query

    @pytest.mark.asyncio
    async def test_get_issue_comments_since_filter(self):
        client = LinearClient(api_key="lin_test_key")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "issue": {
                    "comments": {
                        "nodes": [
                            {
                                "body": "Old",
                                "createdAt": "2025-01-01T00:00:00Z",
                                "user": {"name": "A"},
                            },
                            {
                                "body": "New",
                                "createdAt": "2025-06-01T00:00:00Z",
                                "user": {"name": "B"},
                            },
                        ]
                    }
                }
            }
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await client.get_issue_comments(
                "issue_123", since="2025-03-01T00:00:00Z",
            )

        assert len(result) == 1
        assert result[0]["body"] == "New"


class TestAddComment:
    @pytest.mark.asyncio
    @patch.dict("os.environ", {}, clear=True)
    async def test_add_comment_unconfigured(self):
        client = LinearClient(api_key="")
        result = await client.add_comment("issue_123", "Hello")
        assert result is False

    @pytest.mark.asyncio
    async def test_add_comment_mock(self):
        client = LinearClient(api_key="lin_test_key")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {"commentCreate": {"success": True}}
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await client.add_comment("issue_123", "Test comment")

        assert result is True

        # Verify mutation shape
        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json", call_args[1].get("json", {}))
        assert "CommentCreateInput" in payload.get("query", "")
        assert payload["variables"]["input"]["issueId"] == "issue_123"
        assert payload["variables"]["input"]["body"] == "Test comment"


class TestSearchIssues:
    @pytest.mark.asyncio
    @patch.dict("os.environ", {}, clear=True)
    async def test_search_issues_unconfigured(self):
        client = LinearClient(api_key="")
        result = await client.search_issues("test")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_issues_mock(self):
        client = LinearClient(api_key="lin_test_key")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "issueSearch": {
                    "nodes": [
                        {
                            "id": "id_1",
                            "identifier": "PROJ-1",
                            "title": "First issue",
                            "description": "desc",
                            "state": {"name": "Todo"},
                        },
                        {
                            "id": "id_2",
                            "identifier": "PROJ-2",
                            "title": "Second issue",
                            "description": "",
                            "state": {"name": "Done"},
                        },
                    ]
                }
            }
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await client.search_issues("test", limit=5)

        assert len(result) == 2
        assert result[0]["identifier"] == "PROJ-1"
        assert result[1]["state"] == "Done"

        # Verify query shape + limit
        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json", call_args[1].get("json", {}))
        assert "SearchIssues" in payload.get("query", "")
        assert payload["variables"]["first"] == 5
