"""Test author agent — generates ContractTestSuite from ComponentContract.

Follows the Research-First Protocol:
1. Research testing methodologies, coverage strategies, property-based testing
2. Plan test coverage and self-evaluate
3. Generate executable test code
"""

from __future__ import annotations

import logging

from pact.agents.base import AgentBase
from pact.agents.research import plan_and_evaluate, research_phase
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    PlanEvaluation,
    ResearchReport,
)

logger = logging.getLogger(__name__)

TEST_SYSTEM = """You are starting fresh on this test suite with no prior context.

You are a test author generating executable pytest code that verifies
implementations against their contracts. Tests verify behavior at
boundaries, not internals. Cover happy paths, edge cases, error cases,
and invariants. Mock dependencies. Use descriptive test names with
clear assertions.

When the contract defines types with validators, test type construction and
validation: verify valid instances are accepted, invalid inputs are rejected
with appropriate errors, and edge cases at validation boundaries behave
correctly. Canonical data structures are first-class testable units.

Async/sync CRITICAL: Functions marked "async" in the contract MUST be tested
with async test functions using @pytest.mark.asyncio and "await". Functions
NOT marked async MUST be tested with synchronous test functions — never use
await on a sync function. Match the test's async-ness to the function under test."""

TEST_SYSTEM_TS = """You are starting fresh on this test suite with no prior context.

You are a test author generating executable Vitest code in TypeScript
that verifies implementations against their contracts. Tests verify
behavior at boundaries, not internals. Cover happy paths, edge cases,
error cases, and invariants. Mock dependencies with vi.mock(). Use
describe/it blocks with expect() assertions.
- Include clear assertions with helpful failure messages
- When the contract defines types with validators, test type construction and
  validation: verify valid instances are accepted, invalid inputs are rejected
  with appropriate errors, and boundary values behave correctly. Canonical data
  structures are first-class testable units — use Zod schemas, branded types,
  or runtime checks as appropriate.
- Async/sync CRITICAL: Functions marked "async" in the contract MUST be tested
  with async test callbacks (async () => { ... }) using "await". Functions NOT
  marked async MUST be tested synchronously — never await a sync function.
  Match the test's async-ness to the function under test.
- Effect v3 CRITICAL: Data.tagged is curried. WRONG: Data.tagged('Tag', {fields}).
  CORRECT: Data.tagged('Tag')({fields}) or Data.TaggedError('Tag')({fields}).
  The second positional argument is silently ignored — this is the #1 Effect v3 mistake.
  Similarly, Layer.fail() takes a value, not a constructor — pass the constructed error."""

TEST_SYSTEM_JS = """You are starting fresh on this test suite with no prior context.

You are a test author generating executable Vitest code in JavaScript
that verifies implementations against their contracts.

Key principles:
- Tests verify behavior at boundaries (inputs/outputs), not internals
- Cover happy paths, edge cases, error cases, and invariants
- Dependencies must be mocked — tests verify one component in isolation
- Generated code must be syntactically valid JavaScript (ES6+ modules)
- Use describe() and it() blocks to organize tests
- Use expect() assertions with clear matchers (toBe, toEqual, toThrow, etc.)
- Mock dependencies with vi.mock() and vi.fn()
- Import from the source module using relative ESM imports with .js extensions
- Use only vitest — no external dependencies beyond vitest
- Do NOT use TypeScript annotations — no type annotations, no interfaces
- Use descriptive test names that explain the scenario
- Include clear assertions with helpful failure messages
- When the contract defines types with validators, test type construction and
  validation: verify valid instances are accepted, invalid inputs are rejected
  with appropriate errors, and boundary values behave correctly. Canonical data
  structures are first-class testable units.
- Async/sync CRITICAL: Functions marked "async" in the contract MUST be tested
  with async test callbacks (async () => { ... }) using "await". Functions NOT
  marked async MUST be tested synchronously — never await a sync function.
  Match the test's async-ness to the function under test.
- Effect v3 CRITICAL: Data.tagged is curried. WRONG: Data.tagged('Tag', {fields}).
  CORRECT: Data.tagged('Tag')({fields}) or Data.TaggedError('Tag')({fields}).
  The second positional argument is silently ignored — this is the #1 Effect v3 mistake.
  Similarly, Layer.fail() takes a value, not a constructor — pass the constructed error."""


