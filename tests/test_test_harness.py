"""Tests for the test harness (pytest output parsing)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pact.test_harness import parse_pytest_output, run_contract_tests


class TestParsePytestOutput:
    def test_all_pass(self):
        stdout = """
tests/test_example.py::test_add PASSED
tests/test_example.py::test_sub PASSED

============================== 2 passed ==============================
"""
        result = parse_pytest_output(stdout, "")
        assert result.total == 2
        assert result.passed == 2
        assert result.failed == 0
        assert result.all_passed is True

    def test_with_failures(self):
        stdout = """
tests/test_example.py::test_add PASSED
tests/test_example.py::test_sub FAILED

============================== 1 passed, 1 failed ==============================
"""
        result = parse_pytest_output(stdout, "")
        assert result.total == 2
        assert result.passed == 1
        assert result.failed == 1
        assert result.all_passed is False
        assert len(result.failure_details) == 1

    def test_with_errors(self):
        stdout = """
tests/test_example.py::test_add ERROR

============================== 1 error ==============================
"""
        result = parse_pytest_output(stdout, "")
        assert result.errors >= 1

    def test_summary_fallback(self):
        # No individual test lines, just summary
        stdout = "5 passed, 2 failed in 1.5s"
        result = parse_pytest_output(stdout, "")
        assert result.passed == 5
        assert result.failed == 2
        assert result.total == 7

    def test_collection_error(self):
        stdout = ""
        stderr = "ERROR collecting tests"
        result = parse_pytest_output(stdout, stderr)
        assert result.errors >= 1

    def test_empty_output(self):
        result = parse_pytest_output("", "")
        assert result.total == 0
        assert result.all_passed is False


class TestExtraPaths:
    """Test that extra_paths are included in PYTHONPATH."""

    def test_extra_paths_included(self, tmp_path):
        """extra_paths should appear in the PYTHONPATH env var."""
        test_file = tmp_path / "test_example.py"
        test_file.write_text("def test_pass(): pass")
        impl_dir = tmp_path / "impl"
        impl_dir.mkdir()
        extra = [tmp_path / "child_a" / "src", tmp_path / "child_b" / "src"]
        for p in extra:
            p.mkdir(parents=True)

        captured_env = {}

        original_exec = asyncio.create_subprocess_exec

        async def mock_exec(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            proc = AsyncMock()
            proc.communicate = AsyncMock(
                return_value=(b"test_x PASSED\n1 passed", b""),
            )
            proc.returncode = 0
            return proc

        with patch("pact.test_harness.asyncio.create_subprocess_exec", side_effect=mock_exec):
            asyncio.run(run_contract_tests(test_file, impl_dir, extra_paths=extra))

        pythonpath = captured_env.get("PYTHONPATH", "")
        for p in extra:
            assert str(p) in pythonpath

    def test_no_extra_paths(self, tmp_path):
        """Without extra_paths, PYTHONPATH should just have impl_dir."""
        test_file = tmp_path / "test_example.py"
        test_file.write_text("def test_pass(): pass")
        impl_dir = tmp_path / "impl"
        impl_dir.mkdir()

        captured_env = {}

        async def mock_exec(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            proc = AsyncMock()
            proc.communicate = AsyncMock(
                return_value=(b"test_x PASSED\n1 passed", b""),
            )
            proc.returncode = 0
            return proc

        with patch("pact.test_harness.asyncio.create_subprocess_exec", side_effect=mock_exec):
            asyncio.run(run_contract_tests(test_file, impl_dir))

        pythonpath = captured_env.get("PYTHONPATH", "")
        assert str(impl_dir) in pythonpath
        # Should only have impl_dir and parent
        assert len(pythonpath.split(":")) == 2
