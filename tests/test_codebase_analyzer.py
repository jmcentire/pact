"""Tests for codebase_analyzer — mechanical AST analysis."""

from __future__ import annotations

import ast
import textwrap

import pytest

from pact.codebase_analyzer import (
    analyze_codebase,
    compute_complexity,
    detect_security_patterns,
    discover_source_files,
    discover_tests,
    extract_functions,
    map_test_coverage,
)
from pact.schemas_testgen import (
    CoverageEntry,
    CoverageMap,
    ExtractedFunction,
    ExtractedParameter,
    SecurityRiskLevel,
    SourceFile,
    TestFile,
)


# ── File Discovery ─────────────────────────────────────────────────


class TestFileDiscovery:
    def test_finds_python_files(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x = 1")
        (tmp_path / "src" / "utils.py").write_text("y = 2")
        files = discover_source_files(tmp_path)
        paths = [f.path for f in files]
        assert "src/main.py" in paths
        assert "src/utils.py" in paths

    def test_skips_git_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config.py").write_text("x = 1")
        (tmp_path / "main.py").write_text("y = 2")
        files = discover_source_files(tmp_path)
        paths = [f.path for f in files]
        assert not any(".git" in p for p in paths)
        assert "main.py" in paths

    def test_skips_venv(self, tmp_path):
        (tmp_path / "venv" / "lib").mkdir(parents=True)
        (tmp_path / "venv" / "lib" / "site.py").write_text("x = 1")
        (tmp_path / "app.py").write_text("y = 2")
        files = discover_source_files(tmp_path)
        paths = [f.path for f in files]
        assert not any("venv" in p for p in paths)

    def test_skips_node_modules(self, tmp_path):
        (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
        (tmp_path / "node_modules" / "pkg" / "index.py").write_text("x = 1")
        (tmp_path / "app.py").write_text("y = 2")
        files = discover_source_files(tmp_path)
        paths = [f.path for f in files]
        assert not any("node_modules" in p for p in paths)

    def test_skips_pact_dir(self, tmp_path):
        (tmp_path / ".pact").mkdir()
        (tmp_path / ".pact" / "state.py").write_text("x = 1")
        (tmp_path / "main.py").write_text("y = 2")
        files = discover_source_files(tmp_path)
        paths = [f.path for f in files]
        assert not any(".pact" in p for p in paths)

    def test_skips_test_files(self, tmp_path):
        (tmp_path / "main.py").write_text("x = 1")
        (tmp_path / "test_main.py").write_text("x = 1")
        (tmp_path / "main_test.py").write_text("x = 1")
        files = discover_source_files(tmp_path)
        paths = [f.path for f in files]
        assert "main.py" in paths
        assert "test_main.py" not in paths
        assert "main_test.py" not in paths

    def test_skips_test_directories(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x = 1")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "helper.py").write_text("x = 1")
        files = discover_source_files(tmp_path)
        paths = [f.path for f in files]
        assert "src/main.py" in paths
        assert not any("tests" in p for p in paths)

    def test_discover_test_files(self, tmp_path):
        (tmp_path / "test_auth.py").write_text("def test_login(): pass")
        (tmp_path / "auth_test.py").write_text("def test_logout(): pass")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "helper.py").write_text("def test_helper(): pass")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x = 1")
        tfiles = discover_tests(tmp_path)
        paths = [f.path for f in tfiles]
        assert "test_auth.py" in paths
        assert "auth_test.py" in paths
        assert "tests/helper.py" in paths
        assert "src/main.py" not in paths

    def test_empty_directory(self, tmp_path):
        files = discover_source_files(tmp_path)
        assert files == []

    def test_discover_tests_extracts_names(self, tmp_path):
        (tmp_path / "test_math.py").write_text(textwrap.dedent("""\
            import math_utils

            def test_add():
                assert math_utils.add(1, 2) == 3

            def test_subtract():
                assert math_utils.subtract(3, 1) == 2

            def helper():
                pass
        """))
        tfiles = discover_tests(tmp_path)
        assert len(tfiles) == 1
        tf = tfiles[0]
        assert "test_add" in tf.test_functions
        assert "test_subtract" in tf.test_functions
        assert "helper" not in tf.test_functions
        assert "math_utils" in tf.imported_modules


# ── Function Extraction ────────────────────────────────────────────


class TestFunctionExtraction:
    def test_simple_function(self):
        source = "def hello(name: str) -> str:\n    return f'Hello {name}'"
        funcs = extract_functions("test.py", source)
        assert len(funcs) == 1
        f = funcs[0]
        assert f.name == "hello"
        assert f.return_type == "str"
        assert len(f.params) == 1
        assert f.params[0].name == "name"
        assert f.params[0].type_annotation == "str"

    def test_async_function(self):
        source = "async def fetch(url: str) -> bytes:\n    pass"
        funcs = extract_functions("test.py", source)
        assert len(funcs) == 1
        assert funcs[0].is_async is True
        assert funcs[0].name == "fetch"

    def test_method_detection(self):
        source = textwrap.dedent("""\
            class Foo:
                def bar(self, x: int) -> None:
                    pass
        """)
        funcs = extract_functions("test.py", source)
        assert len(funcs) == 1
        assert funcs[0].is_method is True
        assert funcs[0].name == "bar"

    def test_classmethod(self):
        source = textwrap.dedent("""\
            class Foo:
                @classmethod
                def create(cls, name: str) -> 'Foo':
                    pass
        """)
        funcs = extract_functions("test.py", source)
        assert len(funcs) == 1
        assert funcs[0].is_method is True

    def test_typed_params(self):
        source = "def process(x: int, y: str = 'hello', z: float = 3.14) -> bool:\n    pass"
        funcs = extract_functions("test.py", source)
        f = funcs[0]
        assert len(f.params) == 3
        assert f.params[0].type_annotation == "int"
        assert f.params[0].default == ""
        assert f.params[1].default == "'hello'"
        assert f.params[2].type_annotation == "float"
        assert f.params[2].default == "3.14"

    def test_return_type(self):
        source = "def get_items() -> list[str]:\n    return []"
        funcs = extract_functions("test.py", source)
        assert funcs[0].return_type == "list[str]"

    def test_decorator_extraction(self):
        source = textwrap.dedent("""\
            @property
            def name(self) -> str:
                return self._name
        """)
        funcs = extract_functions("test.py", source)
        assert "property" in funcs[0].decorators

    def test_docstring_extraction(self):
        source = textwrap.dedent('''\
            def hello():
                """Say hello."""
                pass
        ''')
        funcs = extract_functions("test.py", source)
        assert funcs[0].docstring == "Say hello."

    def test_multiple_functions(self):
        source = textwrap.dedent("""\
            def a(): pass
            def b(): pass
            def c(): pass
        """)
        funcs = extract_functions("test.py", source)
        assert len(funcs) == 3
        names = [f.name for f in funcs]
        assert names == ["a", "b", "c"]

    def test_syntax_error_returns_empty(self):
        source = "def broken(:\n    pass"
        funcs = extract_functions("test.py", source)
        assert funcs == []

    def test_line_number(self):
        source = "\n\ndef third_line():\n    pass"
        funcs = extract_functions("test.py", source)
        assert funcs[0].line_number == 3


# ── Cyclomatic Complexity ──────────────────────────────────────────


class TestCyclomaticComplexity:
    def _parse_func(self, source: str) -> ast.AST:
        tree = ast.parse(textwrap.dedent(source))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node
        raise ValueError("No function found")

    def test_base_complexity(self):
        node = self._parse_func("def f():\n    return 1")
        assert compute_complexity(node) == 1

    def test_single_if(self):
        node = self._parse_func("""\
            def f(x):
                if x > 0:
                    return x
                return 0
        """)
        assert compute_complexity(node) == 2

    def test_if_elif_else(self):
        node = self._parse_func("""\
            def f(x):
                if x > 0:
                    return 1
                elif x < 0:
                    return -1
                else:
                    return 0
        """)
        # base(1) + if(1) + elif(1) = 3
        assert compute_complexity(node) == 3

    def test_for_loop(self):
        node = self._parse_func("""\
            def f(items):
                for item in items:
                    print(item)
        """)
        assert compute_complexity(node) == 2

    def test_while_loop(self):
        node = self._parse_func("""\
            def f():
                while True:
                    break
        """)
        assert compute_complexity(node) == 2

    def test_try_except(self):
        node = self._parse_func("""\
            def f():
                try:
                    x = 1
                except ValueError:
                    x = 0
        """)
        assert compute_complexity(node) == 2

    def test_bool_and(self):
        node = self._parse_func("""\
            def f(a, b):
                if a and b:
                    return True
        """)
        # base(1) + if(1) + and(1) = 3
        assert compute_complexity(node) == 3

    def test_bool_or(self):
        node = self._parse_func("""\
            def f(a, b):
                if a or b:
                    return True
        """)
        assert compute_complexity(node) == 3

    def test_triple_bool(self):
        node = self._parse_func("""\
            def f(a, b, c):
                if a and b and c:
                    return True
        """)
        # base(1) + if(1) + and(2: 3 values - 1) = 4
        assert compute_complexity(node) == 4

    def test_nested_ifs(self):
        node = self._parse_func("""\
            def f(a, b):
                if a:
                    if b:
                        return True
                return False
        """)
        assert compute_complexity(node) == 3

    def test_ternary(self):
        node = self._parse_func("""\
            def f(x):
                return x if x > 0 else 0
        """)
        assert compute_complexity(node) == 2

    def test_assert(self):
        node = self._parse_func("""\
            def f(x):
                assert x > 0
                return x
        """)
        assert compute_complexity(node) == 2

    def test_complex_function(self):
        node = self._parse_func("""\
            def f(items, threshold):
                result = []
                for item in items:
                    if item > threshold:
                        if item % 2 == 0:
                            result.append(item)
                        else:
                            result.append(item * 2)
                    elif item == threshold:
                        result.append(0)
                return result
        """)
        # base(1) + for(1) + if(1) + if(1) + elif(1) = 5
        assert compute_complexity(node) == 5


# ── Coverage Mapping ───────────────────────────────────────────────


class TestCoverageMapping:
    def test_name_matching(self):
        sources = [SourceFile(
            path="src/auth.py",
            functions=[
                ExtractedFunction(name="login"),
                ExtractedFunction(name="logout"),
            ],
        )]
        tests = [TestFile(
            path="tests/test_auth.py",
            test_functions=["test_login"],
            imported_modules=["src.auth"],
            referenced_names=["login"],
        )]
        coverage = map_test_coverage(sources, tests)
        assert coverage.covered_count == 1
        assert coverage.uncovered_count == 1

        covered_names = {e.function_name for e in coverage.entries if e.covered}
        assert "login" in covered_names
        assert "logout" not in covered_names

    def test_import_and_reference(self):
        sources = [SourceFile(
            path="src/math.py",
            functions=[ExtractedFunction(name="add"), ExtractedFunction(name="multiply")],
        )]
        tests = [TestFile(
            path="tests/test_math.py",
            test_functions=["test_add", "test_multiply"],
            imported_modules=["src.math"],
            referenced_names=["add", "multiply"],
        )]
        coverage = map_test_coverage(sources, tests)
        assert coverage.coverage_ratio == 1.0

    def test_uncovered(self):
        sources = [SourceFile(
            path="src/utils.py",
            functions=[
                ExtractedFunction(name="parse"),
                ExtractedFunction(name="format"),
                ExtractedFunction(name="validate"),
            ],
        )]
        tests: list[TestFile] = []
        coverage = map_test_coverage(sources, tests)
        assert coverage.coverage_ratio == 0.0
        assert coverage.uncovered_count == 3

    def test_partial_coverage(self):
        sources = [SourceFile(
            path="src/handler.py",
            functions=[
                ExtractedFunction(name="handle_get"),
                ExtractedFunction(name="handle_post"),
            ],
        )]
        tests = [TestFile(
            path="tests/test_handler.py",
            test_functions=["test_handle_get"],
            imported_modules=["src.handler"],
            referenced_names=["handle_get"],
        )]
        coverage = map_test_coverage(sources, tests)
        assert coverage.coverage_ratio == 0.5

    def test_empty_sources(self):
        coverage = map_test_coverage([], [])
        assert coverage.coverage_ratio == 0.0
        assert coverage.total_functions == 0


# ── Security Detection ─────────────────────────────────────────────


class TestSecurityDetection:
    def test_admin_check(self):
        func = ExtractedFunction(
            name="grant_access",
            body_source=textwrap.dedent("""\
                def grant_access(user):
                    if user.is_admin:
                        return True
                    return False
            """),
            complexity=2,
        )
        findings = detect_security_patterns(func)
        assert len(findings) >= 1
        assert any("is_admin" in f.pattern_matched for f in findings)

    def test_role_assignment(self):
        func = ExtractedFunction(
            name="set_user_role",
            body_source=textwrap.dedent("""\
                def set_user_role(user, new_role):
                    if role == 'admin':
                        user.privilege = 'elevated'
            """),
            complexity=2,
        )
        findings = detect_security_patterns(func)
        assert len(findings) >= 1

    def test_permission_grant(self):
        func = ExtractedFunction(
            name="check_permission",
            body_source=textwrap.dedent("""\
                def check_permission(user, resource):
                    if user.permission in allowed:
                        return grant_access(user, resource)
            """),
            complexity=2,
        )
        findings = detect_security_patterns(func)
        assert len(findings) >= 1

    def test_no_false_positive_on_normal_if(self):
        func = ExtractedFunction(
            name="calculate",
            body_source=textwrap.dedent("""\
                def calculate(x, y):
                    if x > 0:
                        return x + y
                    return y
            """),
            complexity=2,
        )
        findings = detect_security_patterns(func)
        assert len(findings) == 0

    def test_security_call_in_conditional(self):
        func = ExtractedFunction(
            name="protected_action",
            body_source=textwrap.dedent("""\
                def protected_action(user):
                    if authenticate(user):
                        return do_action()
            """),
            complexity=2,
        )
        findings = detect_security_patterns(func)
        assert len(findings) >= 1
        assert any("authenticate" in f.pattern_matched for f in findings)

    def test_token_variable(self):
        func = ExtractedFunction(
            name="verify",
            body_source=textwrap.dedent("""\
                def verify(request):
                    if token:
                        return True
            """),
            complexity=2,
        )
        findings = detect_security_patterns(func)
        assert len(findings) >= 1

    def test_empty_body(self):
        func = ExtractedFunction(name="noop", body_source="")
        findings = detect_security_patterns(func)
        assert findings == []


# ── Full Analysis ──────────────────────────────────────────────────


class TestAnalyzeCodebase:
    def test_basic_analysis(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text(textwrap.dedent("""\
            def hello(name: str) -> str:
                return f"Hello {name}"

            def goodbye():
                if True:
                    return "bye"
        """))
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text(textwrap.dedent("""\
            from src.main import hello

            def test_hello():
                assert hello("world") == "Hello world"
        """))

        analysis = analyze_codebase(tmp_path)
        assert analysis.total_source_files >= 1
        assert analysis.total_functions >= 2
        assert analysis.total_test_files >= 1

    def test_security_findings_populated(self, tmp_path):
        (tmp_path / "auth.py").write_text(textwrap.dedent("""\
            def grant_admin(user):
                if user.is_admin:
                    return True
                return False
        """))
        analysis = analyze_codebase(tmp_path)
        assert len(analysis.security.findings) >= 1

    def test_empty_project(self, tmp_path):
        analysis = analyze_codebase(tmp_path)
        assert analysis.total_functions == 0
        assert analysis.total_source_files == 0
        assert analysis.total_test_files == 0
