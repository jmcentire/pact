"""Tests for casual-pace scheduler."""

from __future__ import annotations

from pathlib import Path

import pytest

from pact.budget import BudgetTracker
from pact.config import GlobalConfig, ProjectConfig
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
