"""Tests for implementer module — implementation workflow logic."""

from __future__ import annotations

from pact.schemas import TestFailure, TestResults


class TestTestResults:
    """Test TestResults model used by implementer."""

    def test_all_passed(self):
        r = TestResults(total=3, passed=3, failed=0, errors=0)
        assert r.all_passed is True

    def test_failures_present(self):
        r = TestResults(
            total=3, passed=1, failed=2, errors=0,
            failure_details=[
                TestFailure(test_id="test_a", error_message="assertion failed"),
                TestFailure(test_id="test_b", error_message="type error"),
            ],
        )
        assert r.all_passed is False
        assert len(r.failure_details) == 2

    def test_errors_prevent_pass(self):
        r = TestResults(total=1, passed=0, failed=0, errors=1)
        assert r.all_passed is False

    def test_empty_is_not_passed(self):
        r = TestResults(total=0, passed=0, failed=0, errors=0)
        assert r.all_passed is False
