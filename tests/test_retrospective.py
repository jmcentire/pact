"""Tests for retrospective learning."""
import json
from pathlib import Path

from pact.retrospective import (
    RunRetrospective,
    generate_retrospective,
    load_retrospective,
    load_all_retrospectives,
    _is_passing_build,
    _infer_lessons,
)
from collections import Counter


class TestIsPassingBuild:
    def test_passing(self):
        assert _is_passing_build({"detail": "comp_a: 5/5 passed"}) is True

    def test_failing(self):
        assert _is_passing_build({"detail": "comp_a: 2/5 passed"}) is False

    def test_zero_zero(self):
        assert _is_passing_build({"detail": "comp_a: 0/0 passed"}) is False

    def test_malformed(self):
        assert _is_passing_build({"detail": "something else"}) is False

    def test_empty(self):
        assert _is_passing_build({}) is False


class TestInferLessons:
    def test_high_cost_per_component(self):
        lessons = _infer_lessons(
            total_cost=50.0, components_count=5,
            failure_patterns=[], action_counts=Counter(),
            failed_builds=0, total_builds=5,
        )
        assert any("cost" in l.lower() for l in lessons)

    def test_low_cost_no_warning(self):
        lessons = _infer_lessons(
            total_cost=5.0, components_count=5,
            failure_patterns=[], action_counts=Counter(),
            failed_builds=0, total_builds=5,
        )
        assert not any("cost" in l.lower() for l in lessons)

    def test_high_failure_rate(self):
        lessons = _infer_lessons(
            total_cost=10.0, components_count=5,
            failure_patterns=[], action_counts=Counter(),
            failed_builds=4, total_builds=5,
        )
        assert any("failure rate" in l.lower() for l in lessons)

    def test_systemic_pattern(self):
        lessons = _infer_lessons(
            total_cost=10.0, components_count=5,
            failure_patterns=["Systemic failure detected"],
            action_counts=Counter(),
            failed_builds=0, total_builds=5,
        )
        assert any("systemic" in l.lower() for l in lessons)

    def test_many_archives(self):
        lessons = _infer_lessons(
            total_cost=10.0, components_count=5,
            failure_patterns=[],
            action_counts=Counter({"archive": 5}),
            failed_builds=0, total_builds=5,
        )
        assert any("archive" in l.lower() for l in lessons)


class TestGenerateRetrospective:
    def test_empty_project(self, tmp_path):
        pact_dir = tmp_path / ".pact"
        pact_dir.mkdir()
        retro = generate_retrospective(tmp_path)
        assert retro.run_id == "unknown"
        assert retro.total_cost == 0.0

    def test_with_state(self, tmp_path):
        pact_dir = tmp_path / ".pact"
        pact_dir.mkdir()
        state = {
            "id": "abc123",
            "project_dir": str(tmp_path),
            "status": "completed",
            "total_cost_usd": 12.50,
            "component_tasks": [
                {"component_id": "a", "status": "completed"},
                {"component_id": "b", "status": "completed"},
            ],
            "created_at": "2024-01-01T00:00:00",
            "completed_at": "2024-01-01T01:00:00",
        }
        (pact_dir / "state.json").write_text(json.dumps(state))
        retro = generate_retrospective(tmp_path)
        assert retro.run_id == "abc123"
        assert retro.total_cost == 12.50
        assert retro.components_count == 2
        assert retro.total_duration_seconds == 3600.0

    def test_with_audit(self, tmp_path):
        pact_dir = tmp_path / ".pact"
        pact_dir.mkdir()
        (pact_dir / "state.json").write_text(json.dumps({
            "id": "run1", "project_dir": str(tmp_path), "status": "failed",
            "total_cost_usd": 5.0, "component_tasks": [],
        }))
        audit = [
            {"action": "build", "detail": "comp_a: 5/5 passed"},
            {"action": "build", "detail": "comp_b: 0/5 passed"},
            {"action": "systemic_failure", "detail": "zero_tests"},
        ]
        (pact_dir / "audit.jsonl").write_text(
            "\n".join(json.dumps(e) for e in audit)
        )
        retro = generate_retrospective(tmp_path)
        assert len(retro.failure_patterns) >= 1
        assert any("systemic" in p.lower() for p in retro.failure_patterns)

    def test_saves_retrospective(self, tmp_path):
        pact_dir = tmp_path / ".pact"
        pact_dir.mkdir()
        (pact_dir / "state.json").write_text(json.dumps({
            "id": "run2", "project_dir": str(tmp_path),
            "status": "completed", "total_cost_usd": 1.0,
            "component_tasks": [],
        }))
        generate_retrospective(tmp_path)
        retro_path = pact_dir / "retrospectives" / "run2.json"
        assert retro_path.exists()

    def test_with_test_suites(self, tmp_path):
        pact_dir = tmp_path / ".pact"
        pact_dir.mkdir()
        (pact_dir / "state.json").write_text(json.dumps({
            "id": "run3", "project_dir": str(tmp_path),
            "status": "completed", "total_cost_usd": 1.0,
            "component_tasks": [],
        }))
        # Create test suites
        comp_dir = pact_dir / "contracts" / "comp_a" / "tests"
        comp_dir.mkdir(parents=True)
        suite = {
            "component_id": "comp_a", "contract_version": 1,
            "test_cases": [
                {"id": "t1", "description": "test", "function": "f", "category": "happy_path"},
                {"id": "t2", "description": "test", "function": "f", "category": "error_case"},
                {"id": "t3", "description": "test", "function": "f", "category": "error_case"},
            ],
        }
        (comp_dir / "contract_test_suite.json").write_text(json.dumps(suite))
        retro = generate_retrospective(tmp_path)
        assert retro.largest_test_suite[0] == "comp_a"
        assert retro.largest_test_suite[1] == 3
        assert retro.most_error_cases[0] == "comp_a"
        assert retro.most_error_cases[1] == 2


class TestLoadRetrospective:
    def test_load_saved(self, tmp_path):
        pact_dir = tmp_path / ".pact"
        pact_dir.mkdir()
        (pact_dir / "state.json").write_text(json.dumps({
            "id": "run4", "project_dir": str(tmp_path),
            "status": "completed", "total_cost_usd": 3.0,
            "component_tasks": [],
        }))
        generate_retrospective(tmp_path)
        loaded = load_retrospective(tmp_path, "run4")
        assert loaded is not None
        assert loaded.run_id == "run4"

    def test_load_nonexistent(self, tmp_path):
        result = load_retrospective(tmp_path, "nope")
        assert result is None

    def test_load_all(self, tmp_path):
        retro_dir = tmp_path / ".pact" / "retrospectives"
        retro_dir.mkdir(parents=True)
        for rid in ["r1", "r2"]:
            retro = RunRetrospective(run_id=rid)
            (retro_dir / f"{rid}.json").write_text(retro.model_dump_json())
        all_retros = load_all_retrospectives(tmp_path)
        assert len(all_retros) == 2
