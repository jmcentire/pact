"""Tests for shared context preamble in Claude Code team backend (P2-2)."""
import pytest
from pathlib import Path
from pact.backends.claude_code_team import ClaudeCodeTeamBackend, AgentTask


class TestSharedPreamble:
    def test_write_shared_preamble_creates_file(self, tmp_path):
        backend = ClaudeCodeTeamBackend.__new__(ClaudeCodeTeamBackend)
        backend._prompt_dir = tmp_path
        backend._preamble_path = None

        path = backend.write_shared_preamble("Project SOPs: Use TDD always.")
        assert path.exists()
        assert path.read_text() == "Project SOPs: Use TDD always."

    def test_write_shared_preamble_sets_path(self, tmp_path):
        backend = ClaudeCodeTeamBackend.__new__(ClaudeCodeTeamBackend)
        backend._prompt_dir = tmp_path
        backend._preamble_path = None

        path = backend.write_shared_preamble("context")
        assert backend.preamble_path == path

    def test_preamble_path_none_initially(self):
        backend = ClaudeCodeTeamBackend.__new__(ClaudeCodeTeamBackend)
        backend._preamble_path = None
        assert backend.preamble_path is None

    def test_preamble_path_after_write(self, tmp_path):
        backend = ClaudeCodeTeamBackend.__new__(ClaudeCodeTeamBackend)
        backend._prompt_dir = tmp_path
        backend._preamble_path = None

        backend.write_shared_preamble("context")
        assert backend.preamble_path is not None
        assert backend.preamble_path.name == "shared_preamble.md"

    def test_write_overwrites_existing(self, tmp_path):
        backend = ClaudeCodeTeamBackend.__new__(ClaudeCodeTeamBackend)
        backend._prompt_dir = tmp_path
        backend._preamble_path = None

        backend.write_shared_preamble("v1")
        backend.write_shared_preamble("v2")
        assert backend.preamble_path.read_text() == "v2"

    def test_preamble_filename(self, tmp_path):
        backend = ClaudeCodeTeamBackend.__new__(ClaudeCodeTeamBackend)
        backend._prompt_dir = tmp_path
        backend._preamble_path = None

        path = backend.write_shared_preamble("content")
        assert path.name == "shared_preamble.md"


class TestPreambleInInit:
    def test_init_sets_preamble_none(self):
        """__init__ should set _preamble_path to None."""
        backend = ClaudeCodeTeamBackend(
            model="claude-opus-4-6",
            repo_path="/tmp/test",
        )
        assert backend._preamble_path is None
