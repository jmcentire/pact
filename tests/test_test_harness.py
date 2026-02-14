"""Tests for the test harness (pytest output parsing)."""

from __future__ import annotations

from pact.test_harness import parse_pytest_output


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
