"""Research-first protocol — shared by all agents.

Every agent follows 3 phases before producing work:
  1. Research: best practices, patterns, pitfalls
  2. Plan + self-evaluate: efficiency, compliance, risks
  3. Execute: produce actual work product

This module provides the research and plan-evaluation steps.
"""

from __future__ import annotations

import logging

from pact.agents.base import AgentBase
from pact.schemas import PlanEvaluation, ResearchReport

logger = logging.getLogger(__name__)

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
) -> ResearchReport:
    """Phase 1: Research best practices before beginning work.

    Args:
        agent: The LLM agent to use for research.
        task_description: What the agent is about to do.
        role_context: Role-specific research focus (e.g., "type system design").
        sops: Project SOPs to consider.

    Returns:
        ResearchReport with findings and recommended approach.
    """
    sops_section = f"\n\nProject Operating Procedures:\n{sops}" if sops else ""

    prompt = f"""You are about to: {task_description}

Role-specific focus: {role_context}
{sops_section}

Before beginning, research best practices:
1. What are established patterns for this kind of work?
2. What are common pitfalls?
3. What standards, conventions, or idioms should be followed?
4. Are there existing implementations or libraries that should be referenced?
5. What edge cases are frequently missed?

Produce a concise research report."""

    result, in_tok, out_tok = await agent.assess(
        ResearchReport, prompt, RESEARCH_SYSTEM,
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
) -> PlanEvaluation:
    """Phase 2: Plan and self-evaluate before execution.

    Args:
        agent: The LLM agent to use.
        task_description: What the agent is about to do.
        research: Output of the research phase.
        plan_description: Initial plan to evaluate.
        sops: Project SOPs.
        max_revisions: Max times to revise before escalating.

    Returns:
        PlanEvaluation with decision (proceed/revise/escalate).
    """
    sops_section = f"\n\nProject Operating Procedures:\n{sops}" if sops else ""

    research_summary = "\n".join(
        f"- {f.topic}: {f.finding}" for f in research.findings
    )

    for attempt in range(max_revisions + 1):
        prompt = f"""Task: {task_description}

Research findings:
{research_summary}

Recommended approach from research: {research.recommended_approach}
{sops_section}

Current plan (attempt {attempt + 1}):
{plan_description}

Evaluate this plan:
1. Is this the most efficient approach? (efficiency)
2. Does it comply with the best practices identified? (compliance)
3. What risks remain? (risk)
4. Decision: proceed, revise, or escalate?

If revising, explain what to change in revision_notes."""

        result, in_tok, out_tok = await agent.assess(
            PlanEvaluation, prompt, PLAN_SYSTEM,
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
