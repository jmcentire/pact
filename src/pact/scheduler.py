"""Casual-pace polling scheduler.

Poll-based, not event-loop. Agents invoked for focused bursts,
state fully persisted between bursts. Fundamentally different from
swarm's synchronous pipeline.

Properties:
- Agents invoked for focused bursts, not left running
- State fully persisted between bursts (.pact/state.json)
- Humans can inspect state at any time
- Work can pause/resume across days
- Token efficient — agents only invoked when work exists
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from pact.agents.base import AgentBase
from pact.budget import BudgetExceeded, BudgetTracker
from pact.config import (
    BuildMode,
    GlobalConfig,
    ProjectConfig,
    resolve_backend,
    resolve_build_mode,
    resolve_model,
    resolve_parallel_config,
)
from pact.decomposer import decompose_and_contract, run_interview
from pact.diagnoser import determine_recovery_action, diagnose_failure
from pact.events import EventBus, PactEvent
from pact.implementer import implement_all, implement_all_iterative, implement_component_iterative
from pact.integrator import integrate_all, integrate_all_iterative
from pact.lifecycle import advance_phase, format_run_summary
from pact.project import ProjectManager
from pact.schemas import ComponentTask, DecompositionTree, RunState, TestResults

logger = logging.getLogger(__name__)

# ── Phase Classification ──────────────────────────────────────────

PLANNING_PHASES = {"interview", "shape", "decompose", "diagnose"}
GENERATION_PHASES = {"implement", "integrate"}


def detect_cascade(tree: "DecompositionTree", failed_set: set[str]) -> int:
    """Detect cascade events from the tree structure.

    A cascade event is a unique pair where:
    - A failed component's parent also failed (propagation up)
    - Two failed siblings share a parent (lateral spread)

    Uses frozenset pairs to avoid double-counting (a->b and b->a
    are the same cascade event).

    Returns count of unique cascade events detected.
    """
    seen_pairs: set[frozenset[str]] = set()

    for cid in failed_set:
        # Check parent propagation
        parent = tree.parent_of(cid)
        if parent and parent.component_id in failed_set:
            pair = frozenset({cid, parent.component_id})
            seen_pairs.add(pair)

        # Check sibling spread
        if parent:
            siblings = tree.children_of(parent.component_id)
            for sib in siblings:
                if sib.component_id != cid and sib.component_id in failed_set:
                    pair = frozenset({cid, sib.component_id})
                    seen_pairs.add(pair)

    return len(seen_pairs)


@dataclass
class SystemicPattern:
    """Detected pattern of identical failures across components."""
    pattern_type: str          # "zero_tests", "import_error", "timeout", "identical_failure"
    affected_components: list[str] = field(default_factory=list)
    sample_error: str = ""
    recommendation: str = ""


def detect_systemic_failure(
    results: dict[str, TestResults],
    threshold: int = 3,
) -> SystemicPattern | None:
    """Detect when multiple components fail with the same root cause.

    Args:
        results: Map of component_id to TestResults
        threshold: Minimum components with same failure to trigger detection

    Returns:
        SystemicPattern if detected, None if failures are heterogeneous.

    Patterns detected:
    - All 0/0 (total=0, passed=0) -> "zero_tests" (environment/PATH issue)
    - All same error message in failure_details -> "identical_failure"
    - All have errors but no passed tests -> "import_error" (likely missing dependency)
    """
    if len(results) < threshold:
        return None

    # Pattern 1: All zero-zero (no tests collected)
    zero_zero = [
        cid for cid, r in results.items()
        if r.total == 0 and r.passed == 0
    ]
    if len(zero_zero) >= threshold:
        sample = ""
        for cid in zero_zero:
            r = results[cid]
            if r.failure_details:
                sample = r.failure_details[0].error_message
                break
        return SystemicPattern(
            pattern_type="zero_tests",
            affected_components=zero_zero,
            sample_error=sample or "No tests collected (0 total, 0 passed)",
            recommendation="Check PATH and PYTHONPATH in test environment. Likely pytest not found or test collection failed globally.",
        )

    # Pattern 2: All have errors, no passes (likely import/collection error)
    all_error_no_pass = [
        cid for cid, r in results.items()
        if r.errors > 0 and r.passed == 0
    ]
    if len(all_error_no_pass) >= threshold:
        # Check if errors share a common message
        error_msgs = []
        for cid in all_error_no_pass:
            r = results[cid]
            for fd in r.failure_details:
                if fd.error_message:
                    error_msgs.append(fd.error_message)
                    break

        sample = error_msgs[0] if error_msgs else "Collection/import error"
        return SystemicPattern(
            pattern_type="import_error",
            affected_components=all_error_no_pass,
            sample_error=sample,
            recommendation="Check for missing dependencies or import errors affecting all components.",
        )

    # Pattern 3: Identical failure messages across components
    failed = {
        cid: r for cid, r in results.items()
        if not r.all_passed and r.failure_details
    }
    if len(failed) >= threshold:
        # Group by first failure message
        msg_groups: dict[str, list[str]] = {}
        for cid, r in failed.items():
            msg = r.failure_details[0].error_message if r.failure_details else ""
            if msg:
                msg_groups.setdefault(msg, []).append(cid)

        for msg, cids in msg_groups.items():
            if len(cids) >= threshold:
                return SystemicPattern(
                    pattern_type="identical_failure",
                    affected_components=cids,
                    sample_error=msg,
                    recommendation=f"All {len(cids)} components failed with identical error. Fix the root cause rather than individual components.",
                )

    return None


class Scheduler:
    """Casual-pace scheduler — poll, burst, persist, sleep."""

    def __init__(
        self,
        project: ProjectManager,
        global_config: GlobalConfig,
        project_config: ProjectConfig,
        budget: BudgetTracker,
        event_bus: EventBus | None = None,
    ) -> None:
        self.project = project
        self.global_config = global_config
        self.project_config = project_config
        self.budget = budget
        self.event_bus = event_bus or EventBus(
            project.project_dir, global_config, project_config,
        )
        self.check_interval = (
            project_config.check_interval
            or global_config.check_interval
        )
        self._standards_brief: str = ""

    @property
    def build_mode(self) -> BuildMode:
        """Resolve the effective build mode."""
        return resolve_build_mode(self.project_config, self.global_config)

    def _make_agent(self, role: str) -> AgentBase:
        """Create an agent configured for a specific role."""
        model = resolve_model(role, self.project_config, self.global_config)
        backend = resolve_backend(role, self.project_config, self.global_config)
        self.budget.set_model_pricing(model)
        return AgentBase(budget=self.budget, model=model, backend=backend)

    async def run_once(self) -> RunState:
        """Run a single burst of work. Returns updated state."""
        state = self.project.load_state()

        if state.status in ("completed", "failed", "budget_exceeded"):
            return state

        try:
            state = await self._do_burst(state)
        except BudgetExceeded:
            state.status = "budget_exceeded"
            state.pause_reason = "Budget cap reached"
            state.completed_at = datetime.now().isoformat()
            logger.warning("Budget exceeded for %s", state.id)
        except Exception as e:
            state.fail(f"Unexpected error: {e}")
            logger.exception("Scheduler error for %s", state.id)

        # Sync budget tracker totals to persistent state
        in_tok, out_tok = self.budget.project_tokens
        state.total_tokens = in_tok + out_tok
        state.total_cost_usd = self.budget.project_spend

        state.last_check_in = datetime.now().isoformat()
        self.project.save_state(state)
        return state

    async def run_forever(self) -> RunState:
        """Run the scheduler loop until completion or failure."""
        while True:
            state = await self.run_once()
            if state.status in ("completed", "failed", "budget_exceeded"):
                logger.info("Run complete: %s", format_run_summary(state))
                return state
            if state.status == "paused":
                logger.info("Run paused: %s", state.pause_reason)
                return state
            await asyncio.sleep(self.check_interval)

    async def _do_burst(self, state: RunState) -> RunState:
        """Execute one phase of work."""
        sops = self.project.load_sops()
        phase = state.phase

        # Snapshot token counts before phase dispatch
        pre_in, pre_out = self.budget.project_tokens

        # Resolve context_max_chars from config
        context_max_chars = (
            self.project_config.context_max_chars
            if self.project_config.context_max_chars is not None
            else self.global_config.context_max_chars
        )

        # Gather external context and learnings for agent phases
        external_context = ""
        learnings_str = ""
        if phase in ("implement", "integrate", "diagnose"):
            try:
                from pact.human.context import gather_context
                ctx = await gather_context(self.event_bus, phase=phase)
                external_context = ctx.format_for_prompt(max_chars=context_max_chars)
            except Exception:
                pass

            try:
                raw_learnings = self.project.load_learnings()
                if raw_learnings:
                    from pact.agents.base import AgentBase
                    learnings_str = AgentBase.with_learnings(None, raw_learnings)
            except Exception:
                pass

        await self.event_bus.emit(PactEvent(
            kind="phase_start",
            project_name=self.project.project_dir.name,
            detail=phase,
        ))

        if phase == "interview":
            state = await self._phase_interview(state, sops)
        elif phase == "shape":
            state = await self._phase_shape(state, sops)
        elif phase == "decompose":
            state = await self._phase_decompose(state, sops)
        elif phase == "contract":
            # Contract phase is part of decompose
            advance_phase(state)
        elif phase == "implement":
            state = await self._phase_implement(
                state, sops,
                external_context=external_context,
                learnings=learnings_str,
            )
        elif phase == "integrate":
            state = await self._phase_integrate(
                state, sops,
                external_context=external_context,
                learnings=learnings_str,
            )
        elif phase == "diagnose":
            state = await self._phase_diagnose(state, sops)
        elif phase == "complete":
            state.complete()
            await self.event_bus.emit(PactEvent(
                kind="run_complete",
                project_name=self.project.project_dir.name,
                detail="completed",
            ))

        # Emit phase_complete if we advanced
        if state.phase != phase and state.status == "active":
            await self.event_bus.emit(PactEvent(
                kind="phase_complete",
                project_name=self.project.project_dir.name,
                detail=phase,
                component_id=str(len(self.project.load_tree().nodes)) if phase == "decompose" and self.project.load_tree() else "",
            ))

        # ── Health instrumentation (never blocks the pipeline) ──
        try:
            from pact.health import HealthMetrics

            post_in, post_out = self.budget.project_tokens
            delta_in = post_in - pre_in
            delta_out = post_out - pre_out

            metrics = HealthMetrics.from_dict(state.health_snapshot)

            # Record per-phase tokens
            if delta_in > 0 or delta_out > 0:
                metrics.record_phase_tokens(phase, delta_in, delta_out)

            # Categorize as planning or generation
            if phase in PLANNING_PHASES and (delta_in > 0 or delta_out > 0):
                metrics.record_planning(delta_in, delta_out)
            elif phase in GENERATION_PHASES and (delta_in > 0 or delta_out > 0):
                metrics.record_generation(delta_in, delta_out)

            # Sync total spend from budget tracker
            metrics.total_spend = self.budget.project_spend
            metrics.budget_cap = self.budget.per_project_cap

            state.health_snapshot = metrics.to_dict()

            # Run health check and apply remedies
            state = self._check_health_and_remediate(state)
        except Exception:
            pass  # Health instrumentation never blocks the pipeline

        # Budget warning at 80%
        if self.budget.spend_percentage >= 80.0 and state.status == "active":
            await self.event_bus.emit(PactEvent(
                kind="budget_warning",
                project_name=self.project.project_dir.name,
                detail=f"{self.budget.spend_percentage:.0f}% spent (${self.budget.project_spend:.2f} of ${self.budget.per_project_cap:.2f})",
            ))

        # Emit human_needed when paused
        if state.status == "paused":
            await self.event_bus.emit(PactEvent(
                kind="human_needed",
                project_name=self.project.project_dir.name,
                detail=state.pause_reason,
            ))

        return state

    async def _phase_interview(self, state: RunState, sops: str) -> RunState:
        """Run interview phase."""
        existing = self.project.load_interview()
        if existing and existing.approved:
            advance_phase(state)
            return state

        agent = self._make_agent("decomposer")
        try:
            task = self.project.load_task()
            result = await run_interview(agent, task, sops)
            self.project.save_interview(result)
            self.project.append_audit("interview", f"{len(result.questions)} questions")

            if not result.questions:
                result.approved = True
                self.project.save_interview(result)
                advance_phase(state)
            else:
                state.interview_result = result
                state.pause("Interview questions pending — waiting for user answers")
        finally:
            await agent.close()

        return state

    async def _phase_shape(self, state: RunState, sops: str) -> RunState:
        """Run optional shaping phase (Shape Up methodology).

        Skips immediately if shaping is disabled in config.
        """
        shaping_enabled = (
            self.project_config.shaping
            if self.project_config.shaping is not None
            else self.global_config.shaping
        )
        if not shaping_enabled:
            advance_phase(state)
            return state

        # Already have a pitch? Skip.
        existing_pitch = self.project.load_pitch()
        if existing_pitch is not None:
            advance_phase(state)
            return state

        from pact.agents.shaper import Shaper

        agent = self._make_agent("decomposer")
        try:
            depth = (
                self.project_config.shaping_depth
                or self.global_config.shaping_depth
            )
            rigor = (
                self.project_config.shaping_rigor
                or self.global_config.shaping_rigor
            )
            budget_pct = (
                self.project_config.shaping_budget_pct
                if self.project_config.shaping_budget_pct is not None
                else self.global_config.shaping_budget_pct
            )

            shaper = Shaper(
                agent=agent,
                shaping_depth=depth,
                shaping_rigor=rigor,
                shaping_budget_pct=budget_pct,
            )

            task = self.project.load_task()
            interview = self.project.load_interview()
            interview_context = ""
            if interview:
                answers = "\n".join(
                    f"  Q: {q}\n  A: {interview.user_answers.get(q, 'No answer')}"
                    for q in interview.questions
                )
                interview_context = f"Interview:\n{answers}"

            pitch = await shaper.shape(
                task=task,
                sops=sops,
                interview_context=interview_context,
                budget_used=self.budget.project_spend,
                budget_total=self.budget.per_project_cap,
            )
            self.project.save_pitch(pitch)
            self.project.append_audit("shape", f"depth={depth}, appetite={pitch.appetite}")
            advance_phase(state)
        except Exception as e:
            logger.error("Shaping failed: %s", e)
            self.project.append_audit("shape_error", str(e))
            # On failure, skip shaping and proceed to decompose
            advance_phase(state)
        finally:
            await agent.close()

        return state

    async def _phase_decompose(self, state: RunState, sops: str) -> RunState:
        """Run decomposition + contract + test generation."""
        agent = self._make_agent("decomposer")
        try:
            gate = await decompose_and_contract(
                agent, self.project, sops=sops,
                max_plan_revisions=self.global_config.max_plan_revisions,
                build_mode=self.build_mode.value,
            )

            if gate.passed:
                # Record decompose-phase artifact counts for health.
                # Use delta against previously-counted artifacts to avoid
                # inflation when diagnose loops back to decompose.
                try:
                    from pact.health import HealthMetrics
                    metrics = HealthMetrics.from_dict(state.health_snapshot)
                    contracts = self.project.load_all_contracts()
                    test_suites = self.project.load_all_test_suites()
                    new_contracts = max(0, len(contracts) - metrics.contracts_produced)
                    new_tests = max(0, len(test_suites) - metrics.tests_produced)
                    metrics.contracts_produced += new_contracts
                    metrics.tests_produced += new_tests
                    state.health_snapshot = metrics.to_dict()
                except Exception:
                    pass  # Health recording never blocks

                # Set up component tasks
                tree = self.project.load_tree()
                if tree:
                    state.component_tasks = [
                        ComponentTask(component_id=cid)
                        for cid in tree.topological_order()
                    ]

                    # Auto-generate task list
                    try:
                        from pact.task_list import generate_task_list
                        contracts = self.project.load_all_contracts()
                        test_suites = self.project.load_all_test_suites()
                        task_list = generate_task_list(
                            tree, contracts, test_suites,
                            self.project.project_dir.name,
                        )
                        self.project.save_task_list(task_list)
                        self.project.append_audit(
                            "tasks_generated", f"{task_list.total} tasks",
                        )
                    except Exception as e:
                        logger.debug("Task list generation failed: %s", e)

                    # Collect and persist global standards
                    try:
                        from pact.standards import collect_standards, render_standards_brief
                        standards = collect_standards(
                            contracts, sops,
                            config_env=self.project_config.environment or self.global_config.environment,
                        )
                        self._standards_brief = render_standards_brief(standards)
                        # Persist for inspection
                        import json as _json
                        standards_path = self.project._pact_dir / "standards.json"
                        standards_path.write_text(_json.dumps(standards.to_dict(), indent=2))
                    except Exception as e:
                        logger.debug("Standards collection failed: %s", e)

                pcfg = resolve_parallel_config(self.project_config, self.global_config)
                if pcfg.plan_only:
                    # Plan-only mode: stop after contracts + tests are generated
                    state.pause(
                        "Plan-only mode: decomposition and contracts complete. "
                        "Use 'pact build <project> <component_id>' to implement "
                        "specific components, or disable plan_only to implement all."
                    )
                else:
                    advance_phase(state)  # -> contract
                    advance_phase(state)  # -> implement
            else:
                state.fail(f"Contract validation failed: {gate.reason}")
        finally:
            await agent.close()

        return state

    def _make_agent_factory(self, role: str):
        """Create a factory that produces fresh agents for parallel/competitive modes."""
        def factory() -> AgentBase:
            return self._make_agent(role)
        return factory

    async def _phase_implement(
        self, state: RunState, sops: str,
        target_components: set[str] | None = None,
        external_context: str = "",
        learnings: str = "",
    ) -> RunState:
        """Implement all leaf components."""
        tree = self.project.load_tree()
        if not tree:
            state.fail("No decomposition tree found")
            return state

        # Inject standards into external context
        if self._standards_brief:
            external_context = self._standards_brief + "\n\n" + external_context if external_context else self._standards_brief

        max_attempts = (
            self.project_config.max_implementation_attempts
            or self.global_config.max_implementation_attempts
        )

        pcfg = resolve_parallel_config(self.project_config, self.global_config)

        # Detect if code_author backend supports iterative implementation
        code_author_backend = resolve_backend(
            "code_author", self.project_config, self.global_config,
        )
        code_author_model = resolve_model(
            "code_author", self.project_config, self.global_config,
        )

        if code_author_backend in ("claude_code", "claude_code_team"):
            # Iterative path: Claude Code writes, tests, fixes in a loop
            logger.info(
                "Using iterative Claude Code implementation (%s, %s)",
                code_author_backend, code_author_model,
            )
            results = await implement_all_iterative(
                project=self.project,
                tree=tree,
                budget=self.budget,
                model=code_author_model,
                sops=sops,
                parallel=pcfg.parallel,
                max_concurrent=pcfg.max_concurrent,
                target_components=target_components,
                external_context=external_context,
                learnings=learnings,
                timeout=self.global_config.autonomous_timeout or 1800,
            )
        else:
            # API-based path: structured extraction with blind retries
            agent = self._make_agent("code_author")
            try:
                results = await implement_all(
                    agent, self.project, tree,
                    max_attempts=max_attempts,
                    sops=sops,
                    max_plan_revisions=self.global_config.max_plan_revisions,
                    parallel=pcfg.parallel,
                    competitive=pcfg.competitive,
                    competitive_agents=pcfg.agent_count,
                    max_concurrent=pcfg.max_concurrent,
                    agent_factory=self._make_agent_factory("code_author") if (pcfg.parallel or pcfg.competitive) else None,
                    target_components=target_components,
                    external_context=external_context,
                    learnings=learnings,
                )
            finally:
                await agent.close()

        # --- Common post-implementation logic (both paths) ---

        # Record health metrics from implementation results
        try:
            from pact.health import HealthMetrics
            metrics = HealthMetrics.from_dict(state.health_snapshot)

            for cid, r in results.items():
                if r.all_passed:
                    metrics.record_attempt(success=True)
                    metrics.implementations_produced += 1
                    # tests_produced already counted in _phase_decompose
                else:
                    metrics.record_attempt(success=False)
                    metrics.record_component_failure(cid)
                if r.passed > 0 or r.failed > 0:
                    metrics.record_test_run(r.passed, r.failed)

            # Detect cascades — include previously-failed tree nodes
            # so cross-phase cascades (implement → integrate) are visible.
            # Use max() not accumulation: detect_cascade returns the total
            # cascade picture at this point in time. Accumulating would
            # double-count persistent cascades across bursts.
            failed_set = {cid for cid, r in results.items() if not r.all_passed}
            if tree:
                for nid, node in tree.nodes.items():
                    if node.implementation_status == "failed":
                        failed_set.add(nid)
            if failed_set and tree:
                cascades = detect_cascade(tree, failed_set)
                metrics.cascade_events = max(metrics.cascade_events, cascades)

            state.health_snapshot = metrics.to_dict()
        except Exception:
            pass  # Health recording never blocks

        # Update task list statuses
        try:
            from pact.task_list import update_task_status
            task_list = self.project.load_task_list()
            if task_list:
                tree = self.project.load_tree()
                if tree:
                    for cid, r in results.items():
                        node = tree.nodes.get(cid)
                        impl_status = "tested" if r.all_passed else "failed"
                        update_task_status(task_list, cid, impl_status)
                    self.project.save_task_list(task_list)
        except Exception as e:
            logger.debug("Task list update failed: %s", e)

        # Check for systemic failure pattern
        systemic = detect_systemic_failure(results)
        if systemic:
            logger.warning(
                "Systemic failure detected: %s (%d components). %s",
                systemic.pattern_type,
                len(systemic.affected_components),
                systemic.recommendation,
            )
            state.pause(
                f"Systemic failure: {systemic.pattern_type} "
                f"({len(systemic.affected_components)} components). "
                f"{systemic.recommendation}"
            )
            self.project.save_state(state)
            self.project.append_audit(
                "systemic_failure",
                f"{systemic.pattern_type}: {systemic.sample_error[:200]}",
            )
            return state

        # Emit per-component events
        for cid, r in results.items():
            if r.all_passed:
                await self.event_bus.emit(PactEvent(
                    kind="component_complete",
                    project_name=self.project.project_dir.name,
                    component_id=cid,
                    test_results=r,
                ))
            else:
                await self.event_bus.emit(PactEvent(
                    kind="component_failed",
                    project_name=self.project.project_dir.name,
                    component_id=cid,
                    detail=f"{r.failed}/{r.total} tests failed",
                    test_results=r,
                ))

        # Check for failures
        failed = [cid for cid, r in results.items() if not r.all_passed]
        if failed:
            state.phase = "diagnose"
            state.pause_reason = f"Components failed: {', '.join(failed)}"
        else:
            advance_phase(state)  # -> integrate

        return state

    async def _phase_integrate(
        self, state: RunState, sops: str,
        external_context: str = "",
        learnings: str = "",
    ) -> RunState:
        """Integrate all non-leaf components."""
        tree = self.project.load_tree()
        if not tree:
            state.fail("No decomposition tree found")
            return state

        # Inject standards into external context
        if self._standards_brief:
            external_context = self._standards_brief + "\n\n" + external_context if external_context else self._standards_brief

        # Check if there are any non-leaf components
        non_leaves = [n for n in tree.nodes.values() if n.children]
        if not non_leaves:
            # No integration needed — single component or all leaves
            advance_phase(state)  # -> complete
            state.complete()
            return state

        pcfg = resolve_parallel_config(self.project_config, self.global_config)

        code_author_backend = resolve_backend(
            "code_author", self.project_config, self.global_config,
        )
        code_author_model = resolve_model(
            "code_author", self.project_config, self.global_config,
        )

        if code_author_backend in ("claude_code", "claude_code_team"):
            results = await integrate_all_iterative(
                project=self.project,
                tree=tree,
                budget=self.budget,
                model=code_author_model,
                sops=sops,
                parallel=pcfg.parallel,
                max_concurrent=pcfg.max_concurrent,
                external_context=external_context,
                learnings=learnings,
            )
        else:
            agent = self._make_agent("code_author")
            try:
                results = await integrate_all(
                    agent, self.project, tree,
                    max_attempts=self.global_config.max_implementation_attempts,
                    sops=sops,
                    parallel=pcfg.parallel,
                    max_concurrent=pcfg.max_concurrent,
                    agent_factory=self._make_agent_factory("code_author") if pcfg.parallel else None,
                )
            finally:
                await agent.close()

        # Record health metrics from integration results
        try:
            from pact.health import HealthMetrics
            metrics = HealthMetrics.from_dict(state.health_snapshot)

            for cid, r in results.items():
                if r.all_passed:
                    metrics.record_attempt(success=True)
                    metrics.implementations_produced += 1
                else:
                    metrics.record_attempt(success=False)
                    metrics.record_component_failure(cid)
                if r.passed > 0 or r.failed > 0:
                    metrics.record_test_run(r.passed, r.failed)

            # Detect cascades — include previously-failed tree nodes
            # so implement→integrate cascades are visible.
            # Use max() not accumulation to avoid double-counting
            # persistent cascades across bursts.
            failed_set = {cid for cid, r in results.items() if not r.all_passed}
            if tree:
                for nid, node in tree.nodes.items():
                    if node.implementation_status == "failed":
                        failed_set.add(nid)
            if failed_set and tree:
                cascades = detect_cascade(tree, failed_set)
                metrics.cascade_events = max(metrics.cascade_events, cascades)

            state.health_snapshot = metrics.to_dict()
        except Exception:
            pass  # Health recording never blocks

        # Update task list statuses for integration results
        try:
            from pact.task_list import update_task_status
            task_list = self.project.load_task_list()
            if task_list:
                for cid, r in results.items():
                    impl_status = "tested" if r.all_passed else "failed"
                    update_task_status(task_list, cid, impl_status)
                self.project.save_task_list(task_list)
        except Exception as e:
            logger.debug("Task list update failed: %s", e)

        failed = [cid for cid, r in results.items() if not r.all_passed]
        if failed:
            state.phase = "diagnose"
            state.pause_reason = f"Integration failed: {', '.join(failed)}"
        else:
            state.complete()

        return state

    async def build_component(
        self, component_id: str,
        competitive: bool = False,
        num_agents: int = 2,
    ) -> RunState:
        """Build (or rebuild) a specific component.

        Archives any existing implementation as informational context,
        then implements the component against its contract.
        """
        state = self.project.load_state()
        # Snapshot tokens before build for delta measurement
        pre_in, pre_out = self.budget.project_tokens

        tree = self.project.load_tree()
        if not tree:
            state.fail("No decomposition tree found")
            self.project.save_state(state)
            return state

        node = tree.nodes.get(component_id)
        if not node:
            state.fail(f"Component not found: {component_id}")
            self.project.save_state(state)
            return state

        sops = self.project.load_sops()
        contracts = self.project.load_all_contracts()
        test_suites = self.project.load_all_test_suites()

        if component_id not in contracts:
            state.fail(f"No contract for component: {component_id}")
            self.project.save_state(state)
            return state
        if component_id not in test_suites:
            state.fail(f"No test suite for component: {component_id}")
            self.project.save_state(state)
            return state

        # Archive current implementation as context for new agent
        archive_id = self.project.archive_current_impl(
            component_id, reason="Rebuilt via cf build",
        )
        if archive_id:
            self.project.append_audit(
                "archive",
                f"Archived {component_id} as {archive_id} for rebuild",
            )
            logger.info("Archived existing impl as %s", archive_id)

        max_attempts = (
            self.project_config.max_implementation_attempts
            or self.global_config.max_implementation_attempts
        )

        contract = contracts[component_id]
        dep_contracts = {
            dep_id: contracts[dep_id]
            for dep_id in contract.dependencies
            if dep_id in contracts
        }

        # Detect backend for routing
        code_author_backend = resolve_backend(
            "code_author", self.project_config, self.global_config,
        )
        code_author_model = resolve_model(
            "code_author", self.project_config, self.global_config,
        )

        if code_author_backend in ("claude_code", "claude_code_team") and not competitive:
            # Iterative path: Claude Code writes, tests, fixes in a loop
            logger.info(
                "Building %s iteratively via Claude Code (%s)",
                component_id, code_author_model,
            )
            test_results = await implement_component_iterative(
                project=self.project,
                component_id=component_id,
                contract=contract,
                test_suite=test_suites[component_id],
                budget=self.budget,
                model=code_author_model,
                dependency_contracts=dep_contracts or None,
                sops=sops,
            )
        elif competitive:
            from pact.implementer import implement_component_competitive
            agent_factory = self._make_agent_factory("code_author")
            test_results = await implement_component_competitive(
                agent_factory,
                self.project, component_id, contract,
                test_suites[component_id],
                dependency_contracts=dep_contracts or None,
                max_attempts=max_attempts,
                num_agents=num_agents,
                sops=sops,
                max_plan_revisions=self.global_config.max_plan_revisions,
            )
        else:
            from pact.implementer import implement_component
            agent = self._make_agent("code_author")
            try:
                test_results = await implement_component(
                    agent, self.project, component_id, contract,
                    test_suites[component_id],
                    dependency_contracts=dep_contracts or None,
                    max_attempts=max_attempts,
                    sops=sops,
                    max_plan_revisions=self.global_config.max_plan_revisions,
                )
            finally:
                await agent.close()

        # Update tree status
        node.implementation_status = (
            "tested" if test_results.all_passed else "failed"
        )
        node.test_results = test_results
        self.project.save_tree(tree)

        # Update task list statuses
        try:
            from pact.task_list import update_task_status
            task_list = self.project.load_task_list()
            if task_list:
                update_task_status(
                    task_list, component_id,
                    node.implementation_status,
                )
                self.project.save_task_list(task_list)
        except Exception as e:
            logger.debug("Task list update failed: %s", e)

        self.project.append_audit(
            "build",
            f"{component_id}: {test_results.passed}/{test_results.total} passed"
            + (f" (competitive, {num_agents} agents)" if competitive else ""),
        )

        # Record health metrics for build_component
        try:
            from pact.health import HealthMetrics
            metrics = HealthMetrics.from_dict(state.health_snapshot)

            # Token delta for this build
            post_in, post_out = self.budget.project_tokens
            delta_in = post_in - pre_in
            delta_out = post_out - pre_out
            if delta_in > 0 or delta_out > 0:
                metrics.record_phase_tokens("implement", delta_in, delta_out)
                metrics.record_generation(delta_in, delta_out)

            if test_results.all_passed:
                metrics.record_attempt(success=True)
                metrics.implementations_produced += 1
            else:
                metrics.record_attempt(success=False)
                metrics.record_component_failure(component_id)
            if test_results.passed > 0 or test_results.failed > 0:
                metrics.record_test_run(test_results.passed, test_results.failed)

            metrics.total_spend = self.budget.project_spend
            metrics.budget_cap = self.budget.per_project_cap
            state.health_snapshot = metrics.to_dict()
        except Exception:
            pass  # Health recording never blocks

        # Sync budget tracker totals to persistent state
        in_tok, out_tok = self.budget.project_tokens
        state.total_tokens = in_tok + out_tok
        state.total_cost_usd = self.budget.project_spend

        state.last_check_in = datetime.now().isoformat()
        self.project.save_state(state)
        return state

    def _check_health_and_remediate(self, state: RunState) -> RunState:
        """Check health and apply automated remedies if needed.

        Auto-safe remedies (skip_cascaded, informational) are applied
        immediately. Config-changing remedies (max_plan_revisions, shaping)
        are surfaced as proposals in the pause message — the user accepts
        them via FIFO directive.

        Also persists the overall_status and critical findings into
        health_snapshot so that format_run_summary can read them
        without re-running check_health (which has logging side effects).
        """
        from pact.health import HealthMetrics, check_health, should_abort, suggest_remedies

        metrics = HealthMetrics.from_dict(state.health_snapshot)
        thresholds = getattr(self.project_config, "health_thresholds", {}) or {}
        report = check_health(metrics, thresholds=thresholds)

        # Persist report summary into snapshot for side-effect-free reads
        snapshot = state.health_snapshot
        snapshot["_overall_status"] = report.overall_status.value
        snapshot["_critical_findings"] = [
            f"[{f.condition}] {f.message[:80]}"
            for f in report.critical_findings[:3]
        ]
        state.health_snapshot = snapshot

        if report.overall_status.value != "healthy":
            all_remedies = suggest_remedies(report, metrics)
            auto_applied = self._apply_auto_remedies(
                [r for r in all_remedies if r.auto], state,
            )
            proposed = [r for r in all_remedies if not r.auto]

            if auto_applied:
                self.project.append_audit(
                    "health_remedy",
                    "; ".join(auto_applied),
                )

            if should_abort(report):
                parts = []
                if auto_applied:
                    parts.append(f"Applied: {'; '.join(auto_applied)}")
                if proposed:
                    proposals = "; ".join(r.description for r in proposed)
                    parts.append(f"Proposed: {proposals}")
                    # Store proposals in snapshot for CLI display
                    snapshot["_proposed_remedies"] = [
                        {"kind": r.kind, "description": r.description, "fifo_hint": r.fifo_hint}
                        for r in proposed
                    ]
                    state.health_snapshot = snapshot

                summary = ". ".join(parts) if parts else "no remedies available"
                state.pause(
                    f"Health check: dysmemic pressure detected. {summary}. "
                    f"Review with 'pact health'."
                )

        return state

    def _apply_auto_remedies(self, remedies: list, state: RunState) -> list[str]:
        """Apply auto-safe remedies only. Returns descriptions of what was applied.

        Only informational remedies are auto-safe. Everything that modifies
        state or config is a proposal for the user — the system does not
        unilaterally reduce its own degrees of freedom.
        """
        applied: list[str] = []

        for remedy in remedies:
            try:
                if remedy.kind == "informational":
                    applied.append(remedy.description)

            except Exception as e:
                logger.debug("Auto-remedy '%s' failed: %s", remedy.kind, e)

        return applied

    def apply_remedy(self, kind: str, value: str | int | None = None) -> str:
        """Apply a user-approved remedy by kind. Called from daemon on FIFO directive.

        Returns a description of what was applied, or empty string if nothing changed.
        """
        if kind == "max_plan_revisions":
            target = int(value) if value is not None else 1
            old_val = self.global_config.max_plan_revisions
            if old_val != target:
                self.global_config.max_plan_revisions = max(1, target)
                msg = f"Reduced max_plan_revisions {old_val} -> {self.global_config.max_plan_revisions}"
                self.project.append_audit("remedy_applied", msg)
                return msg

        elif kind == "shaping":
            if self.global_config.shaping:
                self.global_config.shaping = False
                msg = "Disabled shaping"
                self.project.append_audit("remedy_applied", msg)
                return msg

        elif kind == "skip_cascaded":
            tree = self.project.load_tree()
            if tree:
                currently_failed = {
                    nid for nid, n in tree.nodes.items()
                    if n.implementation_status == "failed"
                }
                skipped = []
                for cid in currently_failed:
                    for sub_id in tree.subtree(cid):
                        if sub_id == cid:
                            continue
                        node = tree.nodes.get(sub_id)
                        if node and node.implementation_status == "pending":
                            node.implementation_status = "failed"
                            skipped.append(sub_id)
                if skipped:
                    self.project.save_tree(tree)
                    msg = f"Skipped cascaded: {', '.join(skipped)}"
                    self.project.append_audit("remedy_applied", msg)
                    return msg

        return ""

    async def _phase_diagnose(self, state: RunState, sops: str) -> RunState:
        """Diagnose failures and determine recovery action.

        Increments phase_cycles each time we enter diagnose. If the cycle
        count exceeds max_phase_cycles, pauses for human review instead of
        looping back to implement/integrate indefinitely.
        """
        state.phase_cycles += 1
        max_cycles = self.global_config.max_phase_cycles

        if state.phase_cycles > max_cycles:
            state.pause(
                f"Phase cycle limit reached ({state.phase_cycles} diagnose cycles, "
                f"max={max_cycles}). Human review required."
            )
            logger.warning(
                "Phase cycle limit reached (%d > %d) — pausing for human review",
                state.phase_cycles, max_cycles,
            )
            return state

        tree = self.project.load_tree()
        if not tree:
            state.fail("No tree for diagnosis")
            return state

        # Detect systemic failure before spending API calls on diagnosis
        failed_nodes = [
            n for n in tree.nodes.values()
            if n.implementation_status == "failed" and n.test_results
        ]
        failed_results = {
            n.component_id: n.test_results for n in failed_nodes
        }

        if len(failed_results) >= 3:
            pattern = detect_systemic_failure(failed_results)
            if pattern:
                state.pause(
                    f"Systemic failure in diagnose: {pattern.pattern_type} "
                    f"across {len(pattern.affected_components)} components. "
                    f"{pattern.recommendation}"
                )
                logger.warning(
                    "Systemic failure detected in diagnose: %s (%d components)",
                    pattern.pattern_type, len(pattern.affected_components),
                )
                return state

        agent = self._make_agent("trace_analyst")
        try:
            for node in failed_nodes:
                diagnosis = await diagnose_failure(
                    agent, self.project,
                    node.component_id,
                    node.test_results,
                    sops=sops,
                )

                if diagnosis:
                    action = determine_recovery_action(diagnosis)
                    if action == "reimplement":
                        node.implementation_status = "pending"
                        state.phase = "implement"
                    elif action == "reglue":
                        state.phase = "integrate"
                    elif action == "update_contract":
                        state.phase = "decompose"
                    elif action == "redesign":
                        state.fail(f"Design bug in {node.component_id}: requires human intervention")
                        return state

            project_tree = self.project.load_tree()
            if project_tree:
                self.project.save_tree(tree)

        finally:
            await agent.close()

        return state
