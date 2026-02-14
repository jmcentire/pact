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
from datetime import datetime

from pact.agents.base import AgentBase
from pact.budget import BudgetExceeded, BudgetTracker
from pact.config import (
    GlobalConfig,
    ProjectConfig,
    resolve_backend,
    resolve_model,
    resolve_parallel_config,
)
from pact.decomposer import decompose_and_contract, run_interview
from pact.diagnoser import determine_recovery_action, diagnose_failure
from pact.implementer import implement_all
from pact.integrator import integrate_all
from pact.lifecycle import advance_phase, format_run_summary
from pact.project import ProjectManager
from pact.schemas import ComponentTask, RunState

logger = logging.getLogger(__name__)


class Scheduler:
    """Casual-pace scheduler — poll, burst, persist, sleep."""

    def __init__(
        self,
        project: ProjectManager,
        global_config: GlobalConfig,
        project_config: ProjectConfig,
        budget: BudgetTracker,
    ) -> None:
        self.project = project
        self.global_config = global_config
        self.project_config = project_config
        self.budget = budget
        self.check_interval = (
            project_config.check_interval
            or global_config.check_interval
        )

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

        if state.phase == "interview":
            state = await self._phase_interview(state, sops)
        elif state.phase == "decompose":
            state = await self._phase_decompose(state, sops)
        elif state.phase == "contract":
            # Contract phase is part of decompose
            advance_phase(state)
        elif state.phase == "implement":
            state = await self._phase_implement(state, sops)
        elif state.phase == "integrate":
            state = await self._phase_integrate(state, sops)
        elif state.phase == "diagnose":
            state = await self._phase_diagnose(state, sops)
        elif state.phase == "complete":
            state.complete()

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

    async def _phase_decompose(self, state: RunState, sops: str) -> RunState:
        """Run decomposition + contract + test generation."""
        agent = self._make_agent("decomposer")
        try:
            gate = await decompose_and_contract(
                agent, self.project, sops=sops,
                max_plan_revisions=self.global_config.max_plan_revisions,
            )

            if gate.passed:
                # Set up component tasks
                tree = self.project.load_tree()
                if tree:
                    state.component_tasks = [
                        ComponentTask(component_id=cid)
                        for cid in tree.topological_order()
                    ]

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
    ) -> RunState:
        """Implement all leaf components."""
        tree = self.project.load_tree()
        if not tree:
            state.fail("No decomposition tree found")
            return state

        max_attempts = (
            self.project_config.max_implementation_attempts
            or self.global_config.max_implementation_attempts
        )

        pcfg = resolve_parallel_config(self.project_config, self.global_config)

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
            )

            # Check for failures
            failed = [cid for cid, r in results.items() if not r.all_passed]
            if failed:
                state.phase = "diagnose"
                state.pause_reason = f"Components failed: {', '.join(failed)}"
            else:
                advance_phase(state)  # -> integrate
        finally:
            await agent.close()

        return state

    async def _phase_integrate(self, state: RunState, sops: str) -> RunState:
        """Integrate all non-leaf components."""
        tree = self.project.load_tree()
        if not tree:
            state.fail("No decomposition tree found")
            return state

        # Check if there are any non-leaf components
        non_leaves = [n for n in tree.nodes.values() if n.children]
        if not non_leaves:
            # No integration needed — single component or all leaves
            advance_phase(state)  # -> complete
            state.complete()
            return state

        pcfg = resolve_parallel_config(self.project_config, self.global_config)

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

            failed = [cid for cid, r in results.items() if not r.all_passed]
            if failed:
                state.phase = "diagnose"
                state.pause_reason = f"Integration failed: {', '.join(failed)}"
            else:
                state.complete()
        finally:
            await agent.close()

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

        if competitive:
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

        self.project.append_audit(
            "build",
            f"{component_id}: {test_results.passed}/{test_results.total} passed"
            + (f" (competitive, {num_agents} agents)" if competitive else ""),
        )

        # Sync budget tracker totals to persistent state
        in_tok, out_tok = self.budget.project_tokens
        state.total_tokens = in_tok + out_tok
        state.total_cost_usd = self.budget.project_spend

        state.last_check_in = datetime.now().isoformat()
        self.project.save_state(state)
        return state

    async def _phase_diagnose(self, state: RunState, sops: str) -> RunState:
        """Diagnose failures and determine recovery action."""
        tree = self.project.load_tree()
        if not tree:
            state.fail("No tree for diagnosis")
            return state

        agent = self._make_agent("trace_analyst")
        try:
            for node in tree.nodes.values():
                if node.implementation_status != "failed":
                    continue
                if not node.test_results:
                    continue

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
