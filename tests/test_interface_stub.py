"""Tests for interface stub generation — the agent's mental model."""

from __future__ import annotations

from pact.interface_stub import (
    _map_type_js,
    render_dependency_map,
    render_handoff_brief,
    render_progress_snapshot,
    render_stub,
    render_stub_js,
)
from pact.schemas import (
    ComponentContract,
    ComponentTask,
    ContractTestSuite,
    DecompositionNode,
    DecompositionTree,
    ErrorCase,
    FieldSpec,
    FunctionContract,
    RunState,
    TestCase,
    TestFailure,
    TestResults,
    TypeSpec,
    ValidatorSpec,
)


def _make_pricing_contract() -> ComponentContract:
    """A realistic pricing contract for testing."""
    return ComponentContract(
        component_id="pricing",
        name="Pricing Engine",
        description="Calculates nightly prices for unit stays",
        version=1,
        types=[
            TypeSpec(
                name="PriceResult",
                kind="struct",
                description="Final price calculation result",
                fields=[
                    FieldSpec(name="base_price", type_ref="float", description="Before tax"),
                    FieldSpec(name="tax_amount", type_ref="float"),
                    FieldSpec(name="total", type_ref="float"),
                    FieldSpec(
                        name="currency", type_ref="str", required=False,
                        default='"USD"',
                        validators=[ValidatorSpec(kind="regex", expression=r"^[A-Z]{3}$")],
                    ),
                ],
            ),
            TypeSpec(
                name="PricingError",
                kind="enum",
                variants=["unit_not_found", "invalid_dates", "no_rate"],
            ),
        ],
        functions=[
            FunctionContract(
                name="calculate_price",
                description="Calculate the nightly price for a unit stay",
                inputs=[
                    FieldSpec(name="unit_id", type_ref="str"),
                    FieldSpec(
                        name="check_in", type_ref="str",
                        validators=[ValidatorSpec(kind="regex", expression=r"^\d{4}-\d{2}-\d{2}$")],
                    ),
                    FieldSpec(name="check_out", type_ref="str"),
                    FieldSpec(
                        name="guest_count", type_ref="int",
                        required=False, default="1",
                        validators=[ValidatorSpec(kind="range", expression="1, 20")],
                    ),
                ],
                output_type="PriceResult",
                error_cases=[
                    ErrorCase(name="UNIT_NOT_FOUND", condition="unit_id not in inventory", error_type="PricingError"),
                    ErrorCase(name="INVALID_DATES", condition="check_in >= check_out", error_type="PricingError"),
                ],
                preconditions=["check_in < check_out", "unit_id is non-empty"],
                postconditions=["result.total > 0", "result.total == result.base_price + result.tax_amount"],
                idempotent=True,
            ),
        ],
        dependencies=["inventory"],
        invariants=["All prices are in the configured currency"],
    )


def _make_inventory_contract() -> ComponentContract:
    return ComponentContract(
        component_id="inventory",
        name="Inventory Service",
        description="Manages unit availability",
        types=[
            TypeSpec(
                name="AvailabilityResult",
                kind="struct",
                fields=[
                    FieldSpec(name="available", type_ref="bool"),
                    FieldSpec(name="unit_id", type_ref="str"),
                ],
            ),
        ],
        functions=[
            FunctionContract(
                name="check_availability",
                description="Check if a unit is available",
                inputs=[
                    FieldSpec(name="unit_id", type_ref="str"),
                    FieldSpec(name="check_in", type_ref="str"),
                    FieldSpec(name="check_out", type_ref="str"),
                ],
                output_type="AvailabilityResult",
                error_cases=[
                    ErrorCase(name="UNIT_NOT_FOUND", condition="unit_id unknown", error_type="NotFoundError"),
                ],
            ),
        ],
    )


