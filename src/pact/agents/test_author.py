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

TEST_SYSTEM = """You are a test author for contract-driven development.
Your job is to generate executable pytest test code that verifies
implementations against their contracts.

Key principles:
- Tests verify behavior at boundaries (inputs/outputs), not internals
- Cover happy paths, edge cases, error cases, and invariants
- Dependencies must be mocked — tests verify one component in isolation
- Generated code must be syntactically valid Python
- Use descriptive test names that explain the scenario
- Include clear assertions with helpful failure messages"""


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
            parts.append(f"  {f.name}({inputs}) -> {f.output_type}")
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
) -> tuple[ContractTestSuite, ResearchReport, PlanEvaluation]:
    """Generate a ContractTestSuite following the Research-First Protocol.

    Returns:
        Tuple of (test_suite, research_report, plan_evaluation).
    """
    func_summary = "\n".join(
        f"  - {f.name}({', '.join(i.name + ': ' + i.type_ref for i in f.inputs)}) -> {f.output_type}"
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

    # Dynamic prompt
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
- Import the component module as: from src.{contract.component_id} import *
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
        ContractTestSuite, prompt, TEST_SYSTEM, cache_prefix=cache_prefix,
    )

    # Ensure required fields
    suite.component_id = contract.component_id
    suite.contract_version = contract.version

    logger.info(
        "Tests authored for %s: %d cases (%d tokens)",
        contract.component_id, len(suite.test_cases), in_tok + out_tok,
    )

    return suite, research, plan
