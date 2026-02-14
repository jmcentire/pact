"""Code author agent — implements black boxes against contracts.

Follows the Research-First Protocol:
1. Research algorithmic approaches, libraries, security best practices
2. Plan implementation approach and self-evaluate
3. Write implementation code
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
    TestResults,
)

logger = logging.getLogger(__name__)

CODE_SYSTEM = """You are a code author implementing a component against its contract.
The contract defines WHAT to build. The tests define HOW to verify.
Your job is to produce an implementation that passes all contract tests.

Key principles:
- Implement exactly what the contract specifies, nothing more
- All types defined in the contract must be implemented
- All functions must match their contract signatures
- Error cases must be handled as specified
- Do not add features beyond the contract
- Write clean, readable code"""


class ImplementationResult:
    """Result of a code author run."""

    def __init__(
        self,
        files: dict[str, str],
        research: ResearchReport,
        plan: PlanEvaluation,
    ) -> None:
        self.files = files
        self.research = research
        self.plan = plan


async def author_code(
    agent: AgentBase,
    contract: ComponentContract,
    test_suite: ContractTestSuite,
    dependency_contracts: dict[str, ComponentContract] | None = None,
    prior_failures: list[str] | None = None,
    prior_test_results: TestResults | None = None,
    attempt: int = 1,
    sops: str = "",
    max_plan_revisions: int = 2,
) -> ImplementationResult:
    """Generate implementation code following the Research-First Protocol.

    Args:
        agent: The LLM agent (typically claude_code backend).
        contract: The ComponentContract to implement.
        test_suite: The ContractTestSuite to pass.
        prior_failures: Descriptions of prior failed attempts (no reasoning).
        sops: Project SOPs.
        max_plan_revisions: Max plan revision attempts.

    Returns:
        ImplementationResult with files dict, research, and plan.
    """
    func_summary = "\n".join(
        f"  - {f.name}({', '.join(i.name + ': ' + i.type_ref for i in f.inputs)}) -> {f.output_type}"
        for f in contract.functions
    )
    type_summary = "\n".join(
        f"  - {t.name} ({t.kind})" for t in contract.types
    )

    failure_context = ""
    if prior_failures:
        failure_context = (
            "\n\nPrior attempts FAILED with these errors "
            "(do NOT repeat the same mistakes):\n"
            + "\n".join(f"  - {f}" for f in prior_failures)
        )

    task_desc = (
        f"Implement component '{contract.name}' (id: {contract.component_id}).\n"
        f"Functions:\n{func_summary}\n"
        f"Types:\n{type_summary}\n"
        f"Dependencies: {contract.dependencies}"
        f"{failure_context}"
    )

    # Phase 1: Research
    research = await research_phase(
        agent, task_desc,
        role_context=(
            "Focus on algorithmic approaches, existing libraries, "
            "performance considerations, security best practices for the domain."
        ),
        sops=sops,
    )

    # Phase 2: Plan
    plan_desc = (
        f"Implementation plan for '{contract.name}':\n"
        f"- Approach: {research.recommended_approach}\n"
        f"- Implement all {len(contract.types)} types\n"
        f"- Implement all {len(contract.functions)} functions\n"
        f"- Handle all error cases\n"
        f"- Must pass {len(test_suite.test_cases)} contract tests"
    )
    plan = await plan_and_evaluate(
        agent, task_desc, research, plan_desc,
        sops=sops, max_revisions=max_plan_revisions,
    )

    # Phase 3: Generate code — using the handoff brief as the mental model
    from pact.interface_stub import render_handoff_brief

    all_contracts = dict(dependency_contracts or {})
    all_contracts[contract.component_id] = contract

    handoff = render_handoff_brief(
        component_id=contract.component_id,
        contract=contract,
        contracts=all_contracts,
        test_suite=test_suite,
        test_results=prior_test_results,
        prior_failures=prior_failures,
        attempt=attempt,
        sops=sops,
    )

    prompt = f"""Implement the component described in this handoff brief.

{handoff}

Research approach: {research.recommended_approach}
Plan: {plan.plan_summary}

Requirements:
- Produce a single Python module implementing all types and functions
- All type names and function signatures must match the interface stub EXACTLY
- Handle all error cases as specified in the stub docstrings
- Dependencies should be accepted as constructor/function parameters (dependency injection)
- Code must be clean, well-structured Python with type annotations
- Must pass ALL tests listed in the brief

Respond with a JSON object containing a "files" dict where keys are filenames
and values are file contents. At minimum include a main module file.

Example response format:
{{"files": {{"module.py": "# implementation code..."}}}}"""

    # Use a simple wrapper model for the response
    from pydantic import BaseModel

    class CodeResponse(BaseModel):
        """Generated implementation files."""
        files: dict[str, str]

    response, in_tok, out_tok = await agent.assess(
        CodeResponse, prompt, CODE_SYSTEM,
    )

    logger.info(
        "Code authored for %s: %d files (%d tokens)",
        contract.component_id, len(response.files), in_tok + out_tok,
    )

    return ImplementationResult(
        files=response.files,
        research=research,
        plan=plan,
    )
