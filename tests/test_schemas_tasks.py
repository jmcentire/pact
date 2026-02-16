"""Tests for task list, analysis, and checklist schemas."""

from __future__ import annotations

import json

import pytest

from pact.schemas_tasks import (
    AnalysisFinding,
    AnalysisReport,
    ChecklistCategory,
    ChecklistItem,
    FindingCategory,
    FindingSeverity,
    PhaseCheckpoint,
    RequirementsChecklist,
    TaskCategory,
    TaskItem,
    TaskList,
    TaskPhase,
    TaskStatus,
)


# ── TaskPhase ───────────────────────────────────────────────────────


class TestTaskPhase:
    def test_all_values(self):
        assert TaskPhase.setup == "setup"
        assert TaskPhase.foundational == "foundational"
        assert TaskPhase.component == "component"
        assert TaskPhase.integration == "integration"
        assert TaskPhase.polish == "polish"

    def test_is_str(self):
        assert isinstance(TaskPhase.setup, str)


# ── TaskStatus ──────────────────────────────────────────────────────


class TestTaskStatus:
    def test_all_values(self):
        assert TaskStatus.pending == "pending"
        assert TaskStatus.in_progress == "in_progress"
        assert TaskStatus.completed == "completed"
        assert TaskStatus.skipped == "skipped"
        assert TaskStatus.failed == "failed"

    def test_is_str(self):
        assert isinstance(TaskStatus.pending, str)


# ── TaskCategory ────────────────────────────────────────────────────


class TestTaskCategory:
    def test_all_values(self):
        assert TaskCategory.scaffold == "scaffold"
        assert TaskCategory.type_definition == "type_definition"
        assert TaskCategory.contract_review == "contract_review"
        assert TaskCategory.test_setup == "test_setup"
        assert TaskCategory.test_write == "test_write"
        assert TaskCategory.implement == "implement"
        assert TaskCategory.verify == "verify"
        assert TaskCategory.integrate == "integrate"
        assert TaskCategory.validate == "validate"
        assert TaskCategory.document == "document"


# ── TaskItem ────────────────────────────────────────────────────────


class TestTaskItem:
    def test_construction_defaults(self):
        t = TaskItem(id="T001", phase=TaskPhase.setup, description="Init project")
        assert t.id == "T001"
        assert t.phase == TaskPhase.setup
        assert t.component_id == ""
        assert t.description == "Init project"
        assert t.file_path == ""
        assert t.status == TaskStatus.pending
        assert t.parallel is False
        assert t.depends_on == []
        assert t.category == TaskCategory.scaffold

    def test_construction_all_fields(self):
        t = TaskItem(
            id="T042",
            phase=TaskPhase.component,
            component_id="pricing",
            description="Implement pricing",
            file_path="implementations/pricing/src/",
            status=TaskStatus.in_progress,
            parallel=True,
            depends_on=["T041"],
            category=TaskCategory.implement,
        )
        assert t.component_id == "pricing"
        assert t.status == TaskStatus.in_progress
        assert t.parallel is True
        assert t.depends_on == ["T041"]
        assert t.category == TaskCategory.implement

    def test_all_status_values(self):
        for status in TaskStatus:
            t = TaskItem(id="T001", phase=TaskPhase.setup, description="d", status=status)
            assert t.status == status

    def test_json_roundtrip(self):
        t = TaskItem(
            id="T001", phase=TaskPhase.component,
            component_id="auth", description="Review",
            depends_on=["T000"], category=TaskCategory.contract_review,
        )
        data = json.loads(t.model_dump_json())
        t2 = TaskItem.model_validate(data)
        assert t2.id == t.id
        assert t2.phase == t.phase
        assert t2.depends_on == t.depends_on


# ── PhaseCheckpoint ─────────────────────────────────────────────────


class TestPhaseCheckpoint:
    def test_construction(self):
        cp = PhaseCheckpoint(
            after_phase=TaskPhase.component,
            description="All leaf components verified",
        )
        assert cp.after_phase == TaskPhase.component
        assert cp.validation == ""

    def test_with_validation(self):
        cp = PhaseCheckpoint(
            after_phase=TaskPhase.integration,
            description="All integrations verified",
            validation="Run full test suite",
        )
        assert cp.validation == "Run full test suite"


