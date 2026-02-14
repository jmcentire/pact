"""Tests for project directory lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pact.project import ProjectManager
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionNode,
    DecompositionTree,
    DesignDocument,
    FieldSpec,
    FunctionContract,
    InterviewResult,
    RunState,
    TestCase,
)


@pytest.fixture
def tmp_project(tmp_path: Path) -> ProjectManager:
    """Create and init a temporary project."""
    pm = ProjectManager(tmp_path / "test-project")
    pm.init()
    return pm


class TestProjectInit:
    def test_creates_directories(self, tmp_project: ProjectManager):
        assert tmp_project.project_dir.exists()
        assert (tmp_project.project_dir / ".pact").exists()
        assert (tmp_project.project_dir / ".pact" / "decomposition").exists()
        assert (tmp_project.project_dir / ".pact" / "contracts").exists()
        assert (tmp_project.project_dir / ".pact" / "implementations").exists()
        assert (tmp_project.project_dir / ".pact" / "compositions").exists()
        assert (tmp_project.project_dir / ".pact" / "learnings").exists()

    def test_creates_task_template(self, tmp_project: ProjectManager):
        assert tmp_project.task_path.exists()
        assert "Task" in tmp_project.task_path.read_text()

    def test_creates_sops_template(self, tmp_project: ProjectManager):
        assert tmp_project.sops_path.exists()
        assert "Operating Procedures" in tmp_project.sops_path.read_text()

    def test_creates_config(self, tmp_project: ProjectManager):
        assert tmp_project.config_path.exists()

    def test_creates_design_doc(self, tmp_project: ProjectManager):
        assert tmp_project.design_path.exists()

    def test_idempotent(self, tmp_project: ProjectManager):
        # Write custom content
        tmp_project.task_path.write_text("# My Task")
        # Re-init should not overwrite
        tmp_project.init()
        assert tmp_project.task_path.read_text() == "# My Task"


class TestTaskAndConfig:
    def test_load_task(self, tmp_project: ProjectManager):
        tmp_project.task_path.write_text("# Build pricing engine")
        task = tmp_project.load_task()
        assert "pricing engine" in task

    def test_load_task_missing(self, tmp_path: Path):
        pm = ProjectManager(tmp_path / "no-project")
        with pytest.raises(FileNotFoundError):
            pm.load_task()

    def test_load_sops(self, tmp_project: ProjectManager):
        sops = tmp_project.load_sops()
        assert "Operating Procedures" in sops

    def test_load_sops_missing(self, tmp_path: Path):
        pm = ProjectManager(tmp_path / "no-project")
        assert pm.load_sops() == ""

    def test_load_config(self, tmp_project: ProjectManager):
        config = tmp_project.load_config()
        assert config.budget == 10.00


class TestRunState:
    def test_create_and_save(self, tmp_project: ProjectManager):
        state = tmp_project.create_run()
        assert state.status == "active"
        tmp_project.save_state(state)
        assert tmp_project.has_state()

    def test_load_state(self, tmp_project: ProjectManager):
        state = tmp_project.create_run()
        tmp_project.save_state(state)
        loaded = tmp_project.load_state()
        assert loaded.id == state.id

    def test_load_state_missing(self, tmp_project: ProjectManager):
        with pytest.raises(FileNotFoundError):
            tmp_project.load_state()

    def test_clear_state(self, tmp_project: ProjectManager):
        state = tmp_project.create_run()
        tmp_project.save_state(state)
        tmp_project.clear_state()
        assert not tmp_project.has_state()
        # Directories should be recreated
        assert (tmp_project.project_dir / ".pact" / "contracts").exists()


class TestAudit:
    def test_append_and_load(self, tmp_project: ProjectManager):
        tmp_project.append_audit("test_action", "some detail")
        entries = tmp_project.load_audit()
        assert len(entries) == 1
        assert entries[0]["action"] == "test_action"

    def test_multiple_entries(self, tmp_project: ProjectManager):
        tmp_project.append_audit("action1", "d1")
        tmp_project.append_audit("action2", "d2")
        entries = tmp_project.load_audit()
        assert len(entries) == 2

    def test_empty_audit(self, tmp_project: ProjectManager):
        entries = tmp_project.load_audit()
        assert entries == []


class TestDecomposition:
    def test_save_and_load_tree(self, tmp_project: ProjectManager):
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root", description="r",
                ),
            },
        )
        tmp_project.save_tree(tree)
        loaded = tmp_project.load_tree()
        assert loaded is not None
        assert loaded.root_id == "root"

    def test_load_tree_missing(self, tmp_project: ProjectManager):
        assert tmp_project.load_tree() is None

    def test_save_and_load_interview(self, tmp_project: ProjectManager):
        result = InterviewResult(
            risks=["risk1"],
            questions=["q1"],
        )
        tmp_project.save_interview(result)
        loaded = tmp_project.load_interview()
        assert loaded is not None
        assert loaded.risks == ["risk1"]

    def test_save_decisions(self, tmp_project: ProjectManager):
        decisions = [{"ambiguity": "auth", "decision": "JWT", "rationale": "simpler"}]
        tmp_project.save_decisions(decisions)
        path = tmp_project.project_dir / ".pact" / "decomposition" / "decisions.json"
        assert path.exists()


class TestContracts:
    def test_save_and_load_contract(self, tmp_project: ProjectManager):
        contract = ComponentContract(
            component_id="pricing",
            name="Pricing",
            description="Pricing engine",
            functions=[
                FunctionContract(
                    name="calc", description="d",
                    inputs=[FieldSpec(name="x", type_ref="str")],
                    output_type="float",
                ),
            ],
        )
        tmp_project.save_contract(contract)
        loaded = tmp_project.load_contract("pricing")
        assert loaded is not None
        assert loaded.name == "Pricing"

    def test_saves_history(self, tmp_project: ProjectManager):
        contract = ComponentContract(
            component_id="pricing",
            name="Pricing",
            description="d",
        )
        tmp_project.save_contract(contract)
        history_dir = tmp_project.project_dir / ".pact" / "contracts" / "pricing" / "history"
        assert any(history_dir.iterdir())

    def test_load_all_contracts(self, tmp_project: ProjectManager):
        for cid in ["a", "b", "c"]:
            c = ComponentContract(component_id=cid, name=cid.upper(), description="d")
            tmp_project.save_contract(c)
        all_c = tmp_project.load_all_contracts()
        assert len(all_c) == 3

    def test_load_contract_missing(self, tmp_project: ProjectManager):
        assert tmp_project.load_contract("nonexistent") is None


class TestTestSuites:
    def test_save_and_load(self, tmp_project: ProjectManager):
        suite = ContractTestSuite(
            component_id="pricing",
            contract_version=1,
            test_cases=[
                TestCase(id="t1", description="d", function="f", category="happy_path"),
            ],
            generated_code="def test_it(): pass",
        )
        tmp_project.save_test_suite(suite)
        loaded = tmp_project.load_test_suite("pricing")
        assert loaded is not None
        assert len(loaded.test_cases) == 1

    def test_saves_code_file(self, tmp_project: ProjectManager):
        suite = ContractTestSuite(
            component_id="pricing",
            contract_version=1,
            generated_code="def test_it(): pass",
        )
        tmp_project.save_test_suite(suite)
        code_path = tmp_project.test_code_path("pricing")
        assert code_path.exists()
        assert "test_it" in code_path.read_text()

    def test_load_all(self, tmp_project: ProjectManager):
        for cid in ["a", "b"]:
            s = ContractTestSuite(
                component_id=cid, contract_version=1,
                test_cases=[TestCase(id="t", description="d", function="f", category="happy_path")],
            )
            tmp_project.save_test_suite(s)
        all_s = tmp_project.load_all_test_suites()
        assert len(all_s) == 2


class TestImplementations:
    def test_impl_dir(self, tmp_project: ProjectManager):
        d = tmp_project.impl_dir("pricing")
        assert d.exists()

    def test_impl_src_dir(self, tmp_project: ProjectManager):
        d = tmp_project.impl_src_dir("pricing")
        assert d.exists()
        assert d.name == "src"

    def test_save_metadata(self, tmp_project: ProjectManager):
        tmp_project.save_impl_metadata("pricing", {"attempt": 1})
        path = tmp_project.impl_dir("pricing") / "metadata.json"
        assert path.exists()


class TestLearnings:
    def test_append_and_load(self, tmp_project: ProjectManager):
        tmp_project.append_learning({"lesson": "Use Result types", "category": "pattern"})
        entries = tmp_project.load_learnings()
        assert len(entries) == 1

    def test_empty(self, tmp_project: ProjectManager):
        assert tmp_project.load_learnings() == []


class TestDesignDoc:
    def test_save_and_load(self, tmp_project: ProjectManager):
        doc = DesignDocument(
            project_id="test",
            title="Test Design",
            summary="A test",
        )
        tmp_project.save_design_doc(doc)
        loaded = tmp_project.load_design_doc()
        assert loaded is not None
        assert loaded.title == "Test Design"

    def test_load_missing(self, tmp_project: ProjectManager):
        assert tmp_project.load_design_doc() is None
