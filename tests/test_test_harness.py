"""Tests for the test harness (pytest output parsing)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pact.test_harness import EvalTier, parse_pytest_output, run_contract_tests, select_test_files


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


class TestEvalTier:
    """Tests for tiered evaluation and test file selection."""

    def test_smoke_returns_smoke_test(self, tmp_path):
        smoke_dir = tmp_path / "tests" / "smoke"
        smoke_dir.mkdir(parents=True)
        smoke_file = smoke_dir / "test_comp_a.py"
        smoke_file.write_text("pass")

        result = select_test_files("comp_a", tmp_path, EvalTier.SMOKE)
        assert len(result) == 1
        assert result[0] == smoke_file

    def test_smoke_falls_back_to_contract(self, tmp_path):
        tests_dir = tmp_path / "tests" / "comp_a"
        tests_dir.mkdir(parents=True)
        contract = tests_dir / "contract_test.py"
        contract.write_text("pass")

        result = select_test_files("comp_a", tmp_path, EvalTier.SMOKE)
        assert len(result) == 1
        assert result[0] == contract

    def test_smoke_empty_when_no_tests(self, tmp_path):
        result = select_test_files("comp_a", tmp_path, EvalTier.SMOKE)
        assert result == []

    def test_standard_returns_contract_only(self, tmp_path):
        tests_dir = tmp_path / "tests" / "comp_a"
        tests_dir.mkdir(parents=True)
        contract = tests_dir / "contract_test.py"
        contract.write_text("pass")
        # Goodhart exists but shouldn't be included
        goodhart_dir = tests_dir / "goodhart"
        goodhart_dir.mkdir()
        (goodhart_dir / "goodhart_test.py").write_text("pass")

        result = select_test_files("comp_a", tmp_path, EvalTier.STANDARD)
        assert len(result) == 1
        assert result[0] == contract

    def test_exhaustive_returns_all(self, tmp_path):
        tests_dir = tmp_path / "tests" / "comp_a"
        tests_dir.mkdir(parents=True)
        contract = tests_dir / "contract_test.py"
        contract.write_text("pass")
        goodhart_dir = tests_dir / "goodhart"
        goodhart_dir.mkdir()
        goodhart = goodhart_dir / "goodhart_test.py"
        goodhart.write_text("pass")
        emission = tests_dir / "emission_test.py"
        emission.write_text("pass")

        result = select_test_files("comp_a", tmp_path, EvalTier.EXHAUSTIVE)
        assert len(result) == 3
        assert contract in result
        assert goodhart in result
        assert emission in result

    def test_exhaustive_partial_files(self, tmp_path):
        tests_dir = tmp_path / "tests" / "comp_a"
        tests_dir.mkdir(parents=True)
        contract = tests_dir / "contract_test.py"
        contract.write_text("pass")
        # No goodhart or emission

        result = select_test_files("comp_a", tmp_path, EvalTier.EXHAUSTIVE)
        assert len(result) == 1
        assert result[0] == contract

    def test_typescript_extension(self, tmp_path):
        tests_dir = tmp_path / "tests" / "comp_a"
        tests_dir.mkdir(parents=True)
        ts_test = tests_dir / "contract_test.test.ts"
        ts_test.write_text("pass")

        result = select_test_files("comp_a", tmp_path, EvalTier.STANDARD, language="typescript")
        assert len(result) == 1
        assert result[0] == ts_test

    def test_enum_values(self):
        assert EvalTier.SMOKE == "smoke"
        assert EvalTier.STANDARD == "standard"
        assert EvalTier.EXHAUSTIVE == "exhaustive"
