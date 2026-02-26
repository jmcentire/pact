"""Tests for CLI test-gen command."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


class TestTestGenSubparser:
    """Verify the test-gen subparser is registered and parses correctly."""

    def test_help_includes_test_gen(self):
        result = subprocess.run(
            [sys.executable, "-m", "pact.cli", "--help"],
            capture_output=True, text=True,
        )
        assert "test-gen" in result.stdout

    def test_test_gen_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "pact.cli", "test-gen", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--dry-run" in result.stdout
        assert "--language" in result.stdout
        assert "--budget" in result.stdout
        assert "--complexity-threshold" in result.stdout
        assert "--json" in result.stdout
        assert "--include-covered" in result.stdout


class TestTestGenDryRun:
    """Dry run should not make any LLM calls."""

    def test_dry_run_basic(self, tmp_path):
        (tmp_path / "main.py").write_text(textwrap.dedent("""\
            def hello(name: str) -> str:
                return f"Hello {name}"
        """))

        result = subprocess.run(
            [sys.executable, "-m", "pact.cli", "test-gen", str(tmp_path), "--dry-run"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Dry Run" in result.stdout

        # Check output files created
        assert (tmp_path / ".pact" / "test-gen" / "analysis.json").exists()
        assert (tmp_path / ".pact" / "test-gen" / "plan.json").exists()
        assert (tmp_path / ".pact" / "test-gen" / "security_audit.md").exists()

    def test_dry_run_json_output(self, tmp_path):
        (tmp_path / "app.py").write_text("def run(): pass")

        result = subprocess.run(
            [sys.executable, "-m", "pact.cli", "test-gen", str(tmp_path), "--dry-run", "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["dry_run"] is True
        assert "coverage_before" in data
        assert "security_findings" in data

    def test_nonexistent_dir_error(self):
        result = subprocess.run(
            [sys.executable, "-m", "pact.cli", "test-gen", "/nonexistent/path/abc123"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "not a directory" in result.stderr


class TestTestGenAnalysis:
    """Verify the analysis output structure."""

    def test_analysis_json_structure(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").write_text(textwrap.dedent("""\
            def login(username: str, password: str) -> bool:
                if username == "admin":
                    return True
                return False

            def logout(session_id: str) -> None:
                pass
        """))

        subprocess.run(
            [sys.executable, "-m", "pact.cli", "test-gen", str(tmp_path), "--dry-run"],
            capture_output=True, text=True,
        )

        analysis_path = tmp_path / ".pact" / "test-gen" / "analysis.json"
        assert analysis_path.exists()
        data = json.loads(analysis_path.read_text())
        assert data["language"] == "python"
        assert len(data["source_files"]) >= 1

    def test_plan_json_structure(self, tmp_path):
        (tmp_path / "utils.py").write_text(textwrap.dedent("""\
            def helper():
                pass
        """))

        subprocess.run(
            [sys.executable, "-m", "pact.cli", "test-gen", str(tmp_path), "--dry-run"],
            capture_output=True, text=True,
        )

        plan_path = tmp_path / ".pact" / "test-gen" / "plan.json"
        assert plan_path.exists()
        data = json.loads(plan_path.read_text())
        assert "entries" in data
        assert "generated_at" in data

    def test_security_audit_with_findings(self, tmp_path):
        (tmp_path / "auth.py").write_text(textwrap.dedent("""\
            def check_admin(user):
                if user.is_admin:
                    return grant_access(user)
                return deny_access(user)
        """))

        subprocess.run(
            [sys.executable, "-m", "pact.cli", "test-gen", str(tmp_path), "--dry-run"],
            capture_output=True, text=True,
        )

        audit_path = tmp_path / ".pact" / "test-gen" / "security_audit.md"
        assert audit_path.exists()
        content = audit_path.read_text()
        assert "Security Audit Report" in content