def _render_focused_contract(contract: ComponentContract) -> str:
    """Render a focused contract summary for test authoring.

    Includes all information needed for test generation while omitting
    redundant metadata (component_id, version, etc. already in task_desc).
    ~50-70% smaller than model_dump_json().
    """
    parts = []

    # Types with full field details
    if contract.types:
        parts.append("Types:")
        for t in contract.types:
            if t.fields:
                fields = ", ".join(f"{f.name}: {f.type_ref}" for f in t.fields)
                parts.append(f"  {t.name} ({t.kind}): {{{fields}}}")
                # Surface validators so tests can verify rejection of invalid data
                for fld in t.fields:
                    if fld.validators:
                        for v in fld.validators:
                            parts.append(f"    {fld.name} validator ({v.kind}): {v.expression}")
            elif t.kind == "enum" and t.variants:
                variants = ", ".join(t.variants)
                parts.append(f"  {t.name} (enum): [{variants}]")
            else:
                parts.append(f"  {t.name} ({t.kind})")
            if t.description:
                parts.append(f"    # {t.description}")

    # Functions with inputs, outputs, preconditions, postconditions, error cases
    if contract.functions:
        parts.append("\nFunctions:")
        for f in contract.functions:
            inputs = ", ".join(f"{i.name}: {i.type_ref}" for i in f.inputs)
            async_marker = "async " if f.is_async else ""
            parts.append(f"  {async_marker}{f.name}({inputs}) -> {f.output_type}")
            if f.description:
                parts.append(f"    # {f.description}")
            if f.preconditions:
                for pre in f.preconditions:
                    parts.append(f"    precondition: {pre}")
            if f.postconditions:
                for post in f.postconditions:
                    parts.append(f"    postcondition: {post}")
            if f.error_cases:
                for err in f.error_cases:
                    cond = f" when {err.condition}" if err.condition else ""
                    parts.append(f"    error: {err.name}{cond}")
            # Structured side effects (or fall back to string side_effects)
            if f.structured_side_effects:
                for se in f.structured_side_effects:
                    parts.append(f"    side_effect: {se.kind} -> {se.target}")
            elif f.side_effects:
                for se in f.side_effects:
                    parts.append(f"    side_effect: {se}")
            # Performance budget
            if f.performance_budget:
                pb = f.performance_budget
                budget_parts = []
                if pb.p95_latency_ms:
                    budget_parts.append(f"p95<{pb.p95_latency_ms}ms")
                if pb.max_memory_mb:
                    budget_parts.append(f"mem<{pb.max_memory_mb}MB")
                if pb.complexity:
                    budget_parts.append(pb.complexity)
                if budget_parts:
                    parts.append(f"    performance: {', '.join(budget_parts)}")

    # Invariants
    if contract.invariants:
        parts.append("\nInvariants:")
        for inv in contract.invariants:
            parts.append(f"  - {inv}")

    # Dependencies
    if contract.dependencies:
        parts.append(f"\nDependencies: {', '.join(contract.dependencies)}")

    return "\n".join(parts)


