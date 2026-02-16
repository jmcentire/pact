"""Tests for interactive (Claude Code team) implementation (P2-1)."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from pact.schemas import ComponentContract, ContractTestSuite, FunctionContract, TypeSpec


def _make_contract():
    return ComponentContract(
        component_id="test_comp",
        name="Test Component",
        description="A test component",
        types=[TypeSpec(name="Item", kind="struct", fields=[])],
        functions=[FunctionContract(
            name="process",
            description="Process an item",
            inputs=[],
            output_type="Item",
        )],
    )


def _make_test_suite():
    return ContractTestSuite(
        component_id="test_comp",
        contract_version=1,
        test_cases=[],
        generated_code="def test_placeholder(): pass",
    )


class TestImplementComponentInteractive:
    def test_function_exists(self):
        from pact.implementer import implement_component_interactive
        assert callable(implement_component_interactive)

    def test_prompt_contains_handoff(self, tmp_path):
        """The prompt sent to the team backend should contain handoff brief content."""
        from pact.implementer import implement_component_interactive
        from pact.project import ProjectManager

        # We'll verify the prompt by checking what AgentTask gets
        project = ProjectManager(tmp_path)
        project.init()
        contract = _make_contract()
        test_suite = _make_test_suite()

        captured_tasks = []

        class MockTeamBackend:
            async def spawn_agent(self, task):
                captured_tasks.append(task)

            async def wait_for_completion(self, output_file, timeout=None):
                return ""

        import asyncio
        asyncio.run(implement_component_interactive(
            team_backend=MockTeamBackend(),
            project=project,
            component_id="test_comp",
            contract=contract,
            test_suite=test_suite,
        ))

        assert len(captured_tasks) == 1
        assert "Test Component" in captured_tasks[0].prompt
        assert "process" in captured_tasks[0].prompt

    def test_prompt_contains_test_instructions(self, tmp_path):
        """Prompt should tell the agent to run tests."""
        from pact.implementer import implement_component_interactive
        from pact.project import ProjectManager

        project = ProjectManager(tmp_path)
        project.init()
        contract = _make_contract()
        test_suite = _make_test_suite()

        captured_tasks = []

        class MockTeamBackend:
            async def spawn_agent(self, task):
                captured_tasks.append(task)

            async def wait_for_completion(self, output_file, timeout=None):
                return ""

        import asyncio
        asyncio.run(implement_component_interactive(
            team_backend=MockTeamBackend(),
            project=project,
            component_id="test_comp",
            contract=contract,
            test_suite=test_suite,
        ))

        assert "pytest" in captured_tasks[0].prompt

    def test_test_file_written(self, tmp_path):
        """Test file should be written before agent is spawned."""
        from pact.implementer import implement_component_interactive
        from pact.project import ProjectManager

        project = ProjectManager(tmp_path)
        project.init()
        contract = _make_contract()
        test_suite = _make_test_suite()

        class MockTeamBackend:
            async def spawn_agent(self, task):
                pass
            async def wait_for_completion(self, output_file, timeout=None):
                return ""

        import asyncio
        asyncio.run(implement_component_interactive(
            team_backend=MockTeamBackend(),
            project=project,
            component_id="test_comp",
            contract=contract,
            test_suite=test_suite,
        ))

        test_file = project.test_code_path("test_comp")
        assert test_file.exists()

    def test_audit_entry_written(self, tmp_path):
        """Implementation should create audit entries."""
        from pact.implementer import implement_component_interactive
        from pact.project import ProjectManager

        project = ProjectManager(tmp_path)
        project.init()
        contract = _make_contract()
        test_suite = _make_test_suite()

        class MockTeamBackend:
            async def spawn_agent(self, task):
                pass
            async def wait_for_completion(self, output_file, timeout=None):
                return ""

        import asyncio
        asyncio.run(implement_component_interactive(
            team_backend=MockTeamBackend(),
            project=project,
            component_id="test_comp",
            contract=contract,
            test_suite=test_suite,
        ))

        audit_path = tmp_path / ".pact" / "audit.jsonl"
        assert audit_path.exists()
        content = audit_path.read_text()
        assert "interactive" in content

    def test_handles_spawn_failure(self, tmp_path):
        """Should handle team backend failures gracefully."""
        from pact.implementer import implement_component_interactive
        from pact.project import ProjectManager

        project = ProjectManager(tmp_path)
        project.init()
        contract = _make_contract()
        test_suite = _make_test_suite()

        class MockTeamBackend:
            async def spawn_agent(self, task):
                raise RuntimeError("No tmux session")
            async def wait_for_completion(self, output_file, timeout=None):
                return ""

        import asyncio
        # Should not raise -- handles error gracefully
        result = asyncio.run(implement_component_interactive(
            team_backend=MockTeamBackend(),
            project=project,
            component_id="test_comp",
            contract=contract,
            test_suite=test_suite,
        ))
        # Returns TestResults even on failure
        assert result is not None

    def test_metadata_saved(self, tmp_path):
        """Implementation metadata should include method=interactive."""
        from pact.implementer import implement_component_interactive
        from pact.project import ProjectManager

        project = ProjectManager(tmp_path)
        project.init()
        contract = _make_contract()
        test_suite = _make_test_suite()

        class MockTeamBackend:
            async def spawn_agent(self, task):
                pass
            async def wait_for_completion(self, output_file, timeout=None):
                return ""

        import asyncio
        asyncio.run(implement_component_interactive(
            team_backend=MockTeamBackend(),
            project=project,
            component_id="test_comp",
            contract=contract,
            test_suite=test_suite,
        ))

        meta_path = project.impl_dir("test_comp") / "metadata.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta.get("method") == "interactive"


class TestImplementComponentIterative:
    """Tests for the iterative Claude Code implementation path."""

    def test_function_exists(self):
        from pact.implementer import implement_component_iterative
        assert callable(implement_component_iterative)

    def test_implement_all_iterative_exists(self):
        from pact.implementer import implement_all_iterative
        assert callable(implement_all_iterative)

    def test_prompt_contains_contract_info(self, tmp_path):
        """The iterative prompt should include contract info and test instructions."""
        from pact.implementer import implement_component_iterative
        from pact.project import ProjectManager
        from pact.budget import BudgetTracker

        project = ProjectManager(tmp_path)
        project.init()
        contract = _make_contract()
        test_suite = _make_test_suite()

        captured_prompts = []

        async def mock_implement(self, prompt, **kwargs):
            captured_prompts.append(prompt)
            return "done", 100, 50

        import asyncio
        with patch(
            "pact.backends.claude_code.ClaudeCodeBackend.implement",
            mock_implement,
        ):
            budget = BudgetTracker(per_project_cap=10.0)
            asyncio.run(implement_component_iterative(
                project=project,
                component_id="test_comp",
                contract=contract,
                test_suite=test_suite,
                budget=budget,
                model="claude-sonnet-4-5-20250929",
            ))

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        assert "Test Component" in prompt
        assert "process" in prompt
        assert "pytest" in prompt
        assert "Pydantic v2" in prompt

    def test_test_file_written(self, tmp_path):
        """Test file should be written before Claude Code session starts."""
        from pact.implementer import implement_component_iterative
        from pact.project import ProjectManager
        from pact.budget import BudgetTracker

        project = ProjectManager(tmp_path)
        project.init()
        contract = _make_contract()
        test_suite = _make_test_suite()

        async def mock_implement(self, prompt, **kwargs):
            return "done", 100, 50

        import asyncio
        with patch(
            "pact.backends.claude_code.ClaudeCodeBackend.implement",
            mock_implement,
        ):
            budget = BudgetTracker(per_project_cap=10.0)
            asyncio.run(implement_component_iterative(
                project=project,
                component_id="test_comp",
                contract=contract,
                test_suite=test_suite,
                budget=budget,
            ))

        test_file = project.test_code_path("test_comp")
        assert test_file.exists()

    def test_metadata_saved_with_iterative_method(self, tmp_path):
        """Metadata should record method=iterative_claude_code."""
        from pact.implementer import implement_component_iterative
        from pact.project import ProjectManager
        from pact.budget import BudgetTracker

        project = ProjectManager(tmp_path)
        project.init()
        contract = _make_contract()
        test_suite = _make_test_suite()

        async def mock_implement(self, prompt, **kwargs):
            return "done", 100, 50

        import asyncio
        with patch(
            "pact.backends.claude_code.ClaudeCodeBackend.implement",
            mock_implement,
        ):
            budget = BudgetTracker(per_project_cap=10.0)
            asyncio.run(implement_component_iterative(
                project=project,
                component_id="test_comp",
                contract=contract,
                test_suite=test_suite,
                budget=budget,
                model="claude-sonnet-4-5-20250929",
            ))

        meta_path = project.impl_dir("test_comp") / "metadata.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta.get("method") == "iterative_claude_code"
        assert meta.get("model") == "claude-sonnet-4-5-20250929"

    def test_handles_implement_failure(self, tmp_path):
        """Should handle Claude Code session failures gracefully."""
        from pact.implementer import implement_component_iterative
        from pact.project import ProjectManager
        from pact.budget import BudgetTracker

        project = ProjectManager(tmp_path)
        project.init()
        contract = _make_contract()
        test_suite = _make_test_suite()

        async def mock_implement_fail(self, prompt, **kwargs):
            raise RuntimeError("Claude CLI not found")

        import asyncio
        with patch(
            "pact.backends.claude_code.ClaudeCodeBackend.implement",
            mock_implement_fail,
        ):
            budget = BudgetTracker(per_project_cap=10.0)
            result = asyncio.run(implement_component_iterative(
                project=project,
                component_id="test_comp",
                contract=contract,
                test_suite=test_suite,
                budget=budget,
            ))

        # Should still return TestResults (not crash)
        assert result is not None

    def test_audit_entries_written(self, tmp_path):
        """Implementation and test_run audit entries should be created."""
        from pact.implementer import implement_component_iterative
        from pact.project import ProjectManager
        from pact.budget import BudgetTracker

        project = ProjectManager(tmp_path)
        project.init()
        contract = _make_contract()
        test_suite = _make_test_suite()

        async def mock_implement(self, prompt, **kwargs):
            return "done", 100, 50

        import asyncio
        with patch(
            "pact.backends.claude_code.ClaudeCodeBackend.implement",
            mock_implement,
        ):
            budget = BudgetTracker(per_project_cap=10.0)
            asyncio.run(implement_component_iterative(
                project=project,
                component_id="test_comp",
                contract=contract,
                test_suite=test_suite,
                budget=budget,
            ))

        audit_path = tmp_path / ".pact" / "audit.jsonl"
        assert audit_path.exists()
        content = audit_path.read_text()
        assert "iterative" in content
        assert "test_run" in content
