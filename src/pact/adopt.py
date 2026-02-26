"""Codebase adoption — bring any existing codebase under pact governance.

Bridges the gap between test-gen (which analyzes) and the daemon (which
monitors and fixes). Creates the full project state the daemon expects:
DecompositionTree, contracts, test suites, RunState, implementations.

The existing code IS the spec. Contracts describe what it actually does.
Tests verify it. The daemon then monitors for regressions.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from pact.agents.base import AgentBase
from pact.budget import BudgetTracker
from pact.codebase_analyzer import analyze_codebase
from pact.project import ProjectManager
from pact.schemas import (
    ComponentContract,
    ComponentTask,
    ContractTestSuite,
    DecompositionNode,
    DecompositionTree,
    InterviewResult,
    RunState,
)
from pact.schemas_testgen import (
    CodebaseAnalysis,
    TestGenPlan,
)
from pact.test_gen import (
    plan_test_generation,
    render_security_audit,
    reverse_engineer_contract,
)

logger = logging.getLogger(__name__)


# ── Tree Construction ──────────────────────────────────────────────


def build_decomposition_tree(analysis: CodebaseAnalysis) -> DecompositionTree:
    """Convert a CodebaseAnalysis into a DecompositionTree.

    Each source file with functions becomes a leaf component.
    Packages (directories) become intermediate nodes.
    Root is the project itself.
    """
    nodes: dict[str, DecompositionNode] = {}

    # Root node
    root_id = "root"
    nodes[root_id] = DecompositionNode(
        component_id=root_id,
        name="Project Root",
        description=f"Adopted codebase at {analysis.root_path}",
        depth=0,
        children=[],
    )

    # Track intermediate package nodes
    package_nodes: dict[str, str] = {}  # "src/utils" -> component_id

    for sf in analysis.source_files:
        if not sf.functions:
            continue

        # Derive component_id from file path
        component_id = sf.path.replace("/", "_").replace("\\", "_")
        if component_id.endswith(".py"):
            component_id = component_id[:-3]

        # Derive a clean name
        name = Path(sf.path).stem.replace("_", " ").title()

        # Determine parent: package directory or root
        parent_parts = Path(sf.path).parent.parts
        parent_id = root_id

        if parent_parts and str(Path(sf.path).parent) != ".":
            # Create intermediate package nodes as needed
            for i in range(len(parent_parts)):
                pkg_path = "/".join(parent_parts[: i + 1])
                if pkg_path not in package_nodes:
                    pkg_id = pkg_path.replace("/", "_")
                    pkg_name = parent_parts[i].replace("_", " ").title()
                    nodes[pkg_id] = DecompositionNode(
                        component_id=pkg_id,
                        name=pkg_name,
                        description=f"Package: {pkg_path}",
                        depth=i + 1,
                        parent_id=parent_id if i == 0 else package_nodes.get(
                            "/".join(parent_parts[:i]), root_id
                        ),
                        children=[],
                    )
                    package_nodes[pkg_path] = pkg_id

                    # Add to parent's children
                    actual_parent = nodes[pkg_id].parent_id
                    if actual_parent and actual_parent in nodes:
                        if pkg_id not in nodes[actual_parent].children:
                            nodes[actual_parent].children.append(pkg_id)

                parent_id = package_nodes[pkg_path]

        # Create leaf node for the source file
        depth = len(parent_parts) + 1 if parent_parts and str(Path(sf.path).parent) != "." else 1
        func_names = [f.name for f in sf.functions]
        nodes[component_id] = DecompositionNode(
            component_id=component_id,
            name=name,
            description=f"Module {sf.path}: {', '.join(func_names[:5])}"
            + (f" (+{len(func_names) - 5} more)" if len(func_names) > 5 else ""),
            depth=depth,
            parent_id=parent_id,
            children=[],
        )

        # Add to parent's children
        if parent_id in nodes and component_id not in nodes[parent_id].children:
            nodes[parent_id].children.append(component_id)

    return DecompositionTree(root_id=root_id, nodes=nodes)


# ── Implementation Linking ─────────────────────────────────────────


def link_existing_implementations(
    project: ProjectManager,
    analysis: CodebaseAnalysis,
    tree: DecompositionTree,
) -> None:
    """Link existing source files as implementations.

    Instead of generating code, we symlink or copy the existing source
    into the implementation directories the daemon expects.
    """
    root = Path(analysis.root_path)

    for sf in analysis.source_files:
        if not sf.functions:
            continue

        component_id = sf.path.replace("/", "_").replace("\\", "_")
        if component_id.endswith(".py"):
            component_id = component_id[:-3]

        if component_id not in tree.nodes:
            continue

        # Create impl directory and copy/symlink source
        src_dir = project.impl_src_dir(component_id)
        source_path = root / sf.path
        if source_path.exists():
            dest_path = src_dir / source_path.name
            shutil.copy2(source_path, dest_path)

        # Save metadata
        project.save_impl_metadata(component_id, {
            "adopted": True,
            "source_path": sf.path,
            "functions": len(sf.functions),
            "adopted_at": datetime.now().isoformat(),
        })


# ── Adoption Pipeline ─────────────────────────────────────────────


async def adopt_codebase(
    project_path: str | Path,
    language: str = "python",
    budget: float = 10.0,
    model: str = "claude-sonnet-4-5-20250929",
    backend: str = "anthropic",
    complexity_threshold: int = 5,
    dry_run: bool = False,
) -> AdoptionResult:
    """Adopt an existing codebase into a full pact project.

    Steps:
    1. Mechanical analysis (reuses test-gen)
    2. Build decomposition tree from modules
    3. Initialize project state
    4. Reverse-engineer contracts from source (LLM)
    5. Generate tests from contracts (LLM)
    6. Link existing code as implementations
    7. Create RunState ready for daemon

    Args:
        project_path: Root of the codebase to adopt.
        language: Programming language.
        budget: LLM budget in dollars.
        model: LLM model for generation.
        backend: LLM backend.
        complexity_threshold: Priority threshold for complexity.
        dry_run: If True, only do mechanical phases (no LLM).

    Returns:
        AdoptionResult with summary.
    """
    project_path = Path(project_path).resolve()

    # Phase 1: Mechanical analysis
    logger.info("Analyzing codebase at %s...", project_path)
    analysis = analyze_codebase(project_path, language)
    logger.info(
        "Found %d source files, %d functions, %d test files",
        analysis.total_source_files, analysis.total_functions, analysis.total_test_files,
    )

    # Phase 2: Build decomposition tree
    tree = build_decomposition_tree(analysis)
    # Exclude root-only tree (empty project) from leaf count
    leaves = tree.leaves()
    leaf_count = len(leaves) if any(n.component_id != "root" for n in leaves) else 0
    logger.info("Built decomposition tree: %d nodes, %d leaves", len(tree.nodes), leaf_count)

    # Phase 3: Initialize project
    project = ProjectManager(project_path)
    project.init(budget=budget)

    # Write task.md describing the adoption
    project.task_path.write_text(
        f"# Adopted Codebase\n\n"
        f"This project was adopted from an existing codebase.\n\n"
        f"## Analysis\n"
        f"- Source files: {analysis.total_source_files}\n"
        f"- Functions: {analysis.total_functions}\n"
        f"- Test files: {analysis.total_test_files}\n"
        f"- Coverage: {analysis.coverage.coverage_ratio:.0%}\n"
        f"- Security findings: {len(analysis.security.findings)}\n"
    )

    # Save tree and interview (auto-approved for adoption)
    project.save_tree(tree)
    project.save_interview(InterviewResult(
        risks=[f"Adopted codebase with {len(analysis.security.findings)} security findings"],
        assumptions=["Existing code is the specification", "Contracts describe actual behavior"],
        approved=True,
    ))

    # Build plan for prioritization
    plan = plan_test_generation(analysis, complexity_threshold, skip_covered=False)

    # Save security audit
    audit_dir = project_path / ".pact" / "test-gen"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "security_audit.md").write_text(render_security_audit(analysis.security))
    (audit_dir / "analysis.json").write_text(analysis.model_dump_json(indent=2))

    result = AdoptionResult(
        components=leaf_count,
        total_functions=analysis.total_functions,
        coverage_before=analysis.coverage.coverage_ratio,
        security_findings=len(analysis.security.findings),
    )

    if dry_run:
        result.dry_run = True
        # Still create state so user can inspect
        state = _create_adoption_state(project, tree, "interview")
        state.pause("Dry run — adoption not finalized")
        project.save_state(state)
        project.append_audit("adopt_dry_run", f"{leaf_count} components analyzed")
        return result

    # Phase 4-5: LLM-powered contract + test generation
    budget_tracker = BudgetTracker(per_project_cap=budget)
    budget_tracker.set_model_pricing(model)
    budget_tracker.start_project()

    agent = AgentBase(budget_tracker, model=model, backend=backend)

    # Group source files by component for processing
    try:
        for sf in analysis.source_files:
            if not sf.functions:
                continue

            if budget_tracker.is_exceeded():
                logger.warning("Budget exceeded, stopping contract generation")
                break

            component_id = sf.path.replace("/", "_").replace("\\", "_")
            if component_id.endswith(".py"):
                component_id = component_id[:-3]

            if component_id not in tree.nodes:
                continue

            # Read source
            source_path = project_path / sf.path
            if not source_path.exists():
                continue
            source_code = source_path.read_text(encoding="utf-8", errors="replace")

            module_name = sf.path.replace("/", ".").replace("\\", ".")
            if module_name.endswith(".py"):
                module_name = module_name[:-3]
            function_names = [f.name for f in sf.functions]

            # Phase 4: Reverse-engineer contract
            logger.info("Contracting %s (%d functions)...", module_name, len(function_names))
            contract = await reverse_engineer_contract(
                agent, source_code, module_name, function_names,
            )
            # Ensure component_id matches tree
            contract.component_id = component_id
            project.save_contract(contract)
            result.contracts_generated += 1
            project.append_audit(
                "adopt_contract", f"Contract for {component_id}",
                component_id=component_id,
            )

            if budget_tracker.is_exceeded():
                break

            # Phase 5: Generate tests
            logger.info("Testing %s...", module_name)
            from pact.agents.test_author import author_tests
            suite, _research, _plan = await author_tests(
                agent, contract, language=language,
            )
            # Fix import paths for the actual module location
            if suite.generated_code:
                old_import = f"from src.{contract.component_id} import"
                new_import = f"from {module_name} import"
                suite.generated_code = suite.generated_code.replace(old_import, new_import)
            suite.component_id = component_id
            project.save_test_suite(suite)
            result.tests_generated += 1
            project.append_audit(
                "adopt_tests", f"Tests for {component_id}: {len(suite.test_cases)} cases",
                component_id=component_id,
            )

    finally:
        await agent.close()
        result.total_cost_usd = budget_tracker.project_spend

    # Phase 6: Link existing code as implementations
    link_existing_implementations(project, analysis, tree)

    # Phase 7: Create RunState
    state = _create_adoption_state(project, tree, "implement")
    project.save_state(state)
    project.append_audit(
        "adopt_complete",
        f"{result.contracts_generated} contracts, {result.tests_generated} tests, ${result.total_cost_usd:.4f}",
    )

    return result


def _create_adoption_state(
    project: ProjectManager,
    tree: DecompositionTree,
    target_phase: str,
) -> RunState:
    """Create a RunState for an adopted project.

    Sets the phase past interview/decompose since those are handled
    by the adoption process. Components are marked as 'contracted'
    (have contracts but not yet validated).
    """
    component_tasks = []
    for node_id, node in tree.nodes.items():
        if not node.children:  # Leaf nodes only
            component_tasks.append(ComponentTask(
                component_id=node_id,
                status="pending",
            ))

    return RunState(
        id=uuid4().hex[:12],
        project_dir=str(project.project_dir),
        status="active",
        phase=target_phase,
        created_at=datetime.now().isoformat(),
        component_tasks=component_tasks,
    )


# ── Result Model ───────────────────────────────────────────────────


class AdoptionResult:
    """Summary of a codebase adoption."""

    def __init__(
        self,
        components: int = 0,
        total_functions: int = 0,
        coverage_before: float = 0.0,
        security_findings: int = 0,
    ):
        self.components = components
        self.total_functions = total_functions
        self.coverage_before = coverage_before
        self.security_findings = security_findings
        self.contracts_generated = 0
        self.tests_generated = 0
        self.total_cost_usd = 0.0
        self.dry_run = False

    def summary(self) -> str:
        lines = []
        if self.dry_run:
            lines.append("=== Adoption Dry Run ===")
        else:
            lines.append("=== Adoption Complete ===")

        lines.append(f"Components: {self.components}")
        lines.append(f"Functions: {self.total_functions}")
        lines.append(f"Existing coverage: {self.coverage_before:.0%}")
        lines.append(f"Security findings: {self.security_findings}")

        if not self.dry_run:
            lines.append(f"Contracts generated: {self.contracts_generated}")
            lines.append(f"Tests generated: {self.tests_generated}")
            lines.append(f"Cost: ${self.total_cost_usd:.4f}")
            lines.append("")
            lines.append("Next steps:")
            lines.append("  pact status .          # Review project state")
            lines.append("  pact tree .            # View component tree")
            lines.append("  pact daemon .          # Start monitoring daemon")

        return "\n".join(lines)
