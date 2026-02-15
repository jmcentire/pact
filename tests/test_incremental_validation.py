"""Tests for incremental validation and dependency normalization."""
from pact.contracts import normalize_dependency_name, validate_contract_incremental
from pact.schemas import ComponentContract, FunctionContract, FieldSpec


def _make_contract(cid, deps=None):
    return ComponentContract(
        component_id=cid,
        name=cid.title(),
        description=f"Test {cid}",
        functions=[FunctionContract(
            name="do_thing", description="Does a thing",
            inputs=[FieldSpec(name="x", type_ref="str")],
            output_type="str",
        )],
        dependencies=deps or [],
    )


class TestNormalizeDependencyName:
    def test_exact_match(self):
        assert normalize_dependency_name("shaping_schemas", ["shaping_schemas", "config"]) == "shaping_schemas"

    def test_case_insensitive(self):
        assert normalize_dependency_name("Shaping_Schemas", ["shaping_schemas"]) == "shaping_schemas"

    def test_transposition(self):
        assert normalize_dependency_name("schemas_shaping", ["shaping_schemas"]) == "shaping_schemas"

    def test_no_match_returns_none(self):
        assert normalize_dependency_name("totally_unknown", ["shaping_schemas", "config"]) is None

    def test_exact_match_preferred_over_transposition(self):
        """If exact match exists, use it even if transposition also matches."""
        result = normalize_dependency_name("ab_cd", ["ab_cd", "cd_ab"])
        assert result == "ab_cd"

    def test_empty_known_ids(self):
        assert normalize_dependency_name("anything", []) is None

    def test_single_word(self):
        assert normalize_dependency_name("config", ["config", "schemas"]) == "config"

    def test_single_word_case(self):
        assert normalize_dependency_name("Config", ["config"]) == "config"

    def test_three_word_transposition(self):
        assert normalize_dependency_name("c_b_a", ["a_b_c"]) == "a_b_c"


class TestValidateContractIncremental:
    def test_valid_contract_no_errors(self):
        contract = _make_contract("comp_a")
        errors = validate_contract_incremental(contract, {})
        assert errors == []

    def test_bad_type_ref(self):
        contract = ComponentContract(
            component_id="bad",
            name="Bad",
            description="Bad contract",
            functions=[FunctionContract(
                name="do_thing", description="Does a thing",
                inputs=[FieldSpec(name="x", type_ref="NonexistentType")],
                output_type="str",
            )],
        )
        errors = validate_contract_incremental(contract, {})
        assert any("NonexistentType" in e for e in errors)

    def test_circular_dependency_detected(self):
        existing = {"comp_b": _make_contract("comp_b", deps=["comp_a"])}
        contract = _make_contract("comp_a", deps=["comp_b"])
        errors = validate_contract_incremental(contract, existing)
        assert any("Circular" in e for e in errors)

    def test_no_circular_with_independent(self):
        existing = {"comp_b": _make_contract("comp_b")}
        contract = _make_contract("comp_a", deps=["comp_b"])
        errors = validate_contract_incremental(contract, existing)
        assert errors == []

    def test_missing_name_caught(self):
        contract = ComponentContract(
            component_id="bad",
            name="",
            description="Missing name",
            functions=[FunctionContract(
                name="do_thing", description="D",
                inputs=[], output_type="str",
            )],
        )
        errors = validate_contract_incremental(contract, {})
        assert any("missing name" in e.lower() for e in errors)
