"""Decomposer — Task -> Contracts workflow.

Orchestrates the full decomposition pipeline:
1. Interview: identify risks, ambiguities, questions
2. Decompose: task -> component tree
3. Generate type registry: canonical shared types across all components
4. Generate contracts for each component (referencing the registry)
5. Generate tests for each contract
6. Validate all contracts (mechanical gate)
"""

from __future__ import annotations

import logging
from datetime import datetime

from pydantic import BaseModel

from pact.agents.base import AgentBase
from pact.agents.contract_author import author_contract
from pact.agents.test_author import (
    author_goodhart_tests,
    author_tests,
    generate_emission_compliance_test,
)
from pact.contracts import validate_all_contracts, validate_decomposition_coverage
from pact.project import ProjectManager
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionNode,
    DecompositionTree,
    DesignDocument,
    EngineeringDecision,
    GateResult,
    InterviewResult,
)

logger = logging.getLogger(__name__)

# ── Register Establishment ────────────────────────────────────────────

REGISTER_SYSTEM = """Determine the processing register for this task — the cognitive
mode that should govern all subsequent work. Return a concise register descriptor.

Examples of processing registers:
- rigorous-analytical: formal verification, exhaustive edge cases, defensive coding
- exploratory-generative: creative problem-solving, rapid prototyping, novel approaches
- systematic-verification: methodical coverage, compliance-focused, checklist-driven
- pragmatic-implementation: practical trade-offs, ship-focused, MVP-first

Consider the task domain, constraints, and stakes. One phrase, no explanation."""


class _RegisterResponse(BaseModel):
    """LLM output for register establishment."""
    processing_register: str


async def run_register_establishment(
    agent: AgentBase,
    task: str,
    sops: str = "",
) -> str:
    """Establish processing register before domain content.

    Research (Papers 36-39) shows register is the representational hub
    that domain anchors to. Setting it first in ~15 tokens captures
    98.8% of coordination benefit. Reset clears residual register,
    this call establishes the new one.
    """
    prompt = f"""What processing register should govern this task?

Task summary (first 200 chars): {task[:200]}

SOPs context: {sops[:200] if sops else 'None'}

Return a concise register descriptor (e.g. "rigorous-analytical")."""

    result, _, _ = await agent.assess(_RegisterResponse, prompt, REGISTER_SYSTEM)
    register = result.processing_register.strip().lower()
    logger.info("Processing register established: %s", register)
    return register


# ── Interview ────────────────────────────────────────────────────────

INTERVIEW_SYSTEM = """You are starting fresh on this review with no prior context.

You are a cynical senior architect reviewing a task specification.
Find risks, ambiguities, and missing decisions BEFORE any work begins.
Focus on issues that would cause implementation failures."""


async def run_interview(
    agent: AgentBase,
    task: str,
    sops: str = "",
    processing_register: str = "",
) -> InterviewResult:
    """Run the interview phase — identify risks and ask clarifying questions.

    If processing_register is provided (from config or prior establishment),
    the interview operates within that register. If empty, register is
    established first via run_register_establishment().
    """
    # Establish register if not already set
    if not processing_register:
        processing_register = await run_register_establishment(agent, task, sops)

    register_context = (
        f"\nProcessing register: {processing_register}\n"
        f"Conduct this review in {processing_register} mode.\n"
    )

    prompt = f"""Review this task specification and identify issues:
{register_context}
Task:
{task}

SOPs:
{sops or 'None provided'}

Identify:
1. Risks: What could go wrong during implementation?
2. Ambiguities: What aspects are unclear or underspecified?
3. Questions: Specific questions for the product owner
4. Assumptions: What assumptions will you make if not clarified?
5. Acceptance criteria: What specific, testable conditions must be true
   for this task to be considered DONE? These should be concrete and
   verifiable — not vague qualities but observable outcomes. If the task
   spec already states acceptance criteria, extract them. If it doesn't,
   propose what "done" should mean based on the task description.

Be specific and actionable. Focus on issues that would cause
different engineers to implement incompatible solutions."""

    result, _, _ = await agent.assess(InterviewResult, prompt, INTERVIEW_SYSTEM)
    result.processing_register = processing_register
    return result


# ── Decomposition ────────────────────────────────────────────────────

