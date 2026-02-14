"""Tests for tree, cost, doctor, clean, diff CLI commands."""

from __future__ import annotations

import json
import os
import shutil
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from pact.budget import BudgetTracker
from pact.config import GlobalConfig, ProjectConfig
from pact.project import ProjectManager
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionNode,
    DecompositionTree,
    RunState,
    TestResults,
)


def _make_tree() -> DecompositionTree:
    """Create a simple test tree: root -> [child_a, child_b]."""
    return DecompositionTree(
        root_id="root",
        nodes={
            "root": DecompositionNode(
                component_id="root",
                name="Root Component",
                description="Top-level",
                depth=0,
                children=["child_a", "child_b"],
            ),
            "child_a": DecompositionNode(
                component_id="child_a",
                name="Child A",
                description="First child",
                depth=1,
                parent_id="root",
                implementation_status="tested",
                test_results=TestResults(total=5, passed=5),
            ),
            "child_b": DecompositionNode(
                component_id="child_b",
                name="Child B",
                description="Second child",
                depth=1,
                parent_id="root",
                implementation_status="failed",
                test_results=TestResults(total=7, passed=3, failed=4),
            ),
        },
    )


def _setup_project(tmp_path: Path) -> ProjectManager:
    """Set up a project with tree and state."""
    project = ProjectManager(tmp_path)
    project.init()
    tree = _make_tree()
    project.save_tree(tree)
    state = project.create_run()
    state.total_cost_usd = 2.50
    project.save_state(state)
    return project


