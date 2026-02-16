"""Tests for task list generation, rendering, and status updates."""

from __future__ import annotations

import pytest

from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionNode,
    DecompositionTree,
    EngineeringDecision,
    FieldSpec,
    FunctionContract,
    TestCase,
    TypeSpec,
)
from pact.schemas_tasks import (
    TaskCategory,
    TaskItem,
    TaskList,
    TaskPhase,
    TaskStatus,
)
from pact.task_list import (
    _find_shared_types,
    generate_task_list,
    render_task_list_markdown,
    update_task_status,
)


# ── Fixtures ────────────────────────────────────────────────────────


def _single_leaf_tree() -> DecompositionTree:
    """Single root node, no children (trivial case)."""
    return DecompositionTree(
        root_id="root",
        nodes={
            "root": DecompositionNode(
                component_id="root", name="Root", description="Single component",
                depth=0,
            ),
        },
    )


def _two_leaf_tree() -> DecompositionTree:
    """Root with two leaf children."""
    return DecompositionTree(
        root_id="root",
        nodes={
            "root": DecompositionNode(
                component_id="root", name="Root", description="Root",
                depth=0, children=["auth", "db"],
            ),
            "auth": DecompositionNode(
                component_id="auth", name="Auth", description="Authentication",
                depth=1, parent_id="root",
            ),
            "db": DecompositionNode(
                component_id="db", name="Database", description="DB layer",
                depth=1, parent_id="root",
            ),
        },
    )


def _deep_tree() -> DecompositionTree:
    """Root -> mid -> leaf1, leaf2 (3 levels)."""
    return DecompositionTree(
        root_id="root",
        nodes={
            "root": DecompositionNode(
                component_id="root", name="Root", description="Root",
                depth=0, children=["mid"],
            ),
            "mid": DecompositionNode(
                component_id="mid", name="Middle", description="Middle layer",
                depth=1, parent_id="root", children=["leaf1", "leaf2"],
            ),
            "leaf1": DecompositionNode(
                component_id="leaf1", name="Leaf One", description="First leaf",
                depth=2, parent_id="mid",
            ),
            "leaf2": DecompositionNode(
                component_id="leaf2", name="Leaf Two", description="Second leaf",
                depth=2, parent_id="mid",
            ),
        },
    )


def _make_contract(component_id: str, name: str, types: list[str] | None = None, deps: list[str] | None = None) -> ComponentContract:
    return ComponentContract(
        component_id=component_id,
        name=name,
        description=f"Contract for {name}",
        functions=[
            FunctionContract(
                name="do_something",
                description="Does something",
                inputs=[FieldSpec(name="x", type_ref="str")],
                output_type="bool",
            ),
        ],
        types=[TypeSpec(name=t, kind="struct") for t in (types or [])],
        dependencies=deps or [],
    )


def _make_test_suite(component_id: str) -> ContractTestSuite:
    return ContractTestSuite(
        component_id=component_id,
        contract_version=1,
        test_cases=[
            TestCase(id="t1", description="Happy path", function="do_something", category="happy_path"),
        ],
    )


# ── _find_shared_types ──────────────────────────────────────────────


class TestFindSharedTypes:
    def test_no_shared(self):
        contracts = {
            "a": _make_contract("a", "A", types=["TypeA"]),
            "b": _make_contract("b", "B", types=["TypeB"]),
        }
        assert _find_shared_types(contracts) == []

    def test_shared_type(self):
        contracts = {
            "a": _make_contract("a", "A", types=["SharedType", "TypeA"]),
            "b": _make_contract("b", "B", types=["SharedType", "TypeB"]),
        }
        assert _find_shared_types(contracts) == ["SharedType"]

    def test_multiple_shared(self):
        contracts = {
            "a": _make_contract("a", "A", types=["X", "Y"]),
            "b": _make_contract("b", "B", types=["X", "Y"]),
        }
        result = _find_shared_types(contracts)
        assert set(result) == {"X", "Y"}

    def test_empty_contracts(self):
        assert _find_shared_types({}) == []

    def test_no_types(self):
        contracts = {
            "a": _make_contract("a", "A"),
            "b": _make_contract("b", "B"),
        }
        assert _find_shared_types(contracts) == []


