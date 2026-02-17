"""Tests for global standards collection and rendering."""

from __future__ import annotations

import json

from pact.schemas import (
    ComponentContract,
    FieldSpec,
    FunctionContract,
    TypeSpec,
    ValidatorSpec,
)
from pact.standards import (
    GlobalStandards,
    collect_standards,
    render_standards_brief,
    _extract_conventions,
)


def _make_contract(
    component_id: str,
    types: list[TypeSpec] | None = None,
    functions: list[FunctionContract] | None = None,
    dependencies: list[str] | None = None,
) -> ComponentContract:
    """Helper to create a test contract."""
    return ComponentContract(
        component_id=component_id,
        name=component_id.replace("_", " ").title(),
        description=f"Test contract for {component_id}",
        version=1,
        types=types or [],
        functions=functions or [],
        dependencies=dependencies or [],
        invariants=[],
    )


class TestGlobalStandards:
    """GlobalStandards dataclass."""

    def test_defaults(self):
        s = GlobalStandards()
        assert s.packages == []
        assert s.shared_types == {}
        assert s.conventions == []
        assert s.validators == []
        assert s.tools == []
        assert s.mocks == {}

    def test_to_dict(self):
        s = GlobalStandards(
            packages=["pydantic>=2.0"],
            shared_types={"UserId": "str"},
        )
        d = s.to_dict()
        assert d["packages"] == ["pydantic>=2.0"]
        assert d["shared_types"] == {"UserId": "str"}

    def test_from_dict(self):
        d = {
            "packages": ["pytest"],
            "shared_types": {"Config": "struct(key: str)"},
            "conventions": ["Use snake_case"],
            "validators": [],
            "tools": ["pytest"],
            "mocks": {},
        }
        s = GlobalStandards.from_dict(d)
        assert s.packages == ["pytest"]
        assert s.shared_types == {"Config": "struct(key: str)"}
        assert s.conventions == ["Use snake_case"]

    def test_round_trip_json(self):
        s = GlobalStandards(
            packages=["pydantic>=2.0", "pytest"],
            shared_types={"UserId": "str", "Config": "struct(key: str)"},
            conventions=["Use early returns"],
            validators=["range(1, 100)"],
            tools=["pytest", "mypy"],
            mocks={"db": "MockDB()"},
        )
        serialized = json.dumps(s.to_dict())
        deserialized = GlobalStandards.from_dict(json.loads(serialized))
        assert deserialized.packages == s.packages
        assert deserialized.shared_types == s.shared_types
        assert deserialized.conventions == s.conventions
        assert deserialized.validators == s.validators
        assert deserialized.tools == s.tools
        assert deserialized.mocks == s.mocks

    def test_from_dict_empty(self):
        s = GlobalStandards.from_dict({})
        assert s.packages == []
        assert s.shared_types == {}


