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
- CRITICAL: All types, functions, and error classes must use the EXACT names
  from the contract stub. Check the REQUIRED EXPORTS list at the bottom of the
  stub — every name listed there MUST be importable from your module. Tests
  import these names directly and will fail at collection if any are missing
  or renamed.
- All functions must match their contract signatures exactly
- Error/exception classes referenced in error_cases MUST use the exact class
  names shown (e.g., ConfigFileNotFoundError, not FileNotFoundError)
- Do not add features beyond the contract
- Write clean, readable code
- ALL log statements must include the PACT log key for production traceability
- Use the provided log key preamble at the top of every module"""


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
    external_context: str = "",
    learnings: str = "",
    prior_research: ResearchReport | None = None,
    prior_source: dict[str, str] | None = None,
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

    # Phase 1: Research (or augment prior)
    if prior_research:
        from pact.agents.research import augment_research
        research = await augment_research(
            agent, prior_research,
            supplemental_focus=(
                "Focus on algorithmic approaches, existing libraries, "
                "performance considerations, security best practices for the domain."
            ),
            sops=sops,
        )
    else:
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
    from pact.interface_stub import render_handoff_brief, render_log_key_preamble, project_id_hash

    # Generate log key preamble for production traceability
    pid = project_id_hash(contract.component_id)  # Use component as project proxy
    log_preamble = render_log_key_preamble(pid, contract.component_id)

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
        external_context=external_context,
        learnings=learnings,
        log_key_preamble=log_preamble,
    )

    # The handoff brief is the largest cacheable block
    cache_prefix = handoff

    # Determine if we should use patch mode (high pass rate, targeted fixes)
    use_patch_mode = (
        prior_test_results is not None
        and prior_test_results.total > 0
        and prior_test_results.passed / prior_test_results.total >= 0.8
    )

    if use_patch_mode:
        # Patch mode: preserve working code, only fix specific failures
        failing_tests = []
        if prior_test_results and prior_test_results.failure_details:
            for fd in prior_test_results.failure_details[:10]:
                failing_tests.append(f"  - {fd.test_id}: {fd.error_message}")
        failing_summary = "\n".join(failing_tests) if failing_tests else "  (see prior failures above)"

        # Include prior source in the cache prefix so the model can patch it
        if prior_source:
            prior_source_section = "\n\n## PRIOR IMPLEMENTATION (patch this, do NOT rewrite)\n"
            for fname, content in prior_source.items():
                prior_source_section += f"### {fname}\n```python\n{content}\n```\n"
            cache_prefix += prior_source_section

        prompt = f"""The prior implementation passed {prior_test_results.passed}/{prior_test_results.total} tests.
It is MOSTLY CORRECT. Do NOT rewrite from scratch.

Your task: produce a PATCHED version that fixes ONLY the failing tests while
preserving all passing behavior. The failing tests are:
{failing_summary}

CRITICAL CONSTRAINTS:
- Keep the same overall structure and architecture
- Keep all type names, class names, and function names EXACTLY as they are
- Only modify the specific logic that causes the listed test failures
- Do NOT rename anything — the REQUIRED EXPORTS must remain unchanged
- If a test fails due to edge case handling, add the edge case handling
- If a test fails due to incorrect computation, fix the computation

Research approach: {research.recommended_approach}
Plan: {plan.plan_summary}

Respond with a JSON object containing a "files" dict where keys are filenames
and values are file contents. Include the COMPLETE file (not just the diff).

Example response format:
{{"files": {{"module.py": "# complete patched implementation..."}}}}"""

        logger.info(
            "Using PATCH mode for %s (%d/%d passed previously)",
            contract.component_id,
            prior_test_results.passed,
            prior_test_results.total,
        )
    else:
        # Full implementation mode
        prompt = f"""Implement the component described in the handoff brief above.

Research approach: {research.recommended_approach}
Plan: {plan.plan_summary}

Requirements:
- Produce a single Python module implementing all types and functions
- CRITICAL: All type names, function names, and error class names must match
  the interface stub EXACTLY. See the REQUIRED EXPORTS list at the bottom of
  the stub — every name there MUST be importable from your module
- Handle all error cases using the EXACT exception class names from the stub
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

    response, in_tok, out_tok = await agent.assess_cached(
        CodeResponse, prompt, CODE_SYSTEM, cache_prefix=cache_prefix,
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
