"""Tests for requirements quality checklist generation."""

from __future__ import annotations

import pytest

from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionNode,
    DecompositionTree,
    ErrorCase,
    FieldSpec,
    FunctionContract,
    TestCase,
    TypeSpec,
)
from pact.schemas_tasks import ChecklistCategory
from pact.checklist_gen import generate_checklist, render_checklist_markdown


# ── Fixtures ────────────────────────────────────────────────────────


def _tree(*component_ids: str) -> DecompositionTree:
    nodes = {}
    children = list(component_ids[1:]) if len(component_ids) > 1 else []
    nodes["root"] = DecompositionNode(
        component_id="root", name="Root", description="Root",
        depth=0, children=children,
    )
    for cid in component_ids:
        if cid != "root":
            nodes[cid] = DecompositionNode(
                component_id=cid, name=cid.title(), description=f"{cid}",
                depth=1, parent_id="root",
            )
    return DecompositionTree(root_id="root", nodes=nodes)


def _contract(
    cid: str,
    functions: list[FunctionContract] | None = None,
    invariants: list[str] | None = None,
    deps: list[str] | None = None,
) -> ComponentContract:
    return ComponentContract(
        component_id=cid, name=cid.title(),
        description=f"Contract for {cid}",
        functions=functions or [],
        invariants=invariants or [],
        dependencies=deps or [],
    )


def _suite(cid: str, test_cases: list[TestCase] | None = None) -> ContractTestSuite:
    return ContractTestSuite(
        component_id=cid, contract_version=1,
        test_cases=test_cases or [],
    )


# ── Error handling questions ────────────────────────────────────────


class TestErrorHandlingQuestions:
    def test_function_with_no_error_cases(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(
                name="process", description="Process data",
                inputs=[FieldSpec(name="data", type_ref="str")],
                output_type="bool", error_cases=[],
            ),
        ])}
        suites = {"root": _suite("root")}
        cl = generate_checklist(tree, contracts, suites, "test")
        error_items = [i for i in cl.items if i.category == ChecklistCategory.error_handling]
        assert any("error cases" in i.question.lower() and "process" in i.question for i in error_items)

    def test_function_with_error_cases(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(
                name="process", description="Process data",
                inputs=[FieldSpec(name="data", type_ref="str")],
                output_type="bool",
                error_cases=[ErrorCase(name="invalid", condition="data is empty", error_type="ValueError")],
            ),
        ])}
        suites = {"root": _suite("root")}
        cl = generate_checklist(tree, contracts, suites, "test")
        error_items = [i for i in cl.items if i.category == ChecklistCategory.error_handling and "process" in i.question]
        assert error_items == []  # Has error cases, so no question


# ── Boundary condition questions ────────────────────────────────────


class TestBoundaryQuestions:
    def test_numeric_input_generates_question(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(
                name="calculate", description="Calculate value",
                inputs=[FieldSpec(name="amount", type_ref="float")],
                output_type="float",
            ),
        ])}
        suites = {"root": _suite("root")}
        cl = generate_checklist(tree, contracts, suites, "test")
        boundary = [i for i in cl.items if i.category == ChecklistCategory.edge_cases and "amount" in i.question]
        assert len(boundary) == 1

    def test_string_input_generates_question(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(
                name="parse", description="Parse text",
                inputs=[FieldSpec(name="text", type_ref="str")],
                output_type="dict",
            ),
        ])}
        suites = {"root": _suite("root")}
        cl = generate_checklist(tree, contracts, suites, "test")
        boundary = [i for i in cl.items if i.category == ChecklistCategory.edge_cases and "text" in i.question]
        assert len(boundary) == 1

    def test_complex_type_no_boundary_question(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(
                name="process", description="Process config",
                inputs=[FieldSpec(name="config", type_ref="AppConfig")],
                output_type="bool",
            ),
        ])}
        suites = {"root": _suite("root")}
        cl = generate_checklist(tree, contracts, suites, "test")
        boundary = [i for i in cl.items if i.category == ChecklistCategory.edge_cases and "config" in i.question]
        assert boundary == []


# ── Precondition/postcondition questions ────────────────────────────