# ── TaskList ────────────────────────────────────────────────────────


class TestTaskList:
    def _make_task_list(self) -> TaskList:
        return TaskList(
            project_id="test",
            tasks=[
                TaskItem(id="T001", phase=TaskPhase.setup, description="Init", status=TaskStatus.completed),
                TaskItem(id="T002", phase=TaskPhase.setup, description="Env", status=TaskStatus.completed),
                TaskItem(id="T003", phase=TaskPhase.component, component_id="auth", description="Review", depends_on=["T002"]),
                TaskItem(id="T004", phase=TaskPhase.component, component_id="auth", description="Implement", depends_on=["T003"]),
                TaskItem(id="T005", phase=TaskPhase.component, component_id="db", description="Review"),
                TaskItem(id="T006", phase=TaskPhase.integration, component_id="root", description="Integrate", depends_on=["T004", "T005"]),
                TaskItem(id="T007", phase=TaskPhase.polish, description="Validate", status=TaskStatus.failed),
            ],
        )

    def test_total(self):
        tl = self._make_task_list()
        assert tl.total == 7

    def test_completed(self):
        tl = self._make_task_list()
        assert tl.completed == 2

    def test_pending(self):
        tl = self._make_task_list()
        assert tl.pending == 4  # T003, T004, T005, T006

    def test_tasks_for_phase(self):
        tl = self._make_task_list()
        setup_tasks = tl.tasks_for_phase(TaskPhase.setup)
        assert len(setup_tasks) == 2
        component_tasks = tl.tasks_for_phase(TaskPhase.component)
        assert len(component_tasks) == 3

    def test_tasks_for_component(self):
        tl = self._make_task_list()
        auth_tasks = tl.tasks_for_component("auth")
        assert len(auth_tasks) == 2
        db_tasks = tl.tasks_for_component("db")
        assert len(db_tasks) == 1
        missing = tl.tasks_for_component("nonexistent")
        assert missing == []

    def test_ready_tasks(self):
        tl = self._make_task_list()
        ready = tl.ready_tasks()
        # T003 depends on T002 (completed) -> ready
        # T004 depends on T003 (pending) -> not ready
        # T005 has no deps -> ready
        # T006 depends on T004, T005 (pending) -> not ready
        ready_ids = {t.id for t in ready}
        assert "T003" in ready_ids
        assert "T005" in ready_ids
        assert "T004" not in ready_ids
        assert "T006" not in ready_ids

    def test_ready_tasks_all_completed(self):
        tl = TaskList(
            project_id="test",
            tasks=[
                TaskItem(id="T001", phase=TaskPhase.setup, description="d", status=TaskStatus.completed),
            ],
        )
        assert tl.ready_tasks() == []

    def test_mark_complete(self):
        tl = self._make_task_list()
        assert tl.mark_complete("T003")
        assert tl.tasks[2].status == TaskStatus.completed

    def test_mark_complete_not_found(self):
        tl = self._make_task_list()
        assert not tl.mark_complete("T999")

    def test_empty_task_list(self):
        tl = TaskList(project_id="empty")
        assert tl.total == 0
        assert tl.completed == 0
        assert tl.pending == 0
        assert tl.ready_tasks() == []

    def test_generated_at_auto(self):
        tl = TaskList(project_id="test")
        assert tl.generated_at  # Non-empty

    def test_json_roundtrip(self):
        tl = self._make_task_list()
        data = json.loads(tl.model_dump_json())
        tl2 = TaskList.model_validate(data)
        assert tl2.project_id == tl.project_id
        assert tl2.total == tl.total
        assert tl2.tasks[0].id == "T001"

    def test_checkpoints(self):
        tl = TaskList(
            project_id="test",
            checkpoints=[
                PhaseCheckpoint(after_phase=TaskPhase.component, description="All leaves verified"),
            ],
        )
        assert len(tl.checkpoints) == 1

    def test_tasks_for_phase_empty(self):
        tl = TaskList(project_id="test")
        assert tl.tasks_for_phase(TaskPhase.foundational) == []

    def test_ready_tasks_with_chain(self):
        """Test dependency chain: only first in chain is ready."""
        tl = TaskList(
            project_id="test",
            tasks=[
                TaskItem(id="T001", phase=TaskPhase.component, description="first"),
                TaskItem(id="T002", phase=TaskPhase.component, description="second", depends_on=["T001"]),
                TaskItem(id="T003", phase=TaskPhase.component, description="third", depends_on=["T002"]),
            ],
        )
        ready = tl.ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "T001"

    def test_mark_complete_enables_dependent(self):
        tl = TaskList(
            project_id="test",
            tasks=[
                TaskItem(id="T001", phase=TaskPhase.setup, description="a"),
                TaskItem(id="T002", phase=TaskPhase.setup, description="b", depends_on=["T001"]),
            ],
        )
        assert len(tl.ready_tasks()) == 1
        tl.mark_complete("T001")
        ready = tl.ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "T002"


