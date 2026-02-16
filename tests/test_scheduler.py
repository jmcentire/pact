"""Tests for casual-pace scheduler."""

from __future__ import annotations

from pathlib import Path

import pytest

from pact.budget import BudgetTracker
from pact.config import GlobalConfig, ProjectConfig, resolve_backend
from pact.project import ProjectManager
from pact.scheduler import Scheduler
from pact.schemas import RunState


@pytest.fixture
def scheduler_setup(tmp_path: Path) -> tuple[ProjectManager, Scheduler]:
    """Create a scheduler with a temporary project."""
    pm = ProjectManager(tmp_path / "test-project")
    pm.init()

    gc = GlobalConfig(check_interval=1)  # Fast for testing
    pc = ProjectConfig(budget=10.00)
    budget = BudgetTracker(per_project_cap=10.00)

    scheduler = Scheduler(pm, gc, pc, budget)
    return pm, scheduler


class TestSchedulerInit:
    def test_creates_scheduler(self, scheduler_setup):
        pm, scheduler = scheduler_setup
        assert scheduler.check_interval == 1

    def test_make_agent_uses_config(self, scheduler_setup):
        """Test that _make_agent respects role config."""
        _, scheduler = scheduler_setup
        # This will fail without anthropic installed, which is expected
        # We just test the model resolution
        model = scheduler.global_config.role_models.get("decomposer")
        assert model == "claude-opus-4-6"


class TestSchedulerRunState:
    def test_completed_run_returns_immediately(self, scheduler_setup):
        pm, scheduler = scheduler_setup
        state = pm.create_run()
        state.status = "completed"
        pm.save_state(state)

        # run_once should not change a completed run
        import asyncio
        result = asyncio.run(scheduler.run_once())
        assert result.status == "completed"

    def test_failed_run_returns_immediately(self, scheduler_setup):
        pm, scheduler = scheduler_setup
        state = pm.create_run()
        state.status = "failed"
        state.pause_reason = "test failure"
        pm.save_state(state)

        import asyncio
        result = asyncio.run(scheduler.run_once())
        assert result.status == "failed"

    def test_budget_exceeded_returns_immediately(self, scheduler_setup):
        pm, scheduler = scheduler_setup
        state = pm.create_run()
        state.status = "budget_exceeded"
        pm.save_state(state)

        import asyncio
        result = asyncio.run(scheduler.run_once())
        assert result.status == "budget_exceeded"


class TestSchedulerBackendRouting:
    """Test that the scheduler routes to iterative vs API-based paths."""

    def test_default_claude_code_backend(self):
        """Default global config has code_author = claude_code."""
        gc = GlobalConfig()
        pc = ProjectConfig()
        backend = resolve_backend("code_author", pc, gc)
        assert backend == "claude_code"

    def test_project_override_to_anthropic(self):
        """Project config can override code_author backend."""
        gc = GlobalConfig()
        pc = ProjectConfig(role_backends={"code_author": "anthropic"})
        backend = resolve_backend("code_author", pc, gc)
        assert backend == "anthropic"

    def test_project_override_to_openai(self):
        """Project config can override code_author to openai."""
        gc = GlobalConfig()
        pc = ProjectConfig(role_backends={"code_author": "openai"})
        backend = resolve_backend("code_author", pc, gc)
        assert backend == "openai"

    def test_claude_code_team_detected(self):
        """claude_code_team backend should also trigger iterative path."""
        gc = GlobalConfig(role_backends={
            **GlobalConfig().role_backends,
            "code_author": "claude_code_team",
        })
        pc = ProjectConfig()
        backend = resolve_backend("code_author", pc, gc)
        assert backend == "claude_code_team"

    def test_iterative_imports_available(self):
        """Verify the iterative implementation functions are importable from scheduler."""
        from pact.scheduler import implement_all_iterative, implement_component_iterative
        assert callable(implement_all_iterative)
        assert callable(implement_component_iterative)
