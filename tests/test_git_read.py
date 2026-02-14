"""Tests for GitManager read methods."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pact.human.git import GitManager


def _mock_subprocess(stdout_data: str, returncode: int = 0):
    """Create a mock subprocess result."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (
        stdout_data.encode(),
        b"",
    )
    mock_proc.returncode = returncode
    return mock_proc


class TestGetPrComments:
    @pytest.mark.asyncio
    async def test_get_pr_comments_no_repo(self):
        git = GitManager(repo_path=None)
        result = await git.get_pr_comments(1)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_pr_comments_mock(self, tmp_path: Path):
        git = GitManager(repo_path=tmp_path)

        data = {
            "comments": [
                {
                    "author": {"login": "alice"},
                    "body": "Looks good!",
                    "createdAt": "2025-01-01T00:00:00Z",
                },
                {
                    "author": {"login": "bob"},
                    "body": "One nit",
                    "createdAt": "2025-01-02T00:00:00Z",
                },
            ]
        }

        with patch("asyncio.create_subprocess_exec", return_value=_mock_subprocess(json.dumps(data))):
            result = await git.get_pr_comments(42)

        assert len(result) == 2
        assert result[0]["author"] == "alice"
        assert result[0]["body"] == "Looks good!"
        assert result[1]["author"] == "bob"

    @pytest.mark.asyncio
    async def test_get_pr_comments_failure(self, tmp_path: Path):
        git = GitManager(repo_path=tmp_path)

        with patch("asyncio.create_subprocess_exec", return_value=_mock_subprocess("", returncode=1)):
            result = await git.get_pr_comments(42)

        assert result == []


class TestGetPrReviews:
    @pytest.mark.asyncio
    async def test_get_pr_reviews_no_repo(self):
        git = GitManager(repo_path=None)
        result = await git.get_pr_reviews(1)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_pr_reviews_mock(self, tmp_path: Path):
        git = GitManager(repo_path=tmp_path)

        data = {
            "reviews": [
                {
                    "author": {"login": "carol"},
                    "state": "APPROVED",
                    "body": "Ship it!",
                },
            ]
        }

        with patch("asyncio.create_subprocess_exec", return_value=_mock_subprocess(json.dumps(data))):
            result = await git.get_pr_reviews(42)

        assert len(result) == 1
        assert result[0]["author"] == "carol"
        assert result[0]["state"] == "APPROVED"


class TestGetPrStatus:
    @pytest.mark.asyncio
    async def test_get_pr_status_no_repo(self):
        git = GitManager(repo_path=None)
        result = await git.get_pr_status(1)
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_pr_status_mock(self, tmp_path: Path):
        git = GitManager(repo_path=tmp_path)

        data = {
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
        }

        with patch("asyncio.create_subprocess_exec", return_value=_mock_subprocess(json.dumps(data))):
            result = await git.get_pr_status(42)

        assert result["state"] == "OPEN"
        assert result["mergeable"] == "MERGEABLE"
        assert result["reviewDecision"] == "APPROVED"


class TestAddPrComment:
    @pytest.mark.asyncio
    async def test_add_pr_comment_no_repo(self):
        git = GitManager(repo_path=None)
        result = await git.add_pr_comment(1, "test")
        assert result is False

    @pytest.mark.asyncio
    async def test_add_pr_comment_mock(self, tmp_path: Path):
        git = GitManager(repo_path=tmp_path)

        with patch("asyncio.create_subprocess_exec", return_value=_mock_subprocess("")) as mock_exec:
            result = await git.add_pr_comment(42, "Nice work!")

        assert result is True
        # Verify gh command shape
        call_args = mock_exec.call_args[0]
        assert "gh" in call_args
        assert "pr" in call_args
        assert "comment" in call_args
        assert "42" in call_args
        assert "Nice work!" in call_args


class TestGetOpenPrs:
    @pytest.mark.asyncio
    async def test_get_open_prs_no_repo(self):
        git = GitManager(repo_path=None)
        result = await git.get_open_prs()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_open_prs_mock(self, tmp_path: Path):
        git = GitManager(repo_path=tmp_path)

        data = [
            {
                "number": 10,
                "title": "cf/pricing",
                "url": "https://github.com/org/repo/pull/10",
                "headRefName": "cf/pricing",
            },
            {
                "number": 11,
                "title": "cf/auth",
                "url": "https://github.com/org/repo/pull/11",
                "headRefName": "cf/auth",
            },
        ]

        with patch("asyncio.create_subprocess_exec", return_value=_mock_subprocess(json.dumps(data))) as mock_exec:
            result = await git.get_open_prs(label="pact")

        assert len(result) == 2
        assert result[0]["number"] == 10
        assert result[1]["headRefName"] == "cf/auth"

        # Verify label filtering in command
        call_args = mock_exec.call_args[0]
        assert "--label" in call_args
        assert "pact" in call_args

    @pytest.mark.asyncio
    async def test_get_open_prs_no_label(self, tmp_path: Path):
        git = GitManager(repo_path=tmp_path)

        with patch("asyncio.create_subprocess_exec", return_value=_mock_subprocess("[]")) as mock_exec:
            result = await git.get_open_prs(label="")

        assert result == []
        call_args = mock_exec.call_args[0]
        assert "--label" not in call_args
