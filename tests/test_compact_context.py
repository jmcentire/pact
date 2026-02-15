"""Tests for compact deps (P3-1) and lazy test code (P3-2)."""
import pytest
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    FunctionContract,
    FieldSpec,
    TypeSpec,
    TestCase,
)
from pact.interface_stub import render_compact_deps, render_handoff_brief


def _make_contract(comp_id="comp_a", name="Component A"):
    return ComponentContract(
        component_id=comp_id,
        name=name,
        description="A test component",
        types=[
            TypeSpec(
                name="Item",
                kind="struct",
                fields=[
                    FieldSpec(name="id", type_ref="str"),
                    FieldSpec(name="value", type_ref="float"),
                ],
            ),
            TypeSpec(
                name="Status",
                kind="enum",
                variants=["ACTIVE", "INACTIVE"],
            ),
        ],
        functions=[
            FunctionContract(
                name="process",
                description="Process an item",
                inputs=[
                    FieldSpec(name="item_id", type_ref="str"),
                    FieldSpec(name="count", type_ref="int"),
                ],
                output_type="Item",
            ),
        ],
    )


def _make_test_suite():
    return ContractTestSuite(
        component_id="comp_a",
        contract_version=1,
        test_cases=[
            TestCase(id="test_happy_path", function="process", category="happy_path",
                     description="Basic happy path test"),
            TestCase(id="test_edge_case", function="process", category="edge_case",
                     description="Empty input edge case"),
        ],
        generated_code="def test_happy_path():\n    assert True\n\ndef test_edge_case():\n    assert True\n",
    )


class TestRenderCompactDeps:
    def test_empty_contracts(self):
        assert render_compact_deps({}) == ""

    def test_includes_function_signatures(self):
        contracts = {"comp_a": _make_contract()}
        result = render_compact_deps(contracts)
        assert "process(item_id: str, count: int) -> Item" in result

    def test_includes_struct_types(self):
        contracts = {"comp_a": _make_contract()}
        result = render_compact_deps(contracts)
        assert "Item = {id: str, value: float}" in result

    def test_includes_enum_types(self):
        contracts = {"comp_a": _make_contract()}
        result = render_compact_deps(contracts)
        assert "Status = enum(ACTIVE, INACTIVE)" in result

    def test_includes_component_name(self):
        contracts = {"comp_a": _make_contract()}
        result = render_compact_deps(contracts)
        assert "Component A" in result

    def test_multiple_contracts(self):
        contracts = {
            "comp_a": _make_contract("comp_a", "Component A"),
            "comp_b": _make_contract("comp_b", "Component B"),
        }
        result = render_compact_deps(contracts)
        assert "Component A" in result
        assert "Component B" in result

    def test_smaller_than_full_stub(self):
        from pact.interface_stub import render_stub
        contract = _make_contract()
        compact = render_compact_deps({"comp_a": contract})
        full = render_stub(contract)
        assert len(compact) < len(full)


class TestHandoffBriefLazyTestCode:
    def test_with_test_code_default(self):
        contract = _make_contract()
        test_suite = _make_test_suite()
        brief = render_handoff_brief(
            component_id="comp_a",
            contract=contract,
            contracts={"comp_a": contract},
            test_suite=test_suite,
        )
        # Default includes full test code
        assert "def test_happy_path():" in brief

    def test_without_test_code(self):
        contract = _make_contract()
        test_suite = _make_test_suite()
        brief = render_handoff_brief(
            component_id="comp_a",
            contract=contract,
            contracts={"comp_a": contract},
            test_suite=test_suite,
            include_test_code=False,
        )
        # Should NOT include full test code
        assert "def test_happy_path():" not in brief
        # Should include compact listing
        assert "test_happy_path" in brief
        assert "test_edge_case" in brief

    def test_without_test_code_shows_count(self):
        contract = _make_contract()
        test_suite = _make_test_suite()
        brief = render_handoff_brief(
            component_id="comp_a",
            contract=contract,
            contracts={"comp_a": contract},
            test_suite=test_suite,
            include_test_code=False,
        )
        assert "2 cases" in brief

    def test_without_test_code_smaller(self):
        contract = _make_contract()
        test_suite = _make_test_suite()
        brief_full = render_handoff_brief(
            component_id="comp_a",
            contract=contract,
            contracts={"comp_a": contract},
            test_suite=test_suite,
            include_test_code=True,
        )
        brief_compact = render_handoff_brief(
            component_id="comp_a",
            contract=contract,
            contracts={"comp_a": contract},
            test_suite=test_suite,
            include_test_code=False,
        )
        assert len(brief_compact) < len(brief_full)
