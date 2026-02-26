"""Data models for test-gen pipeline — codebase analysis, security audit, and test generation.

Purely mechanical analysis models (no LLM). Follows the pattern from schemas_tasks.py.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


# ── Extracted Code Models ──────────────────────────────────────────


class ExtractedParameter(BaseModel):
    """A function parameter extracted from AST."""
    name: str
    type_annotation: str = ""
    default: str = ""


class ExtractedFunction(BaseModel):
    """A function extracted from source code via AST parsing."""
    name: str
    params: list[ExtractedParameter] = []
    return_type: str = ""
    complexity: int = 1
    body_source: str = ""
    line_number: int = 0
    is_async: bool = False
    is_method: bool = False
    class_name: str = ""
    decorators: list[str] = []
    docstring: str = ""


class SourceFile(BaseModel):
    """A source file with its extracted functions, imports, and classes."""
    path: str
    functions: list[ExtractedFunction] = []
    imports: list[str] = []
    classes: list[str] = []


class TestFile(BaseModel):
    """An existing test file with its test functions and imported modules."""
    __test__ = False  # Prevent pytest collection
    path: str
    test_functions: list[str] = []
    imported_modules: list[str] = []
    referenced_names: list[str] = []


# ── Coverage Models ────────────────────────────────────────────────


class CoverageEntry(BaseModel):
    """Per-function coverage information."""
    function_name: str
    file_path: str
    complexity: int = 1
    test_count: int = 0
    covered: bool = False


class CoverageMap(BaseModel):
    """Coverage mapping across all source functions."""
    entries: list[CoverageEntry] = []

    @property
    def coverage_ratio(self) -> float:
        """Fraction of functions that have at least one test (0.0 - 1.0)."""
        if not self.entries:
            return 0.0
        covered = sum(1 for e in self.entries if e.covered)
        return covered / len(self.entries)

    @property
    def total_functions(self) -> int:
        return len(self.entries)

    @property
    def covered_count(self) -> int:
        return sum(1 for e in self.entries if e.covered)

    @property
    def uncovered_count(self) -> int:
        return sum(1 for e in self.entries if not e.covered)


# ── Security Models ────────────────────────────────────────────────


class SecurityRiskLevel(StrEnum):
    """Risk level for security findings."""
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class SecurityFinding(BaseModel):
    """A security-relevant finding from static analysis."""
    function_name: str
    file_path: str
    line_number: int = 0
    complexity: int = 1
    risk_level: SecurityRiskLevel = SecurityRiskLevel.medium
    pattern_matched: str = ""
    suggestion: str = ""
    covered: bool = False


class SecurityAuditReport(BaseModel):
    """Complete security audit report."""
    findings: list[SecurityFinding] = []
    analyzed_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.risk_level == SecurityRiskLevel.critical)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.risk_level == SecurityRiskLevel.high)

    @property
    def medium_count(self) -> int:
        return sum(1 for f in self.findings if f.risk_level == SecurityRiskLevel.medium)

    @property
    def low_count(self) -> int:
        return sum(1 for f in self.findings if f.risk_level == SecurityRiskLevel.low)

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.risk_level == SecurityRiskLevel.info)


# ── Analysis & Plan Models ─────────────────────────────────────────


class CodebaseAnalysis(BaseModel):
    """Complete mechanical analysis result for a codebase."""
    root_path: str
    language: str = "python"
    source_files: list[SourceFile] = []
    test_files: list[TestFile] = []
    coverage: CoverageMap = Field(default_factory=CoverageMap)
    security: SecurityAuditReport = Field(default_factory=SecurityAuditReport)

    @property
    def total_functions(self) -> int:
        return sum(len(sf.functions) for sf in self.source_files)

    @property
    def total_source_files(self) -> int:
        return len(self.source_files)

    @property
    def total_test_files(self) -> int:
        return len(self.test_files)


class TestGenPlanEntry(BaseModel):
    """A single function prioritized for test generation."""
    __test__ = False  # Prevent pytest collection
    function_name: str
    file_path: str
    module_name: str = ""
    complexity: int = 1
    security_sensitive: bool = False
    priority: int = 0


class TestGenPlan(BaseModel):
    """Prioritized list of functions needing contracts/tests."""
    __test__ = False  # Prevent pytest collection
    entries: list[TestGenPlanEntry] = []
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    @property
    def total(self) -> int:
        return len(self.entries)

    @property
    def security_sensitive_count(self) -> int:
        return sum(1 for e in self.entries if e.security_sensitive)


class TestGenResult(BaseModel):
    """Final output of the test-gen pipeline."""
    __test__ = False  # Prevent pytest collection
    contracts_generated: int = 0
    tests_generated: int = 0
    security_findings: int = 0
    coverage_before: float = 0.0
    output_path: str = ""
    total_cost_usd: float = 0.0
    dry_run: bool = False