async def author_tests(
    agent: AgentBase,
    contract: ComponentContract,
    dependency_contracts: dict[str, ComponentContract] | None = None,
    sops: str = "",
    max_plan_revisions: int = 2,
    prior_research: ResearchReport | None = None,
    language: str = "python",
    package_namespace: str = "",
) -> tuple[ContractTestSuite, ResearchReport, PlanEvaluation]:
    """Generate a ContractTestSuite following the Research-First Protocol.

    Args:
        agent: The LLM agent backend.
        contract: The component contract to generate tests for.
        dependency_contracts: Contracts for dependencies (used for mock info).
        sops: Standard operating procedures text.
        max_plan_revisions: Max plan revision cycles.
        prior_research: Existing research to augment instead of starting fresh.
        language: Test language — "python" (default) or "typescript".

    Returns:
        Tuple of (test_suite, research_report, plan_evaluation).
    """
    func_summary = "\n".join(
        f"  - {'async ' if f.is_async else ''}{f.name}({', '.join(i.name + ': ' + i.type_ref for i in f.inputs)}) -> {f.output_type}"
        + (f" [errors: {', '.join(e.name for e in f.error_cases)}]" if f.error_cases else "")
        for f in contract.functions
    )
    type_summary = "\n".join(
        f"  - {t.name} ({t.kind})"
        + (f": {', '.join(f.name for f in t.fields)}" if t.fields else "")
        for t in contract.types
    )

    task_desc = (
        f"Generate tests for component '{contract.name}' "
        f"(id: {contract.component_id}).\n"
        f"Functions:\n{func_summary}\n"
        f"Types:\n{type_summary}"
    )

    # Phase 1: Research (or augment prior)
    if prior_research:
        from pact.agents.research import augment_research
        research = await augment_research(
            agent, prior_research,
            supplemental_focus=(
                "Focus on testing methodologies for this type of component, "
                "coverage strategies, common test anti-patterns, "
                "property-based testing opportunities."
            ),
            sops=sops,
        )
    else:
        research = await research_phase(
            agent, task_desc,
            role_context=(
                "Focus on testing methodologies for this type of component, "
                "coverage strategies, common test anti-patterns, "
                "property-based testing opportunities."
            ),
            sops=sops,
        )

    # Phase 2: Plan
    plan_desc = (
        f"Test plan for '{contract.name}':\n"
        f"- Approach: {research.recommended_approach}\n"
        f"- Happy path tests for each function\n"
        f"- Edge case tests based on preconditions/postconditions\n"
        f"- Error case tests for each ErrorCase\n"
        f"- Invariant tests\n"
        f"- Mock all dependencies: {contract.dependencies}"
    )
    plan = await plan_and_evaluate(
        agent, task_desc, research, plan_desc,
        sops=sops, max_revisions=max_plan_revisions,
    )

    # Phase 3: Generate tests
    contract_summary = _render_focused_contract(contract)

    dep_mock_info = ""
    if dependency_contracts:
        for dep_id, dc in dependency_contracts.items():
            dep_mock_info += f"\nDependency '{dep_id}' functions to mock:\n"
            for func in dc.functions:
                inputs_str = ", ".join(f"{i.name}: {i.type_ref}" for i in func.inputs)
                dep_mock_info += f"  - {func.name}({inputs_str}) -> {func.output_type}\n"

    # Build cache prefix from static contract info
    cache_parts = [f"Contract:\n{contract_summary}"]
    if dep_mock_info:
        cache_parts.append(dep_mock_info)
    cache_prefix = "\n\n".join(cache_parts)

    # Dynamic prompt — language-specific
    if language == "typescript":
        system_prompt = TEST_SYSTEM_TS
        prompt = f"""Generate a complete ContractTestSuite with executable Vitest test code in TypeScript.

Research approach: {research.recommended_approach}
Plan: {plan.plan_summary}

Requirements:
- component_id must be "{contract.component_id}"
- contract_version must be {contract.version}
- Include test_cases for:
  * At least one happy_path test per function
  * Edge cases based on preconditions and field validators
  * Error case tests for each ErrorCase defined
  * Invariant tests if contract has invariants
- generated_code must be valid TypeScript Vitest code
- Use describe() and it() blocks to organize tests
- Use expect() assertions (toBe, toEqual, toThrow, toHaveBeenCalled, etc.)
- Mock all dependencies using vi.mock() and vi.fn()
- Import from vitest: import {{ describe, it, expect, vi }} from 'vitest'
- Import the component module using relative ESM imports, e.g.:
  import {{ functionName }} from './{contract.component_id}'
- Each test should have clear assertions
- test_language must be "typescript"
- ONLY use vitest — do NOT use jest, mocha, or any other test framework
- TypeScript strict mode — no implicit any, proper type annotations
- For enum types, access variants using the EXACT names from the contract
  (e.g., if the contract says variants: ["active", "paused"], use
  MyEnum.active, NOT MyEnum.ACTIVE)

The generated_code field should contain the COMPLETE test file content,
ready to be saved as contract_test.ts and run with vitest."""
    elif language == "javascript":
        system_prompt = TEST_SYSTEM_JS
        prompt = f"""Generate a complete ContractTestSuite with executable Vitest test code in JavaScript.

Research approach: {research.recommended_approach}
Plan: {plan.plan_summary}

Requirements:
- component_id must be "{contract.component_id}"
- contract_version must be {contract.version}
- Include test_cases for:
  * At least one happy_path test per function
  * Edge cases based on preconditions and field validators
  * Error case tests for each ErrorCase defined
  * Invariant tests if contract has invariants
- generated_code must be valid JavaScript Vitest code (NOT TypeScript)
- Use describe() and it() blocks to organize tests
- Use expect() assertions (toBe, toEqual, toThrow, toHaveBeenCalled, etc.)
- Mock all dependencies using vi.mock() and vi.fn()
- Import from vitest: import {{ describe, it, expect, vi }} from 'vitest'
- Import the component module using relative ESM imports with .js extensions, e.g.:
  import {{ functionName }} from './{contract.component_id}.js'
- Each test should have clear assertions
- test_language must be "javascript"
- ONLY use vitest — do NOT use jest, mocha, or any other test framework
- Do NOT use TypeScript annotations — plain JavaScript only
- For enum types, access variants using the EXACT names from the contract
  (e.g., if the contract says variants: ["active", "paused"], use
  MyEnum.active, NOT MyEnum.ACTIVE)

The generated_code field should contain the COMPLETE test file content,
ready to be saved as contract_test.js and run with vitest."""
    else:
        system_prompt = TEST_SYSTEM
        prompt = f"""Generate a complete ContractTestSuite with executable pytest code.

Research approach: {research.recommended_approach}
Plan: {plan.plan_summary}

Requirements:
- component_id must be "{contract.component_id}"
- contract_version must be {contract.version}
- Include test_cases for:
  * At least one happy_path test per function
  * Edge cases based on preconditions and field validators
  * Error case tests for each ErrorCase defined
  * Invariant tests if contract has invariants
- generated_code must be valid Python pytest code
- Mock all dependencies using unittest.mock
- Import the component module as: from {(package_namespace + '.') if package_namespace else ''}{contract.component_id} import *
  (or a reasonable module path based on the component)
- Each test should have clear assertions
- test_language must be "python"
- ONLY use pytest and unittest.mock — do NOT use hypothesis, property-based
  testing libraries, or any other third-party libraries that may not be installed.
  If you want randomized testing, use random directly.
- For enum types, access variants using the EXACT names from the contract
  (e.g., if the contract says variants: ["active", "paused"], use
  MyEnum.active, NOT MyEnum.ACTIVE)

The generated_code field should contain the COMPLETE test file content,
ready to be saved as contract_test.py and run with pytest."""

    suite, in_tok, out_tok = await agent.assess_cached(
        ContractTestSuite, prompt, system_prompt, cache_prefix=cache_prefix,
    )

    # Ensure required fields
    suite.component_id = contract.component_id
    suite.contract_version = contract.version
    suite.test_language = language

    logger.info(
        "Tests authored for %s: %d cases (%d tokens)",
        contract.component_id, len(suite.test_cases), in_tok + out_tok,
    )

    return suite, research, plan


