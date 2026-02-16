"""Tests for implementer module — implementation workflow logic."""

from __future__ import annotations

import textwrap
from pathlib import Path

from pact.implementer import (
    _find_defined_names,
    _fuzzy_match,
    validate_and_fix_exports,
)
from pact.interface_stub import get_required_exports
from pact.schemas import (
    ComponentContract,
    ErrorCase,
    FieldSpec,
    FunctionContract,
    TestFailure,
    TestResults,
    TypeSpec,
)


def _make_contract(
    types: list[TypeSpec] | None = None,
    functions: list[FunctionContract] | None = None,
) -> ComponentContract:
    """Helper to build a minimal contract for testing."""
    return ComponentContract(
        name="Test Component",
        component_id="test_comp",
        description="A test component",
        version=1,
        types=types or [],
        functions=functions or [],
        invariants=[],
        dependencies=[],
    )


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


class TestFindDefinedNames:
    """Test AST-based name extraction from Python source."""

    def test_finds_classes(self):
        source = "class Foo:\n    pass\nclass Bar:\n    pass"
        assert _find_defined_names(source) == {"Foo", "Bar"}

    def test_finds_functions(self):
        source = "def hello():\n    pass\ndef world():\n    pass"
        assert _find_defined_names(source) == {"hello", "world"}

    def test_finds_assignments(self):
        source = "X = 42\nMyType = list[str]"
        assert _find_defined_names(source) == {"X", "MyType"}

    def test_finds_annotated_assignments(self):
        source = "x: int = 5"
        assert _find_defined_names(source) == {"x"}

    def test_ignores_nested_names(self):
        source = textwrap.dedent("""\
            class Outer:
                class Inner:
                    pass
                def method(self):
                    pass
        """)
        # Only top-level names
        assert _find_defined_names(source) == {"Outer"}

    def test_handles_syntax_error(self):
        assert _find_defined_names("def broken(") == set()

    def test_async_functions(self):
        source = "async def fetch():\n    pass"
        assert _find_defined_names(source) == {"fetch"}


class TestFuzzyMatch:
    """Test fuzzy matching for export name resolution."""

    def test_exact_case_insensitive(self):
        assert _fuzzy_match("phase", {"Phase", "Other"}) == "Phase"

    def test_substring_match(self):
        # "Phase" is contained in "TaskPhase"
        assert _fuzzy_match("Phase", {"TaskPhase", "Other"}) == "TaskPhase"

    def test_no_match(self):
        assert _fuzzy_match("Phase", {"Completely", "Different"}) is None

    def test_ambiguous_multiple_matches(self):
        # Multiple substring matches — returns None (ambiguous)
        assert _fuzzy_match("Phase", {"TaskPhase", "PhaseManager"}) is None

    def test_reverse_containment(self):
        # "Phase" is contained in "TaskPhase", so it matches both directions
        assert _fuzzy_match("TaskPhase", {"Phase"}) == "Phase"


class TestGetRequiredExports:
    """Test required exports extraction from contracts."""

    def test_types_exported(self):
        contract = _make_contract(types=[
            TypeSpec(name="Phase", kind="enum", variants=["a", "b"]),
            TypeSpec(name="Config", kind="struct"),
        ])
        exports = get_required_exports(contract)
        assert "Phase" in exports
        assert "Config" in exports

    def test_functions_exported(self):
        contract = _make_contract(functions=[
            FunctionContract(
                name="compute",
                description="",
                inputs=[],
                output_type="int",
            ),
        ])
        exports = get_required_exports(contract)
        assert "compute" in exports

    def test_error_types_exported(self):
        contract = _make_contract(functions=[
            FunctionContract(
                name="load",
                description="",
                inputs=[],
                output_type="Config",
                error_cases=[
                    ErrorCase(
                        name="not_found",
                        condition="file missing",
                        error_type="ConfigNotFoundError",
                    ),
                ],
            ),
        ])
        exports = get_required_exports(contract)
        assert "ConfigNotFoundError" in exports

    def test_no_duplicate_error_types(self):
        contract = _make_contract(functions=[
            FunctionContract(
                name="load",
                description="",
                inputs=[],
                output_type="Config",
                error_cases=[
                    ErrorCase(name="e1", condition="c1", error_type="MyError"),
                    ErrorCase(name="e2", condition="c2", error_type="MyError"),
                ],
            ),
        ])
        exports = get_required_exports(contract)
        assert exports.count("MyError") == 1


class TestValidateAndFixExports:
    """Test the export validation and auto-fix gate."""

    def test_all_exports_present(self, tmp_path):
        contract = _make_contract(types=[
            TypeSpec(name="Phase", kind="enum", variants=["a"]),
        ])
        module = tmp_path / "test_comp.py"
        module.write_text("class Phase:\n    pass\n")
        missing = validate_and_fix_exports(tmp_path, contract)
        assert missing == []

    def test_detects_missing_export(self, tmp_path):
        contract = _make_contract(types=[
            TypeSpec(name="Phase", kind="enum", variants=["a"]),
            TypeSpec(name="Config", kind="struct"),
        ])
        module = tmp_path / "test_comp.py"
        module.write_text("class Phase:\n    pass\n")
        missing = validate_and_fix_exports(tmp_path, contract)
        assert "Config" in missing

    def test_auto_aliases_fuzzy_match(self, tmp_path):
        contract = _make_contract(types=[
            TypeSpec(name="Phase", kind="enum", variants=["a"]),
        ])
        module = tmp_path / "test_comp.py"
        module.write_text("class TaskPhase:\n    pass\n")
        missing = validate_and_fix_exports(tmp_path, contract)
        assert missing == []  # Should be auto-fixed
        # Verify alias was injected
        content = module.read_text()
        assert "Phase = TaskPhase" in content

    def test_no_files_returns_all_required(self, tmp_path):
        contract = _make_contract(types=[
            TypeSpec(name="Phase", kind="enum", variants=["a"]),
        ])
        missing = validate_and_fix_exports(tmp_path, contract)
        assert "Phase" in missing

    def test_prefers_component_named_file(self, tmp_path):
        contract = _make_contract(types=[
            TypeSpec(name="Phase", kind="enum", variants=["a"]),
        ])
        # Component is test_comp, so test_comp.py should be preferred
        wrong = tmp_path / "other.py"
        wrong.write_text("# nothing\n")
        right = tmp_path / "test_comp.py"
        right.write_text("class TaskPhase:\n    pass\n")
        missing = validate_and_fix_exports(tmp_path, contract)
        assert missing == []
        # Alias injected into the right file
        assert "Phase = TaskPhase" in right.read_text()
        assert "Phase" not in wrong.read_text()

    def test_empty_contract_no_exports(self, tmp_path):
        contract = _make_contract()
        missing = validate_and_fix_exports(tmp_path, contract)
        assert missing == []
