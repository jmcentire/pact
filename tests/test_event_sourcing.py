"""Tests for event sourcing and audit replay."""
from pact.lifecycle import rebuild_state_from_audit, compute_audit_delta
from pact.schemas import RunState


class TestRebuildStateFromAudit:
    def test_empty_audit_returns_fresh(self):
        state = rebuild_state_from_audit([], "/tmp/test")
        assert state.status == "active"
        assert state.phase == "interview"

    def test_interview_advances_phase(self):
        entries = [
            {"timestamp": "2024-01-01T00:00:00", "action": "interview", "detail": "3 questions"},
        ]
        state = rebuild_state_from_audit(entries, "/tmp/test")
        assert state.phase in ("shape", "decompose")  # past interview

    def test_shape_advances_to_decompose(self):
        entries = [
            {"timestamp": "2024-01-01T00:00:00", "action": "interview", "detail": "0 questions"},
            {"timestamp": "2024-01-01T00:01:00", "action": "shape", "detail": "depth=standard"},
        ]
        state = rebuild_state_from_audit(entries, "/tmp/test")
        assert state.phase == "decompose"

    def test_shape_error_still_advances(self):
        entries = [
            {"timestamp": "2024-01-01T00:00:00", "action": "interview", "detail": "0 questions"},
            {"timestamp": "2024-01-01T00:01:00", "action": "shape_error", "detail": "API error"},
        ]
        state = rebuild_state_from_audit(entries, "/tmp/test")
        assert state.phase == "decompose"

    def test_build_success_tracked(self):
        entries = [
            {"timestamp": "2024-01-01T00:00:00", "action": "interview", "detail": "0 questions"},
            {"timestamp": "2024-01-01T00:01:00", "action": "shape", "detail": "done"},
            {"timestamp": "2024-01-01T00:02:00", "action": "build", "detail": "comp_a: 5/5 passed"},
        ]
        state = rebuild_state_from_audit(entries, "/tmp/test")
        # Component tracked
        completed = [t for t in state.component_tasks if t.status == "completed"]
        assert len(completed) >= 1

    def test_build_failure_tracked(self):
        entries = [
            {"timestamp": "2024-01-01T00:00:00", "action": "build", "detail": "comp_b: 2/5 passed"},
        ]
        state = rebuild_state_from_audit(entries, "/tmp/test")
        failed = [t for t in state.component_tasks if t.status == "failed"]
        assert len(failed) >= 1

    def test_systemic_failure_pauses(self):
        entries = [
            {"timestamp": "2024-01-01T00:00:00", "action": "systemic_failure", "detail": "zero_tests: No tests collected"},
        ]
        state = rebuild_state_from_audit(entries, "/tmp/test")
        assert state.status == "paused"

    def test_multiple_builds_tracked(self):
        entries = [
            {"timestamp": "2024-01-01T00:00:00", "action": "build", "detail": "comp_a: 5/5 passed"},
            {"timestamp": "2024-01-01T00:01:00", "action": "build", "detail": "comp_b: 3/3 passed"},
            {"timestamp": "2024-01-01T00:02:00", "action": "build", "detail": "comp_c: 0/4 passed"},
        ]
        state = rebuild_state_from_audit(entries, "/tmp/test")
        completed = [t for t in state.component_tasks if t.status == "completed"]
        failed = [t for t in state.component_tasks if t.status == "failed"]
        assert len(completed) == 2
        assert len(failed) == 1


class TestComputeAuditDelta:
    def test_consistent_state_no_delta(self):
        state = RunState(id="x", project_dir="/tmp", status="active", phase="interview")
        entries = []
        delta = compute_audit_delta(state, entries)
        assert delta == []

    def test_phase_mismatch_reported(self):
        state = RunState(id="x", project_dir="/tmp", status="active", phase="implement")
        entries = [
            {"timestamp": "2024-01-01T00:00:00", "action": "interview", "detail": "0 questions"},
        ]
        delta = compute_audit_delta(state, entries)
        assert len(delta) > 0
        assert any("phase" in d.lower() for d in delta)

    def test_status_mismatch_reported(self):
        state = RunState(id="x", project_dir="/tmp", status="completed", phase="complete")
        entries = [
            {"timestamp": "2024-01-01T00:00:00", "action": "systemic_failure", "detail": "error"},
        ]
        delta = compute_audit_delta(state, entries)
        assert len(delta) > 0
        assert any("status" in d.lower() for d in delta)