# ── generate_task_list ──────────────────────────────────────────────


class TestGenerateTaskList:
    def test_single_leaf_tree(self):
        tree = _single_leaf_tree()
        contracts = {"root": _make_contract("root", "Root")}
        suites = {"root": _make_test_suite("root")}

        tl = generate_task_list(tree, contracts, suites, "test-proj")
        assert tl.project_id == "test-proj"
        # 2 setup + 5 component + 3 polish = 10
        assert tl.total == 10

    def test_two_leaf_tree(self):
        tree = _two_leaf_tree()
        contracts = {
            "root": _make_contract("root", "Root", deps=["auth", "db"]),
            "auth": _make_contract("auth", "Auth"),
            "db": _make_contract("db", "Database"),
        }
        suites = {
            "root": _make_test_suite("root"),
            "auth": _make_test_suite("auth"),
            "db": _make_test_suite("db"),
        }

        tl = generate_task_list(tree, contracts, suites, "proj")
        # 2 setup + 0 foundational + 10 component (5*2 leaves) + 4 integration (root) + 3 polish = 19
        assert tl.total == 19

    def test_deep_tree(self):
        tree = _deep_tree()
        contracts = {
            "root": _make_contract("root", "Root", deps=["mid"]),
            "mid": _make_contract("mid", "Middle", deps=["leaf1", "leaf2"]),
            "leaf1": _make_contract("leaf1", "Leaf One"),
            "leaf2": _make_contract("leaf2", "Leaf Two"),
        }
        suites = {cid: _make_test_suite(cid) for cid in contracts}

        tl = generate_task_list(tree, contracts, suites, "deep")
        # 2 setup + 10 component (5*2 leaves) + 8 integration (2 non-leaves * 4) + 3 polish = 23
        assert tl.total == 23

    def test_task_ids_unique(self):
        tree = _two_leaf_tree()
        contracts = {cid: _make_contract(cid, cid) for cid in tree.nodes}
        suites = {cid: _make_test_suite(cid) for cid in tree.nodes}
        tl = generate_task_list(tree, contracts, suites, "proj")

        ids = [t.id for t in tl.tasks]
        assert len(ids) == len(set(ids))

    def test_task_ids_sequential(self):
        tree = _two_leaf_tree()
        contracts = {cid: _make_contract(cid, cid) for cid in tree.nodes}
        suites = {cid: _make_test_suite(cid) for cid in tree.nodes}
        tl = generate_task_list(tree, contracts, suites, "proj")

        for i, task in enumerate(tl.tasks, 1):
            assert task.id == f"T{i:03d}"

    def test_setup_phase_always_present(self):
        tree = _single_leaf_tree()
        tl = generate_task_list(tree, {}, {}, "proj")
        setup = tl.tasks_for_phase(TaskPhase.setup)
        assert len(setup) == 2

    def test_polish_phase_always_present(self):
        tree = _single_leaf_tree()
        tl = generate_task_list(tree, {}, {}, "proj")
        polish = tl.tasks_for_phase(TaskPhase.polish)
        assert len(polish) == 3

    def test_shared_types_create_foundational_tasks(self):
        tree = _two_leaf_tree()
        contracts = {
            "root": _make_contract("root", "Root"),
            "auth": _make_contract("auth", "Auth", types=["User", "Token"]),
            "db": _make_contract("db", "DB", types=["User", "Record"]),
        }
        suites = {cid: _make_test_suite(cid) for cid in contracts}
        tl = generate_task_list(tree, contracts, suites, "proj")

        foundational = tl.tasks_for_phase(TaskPhase.foundational)
        assert len(foundational) == 1  # "User" is shared
        assert "User" in foundational[0].description

    def test_tdd_ordering(self):
        """Test that tests come before implementation in component phase."""
        tree = _single_leaf_tree()
        contracts = {"root": _make_contract("root", "Root")}
        suites = {"root": _make_test_suite("root")}
        tl = generate_task_list(tree, contracts, suites, "proj")

        comp_tasks = tl.tasks_for_component("root")
        categories = [t.category for t in comp_tasks]
        # contract_review -> test_setup -> test_write -> implement -> verify
        assert categories.index(TaskCategory.test_write) < categories.index(TaskCategory.implement)
        assert categories.index(TaskCategory.test_setup) < categories.index(TaskCategory.test_write)
        assert categories.index(TaskCategory.contract_review) < categories.index(TaskCategory.test_setup)
        assert categories.index(TaskCategory.implement) < categories.index(TaskCategory.verify)

    def test_integration_depends_on_child_verify(self):
        """Integration tasks depend on child component verify tasks."""
        tree = _two_leaf_tree()
        contracts = {cid: _make_contract(cid, cid) for cid in tree.nodes}
        suites = {cid: _make_test_suite(cid) for cid in tree.nodes}
        tl = generate_task_list(tree, contracts, suites, "proj")

        # Find verify tasks for auth and db
        auth_verify = [t for t in tl.tasks if t.component_id == "auth" and t.category == TaskCategory.verify]
        db_verify = [t for t in tl.tasks if t.component_id == "db" and t.category == TaskCategory.verify]
        assert auth_verify and db_verify

        # Find integration 'integrate' task for root
        root_integrate = [t for t in tl.tasks if t.component_id == "root" and t.category == TaskCategory.integrate]
        assert root_integrate
        assert auth_verify[0].id in root_integrate[0].depends_on
        assert db_verify[0].id in root_integrate[0].depends_on

    def test_parallel_markers(self):
        """First task in parallel group should have parallel=True."""
        tree = _two_leaf_tree()
        contracts = {cid: _make_contract(cid, cid) for cid in tree.nodes}
        suites = {cid: _make_test_suite(cid) for cid in tree.nodes}
        tl = generate_task_list(tree, contracts, suites, "proj")

        component_tasks = tl.tasks_for_phase(TaskPhase.component)
        # First component's review task should be parallel
        assert component_tasks[0].parallel is True

    def test_checkpoints(self):
        tree = _two_leaf_tree()
        contracts = {cid: _make_contract(cid, cid) for cid in tree.nodes}
        suites = {cid: _make_test_suite(cid) for cid in tree.nodes}
        tl = generate_task_list(tree, contracts, suites, "proj")

        assert len(tl.checkpoints) == 2
        phases = [cp.after_phase for cp in tl.checkpoints]
        assert TaskPhase.component in phases
        assert TaskPhase.integration in phases

    def test_file_paths_populated(self):
        tree = _single_leaf_tree()
        contracts = {"root": _make_contract("root", "Root")}
        suites = {"root": _make_test_suite("root")}
        tl = generate_task_list(tree, contracts, suites, "proj")

        review_task = [t for t in tl.tasks if t.category == TaskCategory.contract_review and t.component_id == "root"]
        assert review_task[0].file_path == "contracts/root/interface.json"

        impl_task = [t for t in tl.tasks if t.category == TaskCategory.implement and t.component_id == "root"]
        assert impl_task[0].file_path == "implementations/root/src/"

    def test_empty_tree(self):
        """Empty tree (root only, no contracts) produces minimal task list."""
        tree = _single_leaf_tree()
        tl = generate_task_list(tree, {}, {}, "proj")
        # 2 setup + 5 component (root is a leaf) + 3 polish = 10
        assert tl.total == 10

    def test_with_decisions(self):
        """Decisions parameter accepted but doesn't change output (reserved)."""
        tree = _single_leaf_tree()
        decisions = [EngineeringDecision(ambiguity="auth", decision="JWT", rationale="simpler")]
        tl = generate_task_list(tree, {}, {}, "proj", decisions=decisions)
        assert tl.total >= 5  # Minimal

    def test_all_tasks_start_pending(self):
        tree = _single_leaf_tree()
        contracts = {"root": _make_contract("root", "Root")}
        suites = {"root": _make_test_suite("root")}
        tl = generate_task_list(tree, contracts, suites, "proj")
        assert all(t.status == TaskStatus.pending for t in tl.tasks)

    def test_no_leaves_no_component_checkpoint(self):
        """Tree with only root (which is a leaf) still gets checkpoint."""
        tree = _single_leaf_tree()
        tl = generate_task_list(tree, {}, {}, "proj")
        # Root is a leaf, so component checkpoint should be present
        assert any(cp.after_phase == TaskPhase.component for cp in tl.checkpoints)

    def test_component_tasks_per_leaf(self):
        """Each leaf gets exactly 5 tasks in component phase."""
        tree = _two_leaf_tree()
        contracts = {cid: _make_contract(cid, cid) for cid in tree.nodes}
        suites = {cid: _make_test_suite(cid) for cid in tree.nodes}
        tl = generate_task_list(tree, contracts, suites, "proj")

        auth_comp = [t for t in tl.tasks if t.component_id == "auth" and t.phase == TaskPhase.component]
        assert len(auth_comp) == 5

        db_comp = [t for t in tl.tasks if t.component_id == "db" and t.phase == TaskPhase.component]
        assert len(db_comp) == 5

    def test_integration_tasks_per_nonleaf(self):
        """Each non-leaf gets exactly 4 tasks in integration phase."""
        tree = _two_leaf_tree()
        contracts = {cid: _make_contract(cid, cid) for cid in tree.nodes}
        suites = {cid: _make_test_suite(cid) for cid in tree.nodes}
        tl = generate_task_list(tree, contracts, suites, "proj")

        root_int = [t for t in tl.tasks if t.component_id == "root" and t.phase == TaskPhase.integration]
        assert len(root_int) == 4

    def test_deep_tree_integration_order(self):
        """Deepest non-leaf integrates before shallower."""
        tree = _deep_tree()
        contracts = {cid: _make_contract(cid, cid) for cid in tree.nodes}
        suites = {cid: _make_test_suite(cid) for cid in tree.nodes}
        tl = generate_task_list(tree, contracts, suites, "proj")

        int_tasks = tl.tasks_for_phase(TaskPhase.integration)
        mid_tasks = [t for t in int_tasks if t.component_id == "mid"]
        root_tasks = [t for t in int_tasks if t.component_id == "root"]

        # mid should come before root
        mid_first_idx = tl.tasks.index(mid_tasks[0])
        root_first_idx = tl.tasks.index(root_tasks[0])
        assert mid_first_idx < root_first_idx

    def test_multiple_shared_types(self):
        """Multiple shared types each get a foundational task."""
        contracts = {
            "a": _make_contract("a", "A", types=["TypeX", "TypeY", "TypeZ"]),
            "b": _make_contract("b", "B", types=["TypeX", "TypeY"]),
        }
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(component_id="root", name="Root", description="r", children=["a", "b"]),
                "a": DecompositionNode(component_id="a", name="A", description="a", parent_id="root", depth=1),
                "b": DecompositionNode(component_id="b", name="B", description="b", parent_id="root", depth=1),
            },
        )
        suites = {cid: _make_test_suite(cid) for cid in contracts}
        tl = generate_task_list(tree, contracts, suites, "proj")

        foundational = tl.tasks_for_phase(TaskPhase.foundational)
        assert len(foundational) == 2
        descs = {t.description for t in foundational}
        assert "Define shared type: TypeX" in descs
        assert "Define shared type: TypeY" in descs


