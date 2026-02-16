"""Task list, cross-artifact analysis, and requirements checklist models.

Spec-kit capabilities for Pact: granular phased task lists, consistency
analysis, and requirements quality validation. All purely mechanical —
no LLM calls required.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field


# ── Task List Enums ─────────────────────────────────────────────────


class TaskPhase(StrEnum):
    """Phase of the task lifecycle."""
    setup = "setup"
    foundational = "foundational"
    component = "component"
    integration = "integration"
    polish = "polish"


class TaskStatus(StrEnum):
    """Status of an individual task item."""
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    skipped = "skipped"
    failed = "failed"


class TaskCategory(StrEnum):
    """Category of work for a task item."""
    scaffold = "scaffold"
    type_definition = "type_definition"
    contract_review = "contract_review"
    test_setup = "test_setup"
    test_write = "test_write"
    implement = "implement"
    verify = "verify"
    integrate = "integrate"
    validate = "validate"
    document = "document"


# ── Task List Models ────────────────────────────────────────────────


class TaskItem(BaseModel):
    """A single trackable task in the phased task list."""
    id: str = Field(..., description="Unique task ID, e.g. T001")
    phase: TaskPhase
    component_id: str = Field(default="", description="Associated component ID")
    description: str
    file_path: str = Field(default="", description="Related file or directory path")
    status: TaskStatus = TaskStatus.pending
    parallel: bool = Field(default=False, description="Can run in parallel with siblings")
    depends_on: list[str] = Field(default_factory=list, description="Task IDs this depends on")
    category: TaskCategory = TaskCategory.scaffold


class PhaseCheckpoint(BaseModel):
    """A checkpoint marker after a phase completes."""
    after_phase: TaskPhase
    description: str
    validation: str = Field(default="", description="What to verify at this checkpoint")


class TaskList(BaseModel):
    """Complete phased task list for a project."""
    project_id: str
    tasks: list[TaskItem] = Field(default_factory=list)
    checkpoints: list[PhaseCheckpoint] = Field(default_factory=list)
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    version: int = 1

    @property
    def total(self) -> int:
        """Total number of tasks."""
        return len(self.tasks)

    @property
    def completed(self) -> int:
        """Number of completed tasks."""
        return sum(1 for t in self.tasks if t.status == TaskStatus.completed)

    @property
    def pending(self) -> int:
        """Number of pending tasks."""
        return sum(1 for t in self.tasks if t.status == TaskStatus.pending)

    def tasks_for_phase(self, phase: TaskPhase) -> list[TaskItem]:
        """Return tasks in a specific phase."""
        return [t for t in self.tasks if t.phase == phase]

    def tasks_for_component(self, component_id: str) -> list[TaskItem]:
        """Return tasks for a specific component."""
        return [t for t in self.tasks if t.component_id == component_id]

    def ready_tasks(self) -> list[TaskItem]:
        """Return tasks whose dependencies are all completed."""
        completed_ids = {t.id for t in self.tasks if t.status == TaskStatus.completed}
        return [
            t for t in self.tasks
            if t.status == TaskStatus.pending
            and all(dep in completed_ids for dep in t.depends_on)
        ]

    def mark_complete(self, task_id: str) -> bool:
        """Mark a task as completed. Returns True if found."""
        for t in self.tasks:
            if t.id == task_id:
                t.status = TaskStatus.completed
                return True
        return False


# ── Analysis Enums ──────────────────────────────────────────────────


class FindingSeverity(StrEnum):
    """Severity of an analysis finding."""
    error = "error"
    warning = "warning"
    info = "info"


class FindingCategory(StrEnum):
    """Category of an analysis finding."""
    coverage_gap = "coverage_gap"
    ambiguity = "ambiguity"
    duplication = "duplication"
    consistency = "consistency"
    completeness = "completeness"


# ── Analysis Models ─────────────────────────────────────────────────


class AnalysisFinding(BaseModel):
    """A single finding from cross-artifact analysis."""
    id: str = Field(..., description="Finding ID, e.g. F001")
    severity: FindingSeverity
    category: FindingCategory
    component_id: str = Field(default="", description="Related component ID")
    description: str
    suggestion: str = Field(default="", description="Suggested remediation")
    artifacts: list[str] = Field(default_factory=list, description="Related artifact paths")


class AnalysisReport(BaseModel):
    """Cross-artifact analysis report."""
    project_id: str
    findings: list[AnalysisFinding] = Field(default_factory=list)
    analyzed_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    summary: str = ""

    @property
    def errors(self) -> list[AnalysisFinding]:
        """Return error-severity findings."""
        return [f for f in self.findings if f.severity == FindingSeverity.error]

    @property
    def warnings(self) -> list[AnalysisFinding]:
        """Return warning-severity findings."""
        return [f for f in self.findings if f.severity == FindingSeverity.warning]


# ── Checklist Enums ─────────────────────────────────────────────────


class ChecklistCategory(StrEnum):
    """Category of a requirements checklist item."""
    requirements = "requirements"
    acceptance_criteria = "acceptance_criteria"
    edge_cases = "edge_cases"
    error_handling = "error_handling"
    dependencies = "dependencies"
    testability = "testability"


# ── Checklist Models ────────────────────────────────────────────────


class ChecklistItem(BaseModel):
    """A single requirements validation question."""
    id: str = Field(..., description="Checklist item ID, e.g. C001")
    category: ChecklistCategory
    question: str
    component_id: str = Field(default="", description="Related component ID")
    reference: str = Field(default="", description="Reference to contract/test")
    satisfied: Optional[bool] = Field(default=None, description="Tri-state: True/False/None(unanswered)")


class RequirementsChecklist(BaseModel):
    """Requirements quality validation checklist."""
    project_id: str
    items: list[ChecklistItem] = Field(default_factory=list)
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    @property
    def unanswered(self) -> int:
        """Number of unanswered (None) items."""
        return sum(1 for i in self.items if i.satisfied is None)

    @property
    def satisfied_count(self) -> int:
        """Number of satisfied (True) items."""
        return sum(1 for i in self.items if i.satisfied is True)