# ── FindingSeverity ─────────────────────────────────────────────────


class TestFindingSeverity:
    def test_all_values(self):
        assert FindingSeverity.error == "error"
        assert FindingSeverity.warning == "warning"
        assert FindingSeverity.info == "info"


# ── FindingCategory ─────────────────────────────────────────────────


class TestFindingCategory:
    def test_all_values(self):
        assert FindingCategory.coverage_gap == "coverage_gap"
        assert FindingCategory.ambiguity == "ambiguity"
        assert FindingCategory.duplication == "duplication"
        assert FindingCategory.consistency == "consistency"
        assert FindingCategory.completeness == "completeness"


# ── AnalysisFinding ─────────────────────────────────────────────────


class TestAnalysisFinding:
    def test_construction(self):
        f = AnalysisFinding(
            id="F001",
            severity=FindingSeverity.error,
            category=FindingCategory.coverage_gap,
            description="No contract for component X",
        )
        assert f.id == "F001"
        assert f.component_id == ""
        assert f.suggestion == ""
        assert f.artifacts == []

    def test_all_fields(self):
        f = AnalysisFinding(
            id="F002",
            severity=FindingSeverity.warning,
            category=FindingCategory.ambiguity,
            component_id="pricing",
            description="Short description",
            suggestion="Expand description",
            artifacts=["contracts/pricing/interface.json"],
        )
        assert f.component_id == "pricing"
        assert len(f.artifacts) == 1

    def test_json_roundtrip(self):
        f = AnalysisFinding(
            id="F001", severity=FindingSeverity.info,
            category=FindingCategory.duplication,
            description="Duplicate type",
        )
        data = json.loads(f.model_dump_json())
        f2 = AnalysisFinding.model_validate(data)
        assert f2.id == f.id


# ── AnalysisReport ──────────────────────────────────────────────────


class TestAnalysisReport:
    def test_empty(self):
        r = AnalysisReport(project_id="test")
        assert r.errors == []
        assert r.warnings == []
        assert r.findings == []
        assert r.summary == ""

    def test_errors_property(self):
        r = AnalysisReport(
            project_id="test",
            findings=[
                AnalysisFinding(id="F001", severity=FindingSeverity.error, category=FindingCategory.coverage_gap, description="d"),
                AnalysisFinding(id="F002", severity=FindingSeverity.warning, category=FindingCategory.ambiguity, description="d"),
                AnalysisFinding(id="F003", severity=FindingSeverity.error, category=FindingCategory.consistency, description="d"),
            ],
        )
        assert len(r.errors) == 2
        assert len(r.warnings) == 1

    def test_warnings_property(self):
        r = AnalysisReport(
            project_id="test",
            findings=[
                AnalysisFinding(id="F001", severity=FindingSeverity.warning, category=FindingCategory.ambiguity, description="d"),
                AnalysisFinding(id="F002", severity=FindingSeverity.info, category=FindingCategory.completeness, description="d"),
            ],
        )
        assert len(r.warnings) == 1
        assert r.warnings[0].id == "F001"

    def test_json_roundtrip(self):
        r = AnalysisReport(
            project_id="test",
            findings=[
                AnalysisFinding(id="F001", severity=FindingSeverity.error, category=FindingCategory.coverage_gap, description="d"),
            ],
            summary="1 error found",
        )
        data = json.loads(r.model_dump_json())
        r2 = AnalysisReport.model_validate(data)
        assert r2.project_id == "test"
        assert len(r2.findings) == 1
        assert r2.summary == "1 error found"