class TestConditionQuestions:
    def test_precondition_generates_testability_question(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(
                name="update", description="Update record",
                inputs=[], output_type="bool",
                preconditions=["Record must exist in database"],
            ),
        ])}
        suites = {"root": _suite("root")}
        cl = generate_checklist(tree, contracts, suites, "test")
        testability = [i for i in cl.items if i.category == ChecklistCategory.testability and "precondition" in i.question.lower()]
        assert len(testability) == 1
        assert "Record must exist" in testability[0].question

    def test_postcondition_generates_acceptance_question(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(
                name="save", description="Save data",
                inputs=[], output_type="bool",
                postconditions=["Data persisted to storage"],
            ),
        ])}
        suites = {"root": _suite("root")}
        cl = generate_checklist(tree, contracts, suites, "test")
        acceptance = [i for i in cl.items if i.category == ChecklistCategory.acceptance_criteria and "postcondition" in i.question.lower()]
        assert len(acceptance) == 1


# ── Invariant questions ─────────────────────────────────────────────


class TestInvariantQuestions:
    def test_invariant_generates_question(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", invariants=["Balance must never be negative"])}
        suites = {"root": _suite("root")}
        cl = generate_checklist(tree, contracts, suites, "test")
        invariant_items = [i for i in cl.items if i.category == ChecklistCategory.acceptance_criteria and "invariant" in i.question.lower()]
        assert len(invariant_items) == 1
        assert "Balance" in invariant_items[0].question


# ── Dependency questions ────────────────────────────────────────────


class TestDependencyQuestions:
    def test_dependency_generates_question(self):
        tree = _tree("root", "auth")
        contracts = {
            "root": _contract("root", deps=["auth"]),
            "auth": _contract("auth"),
        }
        suites = {cid: _suite(cid) for cid in contracts}
        cl = generate_checklist(tree, contracts, suites, "test")
        dep_items = [i for i in cl.items if i.category == ChecklistCategory.dependencies]
        assert any("auth" in i.question and "bidirectional" in i.question.lower() for i in dep_items)


# ── Test suite quality questions ────────────────────────────────────


class TestSuiteQuestions:
    def test_missing_happy_path(self):
        tree = _tree("root")
        contracts = {"root": _contract("root")}
        suites = {"root": _suite("root", test_cases=[
            TestCase(id="t1", description="Edge", function="fn", category="edge_case"),
        ])}
        cl = generate_checklist(tree, contracts, suites, "test")
        testability = [i for i in cl.items if "happy path" in i.question.lower() and i.component_id == "root"]
        assert len(testability) >= 1
        assert testability[0].satisfied is False

    def test_missing_error_case(self):
        tree = _tree("root")
        contracts = {"root": _contract("root")}
        suites = {"root": _suite("root", test_cases=[
            TestCase(id="t1", description="Happy", function="fn", category="happy_path"),
        ])}
        cl = generate_checklist(tree, contracts, suites, "test")
        testability = [i for i in cl.items if "error case" in i.question.lower() and i.component_id == "root"]
        assert len(testability) >= 1

    def test_missing_edge_case(self):
        tree = _tree("root")
        contracts = {"root": _contract("root")}
        suites = {"root": _suite("root", test_cases=[
            TestCase(id="t1", description="Happy", function="fn", category="happy_path"),
        ])}
        cl = generate_checklist(tree, contracts, suites, "test")
        edge = [i for i in cl.items if i.category == ChecklistCategory.edge_cases and "edge case" in i.question.lower()]
        assert len(edge) >= 1

    def test_all_categories_present(self):
        tree = _tree("root")
        contracts = {"root": _contract("root")}
        suites = {"root": _suite("root", test_cases=[
            TestCase(id="t1", description="Happy", function="fn", category="happy_path"),
            TestCase(id="t2", description="Error", function="fn", category="error_case"),
            TestCase(id="t3", description="Edge", function="fn", category="edge_case"),
        ])}
        cl = generate_checklist(tree, contracts, suites, "test")
        # Should not generate test category coverage questions for this suite
        suite_coverage = [
            i for i in cl.items
            if i.component_id == "root" and "test suite" in i.question.lower()
        ]
        assert suite_coverage == []


# ── Function test coverage ──────────────────────────────────────────


class TestFunctionCoverage:
    def test_untested_function(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(name="tested_fn", description="d", inputs=[], output_type="str"),
            FunctionContract(name="untested_fn", description="d", inputs=[], output_type="str"),
        ])}
        suites = {"root": _suite("root", test_cases=[
            TestCase(id="t1", description="d", function="tested_fn", category="happy_path"),
        ])}
        cl = generate_checklist(tree, contracts, suites, "test")
        coverage = [i for i in cl.items if "untested_fn" in i.question and "coverage" in i.question.lower()]
        assert len(coverage) == 1
        assert coverage[0].satisfied is False

    def test_function_missing_happy_path(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(name="fn1", description="d", inputs=[], output_type="str"),
        ])}
        suites = {"root": _suite("root", test_cases=[
            TestCase(id="t1", description="d", function="fn1", category="error_case"),
        ])}
        cl = generate_checklist(tree, contracts, suites, "test")
        happy = [i for i in cl.items if "fn1" in i.question and "happy path" in i.question.lower()]
        assert len(happy) == 1

    def test_function_with_error_cases_but_no_error_tests(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(
                name="fn1", description="d", inputs=[], output_type="str",
                error_cases=[ErrorCase(name="bad", condition="c", error_type="ValueError")],
            ),
        ])}
        suites = {"root": _suite("root", test_cases=[
            TestCase(id="t1", description="d", function="fn1", category="happy_path"),
        ])}
        cl = generate_checklist(tree, contracts, suites, "test")
        error_cov = [i for i in cl.items if "fn1" in i.question and "error case" in i.question.lower()]
        assert len(error_cov) == 1


