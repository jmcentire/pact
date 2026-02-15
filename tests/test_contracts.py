"""Tests for contract validation logic."""

from __future__ import annotations

import pytest

from pact.contracts import (
    validate_all_contracts,
    validate_contract_completeness,
    validate_dependency_graph,
    validate_test_suite,
    validate_type_references,
)
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


def _make_contract(
    component_id: str = "test",
    name: str = "Test",
    types: list[TypeSpec] | None = None,
    functions: list[FunctionContract] | None = None,
    dependencies: list[str] | None = None,
) -> ComponentContract:
    return ComponentContract(
        component_id=component_id,
        name=name,
        description="Test component",
        types=types or [],
        functions=functions or [
            FunctionContract(
                name="do_thing",
                description="Does the thing",
                inputs=[FieldSpec(name="x", type_ref="str")],
                output_type="str",
            ),
        ],
        dependencies=dependencies or [],
    )


def _make_test_suite(
    component_id: str = "test",
    code: str = "def test_example(): pass",
) -> ContractTestSuite:
    return ContractTestSuite(
        component_id=component_id,
        contract_version=1,
        test_cases=[
            TestCase(id="t1", description="test", function="do_thing", category="happy_path"),
        ],
        generated_code=code,
    )


class TestValidateTypeReferences:
    def test_primitives_resolve(self):
        c = _make_contract(
            functions=[
                FunctionContract(
                    name="f", description="d",
                    inputs=[FieldSpec(name="x", type_ref="str")],
                    output_type="int",
                ),
            ],
        )
        errors = validate_type_references(c)
        assert errors == []

    def test_custom_type_resolves(self):
        c = _make_contract(
            types=[TypeSpec(name="Price", kind="struct", fields=[FieldSpec(name="amount", type_ref="float")])],
            functions=[
                FunctionContract(
                    name="calc", description="d",
                    inputs=[],
                    output_type="Price",
                ),
            ],
        )
        errors = validate_type_references(c)
        assert errors == []

    def test_unresolved_output_type(self):
        c = _make_contract(
            functions=[
                FunctionContract(
                    name="calc", description="d",
                    inputs=[],
                    output_type="UnknownType",
                ),
            ],
        )
        errors = validate_type_references(c)
        assert len(errors) == 1
        assert "UnknownType" in errors[0]

    def test_unresolved_input_type(self):
        c = _make_contract(
            functions=[
                FunctionContract(
                    name="calc", description="d",
                    inputs=[FieldSpec(name="x", type_ref="MissingType")],
                    output_type="str",
                ),
            ],
        )
        errors = validate_type_references(c)
        assert len(errors) == 1
        assert "MissingType" in errors[0]

    def test_unresolved_field_type(self):
        c = _make_contract(
            types=[TypeSpec(name="Foo", kind="struct", fields=[FieldSpec(name="bar", type_ref="Baz")])],
            functions=[
                FunctionContract(
                    name="f", description="d",
                    inputs=[], output_type="Foo",
                ),
            ],
        )
        errors = validate_type_references(c)
        assert any("Baz" in e for e in errors)

    def test_list_item_type(self):
        c = _make_contract(
            types=[TypeSpec(name="Items", kind="list", item_type="MissingItem")],
            functions=[
                FunctionContract(
                    name="f", description="d",
                    inputs=[], output_type="str",
                ),
            ],
        )
        errors = validate_type_references(c)
        assert any("MissingItem" in e for e in errors)


class TestValidateDependencyGraph:
    def test_acyclic(self):
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="R", description="r",
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
        errors = validate_dependency_graph(tree)
        assert errors == []

    def test_cycle_detected(self):
        tree = DecompositionTree(
            root_id="a",
            nodes={
                "a": DecompositionNode(
                    component_id="a", name="A", description="a",
                    children=["b"],
                ),
                "b": DecompositionNode(
                    component_id="b", name="B", description="b",
                    children=["a"],  # cycle!
                ),
            },
        )
        errors = validate_dependency_graph(tree)
        assert any("cycle" in e.lower() for e in errors)

    def test_missing_child(self):
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="R", description="r",
                    children=["missing"],
                ),
            },
        )
        errors = validate_dependency_graph(tree)
        assert any("missing" in e for e in errors)


