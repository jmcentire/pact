"""Decomposer — Task -> Contracts workflow.

Orchestrates the full decomposition pipeline:
1. Interview: identify risks, ambiguities, questions
2. Decompose: task -> component tree
3. Generate contracts for each component
4. Generate tests for each contract
5. Validate all contracts (mechanical gate)
"""

from __future__ import annotations

import logging
from datetime import datetime

from pact.agents.base import AgentBase
from pact.agents.contract_author import author_contract
from pact.agents.test_author import author_tests
from pact.contracts import validate_all_contracts
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

# ── Interview ────────────────────────────────────────────────────────

INTERVIEW_SYSTEM = """You are a cynical senior architect reviewing a task specification.
Your job is to find risks, ambiguities, and missing decisions BEFORE
any work begins. Be thorough but practical — focus on issues that would
cause implementation failures, not theoretical concerns."""


async def run_interview(
    agent: AgentBase,
    task: str,
    sops: str = "",
) -> InterviewResult:
    """Run the interview phase — identify risks and ask clarifying questions."""
    prompt = f"""Review this task specification and identify issues:

Task:
{task}

SOPs:
{sops or 'None provided'}

Identify:
1. Risks: What could go wrong during implementation?
2. Ambiguities: What aspects are unclear or underspecified?
3. Questions: Specific questions for the product owner
4. Assumptions: What assumptions will you make if not clarified?

Be specific and actionable. Focus on issues that would cause
different engineers to implement incompatible solutions."""

    result, _, _ = await agent.assess(InterviewResult, prompt, INTERVIEW_SYSTEM)
    return result


# ── Decomposition ────────────────────────────────────────────────────

DECOMPOSE_SYSTEM = """You are a software architect deciding how to build a task.

First, decide: should this task be implemented directly as a single module,
or does it genuinely require multiple independent components?

PREFER DIRECT IMPLEMENTATION (is_trivial=true) when:
- The task can be handled by one well-structured module (<500 LOC)
- All functionality shares the same data model and state
- There are no truly independent subsystems
- A single engineer could implement it in one session

DECOMPOSE ONLY when:
- The task has genuinely independent subsystems (e.g., auth + billing + notifications)
- Components need different expertise or could be developed in parallel
- The interfaces between subsystems are clean and natural

If you decompose:
- Each component is a black box with clear inputs, outputs, and responsibilities
- Dependencies between components are explicit
- Leaf components are small enough for one agent to implement
- Keep it shallow — prefer wider over deeper

If the task should be implemented directly, set is_trivial=true and provide
a single component with the full task description."""


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
        interview_context = (
            f"\nInterview results:\n"
            f"Answers:\n{answers}\n"
            f"Assumptions:\n{assumptions}"
        )

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


# ── Full Pipeline ────────────────────────────────────────────────────


async def decompose_and_contract(
    agent: AgentBase,
    project: ProjectManager,
    sops: str = "",
    max_plan_revisions: int = 2,
    build_mode: str = "auto",
) -> GateResult:
    """Run the full decomposition -> contract -> test -> validate pipeline.

    Saves all artifacts to the project directory.

    Returns:
        GateResult from contract validation.
    """
    task = project.load_task()

    # Load or run interview
    interview = project.load_interview()

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
            )

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
            )
            test_suites[component_id] = suite
            project.save_test_suite(suite)
            project.append_audit(
                "tests",
                f"{component_id}: {len(suite.test_cases)} cases",
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