# ── ChecklistCategory ──────────────────────────────────────────────


class TestChecklistCategory:
    def test_all_values(self):
        assert ChecklistCategory.requirements == "requirements"
        assert ChecklistCategory.acceptance_criteria == "acceptance_criteria"
        assert ChecklistCategory.edge_cases == "edge_cases"
        assert ChecklistCategory.error_handling == "error_handling"
        assert ChecklistCategory.dependencies == "dependencies"
        assert ChecklistCategory.testability == "testability"


# ── ChecklistItem ──────────────────────────────────────────────────


class TestChecklistItem:
    def test_construction_defaults(self):
        c = ChecklistItem(
            id="C001",
            category=ChecklistCategory.requirements,
            question="Is the requirement clear?",
        )
        assert c.id == "C001"
        assert c.component_id == ""
        assert c.reference == ""
        assert c.satisfied is None

    def test_tri_state(self):
        assert ChecklistItem(id="C1", category=ChecklistCategory.requirements, question="q", satisfied=None).satisfied is None
        assert ChecklistItem(id="C2", category=ChecklistCategory.requirements, question="q", satisfied=True).satisfied is True
        assert ChecklistItem(id="C3", category=ChecklistCategory.requirements, question="q", satisfied=False).satisfied is False

    def test_json_roundtrip(self):
        c = ChecklistItem(
            id="C001", category=ChecklistCategory.edge_cases,
            question="Are boundary conditions covered?",
            component_id="parser", satisfied=True,
        )
        data = json.loads(c.model_dump_json())
        c2 = ChecklistItem.model_validate(data)
        assert c2.satisfied is True
        assert c2.component_id == "parser"

    def test_json_roundtrip_none_satisfied(self):
        c = ChecklistItem(id="C1", category=ChecklistCategory.requirements, question="q")
        data = json.loads(c.model_dump_json())
        c2 = ChecklistItem.model_validate(data)
        assert c2.satisfied is None


# ── RequirementsChecklist ──────────────────────────────────────────


class TestRequirementsChecklist:
    def test_empty(self):
        rc = RequirementsChecklist(project_id="test")
        assert rc.unanswered == 0
        assert rc.satisfied_count == 0

    def test_unanswered(self):
        rc = RequirementsChecklist(
            project_id="test",
            items=[
                ChecklistItem(id="C1", category=ChecklistCategory.requirements, question="q1"),
                ChecklistItem(id="C2", category=ChecklistCategory.requirements, question="q2", satisfied=True),
                ChecklistItem(id="C3", category=ChecklistCategory.requirements, question="q3"),
            ],
        )
        assert rc.unanswered == 2

    def test_satisfied_count(self):
        rc = RequirementsChecklist(
            project_id="test",
            items=[
                ChecklistItem(id="C1", category=ChecklistCategory.requirements, question="q1", satisfied=True),
                ChecklistItem(id="C2", category=ChecklistCategory.requirements, question="q2", satisfied=False),
                ChecklistItem(id="C3", category=ChecklistCategory.requirements, question="q3", satisfied=True),
            ],
        )
        assert rc.satisfied_count == 2

    def test_json_roundtrip(self):
        rc = RequirementsChecklist(
            project_id="test",
            items=[
                ChecklistItem(id="C1", category=ChecklistCategory.requirements, question="q"),
            ],
        )
        data = json.loads(rc.model_dump_json())
        rc2 = RequirementsChecklist.model_validate(data)
        assert rc2.project_id == "test"
        assert len(rc2.items) == 1

    def test_generated_at_auto(self):
        rc = RequirementsChecklist(project_id="test")
        assert rc.generated_at  # Non-empty