DECOMPOSE_SYSTEM = """You are starting fresh on this decomposition with no prior context.

You are a software architect deciding how to build a task.

Prefer direct implementation (is_trivial=true) when the task can be one
well-structured module (<500 LOC) with shared data model and state.

Decompose only when there are genuinely independent subsystems with clean
interfaces. Each component is a black box with explicit dependencies.
Keep it shallow — prefer wider over deeper."""


class DecompositionResult:
    """Full decomposition result with tree and decisions."""

    def __init__(
        self,
        tree: DecompositionTree,
        decisions: list[EngineeringDecision],
    ) -> None:
        self.tree = tree
        self.decisions = decisions


async def run_decomposition(
    agent: AgentBase,
    task: str,
    interview: InterviewResult | None = None,
    sops: str = "",
    pitch_context: str = "",
    build_mode: str = "auto",
) -> DecompositionResult:
    """Decompose a task into a component tree.

    Args:
        pitch_context: Optional shaping pitch context (from Shape Up phase).
            Injected into the decomposition prompt to guide component boundaries.
        build_mode: "unary" skips LLM and returns single component,
            "hierarchy" always decomposes, "auto" lets LLM decide.
    """
    from pydantic import BaseModel

    # Unary mode: skip LLM, return single component with full task
    if build_mode == "unary":
        root_id = "root"
        node = DecompositionNode(
            component_id=root_id,
            name="Main",
            description=task,
            depth=0,
        )
        tree = DecompositionTree(root_id=root_id, nodes={root_id: node})
        return DecompositionResult(tree=tree, decisions=[])

    class DecomposeResponse(BaseModel):
        """Decomposition output."""
        components: list[dict] = []
        decisions: list[dict] = []
        is_trivial: bool = False

    interview_context = ""
    if interview:
        answers = "\n".join(
            f"  Q: {q}\n  A: {interview.user_answers.get(q, 'No answer')}"
            for q in interview.questions
        )
        assumptions = "\n".join(f"  - {a}" for a in interview.assumptions)
        acceptance = "\n".join(f"  - {a}" for a in interview.acceptance_criteria) if interview.acceptance_criteria else ""
        interview_context = (
            f"\nInterview results:\n"
            f"Answers:\n{answers}\n"
            f"Assumptions:\n{assumptions}"
        )
        if acceptance:
            interview_context += f"\nAcceptance criteria (definition of done):\n{acceptance}"

    shaping_section = ""
    if pitch_context:
        shaping_section = f"\n## SHAPING CONTEXT\n{pitch_context}\n"

    prompt = f"""Decompose this task into components:

Task:
{task}

SOPs:
{sops or 'None provided'}
{interview_context}
{shaping_section}

For each component provide:
- id: short snake_case identifier
- name: human-readable name
- description: what this component does
- dependencies: list of other component IDs it depends on
- children: list of sub-component IDs (empty for leaf nodes)

Also provide engineering decisions for any ambiguities resolved.

If the task can be implemented as a single well-structured module
(even if it has multiple functions and types), set is_trivial=true
and provide a single component with the full description.

Complexity hint: task is ~{len(task.split())} words.{' This appears to be a single-concern task.' if len(task.split()) < 200 else ''}"""

    response, _, _ = await agent.assess(DecomposeResponse, prompt, DECOMPOSE_SYSTEM)

    # Build the tree
    nodes: dict[str, DecompositionNode] = {}
    root_id = ""

    if response.is_trivial or len(response.components) <= 1:
        # Trivial task — single component
        comp = response.components[0] if response.components else {
            "id": "root",
            "name": "Main",
            "description": task[:200],
        }
        root_id = comp.get("id", "root")
        nodes[root_id] = DecompositionNode(
            component_id=root_id,
            name=comp.get("name", "Main"),
            description=comp.get("description", task[:200]),
            depth=0,
        )
    else:
        # Multi-component decomposition
        # Find or create root
        all_ids = {c.get("id", "") for c in response.components}
        child_ids = set()
        for c in response.components:
            child_ids.update(c.get("children", []))

        root_candidates = all_ids - child_ids

        # Build nodes first
        for comp in response.components:
            cid = comp.get("id", "")
            if not cid:
                continue
            nodes[cid] = DecompositionNode(
                component_id=cid,
                name=comp.get("name", cid),
                description=comp.get("description", ""),
                children=comp.get("children", []),
            )

        if len(root_candidates) == 1:
            root_id = next(iter(root_candidates))
        elif len(root_candidates) > 1:
            # Multiple top-level groups — create synthetic root
            root_id = "root"
            top_level = sorted(root_candidates - {""})
            nodes[root_id] = DecompositionNode(
                component_id=root_id,
                name="Root",
                description=task[:200],
                depth=0,
                children=list(top_level),
            )
            logger.info(
                "Created synthetic root for %d top-level groups: %s",
                len(top_level), ", ".join(top_level),
            )
        else:
            root_id = response.components[0].get("id", "root")

        # Assign depths and parent_ids from root downward
        def assign_depth(nid: str, depth: int, parent: str) -> None:
            node = nodes.get(nid)
            if not node:
                return
            node.depth = depth
            node.parent_id = parent
            for child_id in node.children:
                assign_depth(child_id, depth + 1, nid)

        assign_depth(root_id, 0, "")

        # Adopt any remaining orphans (nodes not reachable from root)
        reachable: set[str] = set()
        def collect_reachable(nid: str) -> None:
            reachable.add(nid)
            node = nodes.get(nid)
            if node:
                for child_id in node.children:
                    collect_reachable(child_id)
        collect_reachable(root_id)

        orphans = [nid for nid in nodes if nid not in reachable]
        if orphans:
            logger.warning("Adopting %d orphaned nodes under root: %s", len(orphans), ", ".join(orphans))
            root_node = nodes[root_id]
            for nid in orphans:
                if nid not in root_node.children:
                    root_node.children.append(nid)
                nodes[nid].parent_id = root_id
                nodes[nid].depth = 1

        # Ensure root has children if it has none
        if root_id in nodes and not nodes[root_id].children:
            leaf_ids = [cid for cid in nodes if cid != root_id]
            nodes[root_id].children = leaf_ids

    tree = DecompositionTree(root_id=root_id, nodes=nodes)

    decisions = [
        EngineeringDecision(
            ambiguity=d.get("ambiguity", ""),
            decision=d.get("decision", ""),
            rationale=d.get("rationale", ""),
        )
        for d in response.decisions
    ]

    return DecompositionResult(tree=tree, decisions=decisions)