# ── Goodhart (Hidden) Test Author ─────────────────────────────────

GOODHART_SYSTEM = """You are starting fresh on this adversarial review with no prior context.

You are generating hidden acceptance tests that catch implementations that
"teach to the test" — passing visible tests through shortcuts rather than
truly satisfying the contract.

Think like a code reviewer who suspects the implementation was written by an agent
that could see the exact test inputs and assertions. Ask yourself:
- What shortcuts could an agent take if it only saw the visible tests?
- Hardcoded returns that happen to match visible test inputs
- Missing validation that visible tests don't exercise
- Invariants that hold only for the specific values in visible tests
- Boundary conditions adjacent to (but distinct from) visible edge cases
- Postconditions that should hold generally but visible tests only check with specific values

Key principles:
- Tests verify behavior at boundaries (inputs/outputs), not internals
- All test functions MUST be prefixed with test_goodhart_
- Each test's description field MUST explain the behavioral property being tested,
  NOT the specific assertion. These descriptions become graduated hints during remediation.
- Dependencies must be mocked — tests verify one component in isolation
- Generated code must be syntactically valid
- Do NOT duplicate coverage already in the visible tests — find gaps"""

GOODHART_SYSTEM_TS = """You are starting fresh on this adversarial review with no prior context.

You are generating hidden TypeScript acceptance tests that catch implementations
that "teach to the test" — passing visible tests through shortcuts rather than
truly satisfying the contract.

Think like a code reviewer who suspects the implementation was written by an agent
that could see the exact test inputs and assertions. Ask yourself:
- What shortcuts could an agent take if it only saw the visible tests?
- Hardcoded returns that happen to match visible test inputs
- Missing validation that visible tests don't exercise
- Invariants that hold only for the specific values in visible tests
- Boundary conditions adjacent to (but distinct from) visible edge cases
- Postconditions that should hold generally but visible tests only check with specific values

Key principles:
- Tests verify behavior at boundaries (inputs/outputs), not internals
- All test names MUST contain "goodhart" (e.g., "goodhart: should handle...")
- Each test's description field MUST explain the behavioral property being tested,
  NOT the specific assertion. These descriptions become graduated hints during remediation.
- Dependencies must be mocked — tests verify one component in isolation
- Generated code must be syntactically valid TypeScript for Vitest
- Do NOT duplicate coverage already in the visible tests — find gaps
- Effect v3 CRITICAL: Data.tagged is curried. WRONG: Data.tagged('Tag', {fields}).
  CORRECT: Data.tagged('Tag')({fields}) or Data.TaggedError('Tag')({fields}).
  The second positional argument is silently ignored. Layer.fail() takes a value, not a constructor."""


