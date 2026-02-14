"""Contract author agent — generates ComponentContract from decomposition.

Follows the Research-First Protocol:
1. Research type system design patterns, error handling, naming conventions
2. Plan the contract structure and self-evaluate
3. Generate the ComponentContract
"""

from __future__ import annotations

import logging

from pact.agents.base import AgentBase
from pact.agents.research import plan_and_evaluate, research_phase
from pact.schemas import (
    ComponentContract,
    PlanEvaluation,
    ResearchReport,
)

logger = logging.getLogger(__name__)

CONTRACT_SYSTEM = """You are a contract author for a component-based architecture.
Your job is to define precise, machine-checkable interface contracts.

Key principles:
- Types must be complete and unambiguous
- Error cases must be exhaustive
- Preconditions and postconditions must be verifiable
- Dependencies must be explicitly declared
- Names must be descriptive and consistent"""


async def author_contract(
    agent: AgentBase,
    component_id: str,
    component_name: str,
    component_description: str,
    parent_description: str = "",
    dependency_contracts: dict[str, ComponentContract] | None = None,
    engineering_decisions: list[dict] | None = None,
    sops: str = "",
    max_plan_revisions: int = 2,
) -> tuple[ComponentContract, ResearchReport, PlanEvaluation]:
    """Generate a ComponentContract following the Research-First Protocol.

    Returns:
        Tuple of (contract, research_report, plan_evaluation).
    """
    # Build context
    dep_summary = ""
    if dependency_contracts:
        dep_parts = []
        for dep_id, dep_contract in dependency_contracts.items():
            funcs = ", ".join(f.name for f in dep_contract.functions)
            types = ", ".join(t.name for t in dep_contract.types)
            dep_parts.append(
                f"  - {dep_id} ({dep_contract.name}): functions=[{funcs}], types=[{types}]"
            )
        dep_summary = "\nDependency contracts:\n" + "\n".join(dep_parts)

    decisions_summary = ""
    if engineering_decisions:
        decisions_summary = "\nEngineering decisions:\n" + "\n".join(
            f"  - {d.get('ambiguity', '')}: {d.get('decision', '')}"
            for d in engineering_decisions
        )

    task_desc = (
        f"Define the interface contract for component '{component_name}' "
        f"(id: {component_id}).\n"
        f"Description: {component_description}\n"
        f"Parent context: {parent_description or 'root component'}\n"
        f"{dep_summary}{decisions_summary}"
    )

    # Phase 1: Research
    research = await research_phase(
        agent, task_desc,
        role_context=(
            "Focus on type system design patterns, error handling conventions, "
            "naming conventions, serialization formats for the domain described."
        ),
        sops=sops,
    )

    # Phase 2: Plan and evaluate
    plan_desc = (
        f"Contract for '{component_name}':\n"
        f"- Approach: {research.recommended_approach}\n"
        f"- Types to define based on component description\n"
        f"- Functions covering the component's responsibilities\n"
        f"- Error cases for each function\n"
        f"- Dependencies: {list((dependency_contracts or {}).keys())}"
    )
    plan = await plan_and_evaluate(
        agent, task_desc, research, plan_desc,
        sops=sops, max_revisions=max_plan_revisions,
    )

    if plan.decision == "escalate":
        logger.warning("Contract author escalated for %s", component_id)
        # Return a minimal contract for escalation
        contract = ComponentContract(
            component_id=component_id,
            name=component_name,
            description=f"ESCALATED: {component_description}",
        )
        return contract, research, plan

    # Phase 3: Generate contract — with dependency stubs as mental model
    dep_ids = list((dependency_contracts or {}).keys())

    # Render dependency interfaces as code-shaped stubs (not raw JSON)
    dep_stubs = ""
    if dependency_contracts:
        from pact.interface_stub import render_stub
        dep_stubs = "\nDependency interface stubs (what you can depend on):\n```python\n"
        for dep_id, dc in dependency_contracts.items():
            dep_stubs += render_stub(dc) + "\n"
        dep_stubs += "```\n"

    prompt = f"""Generate a complete ComponentContract for:

Component: {component_name} (id: {component_id})
Description: {component_description}
Parent context: {parent_description or 'root component'}

Research recommended: {research.recommended_approach}

Plan: {plan.plan_summary}

Dependencies: {dep_ids}
{dep_stubs}
{decisions_summary}

Requirements:
- component_id must be "{component_id}"
- name must be "{component_name}"
- Define all types needed (structs, enums, etc.)
- Define all functions with complete input/output types
- Include error cases for each function
- Add preconditions and postconditions where meaningful
- List all dependencies by component_id
- All type references must resolve to types defined in this contract or primitives (str, int, float, bool, None, bytes, dict, list, any)"""

    contract, in_tok, out_tok = await agent.assess(
        ComponentContract, prompt, CONTRACT_SYSTEM,
    )

    # Ensure required fields are set correctly
    contract.component_id = component_id
    contract.name = component_name
    if dep_ids:
        contract.dependencies = dep_ids

    logger.info(
        "Contract authored for %s: %d types, %d functions (%d tokens)",
        component_id, len(contract.types), len(contract.functions),
        in_tok + out_tok,
    )

    return contract, research, plan
