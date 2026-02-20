"""Tests for spec-compliance audit."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pact.auditor import audit_spec_compliance, render_audit_markdown
from pact.schemas import RequirementCoverage, SpecAuditResult


class TestRenderAuditMarkdown:
    def test_empty_result(self):
        result = SpecAuditResult(summary="No task.md found — nothing to audit.")
        md = render_audit_markdown(result)
        assert "Spec Compliance Audit" in md
        assert "No task.md found" in md

    def test_all_covered(self):
        result = SpecAuditResult(
            requirements=[
                RequirementCoverage(requirement="Feature A", status="covered", evidence="comp_a/main.py"),
                RequirementCoverage(requirement="Feature B", status="covered", evidence="comp_b/main.py"),
            ],
            covered_count=2, partial_count=0, gap_count=0, total_count=2,
            summary="2/2 requirements fully covered (100%)",
        )
        md = render_audit_markdown(result)
        assert "[COVERED]" in md
        assert "Feature A" in md
        assert "100%" in md

    def test_mixed_coverage(self):
        result = SpecAuditResult(
            requirements=[
                RequirementCoverage(requirement="Encryption", status="covered", evidence="crypto/"),
                RequirementCoverage(requirement="Key rotation", status="partial", notes="Missing scheduled rotation"),
                RequirementCoverage(requirement="Audit logging", status="gap", notes="Not implemented"),
            ],
            covered_count=1, partial_count=1, gap_count=1, total_count=3,
            summary="1/3 requirements fully covered (33%)",
        )
        md = render_audit_markdown(result)
        assert "[COVERED]" in md
        assert "[PARTIAL]" in md
        assert "[GAP]" in md
        assert "Missing scheduled rotation" in md
        assert "Not implemented" in md


class TestAuditSpecCompliance:
    @pytest.mark.asyncio
    async def test_no_task_md(self, tmp_path):
        """Audit with no task.md returns informative message."""
        project = MagicMock()
        project.task_path = tmp_path / "task.md"  # does not exist
        agent = MagicMock()

        result = await audit_spec_compliance(agent, project)
        assert "No task.md" in result.summary

    @pytest.mark.asyncio
    async def test_no_implementations(self, tmp_path):
        """Audit with task.md but no implementations."""
        task_path = tmp_path / "task.md"
        task_path.write_text("# My Task\n- Do something\n")

        tree = MagicMock()
        tree.nodes = {"comp_a": MagicMock()}

        project = MagicMock()
        project.task_path = task_path
        project.load_tree.return_value = tree
        # impl_src_dir returns a non-existent path
        impl_dir = tmp_path / "impl" / "comp_a" / "src"
        project.impl_src_dir.return_value = impl_dir

        agent = MagicMock()

        result = await audit_spec_compliance(agent, project)
        assert "No implementations" in result.summary

    @pytest.mark.asyncio
    async def test_calls_agent_with_spec_and_code(self, tmp_path):
        """Audit should send both spec and implementation code to the agent."""
        task_path = tmp_path / "task.md"
        task_path.write_text("# Spec\n- Requirement A\n- Requirement B\n")

        # Create fake implementation
        impl_dir = tmp_path / "impl" / "comp_a" / "src"
        impl_dir.mkdir(parents=True)
        (impl_dir / "main.py").write_text("def do_a(): pass\n")

        tree = MagicMock()
        tree.nodes = {"comp_a": MagicMock()}

        project = MagicMock()
        project.task_path = task_path
        project.load_tree.return_value = tree
        project.impl_src_dir.return_value = impl_dir

        mock_result = SpecAuditResult(
            requirements=[
                RequirementCoverage(requirement="Requirement A", status="covered"),
                RequirementCoverage(requirement="Requirement B", status="gap"),
            ],
        )

        agent = MagicMock()
        agent.assess = AsyncMock(return_value=(mock_result, 100, 200))

        result = await audit_spec_compliance(agent, project)

        # Verify agent.assess was called
        agent.assess.assert_called_once()
        call_args = agent.assess.call_args
        prompt = call_args[0][1]  # second positional arg

        # Prompt should contain spec and implementation
        assert "Requirement A" in prompt
        assert "def do_a" in prompt

        # Counts should be recomputed
        assert result.total_count == 2
        assert result.covered_count == 1
        assert result.gap_count == 1
