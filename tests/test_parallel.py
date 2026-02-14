"""Tests for parallel execution, competitive implementations, and related features.

Covers:
- schemas.py: tree traversal methods (leaf_parallel_groups, non_leaf_parallel_groups, subtree)
- config.py: parallel/competitive config fields, resolve_parallel_config
- resolution.py: ScoredAttempt, select_winner, format_resolution_summary
- project.py: attempt storage (attempt_dir, promote_attempt, archive, list_attempts)
- implementer.py: implement_all with parallel/competitive flags
- integrator.py: integrate_all with parallel flag
- scheduler.py: config passthrough, plan_only mode
- cli.py: cf components, cf build
- backends/claude_code_team.py: AgentTask, AgentResult, ClaudeCodeTeamBackend
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from pact.config import (
    GlobalConfig,
    ParallelConfig,
    ProjectConfig,
    load_global_config,
    load_project_config,
    resolve_parallel_config,
)
from pact.project import ProjectManager
from pact.resolution import (
    ScoredAttempt,
    format_resolution_summary,
    select_winner,
)
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionNode,
    DecompositionTree,
    FieldSpec,
    FunctionContract,
    TestCase,
    TestFailure,
    TestResults,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def tmp_project(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "test-project")
    pm.init()
    return pm


def _make_tree() -> DecompositionTree:
    """Build a tree:
        root (depth=0)
        ├── mid_a (depth=1)
        │   ├── leaf_a1 (depth=2)
        │   └── leaf_a2 (depth=2)
        ├── mid_b (depth=1)
        │   └── leaf_b1 (depth=2)
        └── leaf_c (depth=1)
    """
    return DecompositionTree(
        root_id="root",
        nodes={
            "root": DecompositionNode(
                component_id="root", name="Root", description="r",
                depth=0, children=["mid_a", "mid_b", "leaf_c"],
            ),
            "mid_a": DecompositionNode(
                component_id="mid_a", name="Mid A", description="ma",
                depth=1, parent_id="root", children=["leaf_a1", "leaf_a2"],
            ),
            "mid_b": DecompositionNode(
                component_id="mid_b", name="Mid B", description="mb",
                depth=1, parent_id="root", children=["leaf_b1"],
            ),
            "leaf_c": DecompositionNode(
                component_id="leaf_c", name="Leaf C", description="lc",
                depth=1, parent_id="root",
            ),
            "leaf_a1": DecompositionNode(
                component_id="leaf_a1", name="Leaf A1", description="la1",
                depth=2, parent_id="mid_a",
            ),
            "leaf_a2": DecompositionNode(
                component_id="leaf_a2", name="Leaf A2", description="la2",
                depth=2, parent_id="mid_a",
            ),
            "leaf_b1": DecompositionNode(
                component_id="leaf_b1", name="Leaf B1", description="lb1",
                depth=2, parent_id="mid_b",
            ),
        },
    )


def _make_contract(component_id: str) -> ComponentContract:
    return ComponentContract(
        component_id=component_id,
        name=component_id.replace("_", " ").title(),
        description=f"Contract for {component_id}",
        functions=[
            FunctionContract(
                name="process",
                description="Process input",
                inputs=[FieldSpec(name="data", type_ref="str")],
                output_type="str",
            ),
        ],
    )


def _make_test_suite(component_id: str) -> ContractTestSuite:
    return ContractTestSuite(
        component_id=component_id,
        contract_version=1,
        test_cases=[
            TestCase(
                id=f"{component_id}_t1",
                description="happy path",
                function="process",
                category="happy_path",
            ),
        ],
        generated_code="def test_process(): assert True",
    )


# ── schemas.py: Tree traversal ───────────────────────────────────


class TestTreeLeafParallelGroups:
    def test_returns_all_leaves(self):
        tree = _make_tree()
        groups = tree.leaf_parallel_groups()
        assert len(groups) == 1
        leaves = set(groups[0])
        assert leaves == {"leaf_a1", "leaf_a2", "leaf_b1", "leaf_c"}

    def test_empty_tree(self):
        tree = DecompositionTree(root_id="root", nodes={})
        assert tree.leaf_parallel_groups() == []

    def test_single_node_is_leaf(self):
        tree = DecompositionTree(
            root_id="solo",
            nodes={"solo": DecompositionNode(
                component_id="solo", name="Solo", description="s",
            )},
        )
        groups = tree.leaf_parallel_groups()
        assert groups == [["solo"]]


class TestTreeNonLeafParallelGroups:
    def test_deepest_first(self):
        tree = _make_tree()
        groups = tree.non_leaf_parallel_groups()
        # depth=1 non-leaves: mid_a, mid_b (deepest non-leaves)
        # depth=0 non-leaf: root
        assert len(groups) == 2
        # First group = deepest (depth=1)
        assert set(groups[0]) == {"mid_a", "mid_b"}
        # Second group = shallowest (depth=0)
        assert groups[1] == ["root"]

    def test_all_leaves_returns_empty(self):
        tree = DecompositionTree(
            root_id="a",
            nodes={
                "a": DecompositionNode(component_id="a", name="A", description="a"),
                "b": DecompositionNode(component_id="b", name="B", description="b"),
            },
        )
        assert tree.non_leaf_parallel_groups() == []


class TestTreeSubtree:
    def test_full_subtree(self):
        tree = _make_tree()
        subtree = tree.subtree("mid_a")
        assert set(subtree) == {"mid_a", "leaf_a1", "leaf_a2"}

    def test_leaf_subtree(self):
        tree = _make_tree()
        assert tree.subtree("leaf_c") == ["leaf_c"]

    def test_root_subtree(self):
        tree = _make_tree()
        subtree = tree.subtree("root")
        assert len(subtree) == 7  # all nodes


# ── config.py: New fields ───────────────────────────────────────


class TestParallelConfig:
    def test_global_defaults(self):
        gc = GlobalConfig()
        assert gc.parallel_components is False
        assert gc.competitive_implementations is False
        assert gc.competitive_agents == 2
        assert gc.max_concurrent_agents == 4
        assert gc.plan_only is False

    def test_project_defaults(self):
        pc = ProjectConfig()
        assert pc.parallel_components is None
        assert pc.competitive_implementations is None
        assert pc.competitive_agents is None
        assert pc.max_concurrent_agents is None
        assert pc.plan_only is None

    def test_load_global_with_parallel(self, tmp_path: Path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({
            "parallel_components": True,
            "competitive_implementations": True,
            "competitive_agents": 3,
            "max_concurrent_agents": 8,
            "plan_only": True,
        }))
        c = load_global_config(config_path)
        assert c.parallel_components is True
        assert c.competitive_implementations is True
        assert c.competitive_agents == 3
        assert c.max_concurrent_agents == 8
        assert c.plan_only is True

    def test_load_project_with_parallel(self, tmp_path: Path):
        config_path = tmp_path / "pact.yaml"
        config_path.write_text(yaml.dump({
            "parallel_components": True,
            "competitive_agents": 5,
        }))
        c = load_project_config(tmp_path)
        assert c.parallel_components is True
        assert c.competitive_agents == 5
        assert c.competitive_implementations is None  # not set

    def test_resolve_uses_project_override(self):
        gc = GlobalConfig(parallel_components=False, competitive_agents=2)
        pc = ProjectConfig(parallel_components=True, competitive_agents=4)
        cfg = resolve_parallel_config(pc, gc)
        assert cfg.parallel is True
        assert cfg.agent_count == 4

    def test_resolve_falls_back_to_global(self):
        gc = GlobalConfig(parallel_components=True, competitive_implementations=True)
        pc = ProjectConfig()  # all None
        cfg = resolve_parallel_config(pc, gc)
        assert cfg.parallel is True
        assert cfg.competitive is True

    def test_resolve_returns_dataclass(self):
        cfg = resolve_parallel_config(ProjectConfig(), GlobalConfig())
        assert isinstance(cfg, ParallelConfig)
        assert cfg.parallel is False
        assert cfg.competitive is False
        assert cfg.max_concurrent == 4


# ── resolution.py ────────────────────────────────────────────────


class TestScoredAttempt:
    def test_pass_rate(self):
        a = ScoredAttempt(
            attempt_id="a1", component_id="c1",
            test_results=TestResults(total=10, passed=7, failed=3),
            build_duration_seconds=30.0, src_dir="/tmp/a",
        )
        assert a.pass_rate == 0.7

    def test_pass_rate_zero_total(self):
        a = ScoredAttempt(
            attempt_id="a1", component_id="c1",
            test_results=TestResults(total=0, passed=0, failed=0),
            build_duration_seconds=10.0, src_dir="/tmp/a",
        )
        assert a.pass_rate == 0.0

    def test_score_tuple(self):
        a = ScoredAttempt(
            attempt_id="a1", component_id="c1",
            test_results=TestResults(total=5, passed=4, failed=1),
            build_duration_seconds=45.0, src_dir="/tmp/a",
        )
        assert a.score_tuple == (0.8, 45.0)


class TestSelectWinner:
    def test_empty_list(self):
        assert select_winner([]) is None

    def test_single_attempt(self):
        a = ScoredAttempt(
            attempt_id="only", component_id="c1",
            test_results=TestResults(total=3, passed=3),
            build_duration_seconds=10.0, src_dir="/tmp",
        )
        assert select_winner([a]) is a

    def test_higher_pass_rate_wins(self):
        a = ScoredAttempt(
            attempt_id="a", component_id="c1",
            test_results=TestResults(total=10, passed=8, failed=2),
            build_duration_seconds=100.0, src_dir="/tmp/a",
        )
        b = ScoredAttempt(
            attempt_id="b", component_id="c1",
            test_results=TestResults(total=10, passed=10, failed=0),
            build_duration_seconds=50.0, src_dir="/tmp/b",
        )
        winner = select_winner([a, b])
        assert winner.attempt_id == "b"

    def test_longer_build_breaks_tie(self):
        a = ScoredAttempt(
            attempt_id="a", component_id="c1",
            test_results=TestResults(total=5, passed=5),
            build_duration_seconds=30.0, src_dir="/tmp/a",
        )
        b = ScoredAttempt(
            attempt_id="b", component_id="c1",
            test_results=TestResults(total=5, passed=5),
            build_duration_seconds=60.0, src_dir="/tmp/b",
        )
        winner = select_winner([a, b])
        assert winner.attempt_id == "b"

    def test_three_way_competition(self):
        attempts = [
            ScoredAttempt(
                attempt_id=f"a{i}", component_id="c1",
                test_results=TestResults(total=10, passed=p, failed=10 - p),
                build_duration_seconds=float(i * 10), src_dir=f"/tmp/{i}",
            )
            for i, p in [(1, 6), (2, 9), (3, 8)]
        ]
        winner = select_winner(attempts)
        assert winner.attempt_id == "a2"  # 9/10 pass rate


class TestFormatResolutionSummary:
    def test_format(self):
        winner = ScoredAttempt(
            attempt_id="w", component_id="c",
            test_results=TestResults(total=5, passed=5),
            build_duration_seconds=25.0, src_dir="/tmp/w",
        )
        losers = [
            ScoredAttempt(
                attempt_id="l", component_id="c",
                test_results=TestResults(total=5, passed=3, failed=2),
                build_duration_seconds=15.0, src_dir="/tmp/l",
            ),
        ]
        summary = format_resolution_summary(winner, losers)
        assert "Winner: w" in summary
        assert "5/5" in summary
        assert "Lost: l" in summary
        assert "3/5" in summary


# ── project.py: Attempt storage ──────────────────────────────────


class TestAttemptStorage:
    def test_attempt_dir_created(self, tmp_project: ProjectManager):
        d = tmp_project.attempt_dir("comp_a", "attempt_1")
        assert d.exists()
        assert "attempts" in str(d)
        assert "attempt_1" in str(d)

    def test_attempt_src_dir(self, tmp_project: ProjectManager):
        d = tmp_project.attempt_src_dir("comp_a", "attempt_1")
        assert d.exists()
        assert d.name == "src"

    def test_save_attempt_metadata(self, tmp_project: ProjectManager):
        tmp_project.save_attempt_metadata("comp_a", "att1", {"type": "competitive"})
        meta_path = tmp_project.attempt_dir("comp_a", "att1") / "metadata.json"
        assert meta_path.exists()
        data = json.loads(meta_path.read_text())
        assert data["type"] == "competitive"

    def test_save_attempt_test_results(self, tmp_project: ProjectManager):
        results = TestResults(total=3, passed=2, failed=1)
        tmp_project.save_attempt_test_results("comp_a", "att1", results)
        path = tmp_project.attempt_dir("comp_a", "att1") / "test_results.json"
        assert path.exists()

    def test_promote_attempt(self, tmp_project: ProjectManager):
        # Set up attempt with a file
        src = tmp_project.attempt_src_dir("comp_a", "att1")
        (src / "module.py").write_text("# winner code")
        tmp_project.save_attempt_metadata("comp_a", "att1", {"attempt": 1})

        # Promote
        tmp_project.promote_attempt("comp_a", "att1")

        # Check main src has the file
        main_src = tmp_project.impl_src_dir("comp_a")
        assert (main_src / "module.py").exists()
        assert "winner code" in (main_src / "module.py").read_text()

    def test_promote_overwrites_existing(self, tmp_project: ProjectManager):
        # Put something in main src
        main_src = tmp_project.impl_src_dir("comp_a")
        (main_src / "old.py").write_text("# old code")

        # Set up attempt
        att_src = tmp_project.attempt_src_dir("comp_a", "att1")
        (att_src / "new.py").write_text("# new code")

        tmp_project.promote_attempt("comp_a", "att1")

        # Old file gone, new file present
        assert not (main_src / "old.py").exists()
        assert (main_src / "new.py").exists()

    def test_archive_current_impl(self, tmp_project: ProjectManager):
        # Set up current impl
        src = tmp_project.impl_src_dir("comp_a")
        (src / "impl.py").write_text("# old impl")
        tmp_project.save_impl_metadata("comp_a", {"attempt": 1})

        # Archive
        archive_id = tmp_project.archive_current_impl("comp_a", "rebuild")
        assert archive_id is not None
        assert archive_id.startswith("archived_")

        # Archived files exist
        archive_src = tmp_project.attempt_dir("comp_a", archive_id) / "src"
        assert (archive_src / "impl.py").exists()

        # Main src is now empty
        assert not any(src.iterdir())

    def test_archive_empty_impl_returns_none(self, tmp_project: ProjectManager):
        result = tmp_project.archive_current_impl("comp_a", "test")
        assert result is None

    def test_list_attempts(self, tmp_project: ProjectManager):
        # Create a few attempts
        for i in range(3):
            tmp_project.save_attempt_metadata("comp_a", f"att_{i}", {
                "attempt": i, "type": "competitive",
            })

        attempts = tmp_project.list_attempts("comp_a")
        assert len(attempts) == 3
        assert all("attempt_id" in a for a in attempts)

    def test_list_attempts_empty(self, tmp_project: ProjectManager):
        assert tmp_project.list_attempts("nonexistent") == []


# ── implementer.py: Parallel/competitive modes ──────────────────


class TestImplementAllParallel:
    """Test that implement_all passes flags correctly."""

    @pytest.mark.asyncio
    async def test_sequential_default(self, tmp_project: ProjectManager):
        """With both levers off, behavior is sequential (existing)."""
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root", description="r",
                    children=["a", "b"],
                ),
                "a": DecompositionNode(
                    component_id="a", name="A", description="a",
                    parent_id="root",
                ),
                "b": DecompositionNode(
                    component_id="b", name="B", description="b",
                    parent_id="root",
                ),
            },
        )

        for cid in ["root", "a", "b"]:
            tmp_project.save_contract(_make_contract(cid))
            tmp_project.save_test_suite(_make_test_suite(cid))
        tmp_project.save_tree(tree)

        # Mock implement_component to return passing results
        with patch("pact.implementer.implement_component") as mock_impl:
            mock_impl.return_value = TestResults(total=1, passed=1)
            agent = MagicMock()
            from pact.implementer import implement_all
            results = await implement_all(
                agent, tmp_project, tree,
                parallel=False, competitive=False,
            )

        # Should have implemented both leaves sequentially
        assert "a" in results
        assert "b" in results
        assert mock_impl.call_count == 2

    @pytest.mark.asyncio
    async def test_parallel_uses_gather(self, tmp_project: ProjectManager):
        """With parallel=True, leaves should run via asyncio.gather."""
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root", description="r",
                    children=["a", "b"],
                ),
                "a": DecompositionNode(
                    component_id="a", name="A", description="a",
                    parent_id="root",
                ),
                "b": DecompositionNode(
                    component_id="b", name="B", description="b",
                    parent_id="root",
                ),
            },
        )

        for cid in ["root", "a", "b"]:
            tmp_project.save_contract(_make_contract(cid))
            tmp_project.save_test_suite(_make_test_suite(cid))
        tmp_project.save_tree(tree)

        call_order = []

        async def mock_impl(agent, project, cid, contract, test_suite, **kwargs):
            call_order.append(cid)
            return TestResults(total=1, passed=1)

        agent_mock = MagicMock()
        factory = lambda: agent_mock

        with patch("pact.implementer.implement_component", side_effect=mock_impl):
            from pact.implementer import implement_all
            results = await implement_all(
                agent_mock, tmp_project, tree,
                parallel=True,
                agent_factory=factory,
            )

        assert set(results.keys()) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_target_components_filter(self, tmp_project: ProjectManager):
        """target_components should restrict which leaves are implemented."""
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root", description="r",
                    children=["a", "b", "c"],
                ),
                "a": DecompositionNode(component_id="a", name="A", description="a", parent_id="root"),
                "b": DecompositionNode(component_id="b", name="B", description="b", parent_id="root"),
                "c": DecompositionNode(component_id="c", name="C", description="c", parent_id="root"),
            },
        )

        for cid in ["root", "a", "b", "c"]:
            tmp_project.save_contract(_make_contract(cid))
            tmp_project.save_test_suite(_make_test_suite(cid))
        tmp_project.save_tree(tree)

        with patch("pact.implementer.implement_component") as mock_impl:
            mock_impl.return_value = TestResults(total=1, passed=1)
            from pact.implementer import implement_all
            results = await implement_all(
                MagicMock(), tmp_project, tree,
                target_components={"a", "c"},
            )

        assert set(results.keys()) == {"a", "c"}
        assert mock_impl.call_count == 2


# ── integrator.py: Parallel groups ───────────────────────────────


class TestIntegrateAllParallel:
    @pytest.mark.asyncio
    async def test_sequential_default(self, tmp_project: ProjectManager):
        tree = _make_tree()
        for cid in tree.nodes:
            tmp_project.save_contract(_make_contract(cid))
            tmp_project.save_test_suite(_make_test_suite(cid))
        tmp_project.save_tree(tree)

        with patch("pact.integrator.integrate_component") as mock_int:
            mock_int.return_value = TestResults(total=1, passed=1)
            from pact.integrator import integrate_all
            results = await integrate_all(
                MagicMock(), tmp_project, tree,
                parallel=False,
            )

        # Should integrate mid_a, mid_b, and root (3 non-leaves)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_parallel_depth_groups(self, tmp_project: ProjectManager):
        tree = _make_tree()
        for cid in tree.nodes:
            tmp_project.save_contract(_make_contract(cid))
            tmp_project.save_test_suite(_make_test_suite(cid))
        tmp_project.save_tree(tree)

        call_order = []

        async def mock_int(agent, project, parent_id, *args, **kwargs):
            call_order.append(parent_id)
            return TestResults(total=1, passed=1)

        def make_mock_agent():
            m = MagicMock()
            m.close = AsyncMock()
            return m

        with patch("pact.integrator.integrate_component", side_effect=mock_int):
            from pact.integrator import integrate_all
            results = await integrate_all(
                MagicMock(), tmp_project, tree,
                parallel=True,
                agent_factory=make_mock_agent,
            )

        assert len(results) == 3
        # root should come after mid_a and mid_b (deepest first)
        root_idx = call_order.index("root")
        for mid in ["mid_a", "mid_b"]:
            if mid in call_order:
                assert call_order.index(mid) < root_idx


# ── scheduler.py: Config passthrough ────────────────────────────


class TestSchedulerParallelConfig:
    def test_plan_only_pauses_after_decompose(self, tmp_path: Path):
        from pact.budget import BudgetTracker
        from pact.scheduler import Scheduler

        pm = ProjectManager(tmp_path / "proj")
        pm.init()

        gc = GlobalConfig(check_interval=1, plan_only=True)
        pc = ProjectConfig()
        budget = BudgetTracker(per_project_cap=10.00)

        scheduler = Scheduler(pm, gc, pc, budget)

        # Simulate decompose completing successfully
        state = pm.create_run()
        state.phase = "decompose"
        pm.save_state(state)

        # Mock decompose_and_contract to return passing gate, and _make_agent
        from pact.schemas import GateResult

        mock_agent = MagicMock()
        mock_agent.close = AsyncMock()

        with patch.object(scheduler, "_make_agent", return_value=mock_agent), \
             patch("pact.scheduler.decompose_and_contract") as mock_dec:
            mock_dec.return_value = GateResult(passed=True, reason="ok")
            tree = DecompositionTree(
                root_id="root",
                nodes={"root": DecompositionNode(
                    component_id="root", name="Root", description="r",
                )},
            )
            pm.save_tree(tree)

            result = asyncio.run(scheduler.run_once())

        assert result.status == "paused"
        assert "plan_only" in result.pause_reason.lower() or "Plan-only" in result.pause_reason

    def test_agent_factory_created(self, tmp_path: Path):
        from pact.budget import BudgetTracker
        from pact.scheduler import Scheduler

        pm = ProjectManager(tmp_path / "proj")
        pm.init()
        gc = GlobalConfig(check_interval=1)
        pc = ProjectConfig()
        budget = BudgetTracker(per_project_cap=10.00)
        scheduler = Scheduler(pm, gc, pc, budget)

        factory = scheduler._make_agent_factory("code_author")
        assert callable(factory)


# ── cli.py: cf components ────────────────────────────────────────


class TestCLIComponents:
    def test_components_table_output(self, tmp_project: ProjectManager, capsys):
        tree = _make_tree()
        # Set some statuses
        tree.nodes["leaf_a1"].implementation_status = "contracted"
        tree.nodes["leaf_a2"].implementation_status = "tested"
        tree.nodes["leaf_a2"].test_results = TestResults(total=3, passed=3)

        for cid in tree.nodes:
            tmp_project.save_contract(_make_contract(cid))
            tmp_project.save_test_suite(_make_test_suite(cid))
        tmp_project.save_tree(tree)

        import argparse
        from pact.cli import cmd_components

        args = argparse.Namespace(
            project_dir=str(tmp_project.project_dir),
            json_output=False,
        )
        cmd_components(args)
        output = capsys.readouterr().out
        assert "root" in output
        assert "leaf_a1" in output

    def test_components_json_output(self, tmp_project: ProjectManager, capsys):
        tree = DecompositionTree(
            root_id="solo",
            nodes={"solo": DecompositionNode(
                component_id="solo", name="Solo", description="s",
                implementation_status="contracted",
            )},
        )
        tmp_project.save_contract(_make_contract("solo"))
        tmp_project.save_test_suite(_make_test_suite("solo"))
        tmp_project.save_tree(tree)

        import argparse
        from pact.cli import cmd_components

        args = argparse.Namespace(
            project_dir=str(tmp_project.project_dir),
            json_output=True,
        )
        cmd_components(args)
        output = capsys.readouterr().out
        data = json.loads(output)
        assert len(data) == 1
        assert data[0]["id"] == "solo"
        assert data[0]["status"] == "contracted"

    def test_components_no_tree(self, tmp_project: ProjectManager, capsys):
        import argparse
        from pact.cli import cmd_components

        args = argparse.Namespace(
            project_dir=str(tmp_project.project_dir),
            json_output=False,
        )
        cmd_components(args)
        output = capsys.readouterr().out
        assert "No decomposition tree" in output


# ── cli.py: cf build ─────────────────────────────────────────────


class TestCLIBuild:
    def test_build_plan_only(self, tmp_project: ProjectManager, capsys):
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root", description="r",
                    children=["leaf"],
                ),
                "leaf": DecompositionNode(
                    component_id="leaf", name="Leaf", description="l",
                    parent_id="root", implementation_status="contracted",
                ),
            },
        )
        tmp_project.save_contract(_make_contract("leaf"))
        tmp_project.save_test_suite(_make_test_suite("leaf"))
        tmp_project.save_tree(tree)

        state = tmp_project.create_run()
        tmp_project.save_state(state)

        import argparse
        from pact.cli import cmd_build

        args = argparse.Namespace(
            project_dir=str(tmp_project.project_dir),
            component_id="leaf",
            competitive=False,
            agents=2,
            plan_only=True,
        )
        asyncio.run(cmd_build(args))
        output = capsys.readouterr().out
        assert "Component: Leaf" in output
        assert "plan-only" in output.lower() or "Mode:" in output

    def test_build_missing_component(self, tmp_project: ProjectManager, capsys):
        tree = DecompositionTree(
            root_id="root",
            nodes={"root": DecompositionNode(
                component_id="root", name="Root", description="r",
            )},
        )
        tmp_project.save_tree(tree)
        state = tmp_project.create_run()
        tmp_project.save_state(state)

        import argparse
        from pact.cli import cmd_build

        args = argparse.Namespace(
            project_dir=str(tmp_project.project_dir),
            component_id="nonexistent",
            competitive=False,
            agents=2,
            plan_only=False,
        )
        asyncio.run(cmd_build(args))
        output = capsys.readouterr().out
        assert "not found" in output.lower()


# ── backends/claude_code_team.py ─────────────────────────────────


class TestClaudeCodeTeamBackend:
    def test_agent_task_creation(self):
        from pact.backends.claude_code_team import AgentTask

        task = AgentTask(
            prompt="Implement component X",
            output_file="/tmp/output.json",
            pane_name="comp-x",
            model="claude-opus-4-6",
        )
        assert task.prompt == "Implement component X"
        assert task.pane_name == "comp-x"

    def test_agent_result(self):
        from pact.backends.claude_code_team import AgentResult

        result = AgentResult(
            pane_name="comp-x",
            output_file="/tmp/out.json",
            content='{"files": {"mod.py": "code"}}',
            success=True,
        )
        assert result.success
        assert result.pane_name == "comp-x"

    def test_backend_init(self):
        from pact.backends.claude_code_team import ClaudeCodeTeamBackend

        backend = ClaudeCodeTeamBackend(
            model="claude-opus-4-6",
            session_name="test-session",
            max_concurrent=2,
        )
        assert backend._session == "test-session"
        assert backend._max_concurrent == 2

    @pytest.mark.asyncio
    async def test_close_cleanup(self, tmp_path: Path):
        from pact.backends.claude_code_team import ClaudeCodeTeamBackend

        backend = ClaudeCodeTeamBackend(
            model="claude-opus-4-6",
            session_name="test-cleanup",
        )
        # Override prompt dir to a known location
        backend._prompt_dir = tmp_path / "prompts"
        backend._prompt_dir.mkdir()
        (backend._prompt_dir / "test.md").write_text("test")

        await backend.close()
        assert not backend._prompt_dir.exists()


# ── Backend factory ──────────────────────────────────────────────


class TestBackendFactory:
    def test_claude_code_team_returns_claude_code(self):
        """claude_code_team falls back to claude_code for structured calls."""
        from pact.backends import create_backend
        from pact.backends.claude_code import ClaudeCodeBackend
        from pact.budget import BudgetTracker

        budget = BudgetTracker()
        backend = create_backend("claude_code_team", budget, "claude-opus-4-6")
        assert isinstance(backend, ClaudeCodeBackend)

    def test_unknown_backend_raises(self):
        from pact.backends import create_backend
        from pact.budget import BudgetTracker

        with pytest.raises(ValueError, match="Unknown backend"):
            create_backend("nonexistent", BudgetTracker(), "claude-opus-4-6")
