"""Task list generation, rendering, and status updates.

Mechanical transformation of decomposition tree + contracts + test suites
into a phased, checkbox-style task list. No LLM calls required.
"""

from __future__ import annotations

from collections import Counter

from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionTree,
    EngineeringDecision,
)
from pact.schemas_tasks import (
    PhaseCheckpoint,
    TaskCategory,
    TaskItem,
    TaskList,
    TaskPhase,
    TaskStatus,
)


def _next_id(counter: list[int]) -> str:
    """Generate next sequential task ID."""
    counter[0] += 1
    return f"T{counter[0]:03d}"


def _find_shared_types(contracts: dict[str, ComponentContract]) -> list[str]:
    """Find type names that appear in 2+ contracts."""
    type_counts: Counter[str] = Counter()
    for contract in contracts.values():
        for t in contract.types:
            type_counts[t.name] += 1
    return sorted(name for name, count in type_counts.items() if count >= 2)


def generate_task_list(
    tree: DecompositionTree,
    contracts: dict[str, ComponentContract],
    test_suites: dict[str, ContractTestSuite],
    project_id: str,
    decisions: list[EngineeringDecision] | None = None,
) -> TaskList:
    """Generate a phased task list from decomposition artifacts.

    Algorithm:
      Phase 1: SETUP (2 tasks)
      Phase 2: FOUNDATIONAL (shared types across contracts)
      Phase 3: COMPONENT (5 tasks per leaf, TDD order)
      Phase 4: INTEGRATION (4 tasks per non-leaf, deepest first)
      Phase 5: POLISH (3 tasks)
    """
    counter = [0]
    tasks: list[TaskItem] = []
    checkpoints: list[PhaseCheckpoint] = []

    # ── Phase 1: SETUP ──────────────────────────────────────────
    tasks.append(TaskItem(
        id=_next_id(counter),
        phase=TaskPhase.setup,
        description="Initialize project directory structure",
        category=TaskCategory.scaffold,
    ))
    tasks.append(TaskItem(
        id=_next_id(counter),
        phase=TaskPhase.setup,
        description="Verify environment and dependencies",
        category=TaskCategory.scaffold,
        depends_on=[tasks[-1].id],
    ))

    # ── Phase 2: FOUNDATIONAL ───────────────────────────────────
    shared_types = _find_shared_types(contracts)
    for type_name in shared_types:
        tasks.append(TaskItem(
            id=_next_id(counter),
            phase=TaskPhase.foundational,
            description=f"Define shared type: {type_name}",
            category=TaskCategory.type_definition,
            parallel=True,
        ))

    # ── Phase 3: COMPONENT ──────────────────────────────────────
    leaf_groups = tree.leaf_parallel_groups()
    # Build set of leaf IDs for parallel marking
    all_leaf_ids = set()
    for group in leaf_groups:
        all_leaf_ids.update(group)

    # Track verify task IDs per component for integration dependencies
    verify_task_ids: dict[str, str] = {}

    for group in leaf_groups:
        for i, component_id in enumerate(group):
            node = tree.nodes.get(component_id)
            if not node:
                continue

            contract = contracts.get(component_id)
            file_path = f"contracts/{component_id}/interface.json" if contract else ""

            # Task 1: Review contract
            review_id = _next_id(counter)
            tasks.append(TaskItem(
                id=review_id,
                phase=TaskPhase.component,
                component_id=component_id,
                description=f"Review contract for {node.name}",
                file_path=file_path,
                category=TaskCategory.contract_review,
                parallel=(i == 0),  # First task in group is parallel marker
            ))

            # Task 2: Set up test harness
            setup_id = _next_id(counter)
            tasks.append(TaskItem(
                id=setup_id,
                phase=TaskPhase.component,
                component_id=component_id,
                description=f"Set up test harness for {node.name}",
                category=TaskCategory.test_setup,
                depends_on=[review_id],
            ))

            # Task 3: Write contract tests
            test_id = _next_id(counter)
            tasks.append(TaskItem(
                id=test_id,
                phase=TaskPhase.component,
                component_id=component_id,
                description=f"Write contract tests for {node.name}",
                category=TaskCategory.test_write,
                depends_on=[setup_id],
            ))

            # Task 4: Implement
            impl_id = _next_id(counter)
            tasks.append(TaskItem(
                id=impl_id,
                phase=TaskPhase.component,
                component_id=component_id,
                description=f"Implement {node.name}",
                file_path=f"implementations/{component_id}/src/",
                category=TaskCategory.implement,
                depends_on=[test_id],
            ))

            # Task 5: Verify
            verify_id = _next_id(counter)
            tasks.append(TaskItem(
                id=verify_id,
                phase=TaskPhase.component,
                component_id=component_id,
                description=f"Run tests and verify {node.name}",
                category=TaskCategory.verify,
                depends_on=[impl_id],
            ))
            verify_task_ids[component_id] = verify_id

    if all_leaf_ids:
        checkpoints.append(PhaseCheckpoint(
            after_phase=TaskPhase.component,
            description="All leaf components verified",
            validation="All leaf contract tests pass",
        ))

    # ── Phase 4: INTEGRATION ────────────────────────────────────
    non_leaf_groups = tree.non_leaf_parallel_groups()
    for group in non_leaf_groups:
        for component_id in group:
            node = tree.nodes.get(component_id)
            if not node:
                continue

            # Task 1: Review integration contract
            review_id = _next_id(counter)
            tasks.append(TaskItem(
                id=review_id,
                phase=TaskPhase.integration,
                component_id=component_id,
                description=f"Review integration contract for {node.name}",
                category=TaskCategory.contract_review,
            ))

            # Task 2: Write integration tests
            test_id = _next_id(counter)
            tasks.append(TaskItem(
                id=test_id,
                phase=TaskPhase.integration,
                component_id=component_id,
                description=f"Write integration tests for {node.name}",
                category=TaskCategory.test_write,
                parallel=True,
            ))

            # Task 3: Wire children (depends on child verify tasks)
            child_deps = [
                verify_task_ids[child_id]
                for child_id in node.children
                if child_id in verify_task_ids
            ]
            integrate_id = _next_id(counter)
            tasks.append(TaskItem(
                id=integrate_id,
                phase=TaskPhase.integration,
                component_id=component_id,
                description=f"Wire children for {node.name}",
                category=TaskCategory.integrate,
                depends_on=child_deps,
            ))

            # Task 4: Verify integration
            verify_id = _next_id(counter)
            tasks.append(TaskItem(
                id=verify_id,
                phase=TaskPhase.integration,
                component_id=component_id,
                description=f"Run integration tests for {node.name}",
                category=TaskCategory.verify,
                depends_on=[integrate_id],
            ))
            verify_task_ids[component_id] = verify_id

    if non_leaf_groups:
        checkpoints.append(PhaseCheckpoint(
            after_phase=TaskPhase.integration,
            description="All integrations verified",
            validation="All integration tests pass",
        ))

    # ── Phase 5: POLISH ─────────────────────────────────────────
    tasks.append(TaskItem(
        id=_next_id(counter),
        phase=TaskPhase.polish,
        description="Run full contract validation gate",
        category=TaskCategory.validate,
    ))
    tasks.append(TaskItem(
        id=_next_id(counter),
        phase=TaskPhase.polish,
        description="Cross-artifact analysis",
        category=TaskCategory.validate,
    ))
    tasks.append(TaskItem(
        id=_next_id(counter),
        phase=TaskPhase.polish,
        description="Update design document",
        category=TaskCategory.document,
    ))

    return TaskList(
        project_id=project_id,
        tasks=tasks,
        checkpoints=checkpoints,
    )