class TestValidateContractCompleteness:
    def test_valid(self):
        c = _make_contract()
        errors = validate_contract_completeness(c)
        assert errors == []

    def test_no_functions(self):
        c = ComponentContract(
            component_id="test", name="Test", description="t",
            functions=[],
        )
        errors = validate_contract_completeness(c)
        assert any("no functions" in e for e in errors)

    def test_missing_component_id(self):
        c = ComponentContract(
            component_id="", name="Test", description="t",
        )
        errors = validate_contract_completeness(c)
        assert any("component_id" in e for e in errors)

    def test_missing_name(self):
        c = ComponentContract(
            component_id="test", name="", description="t",
        )
        errors = validate_contract_completeness(c)
        assert any("name" in e.lower() for e in errors)

    def test_function_missing_output(self):
        c = _make_contract(
            functions=[
                FunctionContract(
                    name="f", description="d",
                    inputs=[], output_type="",
                ),
            ],
        )
        errors = validate_contract_completeness(c)
        assert any("output_type" in e for e in errors)


class TestValidateTestSuite:
    def test_valid(self):
        s = _make_test_suite()
        errors = validate_test_suite(s)
        assert errors == []

    def test_no_cases(self):
        s = ContractTestSuite(component_id="test", contract_version=1)
        errors = validate_test_suite(s)
        assert any("no test cases" in e for e in errors)

    def test_syntax_error(self):
        s = _make_test_suite(code="def test_bad(:\n    pass")
        errors = validate_test_suite(s)
        assert any("syntax error" in e.lower() for e in errors)

    def test_missing_component_id(self):
        s = ContractTestSuite(
            component_id="", contract_version=1,
            test_cases=[TestCase(id="t", description="d", function="f", category="happy_path")],
        )
        errors = validate_test_suite(s)
        assert any("component_id" in e for e in errors)


class TestValidateAllContracts:
    def test_all_valid(self):
        tree = DecompositionTree(
            root_id="a",
            nodes={
                "a": DecompositionNode(component_id="a", name="A", description="a"),
            },
        )
        contracts = {"a": _make_contract(component_id="a")}
        suites = {"a": _make_test_suite(component_id="a")}

        gate = validate_all_contracts(tree, contracts, suites)
        assert gate.passed

    def test_missing_contract(self):
        tree = DecompositionTree(
            root_id="a",
            nodes={
                "a": DecompositionNode(component_id="a", name="A", description="a"),
            },
        )
        gate = validate_all_contracts(tree, {}, {})
        assert not gate.passed
        assert any("missing contract" in d for d in gate.details)

    def test_missing_test_suite(self):
        tree = DecompositionTree(
            root_id="a",
            nodes={
                "a": DecompositionNode(component_id="a", name="A", description="a"),
            },
        )
        contracts = {"a": _make_contract(component_id="a")}
        gate = validate_all_contracts(tree, contracts, {})
        assert not gate.passed
        assert any("missing test suite" in d for d in gate.details)

    def test_unresolved_dependency(self):
        """Internal dep (in tree) without contract -> error.
        External dep (not in tree) is allowed.
        """
        tree = DecompositionTree(
            root_id="a",
            nodes={
                "a": DecompositionNode(
                    component_id="a", name="A", description="a",
                    children=["missing_dep"],
                ),
                "missing_dep": DecompositionNode(
                    component_id="missing_dep", name="M", description="m",
                    parent_id="a",
                ),
            },
        )
        c = _make_contract(component_id="a", dependencies=["missing_dep"])
        contracts = {"a": c}
        suites = {"a": _make_test_suite(component_id="a")}

        gate = validate_all_contracts(tree, contracts, suites)
        assert not gate.passed
        assert any("missing_dep" in d for d in gate.details)

    def test_multiple_components(self):
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
        contracts = {
            "root": _make_contract(component_id="root", dependencies=["a", "b"]),
            "a": _make_contract(component_id="a"),
            "b": _make_contract(component_id="b"),
        }
        suites = {
            "root": _make_test_suite(component_id="root"),
            "a": _make_test_suite(component_id="a"),
            "b": _make_test_suite(component_id="b"),
        }
        gate = validate_all_contracts(tree, contracts, suites)
        assert gate.passed
