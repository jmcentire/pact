"""Tests for systemic failure detection."""
from pact.scheduler import detect_systemic_failure, SystemicPattern
from pact.schemas import TestResults, TestFailure


class TestDetectSystemicFailure:
    def test_detect_all_zero_zero(self):
        """5 components with total=0, passed=0 -> zero_tests pattern."""
        results = {
            f"comp_{i}": TestResults(total=0, passed=0, failed=0, errors=0)
            for i in range(5)
        }
        pattern = detect_systemic_failure(results)
        assert pattern is not None
        assert pattern.pattern_type == "zero_tests"
        assert len(pattern.affected_components) == 5
        assert "PATH" in pattern.recommendation or "pytest" in pattern.recommendation

    def test_detect_same_import_error(self):
        """3 components with same error -> import_error pattern."""
        results = {
            f"comp_{i}": TestResults(
                total=1, passed=0, failed=0, errors=1,
                failure_details=[TestFailure(
                    test_id="collection",
                    error_message="No module named 'missing_lib'",
                )],
            )
            for i in range(3)
        }
        pattern = detect_systemic_failure(results)
        assert pattern is not None
        assert pattern.pattern_type == "import_error"
        assert len(pattern.affected_components) == 3

    def test_heterogeneous_failures_no_pattern(self):
        """3 components with different errors -> None."""
        results = {
            "comp_0": TestResults(
                total=5, passed=3, failed=2, errors=0,
                failure_details=[TestFailure(test_id="t1", error_message="assertion error A")],
            ),
            "comp_1": TestResults(
                total=5, passed=4, failed=1, errors=0,
                failure_details=[TestFailure(test_id="t2", error_message="assertion error B")],
            ),
            "comp_2": TestResults(
                total=5, passed=2, failed=3, errors=0,
                failure_details=[TestFailure(test_id="t3", error_message="assertion error C")],
            ),
        }
        pattern = detect_systemic_failure(results)
        assert pattern is None

    def test_below_threshold_no_pattern(self):
        """2 components with same error, threshold=3 -> None."""
        results = {
            f"comp_{i}": TestResults(total=0, passed=0, failed=0, errors=0)
            for i in range(2)
        }
        pattern = detect_systemic_failure(results, threshold=3)
        assert pattern is None

    def test_recommendation_is_actionable(self):
        """Pattern recommendations contain specific fix guidance."""
        results = {
            f"comp_{i}": TestResults(total=0, passed=0, failed=0, errors=0)
            for i in range(4)
        }
        pattern = detect_systemic_failure(results)
        assert pattern is not None
        # Should not be vague
        assert len(pattern.recommendation) > 20
        assert any(word in pattern.recommendation.lower() for word in ["check", "fix", "path", "pytest"])

    def test_identical_failure_messages(self):
        """4 components with identical failure messages -> identical_failure."""
        results = {
            f"comp_{i}": TestResults(
                total=10, passed=5, failed=5, errors=0,
                failure_details=[TestFailure(
                    test_id="test_x",
                    error_message="TypeError: unsupported operand type(s)",
                )],
            )
            for i in range(4)
        }
        pattern = detect_systemic_failure(results)
        assert pattern is not None
        assert pattern.pattern_type == "identical_failure"

    def test_mixed_pass_fail_no_systemic(self):
        """Some pass, some fail differently -> None."""
        results = {
            "comp_0": TestResults(total=10, passed=10, failed=0, errors=0),
            "comp_1": TestResults(total=10, passed=10, failed=0, errors=0),
            "comp_2": TestResults(
                total=10, passed=5, failed=5, errors=0,
                failure_details=[TestFailure(test_id="t1", error_message="unique error")],
            ),
        }
        pattern = detect_systemic_failure(results)
        assert pattern is None

    def test_empty_results_returns_none(self):
        pattern = detect_systemic_failure({})
        assert pattern is None

    def test_custom_threshold(self):
        results = {
            f"comp_{i}": TestResults(total=0, passed=0, failed=0, errors=0)
            for i in range(5)
        }
        assert detect_systemic_failure(results, threshold=5) is not None
        assert detect_systemic_failure(results, threshold=6) is None
