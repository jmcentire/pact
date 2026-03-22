"""Tests for focused contract rendering (P3-3)."""
import pytest
from pact.agents.test_author import _render_focused_contract
from pact.schemas import (
    ComponentContract,
    FunctionContract,
    FieldSpec,
    TypeSpec,
    ErrorCase,
    SideEffect,
    SideEffectKind,
    PerformanceBudget,
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

    def test_includes_structured_side_effects(self):
        contract = ComponentContract(
            component_id="side_fx",
            name="SideFx",
            description="Has side effects",
            functions=[
                FunctionContract(
                    name="save",
                    description="Saves data",
                    inputs=[FieldSpec(name="data", type_ref="str")],
                    output_type="None",
                    structured_side_effects=[
                        SideEffect(kind=SideEffectKind.WRITES_FILE, target="state.json"),
                        SideEffect(kind=SideEffectKind.LOGGING, target="app.log"),
                    ],
                ),
            ],
        )
        result = _render_focused_contract(contract)
        assert "side_effect: writes_file -> state.json" in result
        assert "side_effect: logging -> app.log" in result

    def test_includes_string_side_effects_fallback(self):
        contract = ComponentContract(
            component_id="old_fx",
            name="OldFx",
            description="Has old-style side effects",
            functions=[
                FunctionContract(
                    name="read",
                    description="Reads data",
                    inputs=[],
                    output_type="str",
                    side_effects=["reads_file: config.yaml"],
                ),
            ],
        )
        result = _render_focused_contract(contract)
        assert "side_effect: reads_file: config.yaml" in result

    def test_includes_performance_budget(self):
        contract = ComponentContract(
            component_id="perf",
            name="Perf",
            description="Performance-sensitive",
            functions=[
                FunctionContract(
                    name="search",
                    description="Search items",
                    inputs=[FieldSpec(name="query", type_ref="str")],
                    output_type="list[Item]",
                    performance_budget=PerformanceBudget(
                        p95_latency_ms=50,
                        max_memory_mb=256,
                        complexity="O(n log n)",
                    ),
                ),
            ],
        )
        result = _render_focused_contract(contract)
        assert "performance:" in result
        assert "p95<50ms" in result
        assert "mem<256MB" in result
        assert "O(n log n)" in result

    def test_no_performance_budget_when_none(self):
        contract = ComponentContract(
            component_id="noperf",
            name="NoPerf",
            description="No perf constraints",
            functions=[
                FunctionContract(
                    name="greet",
                    description="Greet user",
                    inputs=[],
                    output_type="str",
                ),
            ],
        )
        result = _render_focused_contract(contract)
        assert "performance:" not in result

    def test_async_function_shows_async_marker(self):
        contract = ComponentContract(
            component_id="async_svc",
            name="AsyncSvc",
            description="Service with async functions",
            functions=[
                FunctionContract(
                    name="fetch_data",
                    description="Fetch remote data",
                    inputs=[FieldSpec(name="url", type_ref="str")],
                    output_type="str",
                    is_async=True,
                ),
                FunctionContract(
                    name="parse_data",
                    description="Parse data locally",
                    inputs=[FieldSpec(name="raw", type_ref="str")],
                    output_type="dict",
                    is_async=False,
                ),
            ],
        )
        result = _render_focused_contract(contract)
        assert "async fetch_data(url: str) -> str" in result
        assert "parse_data(raw: str) -> dict" in result
        # parse_data should NOT be async
        assert "async parse_data" not in result

    def test_sync_function_default_no_async_marker(self):
        contract = ComponentContract(
            component_id="sync_only",
            name="SyncOnly",
            description="All sync",
            functions=[
                FunctionContract(
                    name="add",
                    description="Add numbers",
                    inputs=[
                        FieldSpec(name="a", type_ref="int"),
                        FieldSpec(name="b", type_ref="int"),
                    ],
                    output_type="int",
                ),
            ],
        )
        result = _render_focused_contract(contract)
        assert "add(a: int, b: int) -> int" in result
        assert "async " not in result
