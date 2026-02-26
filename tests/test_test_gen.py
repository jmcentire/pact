"""Tests for test_gen pipeline — prioritization, rendering, and mocked LLM calls."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pact.schemas import ComponentContract, ContractTestSuite, FunctionContract, FieldSpec
from pact.schemas_testgen import (
    CodebaseAnalysis,
    CoverageEntry,
    CoverageMap,
    SecurityAuditReport,
    SecurityFinding,
    SecurityRiskLevel,
    SourceFile,
    TestFile,
    TestGenPlan,
    TestGenPlanEntry,
    TestGenResult,
)
from pact.test_gen import (
    plan_test_generation,
    render_security_audit,
    render_summary,
    reverse_engineer_contract,
    run_test_gen,
)


# ── Plan Generation ────────────────────────────────────────────────


class TestPlanGeneration:
    def _make_analysis(
        self,
        entries: list[CoverageEntry] | None = None,
        findings: list[SecurityFinding] | None = None,
    ) -> CodebaseAnalysis:
        return CodebaseAnalysis(
            root_path="/tmp/test",
            coverage=CoverageMap(entries=entries or []),
            security=SecurityAuditReport(findings=findings or []),
        )

    def test_prioritizes_security_sensitive(self):
        analysis = self._make_analysis(
            entries=[
                CoverageEntry(function_name="login", file_path="auth.py", complexity=2, covered=False),
                CoverageEntry(function_name="render", file_path="ui.py", complexity=8, covered=False),
                CoverageEntry(function_name="check_admin", file_path="auth.py", complexity=3, covered=False),
            ],
            findings=[
                SecurityFinding(function_name="check_admin", file_path="auth.py", pattern_matched="variable: is_admin"),
            ],
        )
        plan = plan_test_generation(analysis, complexity_threshold=5)
        assert plan.entries[0].function_name == "check_admin"
        assert plan.entries[0].security_sensitive is True
        assert plan.entries[0].priority == 0

    def test_prioritizes_high_complexity(self):
        analysis = self._make_analysis(
            entries=[
                CoverageEntry(function_name="simple", file_path="a.py", complexity=1, covered=False),
                CoverageEntry(function_name="complex_fn", file_path="b.py", complexity=10, covered=False),
            ],
        )
        plan = plan_test_generation(analysis, complexity_threshold=5)
        assert plan.entries[0].function_name == "complex_fn"
        assert plan.entries[0].priority == 1

    def test_skips_covered_by_default(self):
        analysis = self._make_analysis(
            entries=[
                CoverageEntry(function_name="covered_fn", file_path="a.py", covered=True, test_count=2),
                CoverageEntry(function_name="uncovered_fn", file_path="a.py", covered=False),
            ],
        )
        plan = plan_test_generation(analysis)
        names = [e.function_name for e in plan.entries]
        assert "covered_fn" not in names
        assert "uncovered_fn" in names

    def test_includes_covered_when_flag_set(self):
        analysis = self._make_analysis(
            entries=[
                CoverageEntry(function_name="covered_fn", file_path="a.py", covered=True, test_count=1),
                CoverageEntry(function_name="uncovered_fn", file_path="a.py", covered=False),
            ],
        )
        plan = plan_test_generation(analysis, skip_covered=False)
        names = [e.function_name for e in plan.entries]
        assert "covered_fn" in names
        assert "uncovered_fn" in names

    def test_empty_analysis(self):
        analysis = self._make_analysis()
        plan = plan_test_generation(analysis)
        assert plan.total == 0

    def test_module_name_from_path(self):
        analysis = self._make_analysis(
            entries=[
                CoverageEntry(function_name="foo", file_path="src/utils/helpers.py", covered=False),
            ],
        )
        plan = plan_test_generation(analysis)
        assert plan.entries[0].module_name == "src.utils.helpers"

    def test_security_sensitive_count(self):
        analysis = self._make_analysis(
            entries=[
                CoverageEntry(function_name="login", file_path="auth.py", covered=False),
                CoverageEntry(function_name="render", file_path="ui.py", covered=False),
            ],
            findings=[
                SecurityFinding(function_name="login", file_path="auth.py", pattern_matched="variable: token"),
            ],
        )
        plan = plan_test_generation(analysis)
        assert plan.security_sensitive_count == 1


# ── Security Audit Rendering ──────────────────────────────────────


class TestRenderSecurityAudit:
    def test_empty_report(self):
        report = SecurityAuditReport()
        md = render_security_audit(report)
        assert "Security Audit Report" in md
        assert "No security-sensitive patterns detected" in md

    def test_findings_grouped_by_level(self):
        report = SecurityAuditReport(findings=[
            SecurityFinding(
                function_name="grant_admin", file_path="auth.py",
                line_number=10, complexity=5,
                risk_level=SecurityRiskLevel.critical,
                pattern_matched="variable: is_admin",
                suggestion="Test both paths",
                covered=False,
            ),
            SecurityFinding(
                function_name="check_role", file_path="auth.py",
                line_number=20, complexity=3,
                risk_level=SecurityRiskLevel.high,
                pattern_matched="variable: role",
                covered=False,
            ),
        ])
        md = render_security_audit(report)
        assert "CRITICAL" in md
        assert "HIGH" in md
        assert "grant_admin" in md
        assert "NOT COVERED" in md

    def test_summary_counts(self):
        report = SecurityAuditReport(findings=[
            SecurityFinding(function_name="a", file_path="f.py", risk_level=SecurityRiskLevel.critical),
            SecurityFinding(function_name="b", file_path="f.py", risk_level=SecurityRiskLevel.high),
            SecurityFinding(function_name="c", file_path="f.py", risk_level=SecurityRiskLevel.low),
        ])
        md = render_security_audit(report)
        assert "Critical: 1" in md
        assert "High: 1" in md
        assert "Low: 1" in md
        assert "Total: 3" in md

    def test_covered_tag(self):
        report = SecurityAuditReport(findings=[
            SecurityFinding(
                function_name="verify", file_path="auth.py",
                risk_level=SecurityRiskLevel.low, covered=True,
                pattern_matched="variable: token",
            ),
        ])
        md = render_security_audit(report)
        assert "[covered]" in md


# ── Summary Rendering ──────────────────────────────────────────────


class TestRenderSummary:
    def test_dry_run_summary(self):
        result = TestGenResult(
            coverage_before=0.45,
            security_findings=3,
            output_path="/tmp/.pact/test-gen/",
            dry_run=True,
        )
        text = render_summary(result)
        assert "Dry Run" in text
        assert "45%" in text
        assert "3" in text
        assert "Contracts generated" not in text

    def test_full_run_summary(self):
        result = TestGenResult(
            contracts_generated=5,
            tests_generated=5,
            security_findings=2,
            coverage_before=0.30,
            output_path="/tmp/.pact/test-gen/",
            total_cost_usd=1.2345,
            dry_run=False,
        )
        text = render_summary(result)
        assert "Complete" in text
        assert "Contracts generated: 5" in text
        assert "Tests generated: 5" in text
        assert "$1.2345" in text


# ── Reverse Engineer Contract (mocked) ────────────────────────────


class TestReverseEngineerContract:
    @pytest.mark.asyncio
    async def test_basic_call(self):
        mock_contract = ComponentContract(
            component_id="auth",
            name="Auth",
            description="Authentication module",
            functions=[
                FunctionContract(
                    name="login",
                    description="Authenticate user",
                    inputs=[FieldSpec(name="username", type_ref="str")],
                    output_type="bool",
                ),
            ],
        )
        agent = MagicMock()
        agent.assess_cached = AsyncMock(return_value=(mock_contract, 100, 200))

        result = await reverse_engineer_contract(
            agent, "def login(username): pass", "src.auth", ["login"],
        )
        assert result.component_id == "src_auth"
        assert len(result.functions) == 1
        agent.assess_cached.assert_called_once()

    @pytest.mark.asyncio
    async def test_sets_component_id(self):
        mock_contract = ComponentContract(
            component_id="wrong_id", name="Test", description="Test module",
        )
        agent = MagicMock()
        agent.assess_cached = AsyncMock(return_value=(mock_contract, 50, 100))

        result = await reverse_engineer_contract(
            agent, "x = 1", "src.utils.helpers", [],
        )
        # Should override with derived component_id
        assert result.component_id == "src_utils_helpers"


# ── Dry Run Integration ───────────────────────────────────────────


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_no_llm(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text(textwrap.dedent("""\
            def hello(name: str) -> str:
                return f"Hello {name}"
        """))

        result = await run_test_gen(tmp_path, dry_run=True)
        assert result.dry_run is True
        assert result.contracts_generated == 0
        assert result.tests_generated == 0

        # Output files should exist
        output_dir = tmp_path / ".pact" / "test-gen"
        assert (output_dir / "analysis.json").exists()
        assert (output_dir / "plan.json").exists()
        assert (output_dir / "security_audit.md").exists()
        assert (output_dir / "security_audit.json").exists()

    @pytest.mark.asyncio
    async def test_dry_run_with_security_findings(self, tmp_path):
        (tmp_path / "auth.py").write_text(textwrap.dedent("""\
            def check_admin(user):
                if user.is_admin:
                    return True
                return False
        """))

        result = await run_test_gen(tmp_path, dry_run=True)
        assert result.security_findings >= 1

        # Check security audit content
        audit_path = tmp_path / ".pact" / "test-gen" / "security_audit.md"
        content = audit_path.read_text()
        assert "Security Audit Report" in content

    @pytest.mark.asyncio
    async def test_dry_run_empty_project(self, tmp_path):
        result = await run_test_gen(tmp_path, dry_run=True)
        assert result.dry_run is True
        assert result.security_findings == 0
        assert result.coverage_before == 0.0