# ── render_task_list_markdown ───────────────────────────────────────


class TestRenderTaskListMarkdown:
    def test_header(self):
        tl = TaskList(project_id="my-proj")
        md = render_task_list_markdown(tl)
        assert "# TASKS — my-proj" in md
        assert "Progress: 0/0 completed" in md

    def test_progress_calculation(self):
        tl = TaskList(
            project_id="proj",
            tasks=[
                TaskItem(id="T001", phase=TaskPhase.setup, description="d", status=TaskStatus.completed),
                TaskItem(id="T002", phase=TaskPhase.setup, description="d"),
            ],
        )
        md = render_task_list_markdown(tl)
        assert "1/2 completed (50%)" in md

    def test_checkbox_format(self):
        tl = TaskList(
            project_id="proj",
            tasks=[
                TaskItem(id="T001", phase=TaskPhase.setup, description="Done", status=TaskStatus.completed),
                TaskItem(id="T002", phase=TaskPhase.setup, description="Pending"),
            ],
        )
        md = render_task_list_markdown(tl)
        assert "- [x] T001" in md
        assert "- [ ] T002" in md

    def test_parallel_marker(self):
        tl = TaskList(
            project_id="proj",
            tasks=[
                TaskItem(id="T001", phase=TaskPhase.component, description="Parallel task", parallel=True),
            ],
        )
        md = render_task_list_markdown(tl)
        assert "[P]" in md

    def test_component_id_shown(self):
        tl = TaskList(
            project_id="proj",
            tasks=[
                TaskItem(id="T001", phase=TaskPhase.component, component_id="auth", description="Review"),
            ],
        )
        md = render_task_list_markdown(tl)
        assert "[auth]" in md

    def test_file_path_shown(self):
        tl = TaskList(
            project_id="proj",
            tasks=[
                TaskItem(id="T001", phase=TaskPhase.component, description="Impl", file_path="src/main.py"),
            ],
        )
        md = render_task_list_markdown(tl)
        assert "(src/main.py)" in md

    def test_phase_headers(self):
        tl = TaskList(
            project_id="proj",
            tasks=[
                TaskItem(id="T001", phase=TaskPhase.setup, description="Init"),
                TaskItem(id="T002", phase=TaskPhase.component, description="Build"),
            ],
        )
        md = render_task_list_markdown(tl)
        assert "## Phase: Setup" in md
        assert "## Phase: Component" in md

    def test_checkpoint_rendered(self):
        from pact.schemas_tasks import PhaseCheckpoint
        tl = TaskList(
            project_id="proj",
            tasks=[
                TaskItem(id="T001", phase=TaskPhase.component, description="Build"),
                TaskItem(id="T002", phase=TaskPhase.polish, description="Final"),
            ],
            checkpoints=[
                PhaseCheckpoint(after_phase=TaskPhase.component, description="All verified"),
            ],
        )
        md = render_task_list_markdown(tl)
        assert "CHECKPOINT: All verified" in md

    def test_full_render(self):
        tree = _two_leaf_tree()
        contracts = {cid: _make_contract(cid, cid) for cid in tree.nodes}
        suites = {cid: _make_test_suite(cid) for cid in tree.nodes}
        tl = generate_task_list(tree, contracts, suites, "proj")
        md = render_task_list_markdown(tl)
        assert "# TASKS" in md
        assert "## Phase: Setup" in md
        assert "## Phase: Component" in md
        assert "## Phase: Integration" in md
        assert "## Phase: Polish" in md