class TestCmdTree:
    def test_tree_no_decomposition(self, tmp_path: Path, capsys):
        from pact.cli import cmd_tree
        import argparse

        project = ProjectManager(tmp_path)
        project.init()
        args = argparse.Namespace(project_dir=str(tmp_path), json_output=False, no_cost=False)
        cmd_tree(args)
        captured = capsys.readouterr()
        assert "No decomposition tree found" in captured.out

    def test_tree_renders_nodes(self, tmp_path: Path, capsys):
        from pact.cli import cmd_tree
        import argparse

        _setup_project(tmp_path)
        args = argparse.Namespace(project_dir=str(tmp_path), json_output=False, no_cost=True)
        cmd_tree(args)
        captured = capsys.readouterr()
        assert "Root Component" in captured.out
        assert "Child A" in captured.out
        assert "Child B" in captured.out
        assert "[+]" in captured.out  # tested
        assert "[X]" in captured.out  # failed

    def test_tree_shows_test_results(self, tmp_path: Path, capsys):
        from pact.cli import cmd_tree
        import argparse

        _setup_project(tmp_path)
        args = argparse.Namespace(project_dir=str(tmp_path), json_output=False, no_cost=True)
        cmd_tree(args)
        captured = capsys.readouterr()
        assert "5/5 tests passed" in captured.out
        assert "3/7 tests passed" in captured.out

    def test_tree_json_output(self, tmp_path: Path, capsys):
        from pact.cli import cmd_tree
        import argparse

        _setup_project(tmp_path)
        args = argparse.Namespace(project_dir=str(tmp_path), json_output=True, no_cost=False)
        cmd_tree(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["root_id"] == "root"
        assert len(data["nodes"]) == 3

    def test_tree_status_icons(self, tmp_path: Path, capsys):
        from pact.cli import cmd_tree
        import argparse

        project = ProjectManager(tmp_path)
        project.init()
        tree = DecompositionTree(
            root_id="r",
            nodes={
                "r": DecompositionNode(
                    component_id="r", name="R", description="root",
                    implementation_status="pending",
                ),
            },
        )
        project.save_tree(tree)
        args = argparse.Namespace(project_dir=str(tmp_path), json_output=False, no_cost=True)
        cmd_tree(args)
        captured = capsys.readouterr()
        assert "[ ]" in captured.out


class TestCmdCost:
    def test_cost_no_tree(self, tmp_path: Path, capsys):
        from pact.cli import cmd_cost
        import argparse

        project = ProjectManager(tmp_path)
        project.init()
        args = argparse.Namespace(project_dir=str(tmp_path), detailed=False)
        cmd_cost(args)
        captured = capsys.readouterr()
        assert "No decomposition tree found" in captured.out

    def test_cost_with_tree(self, tmp_path: Path, capsys):
        from pact.cli import cmd_cost
        import argparse

        _setup_project(tmp_path)
        args = argparse.Namespace(project_dir=str(tmp_path), detailed=False)
        cmd_cost(args)
        captured = capsys.readouterr()
        assert "total components" in captured.out
        assert "Estimated remaining:" in captured.out
        assert "Budget remaining:" in captured.out

    def test_cost_detailed(self, tmp_path: Path, capsys):
        from pact.cli import cmd_cost
        import argparse

        _setup_project(tmp_path)
        args = argparse.Namespace(project_dir=str(tmp_path), detailed=True)
        cmd_cost(args)
        captured = capsys.readouterr()
        assert "Per-Component Estimates:" in captured.out
        assert "child_a" in captured.out
        assert "child_b" in captured.out

    def test_cost_shows_categories(self, tmp_path: Path, capsys):
        from pact.cli import cmd_cost
        import argparse

        _setup_project(tmp_path)
        args = argparse.Namespace(project_dir=str(tmp_path), detailed=False)
        cmd_cost(args)
        captured = capsys.readouterr()
        # Should show tested and failed categories
        assert "tested" in captured.out or "complete" in captured.out
        assert "failed" in captured.out


class TestCmdDoctor:
    def test_doctor_no_project(self, capsys):
        from pact.cli import cmd_doctor
        import argparse

        args = argparse.Namespace(project_dir=None)
        cmd_doctor(args)
        captured = capsys.readouterr()
        assert "ANTHROPIC_API_KEY" in captured.out
        assert "Model" in captured.out

    def test_doctor_with_api_key(self, capsys):
        from pact.cli import cmd_doctor
        import argparse

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test12345678"}):
            args = argparse.Namespace(project_dir=None)
            cmd_doctor(args)
            captured = capsys.readouterr()
            assert "[OK]" in captured.out
            assert "...12345678" in captured.out

    def test_doctor_without_api_key(self, capsys):
        from pact.cli import cmd_doctor
        import argparse

        with patch.dict(os.environ, {}, clear=True):
            # Remove ANTHROPIC_API_KEY if set
            env = os.environ.copy()
            env.pop("ANTHROPIC_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                args = argparse.Namespace(project_dir=None)
                cmd_doctor(args)
                captured = capsys.readouterr()
                assert "[FAIL]" in captured.out

    def test_doctor_with_project(self, tmp_path: Path, capsys):
        from pact.cli import cmd_doctor
        import argparse

        _setup_project(tmp_path)
        args = argparse.Namespace(project_dir=str(tmp_path))
        cmd_doctor(args)
        captured = capsys.readouterr()
        assert "Budget" in captured.out
        assert "State" in captured.out
        assert "Decomposition" in captured.out

    def test_doctor_shows_integration_status(self, capsys):
        from pact.cli import cmd_doctor
        import argparse

        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("CF_SLACK_WEBHOOK", None)
            env.pop("LINEAR_API_KEY", None)
            env.pop("PACT_SLACK_BOT_TOKEN", None)
            with patch.dict(os.environ, env, clear=True):
                args = argparse.Namespace(project_dir=None)
                cmd_doctor(args)
                captured = capsys.readouterr()
                assert "Slack" in captured.out
                assert "Linear" in captured.out

    def test_doctor_shows_bot_token_status(self, capsys):
        from pact.cli import cmd_doctor
        import argparse

        with patch.dict(os.environ, {"PACT_SLACK_BOT_TOKEN": "xoxb-test-123"}, clear=False):
            args = argparse.Namespace(project_dir=None)
            cmd_doctor(args)
            captured = capsys.readouterr()
            assert "read+write" in captured.out
            assert "bot token" in captured.out

    def test_doctor_shows_webhook_only(self, capsys):
        from pact.cli import cmd_doctor
        import argparse

        env = os.environ.copy()
        env.pop("PACT_SLACK_BOT_TOKEN", None)
        env["CF_SLACK_WEBHOOK"] = "https://hooks.slack.com/test"
        with patch.dict(os.environ, env, clear=True):
            args = argparse.Namespace(project_dir=None)
            cmd_doctor(args)
            captured = capsys.readouterr()
            assert "write-only" in captured.out
            assert "webhook" in captured.out

    def test_doctor_shows_linear_read_write(self, capsys):
        from pact.cli import cmd_doctor
        import argparse

        with patch.dict(os.environ, {"LINEAR_API_KEY": "lin_test_key"}, clear=False):
            args = argparse.Namespace(project_dir=None)
            cmd_doctor(args)
            captured = capsys.readouterr()
            assert "read+write" in captured.out

    def test_doctor_failed_component_warning(self, tmp_path: Path, capsys):
        from pact.cli import cmd_doctor
        import argparse

        _setup_project(tmp_path)
        args = argparse.Namespace(project_dir=str(tmp_path))
        cmd_doctor(args)
        captured = capsys.readouterr()
        assert "child_b" in captured.out
        assert "[WARN]" in captured.out


class TestCmdClean:
    def test_clean_no_pact_dir(self, tmp_path: Path, capsys):
        from pact.cli import cmd_clean
        import argparse

        args = argparse.Namespace(
            project_dir=str(tmp_path),
            attempts=False, stale=False, clean_all=False,
        )
        cmd_clean(args)
        captured = capsys.readouterr()
        assert "No .pact/ directory found" in captured.out

    def test_clean_stale(self, tmp_path: Path, capsys):
        from pact.cli import cmd_clean
        import argparse

        project = ProjectManager(tmp_path)
        project.init()
        # Create stale artifacts
        (project._pact_dir / "daemon.pid").write_text("12345")
        (project._pact_dir / "shutdown").write_text("shutdown")

        args = argparse.Namespace(
            project_dir=str(tmp_path),
            attempts=False, stale=True, clean_all=False,
        )
        cmd_clean(args)
        captured = capsys.readouterr()
        assert "daemon.pid" in captured.out
        assert "shutdown" in captured.out
        assert not (project._pact_dir / "daemon.pid").exists()
        assert not (project._pact_dir / "shutdown").exists()

    def test_clean_stale_nothing(self, tmp_path: Path, capsys):
        from pact.cli import cmd_clean
        import argparse

        project = ProjectManager(tmp_path)
        project.init()

        args = argparse.Namespace(
            project_dir=str(tmp_path),
            attempts=False, stale=True, clean_all=False,
        )
        cmd_clean(args)
        captured = capsys.readouterr()
        assert "No stale artifacts found" in captured.out

    def test_clean_attempts(self, tmp_path: Path, capsys):
        from pact.cli import cmd_clean
        import argparse

        project = ProjectManager(tmp_path)
        project.init()
        # Create attempt dirs
        attempt_dir = project.attempt_dir("comp_a", "attempt_1")
        (attempt_dir / "src").mkdir(exist_ok=True)
        (attempt_dir / "src" / "main.py").write_text("# code")

        args = argparse.Namespace(
            project_dir=str(tmp_path),
            attempts=True, stale=False, clean_all=False,
        )
        cmd_clean(args)
        captured = capsys.readouterr()
        assert "Removed attempt artifacts" in captured.out
        assert not (project._impl_dir / "comp_a" / "attempts").exists()

    def test_clean_all(self, tmp_path: Path, capsys):
        from pact.cli import cmd_clean
        import argparse

        project = ProjectManager(tmp_path)
        project.init()
        state = project.create_run()
        project.save_state(state)
        project.append_audit("test", "entry")

        args = argparse.Namespace(
            project_dir=str(tmp_path),
            attempts=False, stale=False, clean_all=True,
        )
        cmd_clean(args)
        captured = capsys.readouterr()
        assert "Removed all .pact/ contents" in captured.out
        # state.json should be gone
        assert not project.state_path.exists()
        # But subdirs should be recreated
        assert project._decomp_dir.exists()
        assert project._contracts_dir.exists()

    def test_clean_interactive(self, tmp_path: Path, capsys):
        from pact.cli import cmd_clean
        import argparse

        project = ProjectManager(tmp_path)
        project.init()
        state = project.create_run()
        project.save_state(state)

        args = argparse.Namespace(
            project_dir=str(tmp_path),
            attempts=False, stale=False, clean_all=False,
        )
        cmd_clean(args)
        captured = capsys.readouterr()
        assert "Artifacts in .pact/" in captured.out
        assert "Total:" in captured.out
        assert "--stale" in captured.out


class TestCmdDiff:
    def test_diff_no_implementations(self, tmp_path: Path, capsys):
        from pact.cli import cmd_diff
        import argparse

        project = ProjectManager(tmp_path)
        project.init()

        args = argparse.Namespace(project_dir=str(tmp_path), component_id="comp_a")
        cmd_diff(args)
        captured = capsys.readouterr()
        assert "No implementations found" in captured.out

    def test_diff_single_implementation(self, tmp_path: Path, capsys):
        from pact.cli import cmd_diff
        import argparse

        project = ProjectManager(tmp_path)
        project.init()
        src_dir = project.impl_src_dir("comp_a")
        (src_dir / "main.py").write_text("def hello(): pass")

        args = argparse.Namespace(project_dir=str(tmp_path), component_id="comp_a")
        cmd_diff(args)
        captured = capsys.readouterr()
        assert "Only one implementation found" in captured.out

    def test_diff_two_implementations(self, tmp_path: Path, capsys):
        from pact.cli import cmd_diff
        import argparse

        project = ProjectManager(tmp_path)
        project.init()

        # Main implementation
        src_dir = project.impl_src_dir("comp_a")
        (src_dir / "main.py").write_text("def hello(): return 'hello'\n")

        # Attempt
        attempt_dir = project.attempt_dir("comp_a", "attempt_1")
        attempt_src = attempt_dir / "src"
        attempt_src.mkdir(exist_ok=True)
        (attempt_src / "main.py").write_text("def hello(): return 'world'\n")
        project.save_attempt_metadata("comp_a", "attempt_1", {"type": "competitive"})

        args = argparse.Namespace(project_dir=str(tmp_path), component_id="comp_a")
        cmd_diff(args)
        captured = capsys.readouterr()
        assert "Available implementations" in captured.out
        assert "Diff:" in captured.out
        assert "main.py" in captured.out


class TestBudgetProperties:
    def test_budget_remaining(self):
        bt = BudgetTracker(per_project_cap=10.00)
        bt.set_model_pricing("claude-haiku-4-5-20251001")
        bt.record_tokens(100, 200)
        assert bt.budget_remaining > 0
        assert bt.budget_remaining < 10.00

    def test_budget_remaining_exceeded(self):
        bt = BudgetTracker(per_project_cap=0.001)
        bt.set_model_pricing("claude-opus-4-6")
        bt.record_tokens(100000, 50000)
        assert bt.budget_remaining == 0.0

    def test_spend_percentage(self):
        bt = BudgetTracker(per_project_cap=10.00)
        assert bt.spend_percentage == 0.0
        bt.set_model_pricing("claude-opus-4-6")
        bt.record_tokens(1_000_000, 0)  # $15.00 — 150%
        assert bt.spend_percentage > 100.0

    def test_spend_percentage_zero_cap(self):
        bt = BudgetTracker(per_project_cap=0.0)
        assert bt.spend_percentage == 100.0


class TestAgentWithLearnings:
    def test_with_learnings_empty(self):
        from pact.agents.base import AgentBase
        bt = BudgetTracker()
        # We can't create a full AgentBase without a backend, so test the method directly
        # by calling it as an unbound method
        result = AgentBase.with_learnings(None, [])
        assert result == ""

    def test_with_learnings_formats(self):
        from pact.agents.base import AgentBase
        learnings = [
            {"category": "failure_mode", "lesson": "Watch for edge cases"},
            {"category": "test_pattern", "lesson": "Use parametrize"},
        ]
        result = AgentBase.with_learnings(None, learnings)
        assert "Learnings from previous runs:" in result
        assert "[failure_mode]" in result
        assert "Watch for edge cases" in result
        assert "[test_pattern]" in result

    def test_with_learnings_limits_to_10(self):
        from pact.agents.base import AgentBase
        learnings = [
            {"category": "test_pattern", "lesson": f"Lesson {i}"}
            for i in range(20)
        ]
        result = AgentBase.with_learnings(None, learnings)
        # Should only include last 10
        assert "Lesson 10" in result
        assert "Lesson 19" in result
        assert "Lesson 0" not in result


class TestConfigIntegrationFields:
    def test_global_config_defaults(self):
        gc = GlobalConfig()
        assert gc.slack_webhook == ""
        assert gc.linear_api_key == ""
        assert gc.linear_team_id == ""
        assert gc.git_auto_commit is False
        assert gc.git_auto_branch is False

    def test_project_config_defaults(self):
        pc = ProjectConfig()
        assert pc.slack_webhook == ""
        assert pc.linear_api_key == ""
        assert pc.linear_team_id == ""
        assert pc.git_auto_commit is None
        assert pc.git_auto_branch is None

    def test_load_global_config_with_integrations(self, tmp_path: Path):
        import yaml
        from pact.config import load_global_config

        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({
            "slack_webhook": "https://hooks.slack.com/test",
            "linear_api_key": "lin_test",
            "linear_team_id": "team_123",
            "git_auto_commit": True,
            "git_auto_branch": True,
        }))
        gc = load_global_config(config_path)
        assert gc.slack_webhook == "https://hooks.slack.com/test"
        assert gc.linear_api_key == "lin_test"
        assert gc.linear_team_id == "team_123"
        assert gc.git_auto_commit is True
        assert gc.git_auto_branch is True

    def test_load_project_config_with_integrations(self, tmp_path: Path):
        import yaml
        from pact.config import load_project_config

        config_path = tmp_path / "pact.yaml"
        config_path.write_text(yaml.dump({
            "slack_webhook": "https://hooks.slack.com/project",
            "linear_team_id": "team_456",
            "git_auto_commit": True,
        }))
        pc = load_project_config(tmp_path)
        assert pc.slack_webhook == "https://hooks.slack.com/project"
        assert pc.linear_team_id == "team_456"
        assert pc.git_auto_commit is True
