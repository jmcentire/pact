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

DECOMPOSE_SYSTEM = """You are a software architect decomposing a task into components.
Each component is a black box with clear inputs, outputs, and responsibilities.

Key principles:
- 2-7 components per decomposition level
- Each component has a single, clear responsibility
- Dependencies between components are explicit
- Leaf components are small enough for one agent to implement
- Trivial tasks (depth=0) have the task AS the component"""


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
) -> DecompositionResult:
    """Decompose a task into a component tree."""
    from pydantic import BaseModel

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

    prompt = f"""Decompose this task into components:

Task:
{task}

SOPs:
{sops or 'None provided'}
{interview_context}

For each component provide:
- id: short snake_case identifier
- name: human-readable name
- description: what this component does
- dependencies: list of other component IDs it depends on
- children: list of sub-component IDs (empty for leaf nodes)

Also provide engineering decisions for any ambiguities resolved.

If the task is trivial (single function, no decomposition needed),
set is_trivial=true and provide a single component."""

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
        if root_candidates:
            root_id = next(iter(root_candidates))
        else:
            root_id = response.components[0].get("id", "root")

        for comp in response.components:
            cid = comp.get("id", "")
            if not cid:
                continue
            nodes[cid] = DecompositionNode(
                component_id=cid,
                name=comp.get("name", cid),
                description=comp.get("description", ""),
                depth=1 if cid != root_id else 0,
                parent_id=root_id if cid != root_id else "",
                children=comp.get("children", []),
            )

        # Ensure root has children listed
        if root_id in nodes:
            leaf_ids = [
                cid for cid in nodes
                if cid != root_id and not nodes[cid].children
            ]
            if not nodes[root_id].children:
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
        # Run fresh decomposition
        decomp = await run_decomposition(agent, task, interview, sops)
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
