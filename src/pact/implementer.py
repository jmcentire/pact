"""Implementer — Contract -> Code workflow.

Each component is implemented independently by a code_author agent.
The agent receives: contract, tests, dependency mocks, prior failure descriptions.
The agent does NOT receive: other implementations, decomposer reasoning.

Implementation ordering follows the dependency graph.

Two independent levers:
- parallel_components: independent leaves implement concurrently
- competitive_implementations: N agents implement the SAME component; best wins
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from pact.agents.base import AgentBase
from pact.agents.code_author import author_code
from pact.project import ProjectManager
from pact.resolution import ScoredAttempt, format_resolution_summary, select_winner
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionTree,
    TestResults,
)
from pact.test_harness import run_contract_tests

logger = logging.getLogger(__name__)


async def implement_component(
    agent: AgentBase,
    project: ProjectManager,
    component_id: str,
    contract: ComponentContract,
    test_suite: ContractTestSuite,
    dependency_contracts: dict[str, ComponentContract] | None = None,
    max_attempts: int = 3,
    sops: str = "",
    max_plan_revisions: int = 2,
) -> TestResults:
    """Implement a single component and run its contract tests.

    Returns:
        TestResults from the final attempt.
    """
    prior_failures: list[str] = []
    last_test_results: TestResults | None = None

    for attempt in range(1, max_attempts + 1):
        logger.info(
            "Implementing %s (attempt %d/%d)",
            component_id, attempt, max_attempts,
        )

        # Author code — with full handoff brief as mental model
        result = await author_code(
            agent, contract, test_suite,
            dependency_contracts=dependency_contracts,
            prior_failures=prior_failures if attempt > 1 else None,
            prior_test_results=last_test_results,
            attempt=attempt,
            sops=sops,
            max_plan_revisions=max_plan_revisions,
        )

        # Save implementation files
        src_dir = project.impl_src_dir(component_id)
        for filename, content in result.files.items():
            filepath = src_dir / filename
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(content)

        # Save metadata
        project.save_impl_metadata(component_id, {
            "attempt": attempt,
            "timestamp": datetime.now().isoformat(),
            "files": list(result.files.keys()),
        })
        project.save_impl_research(component_id, result.research)
        project.save_impl_plan(component_id, result.plan)

        project.append_audit(
            "implementation",
            f"{component_id} attempt {attempt}: {len(result.files)} files",
        )

        # Run contract tests
        test_file = project.test_code_path(component_id)
        if not test_file.exists() and test_suite.generated_code:
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(test_suite.generated_code)

        test_results = await run_contract_tests(test_file, src_dir)
        last_test_results = test_results
        project.save_test_results(component_id, test_results)

        project.append_audit(
            "test_run",
            f"{component_id}: {test_results.passed}/{test_results.total} passed",
        )

        if test_results.all_passed:
            logger.info(
                "Component %s passed all %d tests on attempt %d",
                component_id, test_results.total, attempt,
            )
            return test_results

        # Collect failure descriptions for next attempt (fresh agent gets these)
        for failure in test_results.failure_details:
            prior_failures.append(
                f"Test '{failure.test_id}': {failure.error_message}"
            )

        logger.warning(
            "Component %s failed %d/%d tests on attempt %d",
            component_id, test_results.failed + test_results.errors,
            test_results.total, attempt,
        )

    # All attempts exhausted
    logger.error(
        "Component %s failed after %d attempts", component_id, max_attempts,
    )
    return test_results


async def _run_one_competitor(
    agent: AgentBase,
    project: ProjectManager,
    component_id: str,
    contract: ComponentContract,
    test_suite: ContractTestSuite,
    dependency_contracts: dict[str, ComponentContract] | None,
    max_attempts: int,
    sops: str,
    max_plan_revisions: int,
    attempt_id: str,
) -> ScoredAttempt:
    """Run a single competitive attempt, writing to its own attempt directory."""
    prior_failures: list[str] = []
    last_test_results: TestResults | None = None
    start_time = time.monotonic()

    for attempt in range(1, max_attempts + 1):
        logger.info(
            "Competitor %s implementing %s (attempt %d/%d)",
            attempt_id, component_id, attempt, max_attempts,
        )

        result = await author_code(
            agent, contract, test_suite,
            dependency_contracts=dependency_contracts,
            prior_failures=prior_failures if attempt > 1 else None,
            prior_test_results=last_test_results,
            attempt=attempt,
            sops=sops,
            max_plan_revisions=max_plan_revisions,
        )

        # Save to attempt directory (not main src)
        src_dir = project.attempt_src_dir(component_id, attempt_id)
        for filename, content in result.files.items():
            filepath = src_dir / filename
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(content)

        project.save_attempt_metadata(component_id, attempt_id, {
            "attempt": attempt,
            "timestamp": datetime.now().isoformat(),
            "files": list(result.files.keys()),
            "type": "competitive",
        })

        # Run contract tests against this attempt's src
        test_file = project.test_code_path(component_id)
        if not test_file.exists() and test_suite.generated_code:
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(test_suite.generated_code)

        test_results = await run_contract_tests(test_file, src_dir)
        last_test_results = test_results
        project.save_attempt_test_results(component_id, attempt_id, test_results)

        if test_results.all_passed:
            break

        for failure in test_results.failure_details:
            prior_failures.append(
                f"Test '{failure.test_id}': {failure.error_message}"
            )

    duration = time.monotonic() - start_time
    return ScoredAttempt(
        attempt_id=attempt_id,
        component_id=component_id,
        test_results=last_test_results or TestResults(),
        build_duration_seconds=duration,
        src_dir=str(project.attempt_src_dir(component_id, attempt_id)),
    )


async def implement_component_competitive(
    agent_factory: Callable[[], AgentBase],
    project: ProjectManager,
    component_id: str,
    contract: ComponentContract,
    test_suite: ContractTestSuite,
    dependency_contracts: dict[str, ComponentContract] | None = None,
    max_attempts: int = 3,
    num_agents: int = 2,
    sops: str = "",
    max_plan_revisions: int = 2,
) -> TestResults:
    """Run N agents on the same component in parallel. Best wins.

    Each competitor gets its own attempt directory. The winner is promoted
    to the main src/ directory. Losers remain as informational context.
    """
    attempt_ids = [uuid4().hex[:8] for _ in range(num_agents)]
    agents = [agent_factory() for _ in range(num_agents)]

    try:
        tasks = [
            _run_one_competitor(
                agent=agents[i],
                project=project,
                component_id=component_id,
                contract=contract,
                test_suite=test_suite,
                dependency_contracts=dependency_contracts,
                max_attempts=max_attempts,
                sops=sops,
                max_plan_revisions=max_plan_revisions,
                attempt_id=attempt_ids[i],
            )
            for i in range(num_agents)
        ]
        scored_attempts = await asyncio.gather(*tasks)
    finally:
        for agent in agents:
            await agent.close()

    winner = select_winner(list(scored_attempts))
    if not winner:
        return TestResults()

    losers = [a for a in scored_attempts if a.attempt_id != winner.attempt_id]
    summary = format_resolution_summary(winner, losers)
    logger.info("Competitive resolution for %s:\n%s", component_id, summary)

    project.append_audit(
        "competitive_resolution",
        f"{component_id}: winner={winner.attempt_id} "
        f"({winner.test_results.passed}/{winner.test_results.total})",
    )

    # Promote winner to main src/
    project.promote_attempt(component_id, winner.attempt_id)

    return winner.test_results


async def implement_all(
    agent: AgentBase,
    project: ProjectManager,
    tree: DecompositionTree,
    max_attempts: int = 3,
    sops: str = "",
    max_plan_revisions: int = 2,
    parallel: bool = False,
    competitive: bool = False,
    competitive_agents: int = 2,
    max_concurrent: int = 4,
    agent_factory: Callable[[], AgentBase] | None = None,
    target_components: set[str] | None = None,
) -> dict[str, TestResults]:
    """Implement all leaf components.

    Args:
        agent: Agent for sequential mode.
        project: ProjectManager.
        tree: Decomposition tree.
        max_attempts: Max implementation attempts per component.
        sops: Standard operating procedures text.
        max_plan_revisions: Max plan revision loops.
        parallel: If True, implement independent leaves concurrently.
        competitive: If True, run N agents per component.
        competitive_agents: Number of competing agents per component.
        max_concurrent: Maximum concurrent agents (semaphore limit).
        agent_factory: Factory for creating fresh agents (required for parallel/competitive).
        target_components: If set, only implement these component IDs.

    Returns:
        Dict of component_id -> TestResults.
    """
    contracts = project.load_all_contracts()
    test_suites = project.load_all_test_suites()
    results: dict[str, TestResults] = {}

    # Determine which leaves to implement
    leaf_ids = [n.component_id for n in tree.leaves()]
    if target_components:
        leaf_ids = [cid for cid in leaf_ids if cid in target_components]

    # Filter to implementable leaves
    implementable: list[str] = []
    for cid in leaf_ids:
        if cid not in contracts:
            logger.warning("No contract for %s, skipping", cid)
            continue
        if cid not in test_suites:
            logger.warning("No test suite for %s, skipping", cid)
            continue
        implementable.append(cid)

    async def _impl_one(component_id: str, sem: asyncio.Semaphore | None = None) -> tuple[str, TestResults]:
        """Implement one component (sequential or competitive)."""
        if sem:
            async with sem:
                return await _impl_one_inner(component_id)
        return await _impl_one_inner(component_id)

    async def _impl_one_inner(component_id: str) -> tuple[str, TestResults]:
        contract = contracts[component_id]
        dep_contracts = {
            dep_id: contracts[dep_id]
            for dep_id in contract.dependencies
            if dep_id in contracts
        }

        if competitive and agent_factory:
            test_results = await implement_component_competitive(
                agent_factory,
                project, component_id, contract,
                test_suites[component_id],
                dependency_contracts=dep_contracts or None,
                max_attempts=max_attempts,
                num_agents=competitive_agents,
                sops=sops,
                max_plan_revisions=max_plan_revisions,
            )
        else:
            impl_agent = agent_factory() if (parallel and agent_factory) else agent
            try:
                test_results = await implement_component(
                    impl_agent, project, component_id,
                    contract,
                    test_suites[component_id],
                    dependency_contracts=dep_contracts or None,
                    max_attempts=max_attempts,
                    sops=sops,
                    max_plan_revisions=max_plan_revisions,
                )
            finally:
                if parallel and agent_factory and impl_agent is not agent:
                    await impl_agent.close()

        return component_id, test_results

    if parallel and len(implementable) > 1:
        # Parallel execution with concurrency limit
        sem = asyncio.Semaphore(max_concurrent)
        gather_results = await asyncio.gather(
            *[_impl_one(cid, sem) for cid in implementable]
        )
        for component_id, test_results in gather_results:
            results[component_id] = test_results
    else:
        # Sequential (current behavior, unchanged)
        for component_id in implementable:
            _, test_results = await _impl_one(component_id)
            results[component_id] = test_results

    # Update tree node statuses
    for component_id, test_results in results.items():
        node = tree.nodes.get(component_id)
        if node:
            node.implementation_status = (
                "tested" if test_results.all_passed else "failed"
            )
            node.test_results = test_results

    # Save updated tree
    project.save_tree(tree)

    return results