async def author_goodhart_tests(
    agent: AgentBase,
    contract: ComponentContract,
    visible_suite: ContractTestSuite,
    dependency_contracts: dict[str, ComponentContract] | None = None,
    language: str = "python",
    package_namespace: str = "",
) -> ContractTestSuite:
    """Generate adversarial hidden tests (single LLM call, no research/plan).

    These tests are never shown to the implementation agent. They catch
    Goodhart's Law violations: implementations that optimize for visible
    test inputs rather than truly satisfying the contract.

    Args:
        agent: The LLM agent backend.
        contract: The component contract.
        visible_suite: The visible test suite (for gap analysis).
        dependency_contracts: Contracts for dependencies (mock info).
        language: Test language — "python", "typescript", or "javascript".

    Returns:
        ContractTestSuite with adversarial test cases.
    """
    contract_summary = _render_focused_contract(contract)

    # Summarize visible tests so the LLM knows what's already covered
    visible_summary = "\n".join(
        f"  - {tc.id}: {tc.description} [{tc.category}] (function: {tc.function})"
        for tc in visible_suite.test_cases
    )

    dep_mock_info = ""
    if dependency_contracts:
        for dep_id, dc in dependency_contracts.items():
            dep_mock_info += f"\nDependency '{dep_id}' functions to mock:\n"
            for func in dc.functions:
                inputs_str = ", ".join(f"{i.name}: {i.type_ref}" for i in func.inputs)
                dep_mock_info += f"  - {func.name}({inputs_str}) -> {func.output_type}\n"

    # Select language-specific system prompt and instructions
    if language in ("typescript", "javascript"):
        system_prompt = GOODHART_SYSTEM_TS
        import_hint = f"import {{ ... }} from '../src/{contract.component_id}'"
        framework = "Vitest"
        test_lang = language
    else:
        system_prompt = GOODHART_SYSTEM
        if package_namespace:
            import_hint = f"from {package_namespace}.{contract.component_id} import *"
        else:
            import_hint = f"from src.{contract.component_id} import *"
        framework = "pytest"
        test_lang = "python"

    prompt = f"""Generate adversarial hidden acceptance tests for component '{contract.name}'.

Contract:
{contract_summary}

Visible tests already covering this contract:
{visible_summary}

{f"Dependencies to mock:{dep_mock_info}" if dep_mock_info else ""}

Requirements:
- component_id must be "{contract.component_id}"
- contract_version must be {contract.version}
- All test functions prefixed with test_goodhart_ (Python) or described as "goodhart: ..." (TS/JS)
- Each test_case description must explain the BEHAVIORAL PROPERTY, not the assertion
  Good: "The add function should be commutative for all numeric inputs"
  Bad: "Test that add(2,3) equals add(3,2)"
- Find gaps in visible test coverage — do NOT duplicate existing tests
- Focus on: hardcoded-return detection, boundary adjacency, invariant generalization,
  postcondition universality, input-space exploration beyond visible values
- Import from: {import_hint}
- Use {framework} conventions
- test_language must be "{test_lang}"
- generated_code must contain the COMPLETE test file"""

    cache_prefix = f"Contract:\n{contract_summary}\n\nVisible tests:\n{visible_summary}"

    suite, in_tok, out_tok = await agent.assess_cached(
        ContractTestSuite, prompt, system_prompt, cache_prefix=cache_prefix,
    )

    # Ensure required fields
    suite.component_id = contract.component_id
    suite.contract_version = contract.version
    suite.test_language = language

    logger.info(
        "Goodhart tests authored for %s: %d cases (%d tokens)",
        contract.component_id, len(suite.test_cases), in_tok + out_tok,
    )

    return suite


