"""Tests for CLI task/analyze/checklist commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pact.project import ProjectManager
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionNode,
    DecompositionTree,
    FieldSpec,
    FunctionContract,
    RunState,
    TestCase,
)


@pytest.fixture
def project_with_tree(tmp_path: Path) -> ProjectManager:
    """Create a project with a decomposition tree and contracts."""
    pm = ProjectManager(tmp_path / "test-proj")
    pm.init()

    # Create run state
    state = pm.create_run()
    pm.save_state(state)

    # Create tree
    tree = DecompositionTree(
        root_id="root",
        nodes={
            "root": DecompositionNode(
                component_id="root", name="Root", description="Root component",
                depth=0, children=["auth"],
            ),
            "auth": DecompositionNode(
                component_id="auth", name="Auth", description="Authentication",
                depth=1, parent_id="root",
            ),
        },
    )
    pm.save_tree(tree)

    # Create contracts
    contract = ComponentContract(
        component_id="auth", name="Auth",
        description="Authentication module that handles user login and token validation",
        functions=[
            FunctionContract(
                name="validate_token",
                description="Validates a JWT token",
                inputs=[FieldSpec(name="token", type_ref="str")],
                output_type="bool",
            ),
        ],
    )
    pm.save_contract(contract)

    root_contract = ComponentContract(
        component_id="root", name="Root",
        description="Root integration component that orchestrates authentication and authorization",
        dependencies=["auth"],
    )
    pm.save_contract(root_contract)

    # Create test suites
    suite = ContractTestSuite(
        component_id="auth", contract_version=1,
        test_cases=[
            TestCase(id="t1", description="Happy path", function="validate_token", category="happy_path"),
        ],
    )
    pm.save_test_suite(suite)

    return pm


@pytest.fixture
def empty_project(tmp_path: Path) -> ProjectManager:
    """Create a project with no decomposition."""
    pm = ProjectManager(tmp_path / "empty-proj")
    pm.init()
    return pm


# ── pact tasks ──────────────────────────────────────────────────────


class TestCmdTasks:
    def test_no_tree_shows_message(self, empty_project: ProjectManager, capsys):
        from pact.cli import cmd_tasks
        import argparse
        args = argparse.Namespace(
            project_dir=str(empty_project.project_dir),
            regenerate=False, json_output=False,
            phase=None, component=None, complete=None,
        )
        cmd_tasks(args)
        out = capsys.readouterr().out
        assert "No decomposition tree" in out

    def test_generates_task_list(self, project_with_tree: ProjectManager, capsys):
        from pact.cli import cmd_tasks
        import argparse
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            regenerate=False, json_output=False,
            phase=None, component=None, complete=None,
        )
        cmd_tasks(args)
        out = capsys.readouterr().out
        assert "# TASKS" in out
        assert "Phase: Setup" in out

    def test_json_output(self, project_with_tree: ProjectManager, capsys):
        from pact.cli import cmd_tasks
        import argparse
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            regenerate=False, json_output=True,
            phase=None, component=None, complete=None,
        )
        cmd_tasks(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert all("id" in t for t in data)

    def test_filter_by_phase(self, project_with_tree: ProjectManager, capsys):
        from pact.cli import cmd_tasks
        import argparse
        # First generate
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            regenerate=False, json_output=False,
            phase="setup", component=None, complete=None,
        )
        cmd_tasks(args)
        out = capsys.readouterr().out
        assert "2 task(s)" in out

    def test_filter_by_component(self, project_with_tree: ProjectManager, capsys):
        from pact.cli import cmd_tasks
        import argparse
        # Generate first
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            regenerate=False, json_output=False,
            phase=None, component=None, complete=None,
        )
        cmd_tasks(args)
        capsys.readouterr()  # Clear

        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            regenerate=False, json_output=False,
            phase=None, component="auth", complete=None,
        )
        cmd_tasks(args)
        out = capsys.readouterr().out
        assert "auth" in out
        assert "task(s)" in out

    def test_complete_task(self, project_with_tree: ProjectManager, capsys):
        from pact.cli import cmd_tasks
        import argparse
        # Generate first
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            regenerate=False, json_output=False,
            phase=None, component=None, complete=None,
        )
        cmd_tasks(args)
        capsys.readouterr()

        # Complete T001
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            regenerate=False, json_output=False,
            phase=None, component=None, complete="T001",
        )
        cmd_tasks(args)
        out = capsys.readouterr().out
        assert "Marked T001 as completed" in out

    def test_complete_nonexistent_task(self, project_with_tree: ProjectManager, capsys):
        from pact.cli import cmd_tasks
        import argparse
        # Generate first
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            regenerate=False, json_output=False,
            phase=None, component=None, complete=None,
        )
        cmd_tasks(args)
        capsys.readouterr()

        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            regenerate=False, json_output=False,
            phase=None, component=None, complete="T999",
        )
        cmd_tasks(args)
        out = capsys.readouterr().out
        assert "Task not found" in out

    def test_regenerate_flag(self, project_with_tree: ProjectManager, capsys):
        from pact.cli import cmd_tasks
        import argparse
        # Generate first time
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            regenerate=False, json_output=False,
            phase=None, component=None, complete=None,
        )
        cmd_tasks(args)
        capsys.readouterr()

        # Regenerate
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            regenerate=True, json_output=False,
            phase=None, component=None, complete=None,
        )
        cmd_tasks(args)
        out = capsys.readouterr().out
        assert "# TASKS" in out

    def test_invalid_phase(self, project_with_tree: ProjectManager, capsys):
        from pact.cli import cmd_tasks
        import argparse
        # Generate first
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            regenerate=False, json_output=False,
            phase=None, component=None, complete=None,
        )
        cmd_tasks(args)
        capsys.readouterr()

        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            regenerate=False, json_output=False,
            phase="bogus", component=None, complete=None,
        )
        cmd_tasks(args)
        out = capsys.readouterr().out
        assert "Unknown phase" in out


# ── pact analyze ────────────────────────────────────────────────────


class TestCmdAnalyze:
    def test_no_tree_shows_message(self, empty_project: ProjectManager, capsys):
        from pact.cli import cmd_analyze
        import argparse
        args = argparse.Namespace(
            project_dir=str(empty_project.project_dir),
            json_output=False,
        )
        cmd_analyze(args)
        out = capsys.readouterr().out
        assert "No decomposition tree" in out

    def test_renders_findings(self, project_with_tree: ProjectManager, capsys):
        from pact.cli import cmd_analyze
        import argparse
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            json_output=False,
        )
        cmd_analyze(args)
        out = capsys.readouterr().out
        assert "Cross-Artifact Analysis" in out

    def test_json_output(self, project_with_tree: ProjectManager, capsys):
        from pact.cli import cmd_analyze
        import argparse
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            json_output=True,
        )
        cmd_analyze(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "project_id" in data
        assert "findings" in data

    def test_saves_analysis(self, project_with_tree: ProjectManager):
        from pact.cli import cmd_analyze
        import argparse
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            json_output=False,
        )
        cmd_analyze(args)
        assert project_with_tree.analysis_path.exists()


# ── pact checklist ──────────────────────────────────────────────────


class TestCmdChecklist:
    def test_no_tree_shows_message(self, empty_project: ProjectManager, capsys):
        from pact.cli import cmd_checklist
        import argparse
        args = argparse.Namespace(
            project_dir=str(empty_project.project_dir),
            json_output=False,
        )
        cmd_checklist(args)
        out = capsys.readouterr().out
        assert "No decomposition tree" in out

    def test_renders_questions(self, project_with_tree: ProjectManager, capsys):
        from pact.cli import cmd_checklist
        import argparse
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            json_output=False,
        )
        cmd_checklist(args)
        out = capsys.readouterr().out
        assert "Requirements Checklist" in out

    def test_json_output(self, project_with_tree: ProjectManager, capsys):
        from pact.cli import cmd_checklist
        import argparse
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            json_output=True,
        )
        cmd_checklist(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "project_id" in data
        assert "items" in data

    def test_saves_checklist(self, project_with_tree: ProjectManager):
        from pact.cli import cmd_checklist
        import argparse
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            json_output=False,
        )
        cmd_checklist(args)
        assert project_with_tree.checklist_path.exists()


# ── pact export-tasks ───────────────────────────────────────────────


class TestCmdExportTasks:
    def test_no_task_list_shows_message(self, empty_project: ProjectManager, capsys):
        from pact.cli import cmd_export_tasks
        import argparse
        args = argparse.Namespace(project_dir=str(empty_project.project_dir))
        cmd_export_tasks(args)
        out = capsys.readouterr().out
        assert "No task list found" in out

    def test_exports_markdown(self, project_with_tree: ProjectManager, capsys):
        from pact.cli import cmd_tasks, cmd_export_tasks
        import argparse

        # Generate task list first
        args = argparse.Namespace(
            project_dir=str(project_with_tree.project_dir),
            regenerate=False, json_output=False,
            phase=None, component=None, complete=None,
        )
        cmd_tasks(args)
        capsys.readouterr()

        # Export
        args = argparse.Namespace(project_dir=str(project_with_tree.project_dir))
        cmd_export_tasks(args)
        out = capsys.readouterr().out
        assert "Exported:" in out
        assert project_with_tree.tasks_md_path.exists()
