"""Tests for error classification."""
import asyncio
from pact.lifecycle import ErrorClassification, classify_error


class TestClassifyError:
    def test_timeout_is_transient(self):
        assert classify_error(asyncio.TimeoutError()) == ErrorClassification.TRANSIENT

    def test_connection_error_is_transient(self):
        assert classify_error(ConnectionError("reset")) == ErrorClassification.TRANSIENT

    def test_connection_reset_is_transient(self):
        assert classify_error(ConnectionResetError()) == ErrorClassification.TRANSIENT

    def test_budget_exceeded_is_permanent(self):
        from pact.budget import BudgetExceeded
        assert classify_error(BudgetExceeded()) == ErrorClassification.PERMANENT

    def test_value_error_is_permanent(self):
        assert classify_error(ValueError("bad")) == ErrorClassification.PERMANENT

    def test_file_not_found_is_permanent(self):
        assert classify_error(FileNotFoundError("x")) == ErrorClassification.PERMANENT

    def test_permission_error_is_permanent(self):
        assert classify_error(PermissionError("denied")) == ErrorClassification.PERMANENT

    def test_unknown_error_defaults_permanent(self):
        assert classify_error(RuntimeError("wat")) == ErrorClassification.PERMANENT

    def test_systemic_with_3_same_errors(self):
        context = {
            "component_errors": {
                "comp_a": "TimeoutError",
                "comp_b": "TimeoutError",
                "comp_c": "TimeoutError",
            }
        }
        assert classify_error(asyncio.TimeoutError(), context) == ErrorClassification.SYSTEMIC

    def test_not_systemic_with_mixed_errors(self):
        context = {
            "component_errors": {
                "comp_a": "TimeoutError",
                "comp_b": "ValueError",
                "comp_c": "FileNotFoundError",
            }
        }
        # Mixed errors -> falls through to individual classification
        assert classify_error(asyncio.TimeoutError(), context) == ErrorClassification.TRANSIENT

    def test_not_systemic_below_threshold(self):
        context = {
            "component_errors": {
                "comp_a": "TimeoutError",
                "comp_b": "TimeoutError",
            }
        }
        assert classify_error(asyncio.TimeoutError(), context) == ErrorClassification.TRANSIENT

    def test_os_error_network_is_transient(self):
        """OSError that's not FileNotFoundError/PermissionError -> transient."""
        assert classify_error(OSError("network")) == ErrorClassification.TRANSIENT

    def test_httpx_timeout_by_class_name(self):
        """Simulated httpx timeout (by class name pattern)."""
        class ReadTimeout(Exception): pass
        assert classify_error(ReadTimeout()) == ErrorClassification.TRANSIENT

    def test_enum_values(self):
        assert ErrorClassification.TRANSIENT == "transient"
        assert ErrorClassification.PERMANENT == "permanent"
        assert ErrorClassification.SYSTEMIC == "systemic"