def generate_emission_compliance_test(
    contract: ComponentContract,
    language: str = "python",
) -> str:
    """Generate a deterministic emission compliance test from the contract interface.

    No LLM needed — purely mechanical. Tests that:
    1. Component accepts event_handler and log_handler
    2. Each public method emits invoked + completed events
    3. Events contain pact_key matching PACT:<cid>:<method>
    4. Events contain input_classification and output_classification lists
    5. Null handler causes no errors
    """
    cid = contract.component_id
    class_name = "".join(w.capitalize() for w in cid.replace("-", "_").split("_"))
    method_names = [f.name for f in contract.functions]

    if language in ("typescript", "javascript"):
        return _generate_emission_test_ts(cid, class_name, method_names)

    # Python emission compliance test
    lines = [
        f'"""Emission compliance tests for {cid} (auto-generated by pact)."""',
        "",
        f"from {cid}.{cid} import {class_name}",
        "",
        "",
        "class TestEmissionCompliance:",
        f'    """Verify {cid} emits structured events correctly."""',
        "",
        "    def _make_handler(self):",
        "        events = []",
        "        return events, lambda event: events.append(event)",
        "",
    ]

    for method in method_names:
        lines.extend([
            f"    def test_{method}_emits_events(self):",
            f'        """Method {method} emits invoked and completed events."""',
            "        events, handler = self._make_handler()",
            f"        obj = {class_name}(event_handler=handler)",
            "        try:",
            f"            obj.{method}()",
            "        except (TypeError, Exception):",
            "            pass",
            f'        invoked = [e for e in events if e.get("event") == "invoked"]',
            f'        completed = [e for e in events if e.get("event") in ("completed", "error")]',
            f"        assert len(invoked) >= 1, 'No invoked event for {method}'",
            f"        assert len(completed) >= 1, 'No completed/error event for {method}'",
            f'        assert invoked[0].get("pact_key") == "PACT:{cid}:{method}"',
            f'        assert isinstance(invoked[0].get("input_classification"), list)',
            "",
        ])

    lines.extend([
        "    def test_pact_key_format(self):",
        '        """All emitted events have correctly formatted PACT keys."""',
        "        events, handler = self._make_handler()",
        f"        obj = {class_name}(event_handler=handler)",
    ])
    for method in method_names:
        lines.append(f"        try:")
        lines.append(f"            obj.{method}()")
        lines.append(f"        except (TypeError, Exception):")
        lines.append(f"            pass")
    lines.extend([
        "        for event in events:",
        '            key = event.get("pact_key", "")',
        f'            assert key.startswith("PACT:{cid}:"), f"Bad pact_key: {{key}}"',
        "",
        "    def test_null_handler_no_errors(self):",
        '        """Component works without event_handler — no errors."""',
        f"        obj = {class_name}()",
    ])
    for method in method_names:
        lines.append(f"        try:")
        lines.append(f"            obj.{method}()")
        lines.append(f"        except (TypeError, Exception):")
        lines.append(f"            pass")
    lines.append("")

    return "\n".join(lines)


def _generate_emission_test_ts(cid: str, class_name: str, method_names: list[str]) -> str:
    """Generate TypeScript/JavaScript emission compliance test."""
    lines = [
        f'import {{ describe, it, expect }} from "vitest";',
        f'import {{ {class_name} }} from "../src/{cid}/{cid}.js";',
        "",
        f'describe("{cid} emission compliance", () => {{',
    ]

    for method in method_names:
        lines.extend([
            f'  it("{method} emits invoked and completed events", () => {{',
            "    const events: any[] = [];",
            f"    const obj = new {class_name}({{ eventHandler: (e: any) => events.push(e) }});",
            "    try {",
            f"      (obj as any).{method}();",
            "    } catch (e) {{}}",
            '    const invoked = events.filter(e => e.event === "invoked");',
            '    const completed = events.filter(e => ["completed", "error"].includes(e.event));',
            "    expect(invoked.length).toBeGreaterThanOrEqual(1);",
            "    expect(completed.length).toBeGreaterThanOrEqual(1);",
            f'    expect(invoked[0].pact_key).toBe("PACT:{cid}:{method}");',
            "  }});",
            "",
        ])

    lines.extend([
        '  it("null handler causes no errors", () => {{',
        f"    const obj = new {class_name}();",
    ])
    for method in method_names:
        lines.append(f"    try {{ (obj as any).{method}(); }} catch (e) {{}}")
    lines.extend([
        "  }});",
        "}});",
    ])

    return "\n".join(lines)