class TestCollectStandards:
    """collect_standards() extraction logic."""

    def test_empty_contracts(self):
        s = collect_standards({})
        assert s.shared_types == {}
        assert s.packages == ["pydantic>=2.0", "pytest"]  # defaults

    def test_shared_types_detected(self):
        """Types appearing in 2+ contracts are shared."""
        user_type = TypeSpec(name="UserId", kind="primitive", description="User ID")
        contracts = {
            "auth": _make_contract("auth", types=[user_type]),
            "billing": _make_contract("billing", types=[user_type]),
        }
        s = collect_standards(contracts)
        assert "UserId" in s.shared_types

    def test_unique_types_not_shared(self):
        """Types appearing in only 1 contract are not shared."""
        contracts = {
            "auth": _make_contract("auth", types=[
                TypeSpec(name="AuthToken", kind="primitive"),
            ]),
            "billing": _make_contract("billing", types=[
                TypeSpec(name="Invoice", kind="struct"),
            ]),
        }
        s = collect_standards(contracts)
        assert "AuthToken" not in s.shared_types
        assert "Invoice" not in s.shared_types

    def test_shared_type_definition_struct(self):
        """Struct types include field definitions."""
        config_type = TypeSpec(
            name="Config",
            kind="struct",
            fields=[
                FieldSpec(name="key", type_ref="str"),
                FieldSpec(name="value", type_ref="int"),
            ],
        )
        contracts = {
            "a": _make_contract("a", types=[config_type]),
            "b": _make_contract("b", types=[config_type]),
        }
        s = collect_standards(contracts)
        assert "Config" in s.shared_types
        assert "key: str" in s.shared_types["Config"]

    def test_shared_type_definition_enum(self):
        """Enum types include variant names."""
        status_type = TypeSpec(
            name="Status",
            kind="enum",
            variants=["active", "inactive"],
        )
        contracts = {
            "a": _make_contract("a", types=[status_type]),
            "b": _make_contract("b", types=[status_type]),
        }
        s = collect_standards(contracts)
        assert "Status" in s.shared_types
        assert "active" in s.shared_types["Status"]

    def test_shared_validators(self):
        """Validators appearing in 2+ contracts are collected."""
        validator = ValidatorSpec(kind="range", expression="1, 100")
        inp = FieldSpec(name="count", type_ref="int", validators=[validator])
        func = FunctionContract(
            name="do_thing",
            description="Do a thing",
            inputs=[inp],
            output_type="bool",
        )
        contracts = {
            "a": _make_contract("a", functions=[func]),
            "b": _make_contract("b", functions=[func]),
        }
        s = collect_standards(contracts)
        assert "range(1, 100)" in s.validators

    def test_unique_validators_not_collected(self):
        """Validators in only 1 contract are not collected."""
        validator = ValidatorSpec(kind="range", expression="1, 10")
        inp = FieldSpec(name="count", type_ref="int", validators=[validator])
        func = FunctionContract(
            name="do_thing",
            description="Do a thing",
            inputs=[inp],
            output_type="bool",
        )
        contracts = {
            "a": _make_contract("a", functions=[func]),
            "b": _make_contract("b"),
        }
        s = collect_standards(contracts)
        assert "range(1, 10)" not in s.validators

    def test_tools_from_config_env(self):
        env = {"required_tools": ["pytest", "mypy"]}
        s = collect_standards({}, config_env=env)
        assert s.tools == ["pytest", "mypy"]

    def test_conventions_from_sops(self):
        sops = """
- Use early returns to reduce nesting
- Prefer snake_case for function names
- Too short
"""
        s = collect_standards({}, sops=sops)
        assert len(s.conventions) == 2  # "Too short" is <=10 chars, excluded
        assert "Use early returns to reduce nesting" in s.conventions

    def test_default_packages(self):
        s = collect_standards({})
        assert "pydantic>=2.0" in s.packages
        assert "pytest" in s.packages


class TestExtractConventions:
    """_extract_conventions() parsing."""

    def test_bullet_points(self):
        text = "- Use early returns\n- Avoid else blocks when possible"
        result = _extract_conventions(text)
        assert "Use early returns" in result
        assert "Avoid else blocks when possible" in result

    def test_numbered_items(self):
        text = "1. Always validate inputs at boundaries\n2. Use logging for errors"
        result = _extract_conventions(text)
        assert "Always validate inputs at boundaries" in result

    def test_star_bullets(self):
        text = "* Use dependency injection for testability"
        result = _extract_conventions(text)
        assert "Use dependency injection for testability" in result

    def test_short_items_excluded(self):
        text = "- Short\n- Use dependency injection for all services"
        result = _extract_conventions(text)
        assert len(result) == 1  # "Short" is too short

    def test_no_bullets(self):
        text = "This is a paragraph with no bullet points."
        result = _extract_conventions(text)
        assert result == []


class TestRenderStandardsBrief:
    """render_standards_brief() output format."""

    def test_empty_standards(self):
        s = GlobalStandards()
        assert render_standards_brief(s) == ""

    def test_has_header(self):
        s = GlobalStandards(packages=["pydantic>=2.0"])
        brief = render_standards_brief(s)
        assert "## GLOBAL STANDARDS" in brief

    def test_packages_section(self):
        s = GlobalStandards(packages=["pydantic>=2.0", "pytest"])
        brief = render_standards_brief(s)
        assert "### Required Packages" in brief
        assert "- pydantic>=2.0" in brief
        assert "- pytest" in brief

    def test_shared_types_section(self):
        s = GlobalStandards(shared_types={"UserId": "str"})
        brief = render_standards_brief(s)
        assert "### Shared Types" in brief
        assert "`UserId`" in brief

    def test_conventions_section(self):
        s = GlobalStandards(conventions=["Use early returns"])
        brief = render_standards_brief(s)
        assert "### Coding Conventions" in brief
        assert "- Use early returns" in brief

    def test_validators_section(self):
        s = GlobalStandards(validators=["range(1, 100)"])
        brief = render_standards_brief(s)
        assert "### Common Validators" in brief

    def test_tools_section(self):
        s = GlobalStandards(tools=["pytest"])
        brief = render_standards_brief(s)
        assert "### Available Tools" in brief
        assert "- pytest" in brief

    def test_multiple_sections(self):
        s = GlobalStandards(
            packages=["pydantic>=2.0"],
            shared_types={"UserId": "str"},
            conventions=["Use early returns"],
        )
        brief = render_standards_brief(s)
        assert "### Required Packages" in brief
        assert "### Shared Types" in brief
        assert "### Coding Conventions" in brief
