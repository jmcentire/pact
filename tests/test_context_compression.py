"""Tests for tiered context compression."""
from pact.interface_stub import build_code_agent_context, _estimate_tokens
from pact.schemas import (
    ComponentContract, ContractTestSuite, TestCase,
    FunctionContract, FieldSpec,
)


def _make_contract(name: str = "test_component") -> ComponentContract:
    return ComponentContract(
        component_id=name, name=name, description=f"A {name} component",
        functions=[
            FunctionContract(
                name="do_thing",
                description="Does the thing",
                inputs=[FieldSpec(name="x", type_ref="str")],
                output_type="str",
            ),
        ],
    )


def _make_test_suite(component_id: str = "test_component", code: str = "") -> ContractTestSuite:
    return ContractTestSuite(
        component_id=component_id,
        contract_version=1,
        test_cases=[
            TestCase(
                id="test_1", description="Test basic functionality",
                function="do_thing", category="happy_path",
            ),
        ],
        generated_code=code or "def test_do_thing():\n    assert do_thing('hello') == 'hello'",
    )


class TestEstimateTokens:
    def test_empty_string(self):
        assert _estimate_tokens("") == 1  # Floor of 1

    def test_short_string(self):
        tokens = _estimate_tokens("hello world")
        assert tokens > 0

    def test_longer_string_more_tokens(self):
        short = _estimate_tokens("hello")
        long = _estimate_tokens("hello world this is a much longer string")
        assert long > short


class TestBuildCodeAgentContext:
    def test_always_includes_contract_and_tests(self):
        contract = _make_contract()
        suite = _make_test_suite()
        result = build_code_agent_context(contract, suite, max_tokens=100000)
        assert "CONTRACT" in result
        assert "do_thing" in result
        assert "TESTS" in result or "test_do_thing" in result

    def test_always_includes_contract_even_small_budget(self):
        contract = _make_contract()
        suite = _make_test_suite()
        result = build_code_agent_context(contract, suite, max_tokens=50)
        # Contract stub should still be there (tier 1 never truncated)
        assert "do_thing" in result

    def test_includes_decisions_if_room(self):
        contract = _make_contract()
        suite = _make_test_suite()
        decisions = ["Use async/await for all I/O operations", "Prefer composition over inheritance"]
        result = build_code_agent_context(contract, suite, decisions=decisions, max_tokens=100000)
        assert "DECISIONS" in result
        assert "async/await" in result

    def test_excludes_research_by_default(self):
        contract = _make_contract()
        suite = _make_test_suite()
        result = build_code_agent_context(contract, suite, max_tokens=100000)
        assert "RESEARCH" not in result

    def test_includes_research_if_provided_and_room(self):
        contract = _make_contract()
        suite = _make_test_suite()
        research = [
            {"topic": "Error Handling", "finding": "Use structured errors. This improves debugging."},
            {"topic": "Performance", "finding": "Batch operations reduce latency."},
        ]
        result = build_code_agent_context(contract, suite, research=research, max_tokens=100000)
        assert "RESEARCH" in result
        assert "Error Handling" in result

    def test_truncates_decisions_gracefully(self):
        contract = _make_contract()
        suite = _make_test_suite()
        # Many long decisions
        decisions = [f"Decision {i}: " + "x" * 200 for i in range(50)]
        result = build_code_agent_context(contract, suite, decisions=decisions, max_tokens=500)
        # Should still have contract
        assert "do_thing" in result
        # Should have at most a few decisions
        assert result.count("Decision") < 50

    def test_truncates_research_gracefully(self):
        contract = _make_contract()
        suite = _make_test_suite()
        research = [{"topic": f"Topic {i}", "finding": "x" * 200} for i in range(50)]
        result = build_code_agent_context(contract, suite, research=research, max_tokens=500)
        assert "do_thing" in result

    def test_no_decisions_no_section(self):
        contract = _make_contract()
        suite = _make_test_suite()
        result = build_code_agent_context(contract, suite, decisions=None, max_tokens=100000)
        assert "DECISIONS" not in result

    def test_empty_decisions_no_section(self):
        contract = _make_contract()
        suite = _make_test_suite()
        result = build_code_agent_context(contract, suite, decisions=[], max_tokens=100000)
        assert "DECISIONS" not in result

    def test_test_cases_fallback_when_no_code(self):
        contract = _make_contract()
        suite = ContractTestSuite(
            component_id="test_component", contract_version=1,
            test_cases=[
                TestCase(id="t1", description="Check output", function="do_thing", category="happy_path"),
            ],
            generated_code="",
        )
        result = build_code_agent_context(contract, suite, max_tokens=100000)
        assert "TEST CASES" in result
        assert "Check output" in result

    def test_default_max_tokens(self):
        contract = _make_contract()
        suite = _make_test_suite()
        # Should not raise with default
        result = build_code_agent_context(contract, suite)
        assert len(result) > 0