class TestRenderStub:
    def test_header(self):
        contract = _make_pricing_contract()
        stub = render_stub(contract)
        assert "Pricing Engine" in stub
        assert "pricing" in stub
        assert "v1" in stub

    def test_dependencies_shown(self):
        contract = _make_pricing_contract()
        stub = render_stub(contract)
        assert "inventory" in stub

    def test_invariants_shown(self):
        contract = _make_pricing_contract()
        stub = render_stub(contract)
        assert "configured currency" in stub

    def test_struct_rendered(self):
        contract = _make_pricing_contract()
        stub = render_stub(contract)
        assert "class PriceResult:" in stub
        assert "base_price" in stub
        assert "float" in stub

    def test_enum_rendered(self):
        contract = _make_pricing_contract()
        stub = render_stub(contract)
        assert "class PricingError(Enum):" in stub
        assert "UNIT_NOT_FOUND" in stub
        assert "INVALID_DATES" in stub

    def test_function_signature(self):
        contract = _make_pricing_contract()
        stub = render_stub(contract)
        assert "def calculate_price(" in stub
        assert "unit_id: str" in stub
        assert "-> PriceResult:" in stub

    def test_preconditions_in_docstring(self):
        contract = _make_pricing_contract()
        stub = render_stub(contract)
        assert "Preconditions:" in stub
        assert "check_in < check_out" in stub

    def test_postconditions_in_docstring(self):
        contract = _make_pricing_contract()
        stub = render_stub(contract)
        assert "Postconditions:" in stub
        assert "result.total > 0" in stub

    def test_errors_in_docstring(self):
        contract = _make_pricing_contract()
        stub = render_stub(contract)
        assert "Errors:" in stub
        assert "UNIT_NOT_FOUND" in stub

    def test_validators_shown(self):
        contract = _make_pricing_contract()
        stub = render_stub(contract)
        assert "regex" in stub

    def test_idempotent_noted(self):
        contract = _make_pricing_contract()
        stub = render_stub(contract)
        assert "Idempotent: yes" in stub

    def test_side_effects_noted(self):
        contract = _make_pricing_contract()
        stub = render_stub(contract)
        assert "Side effects: none" in stub

    def test_optional_fields_have_defaults(self):
        contract = _make_pricing_contract()
        stub = render_stub(contract)
        # currency is optional with default "USD"
        assert '"USD"' in stub
        # guest_count is optional with default 1
        assert "= 1" in stub

    def test_minimal_contract(self):
        contract = ComponentContract(
            component_id="simple",
            name="Simple",
            description="A simple component",
            functions=[FunctionContract(
                name="do_thing", description="Does the thing",
                inputs=[], output_type="str",
            )],
        )
        stub = render_stub(contract)
        assert "Simple" in stub
        assert "def do_thing()" in stub

    def test_list_type(self):
        contract = ComponentContract(
            component_id="t",
            name="T",
            description="d",
            types=[TypeSpec(name="Prices", kind="list", item_type="float", description="List of prices")],
            functions=[FunctionContract(name="f", description="d", inputs=[], output_type="str")],
        )
        stub = render_stub(contract)
        assert "Prices = list[float]" in stub


class TestRenderDependencyMap:
    def test_shows_dependencies(self):
        contracts = {
            "pricing": _make_pricing_contract(),
            "inventory": _make_inventory_contract(),
        }
        dep_map = render_dependency_map("pricing", contracts)
        assert "inventory" in dep_map.lower()
        assert "check_availability" in dep_map
        assert "AvailabilityResult" in dep_map

    def test_missing_dependency(self):
        contracts = {
            "pricing": _make_pricing_contract(),
            # inventory is missing!
        }
        dep_map = render_dependency_map("pricing", contracts)
        assert "NOT FOUND" in dep_map

    def test_no_dependencies(self):
        contracts = {
            "simple": ComponentContract(
                component_id="simple", name="Simple", description="d",
                functions=[FunctionContract(name="f", description="d", inputs=[], output_type="str")],
            ),
        }
        dep_map = render_dependency_map("simple", contracts)
        assert "simple" in dep_map

    def test_compact_type_info(self):
        contracts = {
            "pricing": _make_pricing_contract(),
            "inventory": _make_inventory_contract(),
        }
        dep_map = render_dependency_map("pricing", contracts)
        # Should show struct fields compactly
        assert "available" in dep_map