# ── Type Registry ────────────────────────────────────────────────────


async def _generate_type_registry(
    agent: AgentBase,
    task: str,
    tree: DecompositionTree,
) -> "TypeRegistry":
    """Generate canonical type definitions for all shared types.

    Runs after decomposition but before contract authoring.  Asks the LLM
    to define every type that will be referenced by two or more components,
    with exact field names, types, and semantics.  Each contract author
    then receives this registry and must use these definitions verbatim.
    """
    from pact.schemas import TypeRegistry, TypeSpec

    # Build component summary for the LLM
    comp_lines = []
    for cid, node in tree.nodes.items():
        comp_lines.append(f"- {cid} ({node.name}): {node.description}")
    components_text = "\n".join(comp_lines)

    prompt = f"""You are defining the canonical type registry for a software project.

Task: {task}

Components:
{components_text}

Define ALL types that will be shared across two or more components.
For each type, specify:
- name: exact PascalCase name
- kind: one of (primitive, struct, enum, list, optional, union)
- owner_component: which component is the authoritative source
- For structs: every field with name, type_ref, and whether it's optional
- For enums: every variant value
- description: one-line purpose

CRITICAL: These definitions are the SINGLE SOURCE OF TRUTH.
Every component contract MUST use these exact type names, exact field
names, and exact field types. No component may add, remove, or rename
fields on a shared type.

Types that are used by only one component should NOT be listed here —
they belong in that component's contract only.

Return a TypeRegistry with a list of TypeSpec objects."""

    registry, _, _ = await agent.assess_cached(
        TypeRegistry, prompt,
        "You are a type system architect. Define precise, complete shared types.",
    )

    logger.info("Type registry: %d shared types", len(registry.types))
    return registry


