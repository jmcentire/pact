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

CONTRACT_SYSTEM = """You are starting fresh on this contract with no prior context.

You are a contract author defining precise, machine-checkable interface contracts.
Types must be complete, error cases exhaustive, pre/postconditions verifiable,
dependencies explicit. Functions with side effects declare structured_side_effects.
Include performance_budget for performance-sensitive functions.

Where reasonable, define canonical types with validators rather than passing raw
primitives. A field like `email: str` should be a validated EmailAddress type; an
`amount: float` should carry range/precision constraints. Use ValidatorSpec to
express domain rules (regex, range, length, custom) so implementations can enforce
them and tests can verify rejection of invalid data. Not every field needs a
validator — use judgment about which fields carry domain semantics worth encoding.

Every contract MUST include:
- data_access: declare reads/writes classification tiers (e.g. PUBLIC, PII, INTERNAL),
  a specific rationale explaining what data is accessed and why (not vague phrases like
  "handles data"), and side_effects listing each external interaction with its
  classification and affected fields.
- authority: declare data domain patterns this component owns (empty list if none).
  If domains is non-empty, rationale is required explaining why this component is
  authoritative for those domains."""


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
    processing_register: str = "",
    type_registry: "TypeRegistry | None" = None,
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

    register_context = ""
    if processing_register:
        register_context = f"\nProcessing register: {processing_register}\n"

    registry_context = ""
    if type_registry and type_registry.types:
        registry_text = type_registry.render_for_prompt()
        registry_context = (
            f"\n{registry_text}\n\n"
            "IMPORTANT: For any type listed in the registry above, you MUST use "
            "the EXACT same name, fields, and field types. Do NOT redefine these "
            "types with different fields. If this component needs additional "
            "component-specific types, define them separately.\n"
        )

    task_desc = (
        f"Define the interface contract for component '{component_name}' "
        f"(id: {component_id}).\n"
        f"{register_context}"
        f"Description: {component_description}\n"
        f"Parent context: {parent_description or 'root component'}\n"
        f"{dep_summary}{decisions_summary}{registry_context}"
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

    # Build cache prefix from static context
    cache_parts = []
    if sops:
        cache_parts.append(f"Project Operating Procedures:\n{sops}")
    if dep_stubs:
        cache_parts.append(dep_stubs)
    if decisions_summary:
        cache_parts.append(decisions_summary)
    cache_prefix = "\n\n".join(cache_parts)

    # Dynamic prompt (component-specific)
    prompt = f"""Generate a complete ComponentContract for:

Component: {component_name} (id: {component_id})
Description: {component_description}
Parent context: {parent_description or 'root component'}

Research recommended: {research.recommended_approach}

Plan: {plan.plan_summary}

Dependencies: {dep_ids}

Requirements:
- component_id must be "{component_id}"
- name must be "{component_name}"
- Define all types needed (structs, enums, etc.)
- Define all functions with complete input/output types
- Include error cases for each function
- Add preconditions and postconditions where meaningful
- List all dependencies by component_id
- All type references must resolve to types defined in this contract or primitives (str, int, float, bool, None, bytes, dict, list, any)
- Declare structured_side_effects for each function (use kind='none' for pure functions)
- Set performance_budget on functions with latency or memory constraints
- data_access: set reads/writes with classification tiers, provide a specific rationale
  (not vague phrases like "handles data" — describe the exact data and purpose),
  and list side_effects with type, classification, fields, and rationale
- authority: set domains this component owns (empty list if non-authoritative),
  with rationale if domains is non-empty"""

    contract, in_tok, out_tok = await agent.assess_cached(
        ComponentContract, prompt, CONTRACT_SYSTEM, cache_prefix=cache_prefix,
    )

    # Ensure required fields are set correctly
    contract.component_id = component_id
    contract.name = component_name
    if processing_register:
        contract.processing_register = processing_register
    if dep_ids:
        contract.dependencies = dep_ids

    logger.info(
        "Contract authored for %s: %d types, %d functions (%d tokens)",
        component_id, len(contract.types), len(contract.functions),
        in_tok + out_tok,
    )

    # Quality audit — non-blocking warnings for vague language
    from pact.quality import audit_contract_specificity
    quality_warnings = audit_contract_specificity(contract)
    for w in quality_warnings:
        logger.warning("Quality: %s", w)

    return contract, research, plan
