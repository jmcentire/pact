"""Architectural assessment models — codebase structural friction analysis.

Data models for mechanical codebase architecture assessment. Detects
shallow modules, hub dependencies, tight coupling, scattered logic,
and test coverage gaps. No LLM calls required.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field

from pact.schemas_tasks import FindingSeverity


# ── Assessment Categories ──────────────────────────────────────────


class AssessmentCategory(StrEnum):
    """Category of an architectural assessment finding."""
    shallow_module = "shallow_module"
    hub_dependency = "hub_dependency"
    tight_coupling = "tight_coupling"
    scattered_logic = "scattered_logic"
    test_gap = "test_gap"


# ── Per-Module Metrics ─────────────────────────────────────────────


class ModuleMetrics(BaseModel):
    """Computed metrics for a single source module."""
    path: str = Field(..., description="Relative path from project root")
    loc: int = Field(0, description="Lines of code (non-empty, non-comment)")
    public_functions: int = Field(0, description="Top-level public function count")
    public_classes: int = Field(0, description="Top-level public class count")
    interface_size: int = Field(0, description="Total public names (functions + classes)")
    depth_ratio: float = Field(
        0.0,
        description="LOC / interface_size — low means shallow",
    )
    fan_in: int = Field(0, description="Number of modules that import this one")
    fan_out: int = Field(0, description="Number of modules this imports from")
    imports: list[str] = Field(
        default_factory=list,
        description="Intra-project modules imported by this module",
    )


# ── Findings ───────────────────────────────────────────────────────


class AssessmentFinding(BaseModel):
    """A single architectural assessment finding."""
    id: str = Field(..., description="Finding ID, e.g. A001")
    severity: FindingSeverity
    category: AssessmentCategory
    module_path: str = Field(default="", description="Primary module involved")
    description: str
    suggestion: str = Field(default="", description="Suggested remediation")
    metric_value: float = Field(
        default=0.0, description="The metric that triggered this finding",
    )
    related_modules: list[str] = Field(
        default_factory=list,
        description="Other modules involved in this finding",
    )


# ── Report ─────────────────────────────────────────────────────────


class AssessmentReport(BaseModel):
    """Architectural assessment report for a codebase."""
    root_path: str
    language: str = "python"
    findings: list[AssessmentFinding] = Field(default_factory=list)
    module_metrics: list[ModuleMetrics] = Field(default_factory=list)
    assessed_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    summary: str = ""

    @property
    def errors(self) -> list[AssessmentFinding]:
        return [f for f in self.findings if f.severity == FindingSeverity.error]

    @property
    def warnings(self) -> list[AssessmentFinding]:
        return [f for f in self.findings if f.severity == FindingSeverity.warning]

    @property
    def infos(self) -> list[AssessmentFinding]:
        return [f for f in self.findings if f.severity == FindingSeverity.info]