def _enforce_type_registry(
    contract: ComponentContract,
    registry: "TypeRegistry",
) -> ComponentContract:
    """Replace any contract type that matches a registry type by name.

    This is the deterministic correction step.  The LLM generated the
    contract (drunken walk #1).  Now we mechanically overwrite any shared
    type with the canonical registry version (deterministic checkpoint).

    Types in the contract that don't match any registry type by name
    are left untouched — they're component-specific.
    """
    registry_by_name = {t.name: t for t in registry.types}
    if not registry_by_name:
        return contract

    corrected_types = []
    corrections = 0
    for ct in contract.types:
        if ct.name in registry_by_name:
            canonical = registry_by_name[ct.name]
            # Check if the LLM's version differs from the registry
            if ct.fields != canonical.fields or ct.variants != canonical.variants:
                corrections += 1
                logger.info(
                    "Type registry enforcement: %s.%s — replaced LLM version with canonical",
                    contract.component_id, ct.name,
                )
            # Use the canonical version but keep owner_component from registry
            corrected_types.append(canonical)
        else:
            corrected_types.append(ct)

    # Also inject any registry types the LLM forgot to include
    contract_type_names = {ct.name for ct in corrected_types}
    for reg_type in registry.types:
        if reg_type.name not in contract_type_names:
            # Only inject if this component is the owner or uses this type
            # in function signatures
            func_type_refs = set()
            for func in contract.functions:
                for inp in func.inputs:
                    func_type_refs.add(inp.type_ref)
                func_type_refs.add(func.output_type)
            if reg_type.name in func_type_refs:
                corrected_types.append(reg_type)
                corrections += 1
                logger.info(
                    "Type registry enforcement: %s.%s — injected missing shared type",
                    contract.component_id, reg_type.name,
                )

    if corrections > 0:
        logger.info(
            "Type registry: %d corrections applied to %s",
            corrections, contract.component_id,
        )

    # Return a new contract with corrected types
    return contract.model_copy(update={"types": corrected_types})


# ── Full Pipeline ────────────────────────────────────────────────────