# ── update_task_status ──────────────────────────────────────────────


class TestUpdateTaskStatus:
    def _make_component_tasks(self, component_id: str) -> list[TaskItem]:
        return [
            TaskItem(id="T001", phase=TaskPhase.component, component_id=component_id, description="Review", category=TaskCategory.contract_review),
            TaskItem(id="T002", phase=TaskPhase.component, component_id=component_id, description="Setup", category=TaskCategory.test_setup),
            TaskItem(id="T003", phase=TaskPhase.component, component_id=component_id, description="Test", category=TaskCategory.test_write),
            TaskItem(id="T004", phase=TaskPhase.component, component_id=component_id, description="Implement", category=TaskCategory.implement),
            TaskItem(id="T005", phase=TaskPhase.component, component_id=component_id, description="Verify", category=TaskCategory.verify),
        ]

    def test_pending_status(self):
        tl = TaskList(project_id="test", tasks=self._make_component_tasks("auth"))
        update_task_status(tl, "auth", "pending")
        assert all(t.status == TaskStatus.pending for t in tl.tasks)

    def test_contracted_status(self):
        tl = TaskList(project_id="test", tasks=self._make_component_tasks("auth"))
        update_task_status(tl, "auth", "contracted")
        statuses = {t.category: t.status for t in tl.tasks}
        assert statuses[TaskCategory.contract_review] == TaskStatus.completed
        assert statuses[TaskCategory.test_setup] == TaskStatus.pending

    def test_implemented_status(self):
        tl = TaskList(project_id="test", tasks=self._make_component_tasks("auth"))
        update_task_status(tl, "auth", "implemented")
        statuses = {t.category: t.status for t in tl.tasks}
        assert statuses[TaskCategory.contract_review] == TaskStatus.completed
        assert statuses[TaskCategory.test_setup] == TaskStatus.completed
        assert statuses[TaskCategory.test_write] == TaskStatus.completed
        assert statuses[TaskCategory.implement] == TaskStatus.completed
        assert statuses[TaskCategory.verify] == TaskStatus.pending

    def test_tested_status(self):
        tl = TaskList(project_id="test", tasks=self._make_component_tasks("auth"))
        update_task_status(tl, "auth", "tested")
        assert all(t.status == TaskStatus.completed for t in tl.tasks)

    def test_failed_status(self):
        tl = TaskList(project_id="test", tasks=self._make_component_tasks("auth"))
        update_task_status(tl, "auth", "failed")
        statuses = {t.category: t.status for t in tl.tasks}
        assert statuses[TaskCategory.verify] == TaskStatus.failed
        assert statuses[TaskCategory.implement] == TaskStatus.completed

    def test_unknown_status_noop(self):
        tl = TaskList(project_id="test", tasks=self._make_component_tasks("auth"))
        update_task_status(tl, "auth", "bogus")
        assert all(t.status == TaskStatus.pending for t in tl.tasks)

    def test_wrong_component_noop(self):
        tl = TaskList(project_id="test", tasks=self._make_component_tasks("auth"))
        update_task_status(tl, "db", "tested")
        assert all(t.status == TaskStatus.pending for t in tl.tasks)

    def test_mixed_components(self):
        tasks = self._make_component_tasks("auth") + [
            TaskItem(id="T006", phase=TaskPhase.component, component_id="db", description="Review", category=TaskCategory.contract_review),
        ]
        # Fix IDs to be unique
        tasks[5] = TaskItem(id="T006", phase=TaskPhase.component, component_id="db", description="Review", category=TaskCategory.contract_review)
        tl = TaskList(project_id="test", tasks=tasks)
        update_task_status(tl, "auth", "tested")
        # auth tasks completed, db task untouched
        auth_tasks = [t for t in tl.tasks if t.component_id == "auth"]
        db_tasks = [t for t in tl.tasks if t.component_id == "db"]
        assert all(t.status == TaskStatus.completed for t in auth_tasks)
        assert all(t.status == TaskStatus.pending for t in db_tasks)

    def test_returns_task_list(self):
        tl = TaskList(project_id="test", tasks=self._make_component_tasks("auth"))
        result = update_task_status(tl, "auth", "tested")
        assert result is tl  # Mutates in place, returns same object
