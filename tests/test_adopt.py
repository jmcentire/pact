"""Tests for codebase adoption pipeline."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pact.adopt import (
    AdoptionResult,
    adopt_codebase,
    build_decomposition_tree,
    link_existing_implementations,
)
from pact.codebase_analyzer import analyze_codebase
from pact.project import ProjectManager
from pact.schemas import DecompositionTree
from pact.schemas_testgen import (
    CodebaseAnalysis,
    ExtractedFunction,
    SourceFile,
)


# ── Tree Construction ──────────────────────────────────────────────


class TestBuildDecompositionTree:
    def test_single_file(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="main.py", functions=[
                    ExtractedFunction(name="hello"),
                ]),
            ],
        )
        tree = build_decomposition_tree(analysis)
        assert "root" in tree.nodes
        assert "main" in tree.nodes
        assert tree.nodes["main"].parent_id == "root"

    def test_nested_packages(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="src/auth/login.py", functions=[
                    ExtractedFunction(name="authenticate"),
                ]),
                SourceFile(path="src/auth/roles.py", functions=[
                    ExtractedFunction(name="check_role"),
                ]),
                SourceFile(path="src/utils.py", functions=[
                    ExtractedFunction(name="helper"),
                ]),
            ],
        )
        tree = build_decomposition_tree(analysis)

        # Should have root, src, src_auth, and leaf nodes
        assert "root" in tree.nodes
        assert "src" in tree.nodes
        assert "src_auth" in tree.nodes
        assert "src_auth_login" in tree.nodes
        assert "src_auth_roles" in tree.nodes
        assert "src_utils" in tree.nodes

        # Check hierarchy
        assert "src" in tree.nodes["root"].children
        assert "src_auth" in tree.nodes["src"].children
        assert "src_auth_login" in tree.nodes["src_auth"].children

    def test_empty_files_skipped(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="empty.py", functions=[]),
                SourceFile(path="real.py", functions=[
                    ExtractedFunction(name="work"),
                ]),
            ],
        )
        tree = build_decomposition_tree(analysis)
        assert "real" in tree.nodes
        assert "empty" not in tree.nodes

    def test_leaves(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="a.py", functions=[ExtractedFunction(name="f1")]),
                SourceFile(path="b.py", functions=[ExtractedFunction(name="f2")]),
            ],
        )
        tree = build_decomposition_tree(analysis)
        leaves = tree.leaves()
        leaf_ids = {n.component_id for n in leaves}
        assert "a" in leaf_ids
        assert "b" in leaf_ids
        assert "root" not in leaf_ids

    def test_component_id_from_path(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="src/core/engine.py", functions=[
                    ExtractedFunction(name="run"),
                ]),
            ],
        )
        tree = build_decomposition_tree(analysis)
        assert "src_core_engine" in tree.nodes

    def test_description_includes_function_names(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="math.py", functions=[
                    ExtractedFunction(name="add"),
                    ExtractedFunction(name="multiply"),
                ]),
            ],
        )
        tree = build_decomposition_tree(analysis)
        desc = tree.nodes["math"].description
        assert "add" in desc
        assert "multiply" in desc


# ── Implementation Linking ─────────────────────────────────────────


class TestLinkExistingImplementations:
    def test_copies_source(self, tmp_path):
        # Create source file
        (tmp_path / "main.py").write_text("def hello(): pass")

        analysis = CodebaseAnalysis(
            root_path=str(tmp_path),
            source_files=[
                SourceFile(path="main.py", functions=[ExtractedFunction(name="hello")]),
            ],
        )
        tree = build_decomposition_tree(analysis)
        project = ProjectManager(tmp_path)
        project.init()

        link_existing_implementations(project, analysis, tree)

        # Check implementation was created
        impl_src = tmp_path / ".pact" / "implementations" / "main" / "src" / "main.py"
        assert impl_src.exists()
        assert "hello" in impl_src.read_text()

        # Check metadata
        meta_path = tmp_path / ".pact" / "implementations" / "main" / "metadata.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["adopted"] is True
        assert meta["source_path"] == "main.py"


# ── Adoption Result ────────────────────────────────────────────────


class TestAdoptionResult:
    def test_summary_dry_run(self):
        r = AdoptionResult(components=5, total_functions=20, coverage_before=0.3, security_findings=2)
        r.dry_run = True
        text = r.summary()
        assert "Dry Run" in text
        assert "Components: 5" in text
        assert "30%" in text

    def test_summary_full_run(self):
        r = AdoptionResult(components=3, total_functions=10, coverage_before=0.5, security_findings=1)
        r.contracts_generated = 3
        r.tests_generated = 3
        r.total_cost_usd = 2.50
        text = r.summary()
        assert "Complete" in text
        assert "Contracts generated: 3" in text
        assert "$2.5000" in text
        assert "pact daemon" in text


# ── Dry Run Integration ───────────────────────────────────────────


class TestAdoptDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_creates_tree(self, tmp_path):
        (tmp_path / "main.py").write_text(textwrap.dedent("""\
            def hello(name: str) -> str:
                return f"Hello {name}"
        """))

        result = await adopt_codebase(tmp_path, dry_run=True)
        assert result.dry_run is True
        assert result.components >= 1
        assert result.total_functions >= 1
        assert result.contracts_generated == 0

        # Check project state was created
        assert (tmp_path / ".pact" / "state.json").exists()
        assert (tmp_path / ".pact" / "decomposition" / "tree.json").exists()
        assert (tmp_path / "task.md").exists()

    @pytest.mark.asyncio
    async def test_dry_run_no_llm(self, tmp_path):
        (tmp_path / "app.py").write_text("def run(): pass")
        result = await adopt_codebase(tmp_path, dry_run=True)
        assert result.total_cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_dry_run_state_is_paused(self, tmp_path):
        (tmp_path / "app.py").write_text("def run(): pass")
        await adopt_codebase(tmp_path, dry_run=True)

        project = ProjectManager(tmp_path)
        state = project.load_state()
        assert state.status == "paused"

    @pytest.mark.asyncio
    async def test_dry_run_security_audit_written(self, tmp_path):
        (tmp_path / "auth.py").write_text(textwrap.dedent("""\
            def check_admin(user):
                if user.is_admin:
                    return True
        """))
        result = await adopt_codebase(tmp_path, dry_run=True)
        assert (tmp_path / ".pact" / "test-gen" / "security_audit.md").exists()

    @pytest.mark.asyncio
    async def test_empty_project(self, tmp_path):
        result = await adopt_codebase(tmp_path, dry_run=True)
        assert result.components == 0
        assert result.total_functions == 0