async def decompose_and_contract(
    agent: AgentBase,
    project: ProjectManager,
    sops: str = "",
    max_plan_revisions: int = 2,
    build_mode: str = "auto",
    processing_register: str = "",
    package_namespace: str = "",
) -> GateResult:
    """Run the full decomposition -> contract -> test -> validate pipeline.

    Saves all artifacts to the project directory.

    Returns:
        GateResult from contract validation.
    """
    task = project.load_task()

    # Load or run interview — register comes from interview or config override
    interview = project.load_interview()
    if not processing_register and interview:
        processing_register = interview.processing_register

    # Load existing tree or run decomposition
    existing_tree = project.load_tree()
    if existing_tree and len(existing_tree.nodes) > 1:
        # Resume from existing decomposition
        decomp_tree = existing_tree
        logger.info("Resuming existing decomposition (%d components)", len(existing_tree.nodes))
        decisions: list[EngineeringDecision] = []
    else:
        # Load shaping pitch context if available
        pitch = project.load_pitch()
        pitch_ctx = ""
        if pitch:
            try:
                from pact.pitch_utils import build_pitch_context_for_handoff
                pitch_ctx = build_pitch_context_for_handoff(pitch)
            except Exception:
                pass

        # Run fresh decomposition
        decomp = await run_decomposition(agent, task, interview, sops, pitch_context=pitch_ctx, build_mode=build_mode)
        decomp_tree = decomp.tree
        decisions = decomp.decisions
        project.save_tree(decomp_tree)
        project.save_decisions([d.model_dump() for d in decisions])
        project.append_audit("decomposition", f"{len(decomp_tree.nodes)} components")

        # Early validation: check decomposition structure before spending
        # LLM calls on contract/test generation
        coverage_warnings = validate_decomposition_coverage(task, decomp_tree)
        for w in coverage_warnings:
            logger.warning("Decomposition: %s", w)
        if coverage_warnings:
            project.append_audit(
                "decomposition_validation",
                f"{len(coverage_warnings)} warnings: {'; '.join(coverage_warnings[:3])}",
            )

    # Generate or load type registry — canonical shared types
    from pact.schemas import TypeRegistry
    type_registry = project.load_type_registry()
    if type_registry is None:
        type_registry = await _generate_type_registry(agent, task, decomp_tree)
        project.save_type_registry(type_registry)
        project.append_audit(
            "type_registry",
            f"{len(type_registry.types)} shared types defined",
        )

    # Generate contracts (leaves first), skipping already-completed ones
    order = decomp_tree.topological_order()
    contracts: dict[str, ComponentContract] = project.load_all_contracts()
    test_suites: dict[str, ContractTestSuite] = project.load_all_test_suites()

    for component_id in order:
        # Skip if contract and tests already exist
        if component_id in contracts and component_id in test_suites:
            logger.info("Skipping %s — contract and tests already exist", component_id)
            continue

        node = decomp_tree.nodes[component_id]

        # Gather dependency contracts
        dep_contracts = {}
        if node.contract and node.contract.dependencies:
            for dep_id in node.contract.dependencies:
                if dep_id in contracts:
                    dep_contracts[dep_id] = contracts[dep_id]

        # Also check tree-level dependencies
        for child_id in node.children:
            if child_id in contracts:
                dep_contracts[child_id] = contracts[child_id]

        # Parent description
        parent = decomp_tree.parent_of(component_id)
        parent_desc = parent.description if parent else ""

        # Author contract (skip if already exists, only missing tests)
        if component_id not in contracts:
            contract, research, plan = await author_contract(
                agent,
                component_id=component_id,
                component_name=node.name,
                component_description=node.description,
                parent_description=parent_desc,
                dependency_contracts=dep_contracts,
                engineering_decisions=[d.model_dump() for d in decisions],
                sops=sops,
                max_plan_revisions=max_plan_revisions,
                processing_register=processing_register,
                type_registry=type_registry,
            )

            # Mechanical correction: replace shared types with registry versions.
            # The LLM may have paraphrased field names or added/removed fields.
            # The registry is the source of truth — no stochastic variance.
            if type_registry and type_registry.types:
                contract = _enforce_type_registry(contract, type_registry)

            contracts[component_id] = contract
            project.save_contract(contract)
            project.save_research(component_id, "contract", research)
            project.append_audit(
                "contract",
                f"{component_id}: {len(contract.functions)} functions",
            )

            # Update node status
            node.implementation_status = "contracted"
            node.contract = contract
        else:
            contract = contracts[component_id]
            logger.info("Skipping contract for %s — already exists", component_id)

        # Author tests (skip if already exist)
        if component_id not in test_suites:
            suite, test_research, test_plan = await author_tests(
                agent, contract,
                dependency_contracts=dep_contracts,
                sops=sops,
                max_plan_revisions=max_plan_revisions,
                language=project.language,
                package_namespace=package_namespace,
            )
            test_suites[component_id] = suite
            project.save_test_suite(suite)
            project.append_audit(
                "tests",
                f"{component_id}: {len(suite.test_cases)} cases",
            )

            # Generate Goodhart (hidden) tests — skip if already exist
            if not project.load_goodhart_suite(component_id):
                goodhart_suite = await author_goodhart_tests(
                    agent, contract, suite,
                    dependency_contracts=dep_contracts,
                    language=project.language,
                    package_namespace=package_namespace,
                )
                project.save_goodhart_suite(goodhart_suite)
                project.append_audit(
                    "goodhart_tests",
                    f"{component_id}: {len(goodhart_suite.test_cases)} hidden cases",
                )

            # Generate emission compliance test (mechanical, no LLM)
            emission_code = generate_emission_compliance_test(
                contract, language=project.language,
            )
            project.save_emission_test(component_id, emission_code)
            project.append_audit(
                "emission_tests",
                f"{component_id}: emission compliance test generated",
            )
        else:
            logger.info("Skipping tests for %s — already exist", component_id)

    # Save updated tree
    project.save_tree(decomp_tree)

    # Validate (mechanical gate)
    gate = validate_all_contracts(decomp_tree, contracts, test_suites)
    project.append_audit(
        "validation",
        f"{'PASSED' if gate.passed else 'FAILED'}: {gate.reason}",
    )

    # Update design document
    doc = project.load_design_doc() or DesignDocument(
        project_id=project.project_dir.name,
        title=f"Design: {project.project_dir.name}",
    )
    doc.decomposition_tree = decomp_tree
    doc.engineering_decisions = decisions
    project.save_design_doc(doc)

    # Write markdown design doc
    from pact.design_doc import render_design_doc
    project.design_path.write_text(render_design_doc(doc))

    return gate
