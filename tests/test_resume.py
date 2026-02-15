"""Tests for pact resume command and strategy."""
from pact.lifecycle import compute_resume_strategy, execute_resume, ResumeStrategy
from pact.schemas import RunState, ComponentTask


class TestComputeResumeStrategy:
    def test_resume_from_failed_implement(self):
        state = RunState(
            id="x", project_dir="/tmp", status="failed",
            phase="implement", pause_reason="Component X failed",
            component_tasks=[
                ComponentTask(component_id="a", status="completed"),
                ComponentTask(component_id="b", status="completed"),
                ComponentTask(component_id="c", status="failed"),
            ],
        )
        strategy = compute_resume_strategy(state)
        assert strategy.resume_phase == "implement"
        assert "a" in strategy.completed_components
        assert "b" in strategy.completed_components
        assert "c" not in strategy.completed_components

    def test_resume_from_paused(self):
        state = RunState(
            id="x", project_dir="/tmp", status="paused",
            phase="interview", pause_reason="Waiting for user",
        )
        strategy = compute_resume_strategy(state)
        assert strategy.resume_phase == "interview"
        assert strategy.completed_components == []

    def test_resume_active_raises(self):
        state = RunState(id="x", project_dir="/tmp", status="active", phase="implement")
        import pytest
        with pytest.raises(ValueError, match="already active"):
            compute_resume_strategy(state)

    def test_resume_completed_raises(self):
        state = RunState(id="x", project_dir="/tmp", status="completed", phase="complete")
        import pytest
        with pytest.raises(ValueError, match="already completed"):
            compute_resume_strategy(state)

    def test_resume_from_diagnose_goes_to_implement(self):
        state = RunState(
            id="x", project_dir="/tmp", status="failed",
            phase="diagnose", pause_reason="Diagnosis failed",
        )
        strategy = compute_resume_strategy(state)
        assert strategy.resume_phase == "implement"

    def test_resume_budget_exceeded(self):
        state = RunState(
            id="x", project_dir="/tmp", status="budget_exceeded",
            phase="implement", pause_reason="Budget cap reached",
        )
        strategy = compute_resume_strategy(state)
        assert strategy.resume_phase == "implement"

    def test_resume_preserves_completed_list(self):
        state = RunState(
            id="x", project_dir="/tmp", status="failed",
            phase="integrate",
            component_tasks=[
                ComponentTask(component_id="a", status="completed"),
                ComponentTask(component_id="b", status="completed"),
                ComponentTask(component_id="c", status="completed"),
                ComponentTask(component_id="d", status="failed"),
            ],
        )
        strategy = compute_resume_strategy(state)
        assert len(strategy.completed_components) == 3


class TestExecuteResume:
    def test_sets_active(self):
        state = RunState(id="x", project_dir="/tmp", status="failed", phase="implement", pause_reason="error")
        strategy = ResumeStrategy(
            last_checkpoint="", completed_components=[], resume_phase="implement", cleared_fields=["pause_reason"],
        )
        result = execute_resume(state, strategy)
        assert result.status == "active"
        assert result.pause_reason == ""
        assert result.phase == "implement"

    def test_sets_custom_phase(self):
        state = RunState(id="x", project_dir="/tmp", status="failed", phase="implement", pause_reason="error")
        strategy = ResumeStrategy(
            last_checkpoint="", completed_components=[], resume_phase="decompose", cleared_fields=["pause_reason"],
        )
        result = execute_resume(state, strategy)
        assert result.phase == "decompose"

    def test_clears_pause_reason(self):
        state = RunState(id="x", project_dir="/tmp", status="paused", phase="interview", pause_reason="Waiting")
        strategy = ResumeStrategy(
            last_checkpoint="", completed_components=[], resume_phase="interview", cleared_fields=["pause_reason"],
        )
        result = execute_resume(state, strategy)
        assert result.pause_reason == ""
