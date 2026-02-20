"""Tests for integrator module — composition logic."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from pact.integrator import (
    integrate_all_iterative,
    integrate_component,
    integrate_component_iterative,
)
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionNode,
    DecompositionTree,
    FieldSpec,
    FunctionContract,
    TestResults,
)


class TestIntegrationTree:
    """Test tree operations relevant to integration."""

    def test_non_leaves_need_integration(self):
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root", description="r",
                    children=["a", "b"],
                ),
                "a": DecompositionNode(
                    component_id="a", name="A", description="a", parent_id="root",
                ),
                "b": DecompositionNode(
                    component_id="b", name="B", description="b", parent_id="root",
                ),
            },
        )
        non_leaves = [n for n in tree.nodes.values() if n.children]
        assert len(non_leaves) == 1
        assert non_leaves[0].component_id == "root"

    def test_all_leaves_skip_integration(self):
        tree = DecompositionTree(
            root_id="main",
            nodes={
                "main": DecompositionNode(
                    component_id="main", name="Main", description="d",
                ),
            },
        )
        non_leaves = [n for n in tree.nodes.values() if n.children]
        assert len(non_leaves) == 0

    def test_child_contracts_for_integration(self):
        """Verify we can gather child contracts for a parent."""
        contracts = {
            "root": ComponentContract(
                component_id="root", name="Root", description="r",
                dependencies=["a", "b"],
                functions=[FunctionContract(
                    name="process", description="d",
                    inputs=[], output_type="str",
                )],
            ),
            "a": ComponentContract(
                component_id="a", name="A", description="a",
                functions=[FunctionContract(
                    name="do_a", description="d",
                    inputs=[], output_type="str",
                )],
            ),
            "b": ComponentContract(
                component_id="b", name="B", description="b",
                functions=[FunctionContract(
                    name="do_b", description="d",
                    inputs=[], output_type="int",
                )],
            ),
        }

        parent = contracts["root"]
        child_contracts = {
            dep: contracts[dep]
            for dep in parent.dependencies
            if dep in contracts
        }
        assert len(child_contracts) == 2
        assert "a" in child_contracts
        assert "b" in child_contracts


def _make_contract(cid, name, funcs=None):
    """Helper to build a ComponentContract."""
    return ComponentContract(
        component_id=cid,
        name=name,
        description=f"{name} description",
        functions=funcs or [FunctionContract(
            name="run", description="d", inputs=[], output_type="str",
        )],
    )


def _make_test_suite(cid, code="# tests"):
    return ContractTestSuite(
        component_id=cid,
        contract_version=1,
        generated_code=code,
    )


class TestIntegrateComponentIterative:
    """Tests for the iterative Claude Code integration path."""

    def test_function_exists(self):
        assert callable(integrate_component_iterative)

    def test_function_is_async(self):
        assert inspect.iscoroutinefunction(integrate_component_iterative)

    def test_signature_has_expected_params(self):
        sig = inspect.signature(integrate_component_iterative)
        params = set(sig.parameters.keys())
        assert "project" in params
        assert "parent_id" in params
        assert "parent_contract" in params
        assert "parent_test_suite" in params
        assert "child_contracts" in params
        assert "budget" in params
        assert "model" in params
        assert "max_turns" in params
        assert "timeout" in params

    def test_prompt_includes_parent_and_children(self, tmp_path):
        """The prompt sent to Claude Code should reference parent and children."""
        parent = _make_contract("root", "Root")
        child_a = _make_contract("a", "ChildA")
        child_b = _make_contract("b", "ChildB")
        test_suite = _make_test_suite("root", "def test_root(): pass")

        project = MagicMock()
        project.project_dir = tmp_path
        project.test_code_path.return_value = tmp_path / "tests" / "test.py"
        project.composition_dir.return_value = tmp_path / "comp" / "root"
        project.impl_src_dir.side_effect = lambda cid: tmp_path / "impl" / cid / "src"
        (tmp_path / "comp" / "root").mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)

        captured_prompt = {}

        async def mock_implement(prompt, working_dir=None, max_turns=30, timeout=600):
            captured_prompt["text"] = prompt
            return ("done", 0, 0)

        budget = MagicMock()
        budget.record_tokens_validated = MagicMock(return_value=True)

        with patch("pact.backends.claude_code.ClaudeCodeBackend") as MockBackend:
            instance = MockBackend.return_value
            instance.implement = AsyncMock(side_effect=mock_implement)

            with patch("pact.integrator.run_contract_tests", new_callable=AsyncMock) as mock_tests:
                mock_tests.return_value = TestResults(
                    total=5, passed=5, failed=0, errors=0,
                )
                asyncio.run(integrate_component_iterative(
                    project=project,
                    parent_id="root",
                    parent_contract=parent,
                    parent_test_suite=test_suite,
                    child_contracts={"a": child_a, "b": child_b},
                    budget=budget,
                ))

        prompt_text = captured_prompt["text"]
        assert "Root" in prompt_text
        assert "ChildA" in prompt_text
        assert "ChildB" in prompt_text

    def test_returns_test_results(self, tmp_path):
        """Should return TestResults from running parent tests."""
        parent = _make_contract("root", "Root")
        test_suite = _make_test_suite("root")

        project = MagicMock()
        project.project_dir = tmp_path
        project.test_code_path.return_value = tmp_path / "tests" / "test.py"
        project.composition_dir.return_value = tmp_path / "comp" / "root"
        project.impl_src_dir.side_effect = lambda cid: tmp_path / "impl" / cid / "src"
        (tmp_path / "comp" / "root").mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)

        budget = MagicMock()
        budget.record_tokens_validated = MagicMock(return_value=True)

        expected = TestResults(total=10, passed=10, failed=0, errors=0)

        with patch("pact.backends.claude_code.ClaudeCodeBackend") as MockBackend:
            instance = MockBackend.return_value
            instance.implement = AsyncMock(return_value=("done", 0, 0))

            with patch("pact.integrator.run_contract_tests", new_callable=AsyncMock) as mock_tests:
                mock_tests.return_value = expected
                result = asyncio.run(integrate_component_iterative(
                    project=project,
                    parent_id="root",
                    parent_contract=parent,
                    parent_test_suite=test_suite,
                    child_contracts={},
                    budget=budget,
                ))

        assert result.total == 10
        assert result.passed == 10
        assert result.all_passed

    def test_handles_implement_failure(self, tmp_path):
        """Should still return test results even if implement() raises."""
        parent = _make_contract("root", "Root")
        test_suite = _make_test_suite("root")

        project = MagicMock()
        project.project_dir = tmp_path
        project.test_code_path.return_value = tmp_path / "tests" / "test.py"
        project.composition_dir.return_value = tmp_path / "comp" / "root"
        project.impl_src_dir.side_effect = lambda cid: tmp_path / "impl" / cid / "src"
        (tmp_path / "comp" / "root").mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)

        budget = MagicMock()

        with patch("pact.backends.claude_code.ClaudeCodeBackend") as MockBackend:
            instance = MockBackend.return_value
            instance.implement = AsyncMock(side_effect=RuntimeError("timeout"))

            with patch("pact.integrator.run_contract_tests", new_callable=AsyncMock) as mock_tests:
                mock_tests.return_value = TestResults(
                    total=5, passed=0, failed=5, errors=0,
                )
                result = asyncio.run(integrate_component_iterative(
                    project=project,
                    parent_id="root",
                    parent_contract=parent,
                    parent_test_suite=test_suite,
                    child_contracts={},
                    budget=budget,
                ))

        assert result.total == 5
        assert result.failed == 5

    def test_audit_entries_written(self, tmp_path):
        """Should write audit entries for integration and test run."""
        parent = _make_contract("root", "Root")
        test_suite = _make_test_suite("root")

        project = MagicMock()
        project.project_dir = tmp_path
        project.test_code_path.return_value = tmp_path / "tests" / "test.py"
        project.composition_dir.return_value = tmp_path / "comp" / "root"
        project.impl_src_dir.side_effect = lambda cid: tmp_path / "impl" / cid / "src"
        (tmp_path / "comp" / "root").mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)

        budget = MagicMock()

        with patch("pact.backends.claude_code.ClaudeCodeBackend") as MockBackend:
            instance = MockBackend.return_value
            instance.implement = AsyncMock(return_value=("done", 0, 0))

            with patch("pact.integrator.run_contract_tests", new_callable=AsyncMock) as mock_tests:
                mock_tests.return_value = TestResults(
                    total=3, passed=3, failed=0, errors=0,
                )
                asyncio.run(integrate_component_iterative(
                    project=project,
                    parent_id="root",
                    parent_contract=parent,
                    parent_test_suite=test_suite,
                    child_contracts={},
                    budget=budget,
                ))

        audit_calls = [c for c in project.append_audit.call_args_list]
        audit_actions = [c[0][0] for c in audit_calls]
        assert "integration" in audit_actions
        assert "test_run" in audit_actions


class TestIntegrateAllIterative:
    """Tests for integrate_all_iterative dispatch."""

    def test_function_exists(self):
        assert callable(integrate_all_iterative)

    def test_function_is_async(self):
        assert inspect.iscoroutinefunction(integrate_all_iterative)

    def test_skips_leaf_nodes(self, tmp_path):
        """Should only integrate non-leaf nodes."""
        tree = DecompositionTree(
            root_id="a",
            nodes={
                "a": DecompositionNode(
                    component_id="a", name="A", description="leaf",
                ),
            },
        )

        project = MagicMock()
        project.load_all_contracts.return_value = {}
        project.load_all_test_suites.return_value = {}
        project.save_tree = MagicMock()

        budget = MagicMock()

        results = asyncio.run(integrate_all_iterative(
            project=project, tree=tree, budget=budget,
        ))

        assert results == {}

    def test_integrates_non_leaves(self, tmp_path):
        """Should dispatch integration for non-leaf nodes."""
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root", description="r",
                    children=["a", "b"],
                ),
                "a": DecompositionNode(
                    component_id="a", name="A", description="a",
                    parent_id="root",
                ),
                "b": DecompositionNode(
                    component_id="b", name="B", description="b",
                    parent_id="root",
                ),
            },
        )

        root_contract = _make_contract("root", "Root")
        a_contract = _make_contract("a", "A")
        b_contract = _make_contract("b", "B")
        root_suite = _make_test_suite("root")

        project = MagicMock()
        project.load_all_contracts.return_value = {
            "root": root_contract, "a": a_contract, "b": b_contract,
        }
        project.load_all_test_suites.return_value = {"root": root_suite}
        project.save_tree = MagicMock()

        expected = TestResults(total=5, passed=5, failed=0, errors=0)

        with patch(
            "pact.integrator.integrate_component_iterative",
            new_callable=AsyncMock,
            return_value=expected,
        ):
            results = asyncio.run(integrate_all_iterative(
                project=project, tree=tree, budget=MagicMock(),
            ))

        assert "root" in results
        assert results["root"].all_passed


class TestIntegrateComponentPrompt:
    """Tests that integrate_component sends full child contracts, not placeholders."""

    def test_prompt_contains_full_child_contracts(self, tmp_path):
        """The prompt should contain actual child contract JSON, not <contract> placeholders."""
        parent = _make_contract("root", "Root", [
            FunctionContract(name="process", description="d", inputs=[], output_type="str"),
        ])
        child_a = _make_contract("a", "ChildA", [
            FunctionContract(name="do_a", description="d", inputs=[], output_type="str"),
        ])
        test_suite = _make_test_suite("root", "def test_root(): pass")

        project = MagicMock()
        project.language = "python"
        project.composition_dir.return_value = tmp_path / "comp" / "root"
        project.test_code_path.return_value = tmp_path / "tests" / "test.py"
        project.impl_src_dir.side_effect = lambda cid: tmp_path / "impl" / cid / "src"
        (tmp_path / "comp" / "root").mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)

        captured_prompt = {}

        async def mock_assess(model, prompt, system):
            captured_prompt["text"] = prompt
            from pydantic import BaseModel
            class R(BaseModel):
                glue_code: str = "# stub"
                composition_test: str = ""
            return R(), 0, 0

        agent = MagicMock()
        agent.assess = AsyncMock(side_effect=mock_assess)

        with patch("pact.integrator.run_contract_tests", new_callable=AsyncMock) as mock_tests:
            mock_tests.return_value = TestResults(total=1, passed=1, failed=0, errors=0)
            asyncio.run(integrate_component(
                agent=agent,
                project=project,
                parent_id="root",
                parent_contract=parent,
                parent_test_suite=test_suite,
                child_contracts={"a": child_a},
            ))

        prompt_text = captured_prompt["text"]
        # Should contain actual contract JSON, not <contract> placeholder
        assert "<contract>" not in prompt_text
        assert "ChildA" in prompt_text
        assert "do_a" in prompt_text
        # Should contain the component_id from the JSON
        assert '"component_id"' in prompt_text or "component_id" in prompt_text

    def test_prompt_includes_child_implementations(self, tmp_path):
        """If child implementations exist on disk, they should appear in the prompt."""
        parent = _make_contract("root", "Root")
        child_a = _make_contract("a", "ChildA")
        test_suite = _make_test_suite("root", "def test_root(): pass")

        # Create a mock child implementation file
        impl_src = tmp_path / "impl" / "a" / "src"
        impl_src.mkdir(parents=True)
        (impl_src / "a.py").write_text("def do_a():\n    return 'hello'\n")

        project = MagicMock()
        project.language = "python"
        project.composition_dir.return_value = tmp_path / "comp" / "root"
        project.test_code_path.return_value = tmp_path / "tests" / "test.py"
        project.impl_src_dir.side_effect = lambda cid: tmp_path / "impl" / cid / "src"
        (tmp_path / "comp" / "root").mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)

        captured_prompt = {}

        async def mock_assess(model, prompt, system):
            captured_prompt["text"] = prompt
            from pydantic import BaseModel
            class R(BaseModel):
                glue_code: str = "# stub"
                composition_test: str = ""
            return R(), 0, 0

        agent = MagicMock()
        agent.assess = AsyncMock(side_effect=mock_assess)

        with patch("pact.integrator.run_contract_tests", new_callable=AsyncMock) as mock_tests:
            mock_tests.return_value = TestResults(total=1, passed=1, failed=0, errors=0)
            asyncio.run(integrate_component(
                agent=agent,
                project=project,
                parent_id="root",
                parent_contract=parent,
                parent_test_suite=test_suite,
                child_contracts={"a": child_a},
            ))

        prompt_text = captured_prompt["text"]
        # Should contain child implementation source code
        assert "def do_a():" in prompt_text
        assert "return 'hello'" in prompt_text
        assert "=== a implementation" in prompt_text
