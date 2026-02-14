"""Tests for run state machine."""

from __future__ import annotations

from pact.lifecycle import advance_phase, create_run, format_run_summary
from pact.schemas import ComponentTask, RunState


class TestCreateRun:
    def test_creates_active_run(self):
        run = create_run("/tmp/test")
        assert run.status == "active"
        assert run.phase == "interview"
        assert run.project_dir == "/tmp/test"
        assert run.id != ""

    def test_unique_ids(self):
        r1 = create_run("/tmp/test1")
        r2 = create_run("/tmp/test2")
        assert r1.id != r2.id


class TestAdvancePhase:
    def test_interview_to_decompose(self):
        state = RunState(id="x", project_dir="/tmp", phase="interview")
        result = advance_phase(state)
        assert result == "decompose"
        assert state.phase == "decompose"

    def test_decompose_to_contract(self):
        state = RunState(id="x", project_dir="/tmp", phase="decompose")
        result = advance_phase(state)
        assert result == "contract"

    def test_contract_to_implement(self):
        state = RunState(id="x", project_dir="/tmp", phase="contract")
        advance_phase(state)
        assert state.phase == "implement"

    def test_implement_to_integrate(self):
        state = RunState(id="x", project_dir="/tmp", phase="implement")
        advance_phase(state)
        assert state.phase == "integrate"

    def test_integrate_to_complete(self):
        state = RunState(id="x", project_dir="/tmp", phase="integrate")
        advance_phase(state)
        assert state.phase == "complete"

    def test_complete_stays(self):
        state = RunState(id="x", project_dir="/tmp", phase="complete")
        advance_phase(state)
        assert state.phase == "complete"

    def test_diagnose_returns_to_implement(self):
        state = RunState(id="x", project_dir="/tmp", phase="diagnose")
        advance_phase(state)
        assert state.phase == "implement"


class TestFormatRunSummary:
    def test_basic(self):
        state = RunState(
            id="abc123",
            project_dir="/tmp/test",
            status="active",
            phase="implement",
        )
        summary = format_run_summary(state)
        assert "abc123" in summary
        assert "active" in summary
        assert "implement" in summary

    def test_with_components(self):
        state = RunState(
            id="abc123",
            project_dir="/tmp/test",
            component_tasks=[
                ComponentTask(component_id="a", status="completed"),
                ComponentTask(component_id="b", status="failed"),
                ComponentTask(component_id="c", status="pending"),
            ],
        )
        summary = format_run_summary(state)
        assert "1/3 done" in summary
        assert "1 failed" in summary

    def test_with_pause(self):
        state = RunState(
            id="abc123",
            project_dir="/tmp/test",
            status="paused",
            pause_reason="Waiting for user",
        )
        summary = format_run_summary(state)
        assert "Waiting for user" in summary
