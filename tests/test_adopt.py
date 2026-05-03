"""Tests for codebase adoption pipeline."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pact.adopt import (
    AdoptionResult,
    adopt_codebase,
    build_decomposition_tree,
    generate_smoke_tests,
    link_existing_implementations,
)
from pact.codebase_analyzer import analyze_codebase
from pact.project import ProjectManager
from pact.schemas import DecompositionTree
from pact.schemas_testgen import (
    CodebaseAnalysis,
    ExtractedFunction,
    SourceFile,
)


# ── Tree Construction ──────────────────────────────────────────────


class TestBuildDecompositionTree:
    def test_single_file(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="main.py", functions=[
                    ExtractedFunction(name="hello"),
                ]),
            ],
        )
        tree = build_decomposition_tree(analysis)
        assert "root" in tree.nodes
        assert "main" in tree.nodes
        assert tree.nodes["main"].parent_id == "root"

    def test_nested_packages(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="src/auth/login.py", functions=[
                    ExtractedFunction(name="authenticate"),
                ]),
                SourceFile(path="src/auth/roles.py", functions=[
                    ExtractedFunction(name="check_role"),
                ]),
                SourceFile(path="src/utils.py", functions=[
                    ExtractedFunction(name="helper"),
                ]),
            ],
        )
        tree = build_decomposition_tree(analysis)

        # Should have root, src, src_auth, and leaf nodes
        assert "root" in tree.nodes
        assert "src" in tree.nodes
        assert "src_auth" in tree.nodes
        assert "src_auth_login" in tree.nodes
        assert "src_auth_roles" in tree.nodes
        assert "src_utils" in tree.nodes

        # Check hierarchy
        assert "src" in tree.nodes["root"].children
        assert "src_auth" in tree.nodes["src"].children
        assert "src_auth_login" in tree.nodes["src_auth"].children

    def test_empty_files_skipped(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="empty.py", functions=[]),
                SourceFile(path="real.py", functions=[
                    ExtractedFunction(name="work"),
                ]),
            ],
        )
        tree = build_decomposition_tree(analysis)
        assert "real" in tree.nodes
        assert "empty" not in tree.nodes

    def test_leaves(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="a.py", functions=[ExtractedFunction(name="f1")]),
                SourceFile(path="b.py", functions=[ExtractedFunction(name="f2")]),
            ],
        )
        tree = build_decomposition_tree(analysis)
        leaves = tree.leaves()
        leaf_ids = {n.component_id for n in leaves}
        assert "a" in leaf_ids
        assert "b" in leaf_ids
        assert "root" not in leaf_ids

    def test_component_id_from_path(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="src/core/engine.py", functions=[
                    ExtractedFunction(name="run"),
                ]),
            ],
        )
        tree = build_decomposition_tree(analysis)
        assert "src_core_engine" in tree.nodes

    def test_description_includes_function_names(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="math.py", functions=[
                    ExtractedFunction(name="add"),
                    ExtractedFunction(name="multiply"),
                ]),
            ],
        )
        tree = build_decomposition_tree(analysis)
        desc = tree.nodes["math"].description
        assert "add" in desc
        assert "multiply" in desc


# ── Smoke Test Generation ─────────────────────────────────────────


class TestGenerateSmokeTests:
    def test_generates_import_test(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="main.py", functions=[
                    ExtractedFunction(name="hello"),
                ]),
            ],
        )
        suites = generate_smoke_tests(analysis)
        assert "test_main.py" in suites
        code = suites["test_main.py"]
        assert "import importlib" in code
        assert 'importlib.import_module("main")' in code

    def test_generates_callable_checks(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="math_utils.py", functions=[
                    ExtractedFunction(name="add"),
                    ExtractedFunction(name="multiply"),
                ]),
            ],
        )
        suites = generate_smoke_tests(analysis)
        code = suites["test_math_utils.py"]
        assert "test_add_is_callable" in code
        assert "test_multiply_is_callable" in code
        assert 'getattr(mod, "add"' in code

    def test_handles_nested_modules(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="src/auth/login.py", functions=[
                    ExtractedFunction(name="authenticate"),
                ]),
            ],
        )
        suites = generate_smoke_tests(analysis)
        assert "test_src_auth_login.py" in suites
        code = suites["test_src_auth_login.py"]
        assert 'importlib.import_module("src.auth.login")' in code

    def test_skips_empty_files(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="empty.py", functions=[]),
                SourceFile(path="real.py", functions=[ExtractedFunction(name="work")]),
            ],
        )
        suites = generate_smoke_tests(analysis)
        assert not any("empty" in k for k in suites)
        assert "test_real.py" in suites

    def test_empty_codebase(self):
        analysis = CodebaseAnalysis(root_path="/tmp/test", source_files=[])
        suites = generate_smoke_tests(analysis)
        assert suites == {}

    def test_multiple_files(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            source_files=[
                SourceFile(path="a.py", functions=[ExtractedFunction(name="f1")]),
                SourceFile(path="b.py", functions=[ExtractedFunction(name="f2")]),
                SourceFile(path="c.py", functions=[ExtractedFunction(name="f3")]),
            ],
        )
        suites = generate_smoke_tests(analysis)
        assert len(suites) == 3


# ── Implementation Linking ─────────────────────────────────────────


class TestLinkExistingImplementations:
    def test_copies_source(self, tmp_path):
        # Create source file
        (tmp_path / "main.py").write_text("def hello(): pass")

        analysis = CodebaseAnalysis(
            root_path=str(tmp_path),
            source_files=[
                SourceFile(path="main.py", functions=[ExtractedFunction(name="hello")]),
            ],
        )
        tree = build_decomposition_tree(analysis)
        project = ProjectManager(tmp_path)
        project.init()

        link_existing_implementations(project, analysis, tree)

        # Check implementation was created (visible src dir)
        impl_src = tmp_path / "src" / "main" / "main.py"
        assert impl_src.exists()
        assert "hello" in impl_src.read_text()

        # Check metadata
        meta_path = tmp_path / ".pact" / "implementations" / "main" / "metadata.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["adopted"] is True
        assert meta["source_path"] == "main.py"


# ── Adoption Result ────────────────────────────────────────────────


class TestAdoptionResult:
    def test_summary_dry_run(self):
        r = AdoptionResult(components=5, total_functions=20, coverage_before=0.3, security_findings=2)
        r.dry_run = True
        text = r.summary()
        assert "Dry Run" in text
        assert "Components: 5" in text
        assert "30%" in text

    def test_summary_full_run(self):
        r = AdoptionResult(components=3, total_functions=10, coverage_before=0.5, security_findings=1)
        r.contracts_generated = 3
        r.tests_generated = 3
        r.total_cost_usd = 2.50
        text = r.summary()
        assert "Complete" in text
        assert "Contracts generated: 3" in text
        assert "$2.5000" in text
        assert "pact daemon" in text


# ── Dry Run Integration ───────────────────────────────────────────


class TestAdoptDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_creates_tree(self, tmp_path):
        (tmp_path / "main.py").write_text(textwrap.dedent("""\
            def hello(name: str) -> str:
                return f"Hello {name}"
        """))

        result = await adopt_codebase(tmp_path, dry_run=True)
        assert result.dry_run is True
        assert result.components >= 1
        assert result.total_functions >= 1
        assert result.contracts_generated == 0

        # Check project state was created
        assert (tmp_path / ".pact" / "state.json").exists()
        assert (tmp_path / "decomposition" / "tree.json").exists()
        assert (tmp_path / "task.md").exists()

    @pytest.mark.asyncio
    async def test_dry_run_no_llm(self, tmp_path):
        (tmp_path / "app.py").write_text("def run(): pass")
        result = await adopt_codebase(tmp_path, dry_run=True)
        assert result.total_cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_dry_run_state_is_paused(self, tmp_path):
        (tmp_path / "app.py").write_text("def run(): pass")
        await adopt_codebase(tmp_path, dry_run=True)

        project = ProjectManager(tmp_path)
        state = project.load_state()
        assert state.status == "paused"

    @pytest.mark.asyncio
    async def test_dry_run_security_audit_written(self, tmp_path):
        (tmp_path / "auth.py").write_text(textwrap.dedent("""\
            def check_admin(user):
                if user.is_admin:
                    return True
        """))
        result = await adopt_codebase(tmp_path, dry_run=True)
        assert (tmp_path / ".pact" / "test-gen" / "security_audit.md").exists()

    @pytest.mark.asyncio
    async def test_dry_run_generates_smoke_tests(self, tmp_path):
        (tmp_path / "math_utils.py").write_text(textwrap.dedent("""\
            def add(a, b):
                return a + b

            def multiply(a, b):
                return a * b
        """))

        result = await adopt_codebase(tmp_path, dry_run=True)
        assert result.smoke_tests_generated >= 1

        smoke_dir = tmp_path / "tests" / "smoke"
        assert smoke_dir.exists()
        test_files = list(smoke_dir.glob("test_*.py"))
        assert len(test_files) >= 1

        # Verify the generated test is valid Python
        code = test_files[0].read_text()
        assert "test_add_is_callable" in code
        assert "test_multiply_is_callable" in code

    @pytest.mark.asyncio
    async def test_smoke_tests_are_runnable(self, tmp_path):
        """Generated smoke tests should actually pass when run against the source."""
        (tmp_path / "greet.py").write_text(textwrap.dedent("""\
            def hello(name):
                return f"Hello {name}"
        """))

        result = await adopt_codebase(tmp_path, dry_run=True)
        assert result.smoke_tests_generated >= 1

        smoke_dir = tmp_path / "tests" / "smoke"
        test_file = smoke_dir / "test_greet.py"
        assert test_file.exists()

        # Run the generated tests with the source on PYTHONPATH
        import subprocess
        proc = subprocess.run(
            ["python3", "-m", "pytest", str(test_file), "-v"],
            capture_output=True, text=True,
            cwd=str(tmp_path),
            env={**__import__("os").environ, "PYTHONPATH": str(tmp_path)},
        )
        assert proc.returncode == 0, f"Smoke tests failed:\n{proc.stdout}\n{proc.stderr}"

    @pytest.mark.asyncio
    async def test_empty_project(self, tmp_path):
        result = await adopt_codebase(tmp_path, dry_run=True)
        assert result.components == 0
        assert result.total_functions == 0
        assert result.smoke_tests_generated == 0


# ── Worker Resolution Policy ──────────────────────────────────────


class TestResolveWorkers:
    """Cover the auto/off/integer parsing and the auto heuristic."""

    def test_off_string_returns_one(self):
        from pact.adopt import _resolve_workers
        assert _resolve_workers("off", 100, 100.0, 8) == 1

    def test_integer_one_returns_one(self):
        from pact.adopt import _resolve_workers
        assert _resolve_workers(1, 100, 100.0, 8) == 1
        assert _resolve_workers("1", 100, 100.0, 8) == 1

    def test_explicit_integer_honored_above_max_concurrent(self):
        # User override must not be silently clamped to max_concurrent_agents.
        from pact.adopt import _resolve_workers
        assert _resolve_workers(16, 100, 100.0, 4) == 16

    def test_zero_is_rejected(self):
        from pact.adopt import _resolve_workers
        with pytest.raises(ValueError, match=">= 1"):
            _resolve_workers(0, 100, 100.0, 4)

    def test_negative_is_rejected(self):
        from pact.adopt import _resolve_workers
        with pytest.raises(ValueError, match=">= 1"):
            _resolve_workers(-3, 100, 100.0, 4)

    def test_garbage_string_is_rejected(self):
        from pact.adopt import _resolve_workers
        with pytest.raises(ValueError, match="auto.*off.*positive integer"):
            _resolve_workers("banana", 100, 100.0, 4)

    def test_auto_capped_by_eligible_files(self):
        from pact.adopt import _resolve_workers
        # 2 files, big budget, big ceiling → 2.
        assert _resolve_workers("auto", 2, 100.0, 8) == 2

    def test_auto_capped_by_max_concurrent(self):
        from pact.adopt import _resolve_workers
        # Many files, big budget, small ceiling → ceiling.
        assert _resolve_workers("auto", 100, 100.0, 4) == 4

    def test_auto_capped_by_budget(self):
        from pact.adopt import _resolve_workers, _PER_FILE_COST_USD_ESTIMATE
        # Budget = 4× the per-file estimate → cap at 4 workers.
        budget = 4 * _PER_FILE_COST_USD_ESTIMATE
        assert _resolve_workers("auto", 100, budget, 8) == 4
        # Tiny budget caps to 1.
        assert _resolve_workers("auto", 100, _PER_FILE_COST_USD_ESTIMATE / 2, 8) == 1

    def test_auto_zero_eligible_returns_one(self):
        # Empty project must not produce 0 workers.
        from pact.adopt import _resolve_workers
        assert _resolve_workers("auto", 0, 100.0, 8) == 1

    def test_auto_zero_budget_returns_one(self):
        from pact.adopt import _resolve_workers
        # Even if budget is $0, floor of 1 worker.
        assert _resolve_workers("auto", 100, 0.0, 8) == 1


# ── Parallel Adopt Loop ───────────────────────────────────────────


def _stub_agent_factory(budget_attached: bool = False):
    """Return a constructor stub for AgentBase that doesn't import anthropic.

    CI installs only [dev] deps — the anthropic SDK is in the optional [cli]
    extra. Adopt tests that exercise the full LLM loop (with mocked LLM
    functions) still construct AgentBase, so we must stub it out.

    If budget_attached is True, the returned mock exposes an _budget attribute
    pointing to the BudgetTracker instance passed to its constructor — needed
    by tests that simulate worker spend via agent._budget.record_tokens(...).
    """
    def _make(budget, model="", backend=""):
        m = MagicMock()
        if budget_attached:
            m._budget = budget
        m.close = AsyncMock()
        return m
    return _make


class TestParallelAdopt:
    """Verify parallel mode produces correct artifacts and respects budget."""

    @pytest.fixture
    def fake_files(self, tmp_path):
        """Create a 4-file fixture that adopt will see as 4 components."""
        for i in range(4):
            (tmp_path / f"mod_{i}.py").write_text(f"def f_{i}(x): return x + {i}\n")
        return tmp_path

    @staticmethod
    def _patch_llm(contract_delay: float = 0.0, test_delay: float = 0.0):
        """Patch the two LLM functions adopt calls. Both record invocations."""
        from pact.schemas import ComponentContract, ContractTestSuite

        contract_calls = []
        test_calls = []

        async def fake_contract(agent, source, module, fn_names, tool_index=None):
            import asyncio
            contract_calls.append(module)
            if contract_delay:
                await asyncio.sleep(contract_delay)
            return ComponentContract(
                component_id="placeholder",  # adopt overwrites this
                name=module,
                description=f"Reverse-engineered {module}",
                functions=[],
            )

        async def fake_tests(agent, contract, language="python"):
            import asyncio
            test_calls.append(contract.component_id)
            if test_delay:
                await asyncio.sleep(test_delay)
            suite = ContractTestSuite(
                component_id=contract.component_id,
                contract_version=1,
                test_cases=[],
                generated_code=f"# tests for {contract.component_id}\n",
            )
            return suite, "research", "plan"

        return fake_contract, fake_tests, contract_calls, test_calls

    @pytest.mark.asyncio
    async def test_serial_path_unchanged_with_workers_one(self, fake_files):
        """workers=1 must produce the same artifacts as 'off' on the same input."""
        fake_contract, fake_tests, c_calls, t_calls = self._patch_llm()
        with patch("pact.adopt.reverse_engineer_contract", fake_contract), \
             patch("pact.agents.test_author.author_tests", fake_tests), \
             patch("pact.adopt.AgentBase", _stub_agent_factory()):
            result = await adopt_codebase(
                fake_files, budget=10.0, workers=1, max_concurrent_agents=4,
            )
        assert result.contracts_generated == 4
        assert result.tests_generated == 4
        # Per-component artifacts present.
        for i in range(4):
            assert (fake_files / "contracts" / f"mod_{i}" / "interface.json").exists()

    @pytest.mark.asyncio
    async def test_parallel_produces_same_component_set_as_serial(self, tmp_path):
        """workers=4 must write contracts and tests for the same component IDs as workers=1."""

        def _make_fixture(root):
            for i in range(4):
                (root / f"mod_{i}.py").write_text(f"def f_{i}(x): return x + {i}\n")

        # Run serial on its own tmp dir.
        serial_dir = tmp_path / "serial"
        serial_dir.mkdir()
        _make_fixture(serial_dir)
        fake_contract, fake_tests, _, _ = self._patch_llm()
        with patch("pact.adopt.reverse_engineer_contract", fake_contract), \
             patch("pact.agents.test_author.author_tests", fake_tests), \
             patch("pact.adopt.AgentBase", _stub_agent_factory()):
            r_serial = await adopt_codebase(
                serial_dir, budget=10.0, workers=1, max_concurrent_agents=4,
            )

        # Run parallel on a fresh disjoint tmp dir.
        parallel_dir = tmp_path / "parallel"
        parallel_dir.mkdir()
        _make_fixture(parallel_dir)
        fake_contract, fake_tests, _, _ = self._patch_llm()
        with patch("pact.adopt.reverse_engineer_contract", fake_contract), \
             patch("pact.agents.test_author.author_tests", fake_tests), \
             patch("pact.adopt.AgentBase", _stub_agent_factory()):
            r_parallel = await adopt_codebase(
                parallel_dir, budget=10.0, workers=4, max_concurrent_agents=4,
            )

        # Same counts.
        assert r_serial.contracts_generated == r_parallel.contracts_generated == 4
        assert r_serial.tests_generated == r_parallel.tests_generated == 4

        # Same set of contract + test component IDs (history dir excluded —
        # filenames are timestamped so they vary by wall-clock).
        def _component_dirs(root, sub):
            d = root / sub
            return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []

        assert _component_dirs(serial_dir, "contracts") == \
               _component_dirs(parallel_dir, "contracts")
        assert _component_dirs(serial_dir, "tests") == \
               _component_dirs(parallel_dir, "tests")

    @pytest.mark.asyncio
    async def test_parallel_actually_overlaps_calls(self, fake_files):
        """Under workers=4 with delayed LLM, peak concurrency exceeds 1."""
        import asyncio
        in_flight = 0
        peak = 0
        lock = asyncio.Lock()

        from pact.schemas import ComponentContract, ContractTestSuite

        async def tracking_contract(agent, source, module, fn_names, tool_index=None):
            nonlocal in_flight, peak
            async with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            await asyncio.sleep(0.05)
            async with lock:
                in_flight -= 1
            return ComponentContract(component_id="x", name="x", description="", functions=[])

        async def fake_tests(agent, contract, language="python"):
            return (
                ContractTestSuite(
                    component_id=contract.component_id,
                    contract_version=1,
                    test_cases=[], generated_code="",
                ),
                "r", "p",
            )

        with patch("pact.adopt.reverse_engineer_contract", tracking_contract), \
             patch("pact.agents.test_author.author_tests", fake_tests), \
             patch("pact.adopt.AgentBase", _stub_agent_factory()):
            await adopt_codebase(
                fake_files, budget=10.0, workers=4, max_concurrent_agents=4,
            )
        assert peak >= 2, f"Expected concurrent contract calls, peak was {peak}"

    @pytest.mark.asyncio
    async def test_resume_skip_under_parallel(self, fake_files):
        """Pre-existing contracts+suites must be skipped (no LLM call) under workers=4.

        Seeds 2 components' artifacts on disk before adopt runs, then runs
        with workers=4 and asserts only the missing 2 trigger LLM calls.
        """
        from pact.schemas import ComponentContract, ContractTestSuite

        # Seed contracts + suites for mod_0 and mod_1 (so they should be skipped).
        project = ProjectManager(fake_files)
        project.init(budget=10.0)
        for cid in ("mod_0", "mod_1"):
            project.save_contract(ComponentContract(
                component_id=cid, name=cid, description="seeded", functions=[],
            ))
            project.save_test_suite(ContractTestSuite(
                component_id=cid, contract_version=1, test_cases=[],
                generated_code="# seeded\n",
            ))

        fake_contract, fake_tests, c_calls, t_calls = self._patch_llm()
        with patch("pact.adopt.reverse_engineer_contract", fake_contract), \
             patch("pact.agents.test_author.author_tests", fake_tests), \
             patch("pact.adopt.AgentBase", _stub_agent_factory()):
            result = await adopt_codebase(
                fake_files, budget=10.0, workers=4, max_concurrent_agents=4,
            )

        # Only mod_2 and mod_3 should have triggered LLM calls.
        assert len(c_calls) == 2, f"Expected 2 contract calls, got {c_calls}"
        assert len(t_calls) == 2, f"Expected 2 test calls, got {t_calls}"
        assert sorted(c_calls) == ["mod_2", "mod_3"]
        # All 4 reported in result (2 resumed + 2 new).
        assert result.contracts_generated == 4
        assert result.tests_generated == 4

    @pytest.mark.asyncio
    async def test_bounded_overshoot_under_tight_budget(self, fake_files):
        """With a budget that allows fewer files than workers, overshoot is bounded."""
        # 4 files, 4 workers, budget that records ~$0.30 per file.
        # Each call charges via record_tokens. With workers=4 all four start
        # before any record, so we expect up to 4 contracts to complete.
        from pact.schemas import ComponentContract, ContractTestSuite

        async def expensive_contract(agent, source, module, fn_names, tool_index=None):
            # Charge $0.30 per call (3M input tokens at sonnet-4-5 rates).
            agent._budget.record_tokens(100_000, 0)  # 100k * $3/M = $0.30
            return ComponentContract(component_id="x", name="x", description="", functions=[])

        async def fake_tests(agent, contract, language="python"):
            agent._budget.record_tokens(100_000, 0)
            return (
                ContractTestSuite(
                    component_id=contract.component_id,
                    contract_version=1,
                    test_cases=[], generated_code="",
                ),
                "r", "p",
            )

        with patch("pact.adopt.reverse_engineer_contract", expensive_contract), \
             patch("pact.agents.test_author.author_tests", fake_tests), \
             patch("pact.adopt.AgentBase", _stub_agent_factory(budget_attached=True)):
            result = await adopt_codebase(
                fake_files, budget=0.50, workers=4, max_concurrent_agents=4,
                model="claude-sonnet-4-5-20250929",
            )

        # Hard budget is $0.50, max single-call cost is $0.30 (one record_tokens).
        # With 4 workers and 2 calls per file (contract + tests), strictly:
        # Actual spend ≤ budget + workers × max_single_call = 0.50 + 4 × 0.30 = $1.70.
        # The bounded-overshoot invariant holds.
        assert result.total_cost_usd <= 0.50 + 4 * 0.30, (
            f"Overshoot {result.total_cost_usd} exceeds bound"
        )
        # And we don't process all 4 files (some workers should bail on
        # is_exceeded() before their second call).
        assert result.tests_generated <= result.contracts_generated

    @pytest.mark.asyncio
    async def test_recoverable_api_error_does_not_kill_siblings(self, fake_files):
        """API-error in one worker must not abort sibling workers under parallel."""
        from pact.schemas import ComponentContract, ContractTestSuite

        async def flaky_contract(agent, source, module, fn_names, tool_index=None):
            if "mod_1" in module:
                # Helper's recoverable-error sniff matches "APIStatusError"
                # in the exception type name OR "stalled"/"500" in the message.
                raise RuntimeError("APIStatusError 500: simulated stall")
            return ComponentContract(
                component_id="x", name=module, description=f"ok {module}", functions=[],
            )

        async def fake_tests(agent, contract, language="python"):
            return (
                ContractTestSuite(
                    component_id=contract.component_id,
                    contract_version=1,
                    test_cases=[], generated_code="",
                ),
                "r", "p",
            )

        with patch("pact.adopt.reverse_engineer_contract", flaky_contract), \
             patch("pact.agents.test_author.author_tests", fake_tests), \
             patch("pact.adopt.AgentBase", _stub_agent_factory()):
            result = await adopt_codebase(
                fake_files, budget=10.0, workers=4, max_concurrent_agents=4,
            )
        # 3 of 4 files succeed; one is skipped.
        assert result.contracts_generated == 3
        assert result.tests_generated == 3

    @pytest.mark.asyncio
    async def test_unexpected_exception_in_parallel_is_propagated(self, fake_files):
        """Programmer errors in workers must propagate (match serial fail-fast)."""
        from pact.schemas import ComponentContract, ContractTestSuite

        async def buggy_contract(agent, source, module, fn_names, tool_index=None):
            if "mod_1" in module:
                # A truly unexpected error — not API-shaped, helper won't catch it.
                raise KeyError("simulated programming bug")
            return ComponentContract(
                component_id="x", name=module, description=f"ok {module}", functions=[],
            )

        async def fake_tests(agent, contract, language="python"):
            return (
                ContractTestSuite(
                    component_id=contract.component_id, contract_version=1,
                    test_cases=[], generated_code="",
                ),
                "r", "p",
            )

        with patch("pact.adopt.reverse_engineer_contract", buggy_contract), \
             patch("pact.agents.test_author.author_tests", fake_tests), \
             patch("pact.adopt.AgentBase", _stub_agent_factory()):
            with pytest.raises(KeyError, match="programming bug"):
                await adopt_codebase(
                    fake_files, budget=10.0, workers=4, max_concurrent_agents=4,
                )