def render_task_list_markdown(task_list: TaskList) -> str:
    """Render a task list as spec-kit style markdown.

    Format:
      # TASKS — {project_id}
      Progress: X/Y completed (Z%)

      ## Phase: Setup
      - [ ] T001 Description
      - [x] T002 [P] [component_id] Description (file_path)

      ---
      CHECKPOINT: All leaf components verified
    """
    lines: list[str] = []

    # Header + progress
    pct = (task_list.completed / task_list.total * 100) if task_list.total else 0
    lines.append(f"# TASKS — {task_list.project_id}")
    lines.append(f"Progress: {task_list.completed}/{task_list.total} completed ({pct:.0f}%)")
    lines.append("")

    current_phase = None
    checkpoint_map = {cp.after_phase: cp for cp in task_list.checkpoints}

    for task in task_list.tasks:
        # Phase header
        if task.phase != current_phase:
            if current_phase is not None:
                # Check for checkpoint after previous phase
                cp = checkpoint_map.get(current_phase)
                if cp:
                    lines.append("")
                    lines.append(f"---")
                    lines.append(f"CHECKPOINT: {cp.description}")
                lines.append("")
            current_phase = task.phase
            lines.append(f"## Phase: {task.phase.value.title()}")
            lines.append("")

        # Task line
        checkbox = "[x]" if task.status == TaskStatus.completed else "[ ]"
        parts = [f"- {checkbox} {task.id}"]

        if task.parallel:
            parts.append("[P]")

        if task.component_id:
            parts.append(f"[{task.component_id}]")

        parts.append(task.description)

        if task.file_path:
            parts.append(f"({task.file_path})")

        lines.append(" ".join(parts))

    # Final checkpoint
    if current_phase is not None:
        cp = checkpoint_map.get(current_phase)
        if cp:
            lines.append("")
            lines.append(f"---")
            lines.append(f"CHECKPOINT: {cp.description}")

    lines.append("")
    return "\n".join(lines)


