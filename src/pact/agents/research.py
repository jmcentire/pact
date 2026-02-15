"""Research-first protocol — shared by all agents.

Every agent follows 3 phases before producing work:
  1. Research: best practices, patterns, pitfalls
  2. Plan + self-evaluate: efficiency, compliance, risks
  3. Execute: produce actual work product

This module provides the research and plan-evaluation steps.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from pact.agents.base import AgentBase
from pact.schemas import PlanEvaluation, ResearchReport

logger = logging.getLogger(__name__)


@contextmanager
def _temporary_model(agent: AgentBase, model: str):
    """Temporarily switch an agent's model, restoring it afterward."""
    original = agent._model
    agent.set_model(model)
    try:
        yield
    finally:
        agent.set_model(original)

RESEARCH_SYSTEM = """You are a senior engineering researcher. Before any work begins,
you research best practices, established patterns, and common pitfalls.
Be thorough but concise. Focus on actionable findings."""

PLAN_SYSTEM = """You are a senior engineering planner. Evaluate the proposed plan
against research findings and task requirements. Be honest about risks
and efficiency. Only mark 'proceed' if the plan is sound."""


async def research_phase(
    agent: AgentBase,
    task_description: str,
    role_context: str,
    sops: str = "",
    research_model: str = "",
) -> ResearchReport:
    """Phase 1: Research best practices before beginning work.

    Args:
        agent: The LLM agent to use for research.
        task_description: What the agent is about to do.
        role_context: Role-specific research focus (e.g., "type system design").
        sops: Project SOPs to consider.
        research_model: If provided, temporarily switch to this model for research.

    Returns:
        ResearchReport with findings and recommended approach.
    """
    # Build the cacheable prefix (SOPs + research template)
    cache_prefix = ""
    if sops:
        cache_prefix = f"Project Operating Procedures:\n{sops}\n\n"

    prompt = f"""You are about to: {task_description}

Role-specific focus: {role_context}

Before beginning, research best practices:
1. What are established patterns for this kind of work?
2. What are common pitfalls?
3. What standards, conventions, or idioms should be followed?
4. Are there existing implementations or libraries that should be referenced?
5. What edge cases are frequently missed?

Produce a concise research report."""

    if research_model:
        with _temporary_model(agent, research_model):
            result, in_tok, out_tok = await agent.assess_cached(
                ResearchReport, prompt, RESEARCH_SYSTEM, cache_prefix=cache_prefix,
            )
    else:
        result, in_tok, out_tok = await agent.assess_cached(
            ResearchReport, prompt, RESEARCH_SYSTEM, cache_prefix=cache_prefix,
        )
    logger.info(
        "Research complete: %d findings, %d tokens",
        len(result.findings), in_tok + out_tok,
    )
    return result


async def plan_and_evaluate(
    agent: AgentBase,
    task_description: str,
    research: ResearchReport,
    plan_description: str,
    sops: str = "",
    max_revisions: int = 2,
    research_model: str = "",
) -> PlanEvaluation:
    """Phase 2: Plan and self-evaluate before execution.

    Args:
        agent: The LLM agent to use.
        task_description: What the agent is about to do.
        research: Output of the research phase.
        plan_description: Initial plan to evaluate.
        sops: Project SOPs.
        max_revisions: Max times to revise before escalating.
        research_model: If provided, temporarily switch to this model for evaluation.

    Returns:
        PlanEvaluation with decision (proceed/revise/escalate).
    """
    research_summary = "\n".join(
        f"- {f.topic}: {f.finding}" for f in research.findings
    )

    # Build cacheable prefix: research findings + SOPs
    cache_parts = [f"Research findings:\n{research_summary}\n\nRecommended approach from research: {research.recommended_approach}"]
    if sops:
        cache_parts.append(f"\nProject Operating Procedures:\n{sops}")
    cache_prefix = "\n".join(cache_parts)

    for attempt in range(max_revisions + 1):
        # Dynamic part: changes each revision attempt
        prompt = f"""Task: {task_description}

Current plan (attempt {attempt + 1}):
{plan_description}

Evaluate this plan:
1. Is this the most efficient approach? (efficiency)
2. Does it comply with the best practices identified? (compliance)
3. What risks remain? (risk)
4. Decision: proceed, revise, or escalate?

If revising, explain what to change in revision_notes."""

        if research_model:
            with _temporary_model(agent, research_model):
                result, in_tok, out_tok = await agent.assess_cached(
                    PlanEvaluation, prompt, PLAN_SYSTEM, cache_prefix=cache_prefix,
                )
        else:
            result, in_tok, out_tok = await agent.assess_cached(
                PlanEvaluation, prompt, PLAN_SYSTEM, cache_prefix=cache_prefix,
            )
        logger.info(
            "Plan evaluation (attempt %d): %s (%d tokens)",
            attempt + 1, result.decision, in_tok + out_tok,
        )

        if result.decision == "proceed":
            return result
        if result.decision == "escalate":
            return result
        if result.decision == "revise" and attempt < max_revisions:
            plan_description = (
                f"{plan_description}\n\n"
                f"Revision notes from self-evaluation:\n{result.revision_notes}"
            )
            continue
        # Max revisions reached, force proceed
        result.decision = "proceed"
        return result

    return result


