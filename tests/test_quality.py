"""Tests for contract quality checks."""
from pact.quality import audit_contract_specificity, VAGUE_PATTERNS
from pact.schemas import (
    ComponentContract, FunctionContract, FieldSpec, ErrorCase, TypeSpec,
    SideEffectKind, SideEffect,
)


def _make_contract(description="A specific component", invariants=None, funcs=None):
    return ComponentContract(
        component_id="test_comp",
        name="Test Component",
        description=description,
        invariants=invariants or [],
        functions=funcs or [FunctionContract(
            name="do_thing", description="Does a specific thing",
            inputs=[FieldSpec(name="x", type_ref="str")],
            output_type="str",
        )],
    )


class TestAuditContractSpecificity:
    def test_clean_contract_no_warnings(self):
        contract = _make_contract(
            description="Validates user input against schema constraints",
            invariants=["All inputs must be non-empty strings"],
        )
        warnings = audit_contract_specificity(contract)
        assert warnings == []

    def test_flags_entire_class_of(self):
        contract = _make_contract(
            description="Prevents an entire class of failures in the system"
        )
        warnings = audit_contract_specificity(contract)
        assert len(warnings) >= 1
        assert any("entire class of" in w for w in warnings)

    def test_flags_best_practice(self):
        contract = _make_contract(
            invariants=["Follows best practices for error handling"]
        )
        warnings = audit_contract_specificity(contract)
        assert len(warnings) >= 1

    def test_flags_properly_handle(self):
        contract = _make_contract(funcs=[FunctionContract(
            name="process",
            description="Will properly handle all edge cases",
            inputs=[FieldSpec(name="x", type_ref="str")],
            output_type="str",
        )])
        warnings = audit_contract_specificity(contract)
        assert any("properly handle" in w.lower() for w in warnings)

    def test_warning_includes_field_path(self):
        contract = _make_contract(
            invariants=["Uses industry standard approaches"]
        )
        warnings = audit_contract_specificity(contract)
        assert any("invariants[0]" in w for w in warnings)

    def test_function_description_checked(self):
        contract = _make_contract(funcs=[FunctionContract(
            name="process",
            description="Handles errors as needed",
            inputs=[FieldSpec(name="x", type_ref="str")],
            output_type="str",
        )])
        warnings = audit_contract_specificity(contract)
        assert any("as needed" in w.lower() for w in warnings)

    def test_multiple_warnings(self):
        contract = _make_contract(
            description="Uses best practices and more",
            invariants=["Scalable and maintainable design"],
        )
        warnings = audit_contract_specificity(contract)
        assert len(warnings) >= 2

    def test_type_description_checked(self):
        contract = ComponentContract(
            component_id="t", name="T", description="OK",
            types=[TypeSpec(
                name="MyType", kind="struct",
                description="Industry standard data structure",
            )],
            functions=[FunctionContract(
                name="f", description="OK",
                inputs=[], output_type="str",
            )],
        )
        warnings = audit_contract_specificity(contract)
        assert any("industry standard" in w.lower() for w in warnings)


class TestSideEffectModels:
    def test_side_effect_kind_values(self):
        assert SideEffectKind.NONE == "none"
        assert SideEffectKind.READS_FILE == "reads_file"
        assert SideEffectKind.WRITES_FILE == "writes_file"
        assert SideEffectKind.NETWORK_CALL == "network_call"

    def test_side_effect_creation(self):
        se = SideEffect(kind=SideEffectKind.WRITES_FILE, target="state.json")
        assert se.kind == SideEffectKind.WRITES_FILE
        assert se.target == "state.json"

    def test_pure_function_side_effect(self):
        se = SideEffect(kind=SideEffectKind.NONE)
        assert se.kind == "none"
        assert se.target == ""

    def test_function_contract_structured_side_effects(self):
        fc = FunctionContract(
            name="save",
            description="Saves data",
            inputs=[FieldSpec(name="data", type_ref="str")],
            output_type="None",
            side_effects=["writes_file: state.json"],
            structured_side_effects=[
                SideEffect(kind=SideEffectKind.WRITES_FILE, target="state.json"),
            ],
        )
        assert len(fc.structured_side_effects) == 1
        assert fc.structured_side_effects[0].kind == SideEffectKind.WRITES_FILE

    def test_backward_compat_string_side_effects(self):
        fc = FunctionContract(
            name="read",
            description="Reads data",
            inputs=[],
            output_type="str",
            side_effects=["reads_file: config.yaml"],
        )
        assert fc.side_effects == ["reads_file: config.yaml"]
        assert fc.structured_side_effects == []
