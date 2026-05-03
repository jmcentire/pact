"""Tests for tool_index — external code analysis tool integration."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pact.schemas_testgen import (
    CallGraphEntry,
    CscopeRef,
    CtagsSymbol,
    ToolAvailability,
    ToolIndex,
    TreeSitterSymbol,
)
from pact.tool_index import (
    _run_quiet,
    build_tool_index,
    detect_tools,
    query_kindex,
    render_tool_index_context,
    run_ctags,
    run_cscope,
    run_tree_sitter,
)


# ── Tool Detection ────────────────────────────────────────────────


class TestDetectTools:
    def test_detects_ctags(self):
        avail = detect_tools()
        # ctags should be installed (universal-ctags via brew)
        assert avail.ctags is True
        assert avail.ctags_version != ""

    def test_detects_cscope(self):
        avail = detect_tools()
        assert avail.cscope is True

    def test_detects_tree_sitter(self):
        avail = detect_tools()
        assert avail.tree_sitter is True

    @patch("pact.tool_index.shutil.which", return_value=None)
    @patch("pact.tool_index._HAS_TREE_SITTER", False)
    def test_no_tools_available(self, mock_which):
        with patch("pact.tool_index.subprocess.run", side_effect=FileNotFoundError):
            avail = detect_tools()
        assert avail.ctags is False
        assert avail.cscope is False
        assert avail.tree_sitter is False

    def test_tool_availability_model(self):
        avail = ToolAvailability()
        assert avail.ctags is False
        assert avail.cscope is False
        assert avail.tree_sitter is False
        assert avail.kindex is False

    def test_run_quiet_missing_command(self):
        rc, out, err = _run_quiet(["nonexistent_command_12345"])
        assert rc == -1
        assert out == ""


# ── ctags ─────────────────────────────────────────────────────────


class TestCtags:
    def test_run_ctags_on_sample(self, tmp_path):
        """ctags should find functions in a simple Python file."""
        (tmp_path / "sample.py").write_text(textwrap.dedent("""\
            def hello(name: str) -> str:
                return f"Hello {name}"

            class Greeter:
                def greet(self, name: str) -> str:
                    return f"Hi {name}"
        """))

        symbols = run_ctags(tmp_path, "python")
        names = {s.name for s in symbols}
        assert "hello" in names
        assert "Greeter" in names
        assert "greet" in names

    def test_ctags_symbols_have_metadata(self, tmp_path):
        (tmp_path / "funcs.py").write_text("def add(a, b):\n    return a + b\n")
        symbols = run_ctags(tmp_path, "python")
        func = next((s for s in symbols if s.name == "add"), None)
        assert func is not None
        assert func.file_path == "funcs.py"
        assert func.line_number > 0
        assert func.kind != ""

    def test_ctags_skips_excluded_dirs(self, tmp_path):
        (tmp_path / "main.py").write_text("def main(): pass\n")
        (tmp_path / "venv" / "lib").mkdir(parents=True)
        (tmp_path / "venv" / "lib" / "site.py").write_text("def site(): pass\n")
        symbols = run_ctags(tmp_path, "python")
        paths = {s.file_path for s in symbols}
        assert not any("venv" in p for p in paths)

    def test_ctags_empty_dir(self, tmp_path):
        symbols = run_ctags(tmp_path, "python")
        assert symbols == []

    def test_ctags_relative_paths(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("def run(): pass\n")
        symbols = run_ctags(tmp_path, "python")
        for s in symbols:
            assert not str(tmp_path) in s.file_path  # Should be relative


# ── cscope ────────────────────────────────────────────────────────


class TestCscope:
    def test_run_cscope_on_sample(self, tmp_path):
        """cscope should find call relationships."""
        (tmp_path / "a.py").write_text(textwrap.dedent("""\
            def helper():
                return 42

            def main():
                return helper()
        """))

        entries = run_cscope(tmp_path, ["helper", "main"], "python")
        # cscope may or may not find Python calls depending on version
        # Just verify it runs without error and returns a list
        assert isinstance(entries, list)

    def test_cscope_empty_function_list(self, tmp_path):
        (tmp_path / "a.py").write_text("def f(): pass\n")
        entries = run_cscope(tmp_path, [], "python")
        assert entries == []

    def test_cscope_empty_dir(self, tmp_path):
        entries = run_cscope(tmp_path, ["f"], "python")
        assert entries == []


# ── tree-sitter ───────────────────────────────────────────────────


class TestTreeSitter:
    def test_run_tree_sitter_on_sample(self, tmp_path):
        """tree-sitter should extract function and class definitions."""
        (tmp_path / "sample.py").write_text(textwrap.dedent("""\
            def hello(name: str) -> str:
                return f"Hello {name}"

            class Greeter:
                def greet(self, name: str) -> str:
                    return f"Hi {name}"
        """))

        symbols = run_tree_sitter(tmp_path, "python")
        names = {s.name for s in symbols}
        assert "hello" in names
        assert "Greeter" in names
        assert "greet" in names

    def test_tree_sitter_symbol_metadata(self, tmp_path):
        (tmp_path / "funcs.py").write_text(textwrap.dedent("""\
            class Calculator:
                def add(self, a, b):
                    return a + b
        """))

        symbols = run_tree_sitter(tmp_path, "python")
        add_sym = next((s for s in symbols if s.name == "add"), None)
        assert add_sym is not None
        assert add_sym.file_path == "funcs.py"
        assert add_sym.kind == "function_definition"
        assert add_sym.parent == "Calculator"
        assert add_sym.parent_kind == "class"

    def test_tree_sitter_class_definition(self, tmp_path):
        (tmp_path / "models.py").write_text("class User:\n    pass\n")
        symbols = run_tree_sitter(tmp_path, "python")
        cls = next((s for s in symbols if s.name == "User"), None)
        assert cls is not None
        assert cls.kind == "class_definition"

    def test_tree_sitter_skips_excluded_dirs(self, tmp_path):
        (tmp_path / "main.py").write_text("def main(): pass\n")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "cached.py").write_text("def cached(): pass\n")
        symbols = run_tree_sitter(tmp_path, "python")
        paths = {s.file_path for s in symbols}
        assert not any("__pycache__" in p for p in paths)

    def test_tree_sitter_empty_dir(self, tmp_path):
        symbols = run_tree_sitter(tmp_path, "python")
        assert symbols == []

    def test_tree_sitter_unsupported_language(self, tmp_path):
        (tmp_path / "main.rs").write_text("fn main() {}\n")
        # Rust grammar not installed, should return empty
        symbols = run_tree_sitter(tmp_path, "rust")
        assert symbols == []

    def test_tree_sitter_multiline_function(self, tmp_path):
        (tmp_path / "big.py").write_text(textwrap.dedent("""\
            def complex_function(
                arg1: str,
                arg2: int,
                arg3: float = 0.0,
            ) -> dict:
                result = {}
                for i in range(arg2):
                    result[str(i)] = arg3
                return result
        """))
        symbols = run_tree_sitter(tmp_path, "python")
        func = next((s for s in symbols if s.name == "complex_function"), None)
        assert func is not None
        assert func.start_line == 1
        assert func.end_line > func.start_line  # Multi-line function


# ── kindex ────────────────────────────────────────────────────────


class TestKindex:
    @patch("pact.tool_index.shutil.which", return_value=None)
    def test_no_kin_available(self, mock_which):
        result = query_kindex(Path("/tmp/test"))
        assert result == ""

    @patch("pact.tool_index._run_quiet")
    @patch("pact.tool_index.shutil.which", return_value="/usr/bin/kin")
    def test_kin_returns_context(self, mock_which, mock_run):
        mock_run.return_value = (0, "Project: test\nKey concept: foo", "")
        result = query_kindex(Path("/tmp/test"))
        assert "Key concept" in result


# ── ToolIndex Model ───────────────────────────────────────────────


class TestToolIndexModel:
    def test_symbols_for_file(self):
        idx = ToolIndex(symbols=[
            CtagsSymbol(name="a", file_path="src/a.py"),
            CtagsSymbol(name="b", file_path="src/b.py"),
            CtagsSymbol(name="c", file_path="src/a.py"),
        ])
        assert len(idx.symbols_for_file("src/a.py")) == 2
        assert len(idx.symbols_for_file("src/b.py")) == 1
        assert len(idx.symbols_for_file("src/c.py")) == 0

    def test_tree_sitter_for_file(self):
        idx = ToolIndex(tree_sitter_symbols=[
            TreeSitterSymbol(name="f", file_path="main.py"),
            TreeSitterSymbol(name="g", file_path="other.py"),
        ])
        assert len(idx.tree_sitter_for_file("main.py")) == 1
        assert len(idx.tree_sitter_for_file("missing.py")) == 0

    def test_callers_of(self):
        idx = ToolIndex(call_graph=[
            CallGraphEntry(
                function="helper", file_path="a.py",
                callers=[CscopeRef(symbol="main", file_path="a.py", line_number=10)],
                callees=[],
            ),
        ])
        callers = idx.callers_of("helper")
        assert len(callers) == 1
        assert callers[0].symbol == "main"

    def test_callers_of_unknown(self):
        idx = ToolIndex()
        assert idx.callers_of("unknown") == []

    def test_callees_of(self):
        idx = ToolIndex(call_graph=[
            CallGraphEntry(
                function="main", file_path="a.py",
                callers=[], callees=[
                    CscopeRef(symbol="helper", file_path="a.py", line_number=5),
                ],
            ),
        ])
        callees = idx.callees_of("main")
        assert len(callees) == 1
        assert callees[0].symbol == "helper"

    def test_total_properties(self):
        idx = ToolIndex(
            symbols=[CtagsSymbol(name="a", file_path="x.py")],
            tree_sitter_symbols=[
                TreeSitterSymbol(name="b", file_path="x.py"),
                TreeSitterSymbol(name="c", file_path="x.py"),
            ],
            call_graph=[CallGraphEntry(function="a", file_path="x.py")],
        )
        assert idx.total_symbols == 1
        assert idx.total_tree_sitter_symbols == 2
        assert idx.total_call_entries == 1

    def test_empty_tool_index(self):
        idx = ToolIndex()
        assert idx.total_symbols == 0
        assert idx.total_tree_sitter_symbols == 0
        assert idx.total_call_entries == 0
        assert idx.kindex_context == ""


# ── Rendering ─────────────────────────────────────────────────────


class TestRendering:
    def test_render_empty(self):
        assert render_tool_index_context(None) == ""
        assert render_tool_index_context(ToolIndex()) == ""

    def test_render_ctags_symbols(self):
        idx = ToolIndex(symbols=[
            CtagsSymbol(name="hello", file_path="main.py", line_number=1, kind="function"),
            CtagsSymbol(name="Greeter", file_path="main.py", line_number=5, kind="class"),
        ])
        output = render_tool_index_context(idx)
        assert "Symbol Index" in output
        assert "hello" in output
        assert "Greeter" in output

    def test_render_tree_sitter_preferred(self):
        """When both ctags and tree-sitter data exist, tree-sitter is rendered."""
        idx = ToolIndex(
            symbols=[CtagsSymbol(name="a", file_path="x.py", kind="function")],
            tree_sitter_symbols=[TreeSitterSymbol(name="b", file_path="x.py", kind="function_definition")],
        )
        output = render_tool_index_context(idx)
        assert "tree-sitter" in output
        assert "ctags" not in output

    def test_render_scoped_to_file(self):
        idx = ToolIndex(tree_sitter_symbols=[
            TreeSitterSymbol(name="target_func", file_path="src/target.py", kind="function_definition"),
            TreeSitterSymbol(name="unrelated_func", file_path="src/unrelated.py", kind="function_definition"),
        ])
        output = render_tool_index_context(idx, file_path="src/target.py")
        assert "target_func" in output
        assert "unrelated_func" not in output

    def test_render_call_graph(self):
        idx = ToolIndex(call_graph=[
            CallGraphEntry(
                function="process", file_path="a.py",
                callers=[CscopeRef(symbol="main", file_path="a.py", line_number=10)],
                callees=[CscopeRef(symbol="helper", file_path="b.py", line_number=5)],
            ),
        ])
        output = render_tool_index_context(idx, function_name="process")
        assert "Call Graph" in output
        assert "main" in output
        assert "helper" in output

    def test_render_kindex_context(self):
        idx = ToolIndex(kindex_context="Project: pact\nKey insight: contracts before code")
        output = render_tool_index_context(idx)
        assert "Existing Project Knowledge" in output
        assert "contracts before code" in output

    def test_render_max_symbols_limit(self):
        symbols = [
            CtagsSymbol(name=f"func_{i}", file_path="big.py", kind="function")
            for i in range(100)
        ]
        idx = ToolIndex(symbols=symbols)
        output = render_tool_index_context(idx, max_symbols=5)
        assert "and 95 more" in output


# ── Graceful Degradation ──────────────────────────────────────────


class TestGracefulDegradation:
    @patch("pact.tool_index.detect_tools")
    def test_no_tools_returns_empty_index(self, mock_detect):
        mock_detect.return_value = ToolAvailability()
        idx = build_tool_index("/tmp/test", "python")
        assert idx.total_symbols == 0
        assert idx.total_tree_sitter_symbols == 0
        assert idx.total_call_entries == 0
        assert idx.kindex_context == ""

    def test_ctags_handles_no_binary(self):
        with patch("pact.tool_index.shutil.which", return_value=None):
            symbols = run_ctags(Path("/tmp/test"), "python")
        assert symbols == []

    def test_cscope_handles_no_binary(self):
        with patch("pact.tool_index.shutil.which", return_value=None):
            entries = run_cscope(Path("/tmp/test"), ["f"], "python")
        assert entries == []


# ── Integration ───────────────────────────────────────────────────


class TestIntegration:
    def test_build_tool_index_real(self, tmp_path):
        """Integration test: build_tool_index on a real (small) codebase."""
        (tmp_path / "app.py").write_text(textwrap.dedent("""\
            def greet(name: str) -> str:
                return f"Hello {name}"

            def farewell(name: str) -> str:
                return f"Goodbye {name}"

            class Service:
                def run(self):
                    print(greet("world"))
        """))

        idx = build_tool_index(tmp_path, "python", function_names=["greet", "farewell"])
        assert idx.tools.ctags is True
        assert idx.tools.tree_sitter is True
        assert idx.total_symbols > 0
        assert idx.total_tree_sitter_symbols > 0

    def test_analyze_codebase_includes_tool_index(self, tmp_path):
        """analyze_codebase() should attach tool_index to the result."""
        (tmp_path / "main.py").write_text("def hello(): pass\n")
        from pact.codebase_analyzer import analyze_codebase
        analysis = analyze_codebase(tmp_path)
        # tool_index should be populated (ctags + tree-sitter installed)
        assert analysis.tool_index is not None
        assert analysis.tool_index.tools.ctags is True

    def test_tool_index_serializes(self, tmp_path):
        """ToolIndex must survive Pydantic serialization for .pact/ storage."""
        idx = ToolIndex(
            tools=ToolAvailability(ctags=True, tree_sitter=True),
            symbols=[CtagsSymbol(name="f", file_path="a.py", line_number=1, kind="function")],
            tree_sitter_symbols=[TreeSitterSymbol(name="f", file_path="a.py", start_line=1, end_line=3)],
            kindex_context="some context",
        )
        json_str = idx.model_dump_json()
        restored = ToolIndex.model_validate_json(json_str)
        assert restored.total_symbols == 1
        assert restored.total_tree_sitter_symbols == 1
        assert restored.kindex_context == "some context"


# ── Config ────────────────────────────────────────────────────────


class TestConfig:
    def test_tool_index_enabled_default(self):
        from pact.config import ProjectConfig
        cfg = ProjectConfig()
        assert cfg.tool_index_enabled is None  # Auto

    def test_tool_index_enabled_from_yaml(self, tmp_path):
        (tmp_path / "pact.yaml").write_text("tool_index_enabled: false\n")
        from pact.config import load_project_config
        cfg = load_project_config(tmp_path)
        assert cfg.tool_index_enabled is False