async def research_for_group(
    agent: AgentBase,
    group_description: str,
    components: list[dict],
    role_context: str,
    sops: str = "",
) -> ResearchReport:
    """One research phase covering multiple related components.

    Instead of researching each component independently, this produces
    findings that apply broadly to a group of sibling components sharing
    a parent. Per-component specifics come later in plan evaluation.

    Args:
        agent: The LLM agent to use.
        group_description: Description of the group (parent component desc).
        components: List of dicts with 'id', 'name', 'description' keys.
        role_context: Role-specific research focus.
        sops: Project SOPs.

    Returns:
        ResearchReport with findings applicable to the entire group.
    """
    component_listing = "\n".join(
        f"  - {c['name']} ({c['id']}): {c.get('description', '')}"
        for c in components
    )

    sops_section = f"\n\nProject Operating Procedures:\n{sops}" if sops else ""

    prompt = f"""You are about to work on a group of related components:

Group context: {group_description}

Components in this group:
{component_listing}

Role-specific focus: {role_context}
{sops_section}

Research best practices that apply to ALL components in this group:
1. What are established patterns for this kind of work?
2. What are common pitfalls across these components?
3. What standards, conventions, or idioms should be followed?
4. Are there shared libraries or utilities that multiple components should use?
5. What cross-cutting concerns (error handling, logging, types) apply to all?

Produce a concise research report with findings applicable to the entire group."""

    result, in_tok, out_tok = await agent.assess_cached(
        ResearchReport, prompt, RESEARCH_SYSTEM,
        cache_prefix=sops_section if sops else "",
    )
    logger.info(
        "Group research complete: %d findings for %d components (%d tokens)",
        len(result.findings), len(components), in_tok + out_tok,
    )
    return result


async def augment_research(
    agent: AgentBase,
    base_research: ResearchReport,
    supplemental_focus: str,
    sops: str = "",
) -> ResearchReport:
    """Augment existing research with additional role-specific findings.

    Much cheaper than full research — sends base findings as context and
    asks only for supplemental findings. The returned report merges base
    and supplemental findings.

    Args:
        agent: The LLM agent.
        base_research: Research from a prior phase (e.g., contract authoring).
        supplemental_focus: What additional focus to research (e.g., testing patterns).
        sops: Project SOPs.

    Returns:
        ResearchReport with merged findings and updated approach.
    """
    base_summary = "\n".join(
        f"- {f.topic}: {f.finding}" for f in base_research.findings
    )

    sops_section = f"\n\nProject Operating Procedures:\n{sops}" if sops else ""

    # Cache the base research (same for all components using this base)
    cache_prefix = f"Existing research findings:\n{base_summary}\n\nExisting approach: {base_research.recommended_approach}"

    prompt = f"""You have existing research findings (see above).

Now focus specifically on: {supplemental_focus}
{sops_section}

What ADDITIONAL findings are needed beyond the existing research?
Focus only on new, supplemental insights — do not repeat the existing findings.

Produce a concise research report with supplemental findings only."""

    result, in_tok, out_tok = await agent.assess_cached(
        ResearchReport, prompt, RESEARCH_SYSTEM,
        cache_prefix=cache_prefix,
    )

    # Merge: base findings + supplemental findings
    merged_findings = list(base_research.findings) + list(result.findings)
    result.findings = merged_findings
    # Keep the supplemental approach if it adds value, otherwise use base
    if not result.recommended_approach or result.recommended_approach == base_research.recommended_approach:
        result.recommended_approach = base_research.recommended_approach

    logger.info(
        "Research augmented: %d base + %d supplemental findings (%d tokens)",
        len(base_research.findings), len(result.findings) - len(base_research.findings),
        in_tok + out_tok,
    )
    return result
