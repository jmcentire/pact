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

    def test_iterative_integration_imports_available(self):
        """Verify the iterative integration functions are importable from scheduler."""
        from pact.scheduler import integrate_all_iterative
        assert callable(integrate_all_iterative)


class TestCascadeDetection:
    """Test cascade event detection from tree structure."""

    def _make_tree(self):
        """Create a simple tree: root -> [a, b], a -> [a1, a2]."""
        from pact.schemas import DecompositionNode, DecompositionTree
        return DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root",
                    description="Root", children=["a", "b"],
                ),
                "a": DecompositionNode(
                    component_id="a", name="A",
                    description="A", parent_id="root",
                    children=["a1", "a2"],
                ),
                "b": DecompositionNode(
                    component_id="b", name="B",
                    description="B", parent_id="root",
                ),
                "a1": DecompositionNode(
                    component_id="a1", name="A1",
                    description="A1", parent_id="a",
                ),
                "a2": DecompositionNode(
                    component_id="a2", name="A2",
                    description="A2", parent_id="a",
                ),
            },
        )

    def test_no_cascade_independent_failures(self):
        """Independent failures (no parent/sibling overlap) = 0 cascades."""
        from pact.scheduler import detect_cascade
        tree = self._make_tree()
        # a1 and b are in different subtrees — no cascade
        assert detect_cascade(tree, {"a1", "b"}) == 0

    def test_cascade_parent_child(self):
        """Parent and child both failed = 1 cascade event (the pair)."""
        from pact.scheduler import detect_cascade
        tree = self._make_tree()
        # a and a1 — a1's parent is a, which is also failed
        # One unique pair: {a, a1}
        assert detect_cascade(tree, {"a", "a1"}) == 1

    def test_cascade_siblings(self):
        """Two siblings both failed = 1 lateral spread event."""
        from pact.scheduler import detect_cascade
        tree = self._make_tree()
        # a1 and a2 are siblings under a — one unique pair: {a1, a2}
        assert detect_cascade(tree, {"a1", "a2"}) == 1

    def test_cascade_full_subtree(self):
        """Parent + both children failed = 3 unique cascade pairs."""
        from pact.scheduler import detect_cascade
        tree = self._make_tree()
        # Unique pairs: {a, a1}, {a, a2}, {a1, a2}
        assert detect_cascade(tree, {"a", "a1", "a2"}) == 3


class TestApplyRemedy:
    """Test user-triggered remedy application via scheduler."""

    def test_apply_max_plan_revisions(self, scheduler_setup):
        pm, scheduler = scheduler_setup
        assert scheduler.global_config.max_plan_revisions == 2
        result = scheduler.apply_remedy("max_plan_revisions", 1)
        assert "2 -> 1" in result
        assert scheduler.global_config.max_plan_revisions == 1

    def test_apply_max_plan_revisions_no_op_when_same(self, scheduler_setup):
        pm, scheduler = scheduler_setup
        scheduler.global_config.max_plan_revisions = 1
        result = scheduler.apply_remedy("max_plan_revisions", 1)
        assert result == ""

    def test_apply_shaping_disable(self, scheduler_setup):
        pm, scheduler = scheduler_setup
        scheduler.global_config.shaping = True
        result = scheduler.apply_remedy("shaping")
        assert "Disabled" in result
        assert scheduler.global_config.shaping is False

    def test_apply_shaping_no_op_when_already_false(self, scheduler_setup):
        pm, scheduler = scheduler_setup
        scheduler.global_config.shaping = False
        result = scheduler.apply_remedy("shaping")
        assert result == ""

    def test_apply_unknown_remedy_returns_empty(self, scheduler_setup):
        pm, scheduler = scheduler_setup
        result = scheduler.apply_remedy("nonexistent")
        assert result == ""


class TestPhaseCycleDetection:
    """Test that diagnose→implement/integrate loops are bounded."""

    def test_phase_cycles_default_zero(self):
        """New RunState should have phase_cycles=0."""
        state = RunState(id="test", project_dir="/tmp/t")
        assert state.phase_cycles == 0

    def test_max_phase_cycles_in_global_config(self):
        """GlobalConfig should have max_phase_cycles with default=3."""
        gc = GlobalConfig()
        assert gc.max_phase_cycles == 3

    def test_phase_cycles_increments_are_persisted(self):
        """phase_cycles should survive JSON round-trip."""
        state = RunState(id="test", project_dir="/tmp/t", phase_cycles=5)
        data = state.model_dump_json()
        restored = RunState.model_validate_json(data)
        assert restored.phase_cycles == 5

    def test_phase_cycles_pauses_after_limit(self):
        """When phase_cycles exceeds max, state should pause."""
        state = RunState(
            id="test", project_dir="/tmp/t",
            phase="diagnose", status="active",
            phase_cycles=3,  # Already at limit
        )
        # Simulating what _phase_diagnose does:
        max_cycles = 3
        state.phase_cycles += 1  # Now 4, exceeds 3
        if state.phase_cycles > max_cycles:
            state.pause(f"Phase cycle limit reached ({state.phase_cycles})")

        assert state.status == "paused"
        assert "cycle limit" in state.pause_reason