# ── Empty/minimal cases ────────────────────────────────────────────


class TestEmptyCases:
    def test_no_contracts(self):
        tree = _tree("root")
        cl = generate_checklist(tree, {}, {}, "test")
        assert cl.items == []

    def test_contract_no_functions(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[])}
        suites = {"root": _suite("root")}
        cl = generate_checklist(tree, contracts, suites, "test")
        # Should still have suite-level checks
        assert isinstance(cl.items, list)

    def test_project_id_set(self):
        tree = _tree("root")
        cl = generate_checklist(tree, {}, {}, "my-project")
        assert cl.project_id == "my-project"


# ── render_checklist_markdown ───────────────────────────────────────


class TestRenderChecklistMarkdown:
    def test_header(self):
        tree = _tree("root")
        cl = generate_checklist(tree, {}, {}, "test")
        md = render_checklist_markdown(cl)
        assert "# Requirements Checklist" in md

    def test_empty_checklist(self):
        tree = _tree("root")
        cl = generate_checklist(tree, {}, {}, "test")
        md = render_checklist_markdown(cl)
        assert "No checklist items" in md

    def test_items_shown(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(
                name="fn1", description="d",
                inputs=[FieldSpec(name="x", type_ref="int")],
                output_type="str",
            ),
        ])}
        suites = {"root": _suite("root")}
        cl = generate_checklist(tree, contracts, suites, "test")
        md = render_checklist_markdown(cl)
        assert "C001" in md

    def test_satisfied_checkboxes(self):
        from pact.schemas_tasks import ChecklistItem, RequirementsChecklist, ChecklistCategory
        cl = RequirementsChecklist(
            project_id="test",
            items=[
                ChecklistItem(id="C1", category=ChecklistCategory.requirements, question="Q1", satisfied=True),
                ChecklistItem(id="C2", category=ChecklistCategory.requirements, question="Q2", satisfied=False),
                ChecklistItem(id="C3", category=ChecklistCategory.requirements, question="Q3"),
            ],
        )
        md = render_checklist_markdown(cl)
        assert "[x] C1" in md  # satisfied
        assert "[!] C2" in md  # unsatisfied
        assert "[ ] C3" in md  # unanswered

    def test_category_headers(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(
                name="fn1", description="d",
                inputs=[FieldSpec(name="x", type_ref="int")],
                output_type="str",
                preconditions=["x > 0"],
            ),
        ])}
        suites = {"root": _suite("root")}
        cl = generate_checklist(tree, contracts, suites, "test")
        md = render_checklist_markdown(cl)
        assert "##" in md  # At least one category section

    def test_reference_shown(self):
        tree = _tree("root")
        contracts = {"root": _contract("root", functions=[
            FunctionContract(name="fn1", description="d", inputs=[], output_type="str"),
        ])}
        suites = {"root": _suite("root")}
        cl = generate_checklist(tree, contracts, suites, "test")
        md = render_checklist_markdown(cl)
        assert "Ref:" in md

    def test_stats_in_header(self):
        from pact.schemas_tasks import ChecklistItem, RequirementsChecklist, ChecklistCategory
        cl = RequirementsChecklist(
            project_id="test",
            items=[
                ChecklistItem(id="C1", category=ChecklistCategory.requirements, question="Q1", satisfied=True),
                ChecklistItem(id="C2", category=ChecklistCategory.requirements, question="Q2"),
            ],
        )
        md = render_checklist_markdown(cl)
        assert "2 total" in md
        assert "1 satisfied" in md
        assert "1 unanswered" in md