class TestRenderHandoffBrief:
    def test_contains_interface_stub(self):
        contract = _make_pricing_contract()
        contracts = {"pricing": contract, "inventory": _make_inventory_contract()}
        brief = render_handoff_brief("pricing", contract, contracts)
        assert "HANDOFF BRIEF" in brief
        assert "YOUR INTERFACE CONTRACT" in brief
        assert "class PriceResult:" in brief

    def test_contains_dependency_map(self):
        contract = _make_pricing_contract()
        contracts = {"pricing": contract, "inventory": _make_inventory_contract()}
        brief = render_handoff_brief("pricing", contract, contracts)
        assert "AVAILABLE DEPENDENCIES" in brief
        assert "check_availability" in brief

    def test_contains_test_info(self):
        contract = _make_pricing_contract()
        suite = ContractTestSuite(
            component_id="pricing",
            contract_version=1,
            test_cases=[
                TestCase(id="test_happy", description="Happy path", function="calculate_price", category="happy_path"),
                TestCase(id="test_error", description="Error case", function="calculate_price", category="error_case"),
            ],
            generated_code="def test_happy(): assert True",
        )
        brief = render_handoff_brief("pricing", contract, {"pricing": contract}, test_suite=suite)
        assert "TESTS TO PASS (2 cases)" in brief
        assert "test_happy" in brief

    def test_marks_previously_failed_tests(self):
        contract = _make_pricing_contract()
        suite = ContractTestSuite(
            component_id="pricing",
            contract_version=1,
            test_cases=[
                TestCase(id="test_happy", description="Happy path", function="f", category="happy_path"),
                TestCase(id="test_error", description="Error case", function="f", category="error_case"),
            ],
        )
        results = TestResults(
            total=2, passed=1, failed=1,
            failure_details=[TestFailure(test_id="test_error", error_message="assertion failed")],
        )
        brief = render_handoff_brief(
            "pricing", contract, {"pricing": contract},
            test_suite=suite, test_results=results,
        )
        assert "PREVIOUSLY FAILED" in brief

    def test_includes_prior_failures(self):
        contract = _make_pricing_contract()
        brief = render_handoff_brief(
            "pricing", contract, {"pricing": contract},
            prior_failures=["Off by one in tax calculation", "Missing None check"],
        )
        assert "PRIOR FAILURES" in brief
        assert "Off by one" in brief
        assert "do NOT repeat" in brief

    def test_includes_sops(self):
        contract = _make_pricing_contract()
        brief = render_handoff_brief(
            "pricing", contract, {"pricing": contract},
            sops="# Rules\n- Use Result types\n- No exceptions",
        )
        assert "OPERATING PROCEDURES" in brief
        assert "Result types" in brief

    def test_attempt_number_shown(self):
        contract = _make_pricing_contract()
        brief = render_handoff_brief(
            "pricing", contract, {"pricing": contract},
            attempt=3,
        )
        assert "Attempt: 3" in brief


class TestRenderProgressSnapshot:
    def test_basic_state(self):
        state = RunState(
            id="abc123", project_dir="/tmp/test",
            status="active", phase="implement",
            total_cost_usd=1.23, total_tokens=50000,
        )
        snapshot = render_progress_snapshot(state)
        assert "abc123" in snapshot
        assert "implement" in snapshot
        assert "$1.23" in snapshot

    def test_with_tree(self):
        state = RunState(id="x", project_dir="/tmp")
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root", description="r",
                    children=["a"], implementation_status="pending",
                ),
                "a": DecompositionNode(
                    component_id="a", name="Component A", description="a",
                    parent_id="root", implementation_status="tested",
                    test_results=TestResults(total=5, passed=5),
                ),
            },
        )
        snapshot = render_progress_snapshot(state, tree=tree)
        assert "[+]" in snapshot  # tested
        assert "[ ]" in snapshot  # pending
        assert "5/5" in snapshot

    def test_paused_state(self):
        state = RunState(
            id="x", project_dir="/tmp",
            status="paused", pause_reason="Waiting for user input",
        )
        snapshot = render_progress_snapshot(state)
        assert "PAUSED" in snapshot
        assert "Waiting for user input" in snapshot


class TestRenderStubJs:
    """Tests for JavaScript JSDoc interface stub generation."""

    def test_basic_rendering(self):
        contract = _make_pricing_contract()
        stub = render_stub_js(contract)
        assert "Pricing Engine" in stub
        assert "pricing" in stub

    def test_contains_jsdoc_typedef(self):
        contract = _make_pricing_contract()
        stub = render_stub_js(contract)
        assert "@typedef" in stub

    def test_contains_function_export(self):
        contract = _make_pricing_contract()
        stub = render_stub_js(contract)
        assert "export function calculate_price" in stub

    def test_no_typescript_syntax(self):
        contract = _make_pricing_contract()
        stub = render_stub_js(contract)
        assert "interface " not in stub
        assert ": string" not in stub
        assert ": number" not in stub

    def test_required_exports_section(self):
        contract = _make_pricing_contract()
        stub = render_stub_js(contract)
        assert "REQUIRED EXPORTS" in stub

    def test_enum_as_jsdoc(self):
        contract = _make_pricing_contract()
        stub = render_stub_js(contract)
        # Enum should render as JSDoc @typedef with union literal
        assert "PricingError" in stub
        assert "unit_not_found" in stub


class TestMapTypeJs:
    """Tests for JSDoc type mapping."""

    def test_str_to_string(self):
        assert _map_type_js("str") == "string"

    def test_int_to_number(self):
        assert _map_type_js("int") == "number"

    def test_bool_to_boolean(self):
        assert _map_type_js("bool") == "boolean"

    def test_custom_type_passthrough(self):
        assert _map_type_js("PriceResult") == "PriceResult"

    def test_list_to_array(self):
        result = _map_type_js("list[str]")
        assert "Array" in result
        assert "string" in result

    def test_optional(self):
        result = _map_type_js("Optional[str]")
        assert "string" in result
        assert "undefined" in result