# ── Status mapping ──────────────────────────────────────────────────

_IMPL_STATUS_TO_TASK_MAP = {
    "pending": {
        TaskCategory.contract_review: TaskStatus.pending,
        TaskCategory.test_setup: TaskStatus.pending,
        TaskCategory.test_write: TaskStatus.pending,
        TaskCategory.implement: TaskStatus.pending,
        TaskCategory.verify: TaskStatus.pending,
    },
    "contracted": {
        TaskCategory.contract_review: TaskStatus.completed,
        TaskCategory.test_setup: TaskStatus.pending,
        TaskCategory.test_write: TaskStatus.pending,
        TaskCategory.implement: TaskStatus.pending,
        TaskCategory.verify: TaskStatus.pending,
    },
    "implemented": {
        TaskCategory.contract_review: TaskStatus.completed,
        TaskCategory.test_setup: TaskStatus.completed,
        TaskCategory.test_write: TaskStatus.completed,
        TaskCategory.implement: TaskStatus.completed,
        TaskCategory.verify: TaskStatus.pending,
    },
    "tested": {
        TaskCategory.contract_review: TaskStatus.completed,
        TaskCategory.test_setup: TaskStatus.completed,
        TaskCategory.test_write: TaskStatus.completed,
        TaskCategory.implement: TaskStatus.completed,
        TaskCategory.verify: TaskStatus.completed,
    },
    "failed": {
        TaskCategory.contract_review: TaskStatus.completed,
        TaskCategory.test_setup: TaskStatus.completed,
        TaskCategory.test_write: TaskStatus.completed,
        TaskCategory.implement: TaskStatus.completed,
        TaskCategory.verify: TaskStatus.failed,
    },
}


def update_task_status(
    task_list: TaskList,
    component_id: str,
    impl_status: str,
) -> TaskList:
    """Update task statuses based on component implementation status.

    Maps implementation_status (pending/contracted/implemented/tested/failed)
    to individual task statuses by category.
    """
    mapping = _IMPL_STATUS_TO_TASK_MAP.get(impl_status)
    if not mapping:
        return task_list

    for task in task_list.tasks:
        if task.component_id == component_id and task.category in mapping:
            task.status = mapping[task.category]

    return task_list
