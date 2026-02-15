"""Tests for focused contract rendering (P3-3)."""
import pytest
from pact.agents.test_author import _render_focused_contract
from pact.schemas import (
    ComponentContract,
    FunctionContract,
    FieldSpec,
    TypeSpec,
    ErrorCase,
)


def _make_contract():
    return ComponentContract(
        component_id="comp_a",
        name="Component A",
        description="A test component",
        types=[
            TypeSpec(
                name="Item",
                kind="struct",
                description="An item",
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
                ],
                output_type="Item",
                preconditions=["item_id must not be empty"],
                postconditions=["result.id == item_id"],
                error_cases=[
                    ErrorCase(name="NotFound", condition="item_id not in store", error_type="not_found"),
                ],
            ),
        ],
        invariants=["All items have unique IDs"],
        dependencies=["dep_a"],
    )


class TestRenderFocusedContract:
    def test_includes_types(self):
        result = _render_focused_contract(_make_contract())
        assert "Item (struct)" in result
        assert "id: str" in result
        assert "value: float" in result

    def test_includes_enum(self):
        result = _render_focused_contract(_make_contract())
        assert "Status (enum)" in result
        assert "ACTIVE" in result
        assert "INACTIVE" in result

    def test_includes_function_signature(self):
        result = _render_focused_contract(_make_contract())
        assert "process(item_id: str) -> Item" in result

    def test_includes_preconditions(self):
        result = _render_focused_contract(_make_contract())
        assert "precondition: item_id must not be empty" in result

    def test_includes_postconditions(self):
        result = _render_focused_contract(_make_contract())
        assert "postcondition: result.id == item_id" in result

    def test_includes_error_cases(self):
        result = _render_focused_contract(_make_contract())
        assert "error: NotFound" in result
        assert "item_id not in store" in result

    def test_includes_invariants(self):
        result = _render_focused_contract(_make_contract())
        assert "All items have unique IDs" in result

    def test_includes_dependencies(self):
        result = _render_focused_contract(_make_contract())
        assert "dep_a" in result

    def test_includes_descriptions(self):
        result = _render_focused_contract(_make_contract())
        assert "An item" in result
        assert "Process an item" in result

    def test_smaller_than_json(self):
        contract = _make_contract()
        focused = _render_focused_contract(contract)
        json_dump = contract.model_dump_json(indent=2)
        assert len(focused) < len(json_dump)

    def test_empty_contract(self):
        contract = ComponentContract(
            component_id="empty",
            name="Empty",
            description="Nothing",
        )
        result = _render_focused_contract(contract)
        # Should not crash, may be empty or minimal
        assert isinstance(result, str)

    def test_function_without_error_cases(self):
        contract = ComponentContract(
            component_id="simple",
            name="Simple",
            description="Simple component",
            functions=[
                FunctionContract(
                    name="hello",
                    description="Say hello",
                    inputs=[],
                    output_type="str",
                ),
            ],
        )
        result = _render_focused_contract(contract)
        assert "hello() -> str" in result
        assert "error:" not in result
