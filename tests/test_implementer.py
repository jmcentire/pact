"""Tests for implementer module — implementation workflow logic."""

from __future__ import annotations

import textwrap
from pathlib import Path

from pact.implementer import (
    _find_defined_names,
    _fix_pydantic_v1_patterns,
    _fuzzy_match,
    _sanitize_filename,
    _to_snake_case,
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

    def test_filters_dotted_method_names(self):
        contract = _make_contract(functions=[
            FunctionContract(
                name="TaskRegistry.__init__",
                description="",
                inputs=[],
                output_type="None",
            ),
        ])
        exports = get_required_exports(contract)
        assert "TaskRegistry.__init__" not in exports

    def test_filters_dunder_names(self):
        contract = _make_contract(functions=[
            FunctionContract(
                name="__contains__",
                description="",
                inputs=[],
                output_type="bool",
            ),
        ])
        exports = get_required_exports(contract)
        assert "__contains__" not in exports

    def test_filters_builtins(self):
        contract = _make_contract(types=[
            TypeSpec(name="frozenset", kind="primitive"),
            TypeSpec(name="MyCustomType", kind="struct"),
        ])
        exports = get_required_exports(contract)
        assert "frozenset" not in exports
        assert "MyCustomType" in exports


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


class TestSanitizeFilename:
    """Test filename sanitization for code author output."""

    def test_strips_src_prefix(self):
        assert _sanitize_filename("src/module.py") == "module.py"

    def test_strips_src_nested(self):
        assert _sanitize_filename("src/pkg/__init__.py") == "pkg/__init__.py"

    def test_no_prefix_unchanged(self):
        assert _sanitize_filename("module.py") == "module.py"

    def test_nested_src_not_stripped(self):
        # Only strip leading src/, not embedded
        assert _sanitize_filename("lib/src/module.py") == "lib/src/module.py"

    def test_double_src_strips_once(self):
        assert _sanitize_filename("src/src/module.py") == "src/module.py"


class TestFixPydanticV1Patterns:
    """Test mechanical Pydantic v1→v2 fixups."""

    def test_replaces_regex_with_pattern(self):
        source = 'name: str = Field(..., regex=r"^[a-z]+$")'
        fixed, changes = _fix_pydantic_v1_patterns(source)
        assert 'pattern=r"^[a-z]+$"' in fixed
        assert "regex=" not in fixed
        assert changes

    def test_removes_pydantic_main_import(self):
        source = "from pydantic.main import ModelMetaclass\nclass Foo: pass"
        fixed, changes = _fix_pydantic_v1_patterns(source)
        assert "pydantic.main" not in fixed
        assert changes

    def test_removes_error_wrappers_import(self):
        source = "from pydantic.error_wrappers import flatten_errors"
        fixed, changes = _fix_pydantic_v1_patterns(source)
        assert "error_wrappers" not in fixed
        assert changes

    def test_removes_always_true(self):
        source = '@field_validator("name", always=True)\ndef check(cls, v): pass'
        fixed, changes = _fix_pydantic_v1_patterns(source)
        assert "always=True" not in fixed
        assert "check" in fixed
        assert changes

    def test_replaces_extra_forbid(self):
        source = 'class Foo(BaseModel, extra=Extra.forbid): pass'
        fixed, changes = _fix_pydantic_v1_patterns(source)
        assert "Extra.forbid" not in fixed
        assert "'forbid'" in fixed
        assert changes

    def test_replaces_base_model_metaclass_with_enum(self):
        source = 'class Phase(str, BaseModelMetaclass):\n    ACTIVE = "active"'
        fixed, changes = _fix_pydantic_v1_patterns(source)
        assert "BaseModelMetaclass" not in fixed
        assert "Enum" in fixed
        assert changes

    def test_no_changes_for_clean_code(self):
        source = 'from pydantic import BaseModel, Field\nclass Foo(BaseModel): pass'
        fixed, changes = _fix_pydantic_v1_patterns(source)
        assert fixed == source
        assert not changes

    def test_multiple_fixes_combined(self):
        source = (
            'from pydantic.main import ModelMetaclass\n'
            'from pydantic import Field, Extra\n'
            'class Foo(BaseModel, extra=Extra.forbid):\n'
            '    name: str = Field(regex=r"^[a-z]+$")\n'
        )
        fixed, changes = _fix_pydantic_v1_patterns(source)
        assert "pydantic.main" not in fixed
        assert "Extra.forbid" not in fixed
        assert "regex=" not in fixed
        assert len(changes) >= 3

    def test_fixes_root_validator(self):
        source = (
            'from pydantic import BaseModel, root_validator\n'
            'class Foo(BaseModel):\n'
            '    @root_validator\n'
            '    def check(cls, values):\n'
            '        return values\n'
        )
        fixed, changes = _fix_pydantic_v1_patterns(source)
        assert "@model_validator(mode='after')" in fixed
        assert "@root_validator" not in fixed
        assert changes

    def test_adds_missing_configdict_import(self):
        source = (
            'from pydantic import BaseModel\n'
            'class Foo(BaseModel):\n'
            '    model_config = ConfigDict(frozen=True)\n'
        )
        fixed, changes = _fix_pydantic_v1_patterns(source)
        assert "from pydantic import ConfigDict" in fixed
        assert changes

    def test_adds_missing_deepcopy_import(self):
        source = 'x = deepcopy(y)\n'
        fixed, changes = _fix_pydantic_v1_patterns(source)
        assert "from copy import deepcopy" in fixed
        assert changes


class TestSnakeCaseMatch:
    """Test PascalCase ↔ snake_case fuzzy matching."""

    def test_to_snake_case(self):
        assert _to_snake_case("ReportGenerator") == "report_generator"
        assert _to_snake_case("RequestRouter") == "request_router"
        assert _to_snake_case("FineTuneBackend") == "fine_tune_backend"
        assert _to_snake_case("already_snake") == "already_snake"

    def test_fuzzy_match_pascal_to_snake(self):
        assert _fuzzy_match("ReportGenerator", {"report_generator"}) == "report_generator"

    def test_fuzzy_match_snake_to_pascal(self):
        assert _fuzzy_match("request_router", {"RequestRouter"}) == "RequestRouter"

    def test_fuzzy_match_underscore_stripped(self):
        assert _fuzzy_match("FineTuneBackend", {"fine_tune_backend"}) == "fine_tune_backend"


class TestClaudeCodeBackendImplement:
    """Test the ClaudeCodeBackend.implement() method (subprocess mocked)."""

    def test_implement_method_exists(self):
        from pact.backends.claude_code import ClaudeCodeBackend
        assert hasattr(ClaudeCodeBackend, "implement")

    def test_implement_returns_tuple(self):
        """implement() should return (text, in_tokens, out_tokens)."""
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock
        from pact.backends.claude_code import ClaudeCodeBackend
        from pact.budget import BudgetTracker

        budget = BudgetTracker(per_project_cap=10.0)
        backend = ClaudeCodeBackend(budget=budget, model="test-model")

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(
            b'{"result": "done", "input_tokens": 500, "output_tokens": 200}',
            b'',
        ))
        mock_proc.pid = 1234
        mock_proc.returncode = 0

        async def mock_create(*args, **kwargs):
            return mock_proc

        with patch("asyncio.create_subprocess_exec", mock_create):
            result = asyncio.run(backend.implement("test prompt"))

        assert isinstance(result, tuple)
        assert len(result) == 3
        text, in_tok, out_tok = result
        assert in_tok == 500
        assert out_tok == 200

    def test_implement_passes_max_turns(self):
        """implement() should pass --max-turns to the claude CLI."""
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock
        from pact.backends.claude_code import ClaudeCodeBackend
        from pact.budget import BudgetTracker

        budget = BudgetTracker(per_project_cap=10.0)
        backend = ClaudeCodeBackend(budget=budget, model="test-model")

        captured_cmds = []

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(
            b'{"result": "ok", "input_tokens": 100, "output_tokens": 50}',
            b'',
        ))
        mock_proc.pid = 1234

        async def mock_create(*args, **kwargs):
            captured_cmds.append(args)
            return mock_proc

        with patch("asyncio.create_subprocess_exec", mock_create):
            asyncio.run(backend.implement("test", max_turns=25))

        # Check that --max-turns 25 is in the command
        cmd_args = captured_cmds[0]
        assert "--max-turns" in cmd_args
        idx = cmd_args.index("--max-turns")
        assert cmd_args[idx + 1] == "25"

    def test_implement_passes_allowed_tools(self):
        """implement() should pass --allowedTools with full tool access."""
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock
        from pact.backends.claude_code import ClaudeCodeBackend
        from pact.budget import BudgetTracker

        budget = BudgetTracker(per_project_cap=10.0)
        backend = ClaudeCodeBackend(budget=budget, model="test-model")

        captured_cmds = []

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(
            b'{"result": "ok", "input_tokens": 100, "output_tokens": 50}',
            b'',
        ))
        mock_proc.pid = 1234

        async def mock_create(*args, **kwargs):
            captured_cmds.append(args)
            return mock_proc

        with patch("asyncio.create_subprocess_exec", mock_create):
            asyncio.run(backend.implement("test"))

        cmd_args = captured_cmds[0]
        assert "--allowedTools" in cmd_args
        idx = cmd_args.index("--allowedTools")
        tools = cmd_args[idx + 1]
        assert "Write" in tools
        assert "Edit" in tools
        assert "Bash" in tools

    def test_implement_pipes_prompt_via_stdin(self):
        """implement() should send prompt via stdin, not as CLI arg."""
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock
        from pact.backends.claude_code import ClaudeCodeBackend
        from pact.budget import BudgetTracker

        budget = BudgetTracker(per_project_cap=10.0)
        backend = ClaudeCodeBackend(budget=budget, model="test-model")

        captured_kwargs = []

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(
            b'{"result": "ok", "input_tokens": 100, "output_tokens": 50}',
            b'',
        ))
        mock_proc.pid = 1234

        async def mock_create(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return mock_proc

        long_prompt = "x" * 10000
        with patch("asyncio.create_subprocess_exec", mock_create):
            asyncio.run(backend.implement(long_prompt))

        # Should use stdin (PIPE), not pass prompt as CLI arg
        assert captured_kwargs[0].get("stdin") is not None
