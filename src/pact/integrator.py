"""Integrator — Composition + I/O tracing workflow.

Integration is itself a black box with a contract. The parent component
has its own ComponentContract and ContractTestSuite. Integration writes
glue code wiring children together.

When parent-level tests fail, I/O tracing finds the failure point.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from pact.agents.base import AgentBase
from pact.project import ProjectManager
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionTree,
    TestResults,
)
from pact.test_harness import run_contract_tests

logger = logging.getLogger(__name__)

GLUE_SYSTEM = """You are an integration engineer wiring child components together.
Given a parent contract and its children's contracts, produce glue code
that composes child implementations into the parent's interface.

Key principles:
- Glue code handles data transformation between components
- Glue code handles routing (which child to call when)
- Glue code does NOT add business logic
- All parent functions must be implemented by delegating to children
- Error propagation must match the parent contract"""


async def integrate_component(
    agent: AgentBase,
    project: ProjectManager,
    parent_id: str,
    parent_contract: ComponentContract,
    parent_test_suite: ContractTestSuite,
    child_contracts: dict[str, ComponentContract],
    max_attempts: int = 3,
    sops: str = "",
) -> TestResults:
    """Integrate child components into a parent.

    Returns:
        TestResults from running parent-level tests.
    """
    from pydantic import BaseModel

    class GlueResponse(BaseModel):
        """Generated glue code."""
        glue_code: str
        composition_test: str = ""

    children_summary = "\n".join(
        f"  - {cid}: {c.name} — {', '.join(f.name for f in c.functions)}"
        for cid, c in child_contracts.items()
    )

    parent_funcs = "\n".join(
        f"  - {f.name}({', '.join(i.name + ': ' + i.type_ref for i in f.inputs)}) -> {f.output_type}"
        for f in parent_contract.functions
    )

    prior_failures: list[str] = []

    for attempt in range(1, max_attempts + 1):
        failure_context = ""
        if prior_failures:
            failure_context = (
                "\nPrior failures:\n"
                + "\n".join(f"  - {f}" for f in prior_failures)
            )

        prompt = f"""Generate glue code to compose children into the parent interface.

Parent: {parent_contract.name} (id: {parent_id})
Parent functions:
{parent_funcs}

Children:
{children_summary}

Parent contract (JSON):
{parent_contract.model_dump_json(indent=2)}

Child contracts:
{{{', '.join(f'"{cid}": <contract>' for cid in child_contracts)}}}
{failure_context}

Generate:
1. glue_code: Python module that imports children and implements parent interface
2. composition_test: Optional additional integration tests

The glue code should:
- Import from each child's module
- Implement each parent function by delegating to appropriate children
- Handle data transformation between child interfaces
- Propagate errors according to parent contract"""

        response, _, _ = await agent.assess(GlueResponse, prompt, GLUE_SYSTEM)

        # Save glue code
        comp_dir = project.composition_dir(parent_id)
        glue_path = comp_dir / "glue.py"
        glue_path.write_text(response.glue_code)

        if response.composition_test:
            test_path = comp_dir / "composition_test.py"
            test_path.write_text(response.composition_test)

        project.append_audit(
            "integration",
            f"{parent_id} attempt {attempt}",
        )

        # Run parent tests
        test_file = project.test_code_path(parent_id)
        if not test_file.exists() and parent_test_suite.generated_code:
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(parent_test_suite.generated_code)

        test_results = await run_contract_tests(test_file, comp_dir)

        # Save results
        results_path = comp_dir / "test_results.json"
        results_path.write_text(test_results.model_dump_json(indent=2))

        if test_results.all_passed:
            logger.info(
                "Integration %s passed all %d tests on attempt %d",
                parent_id, test_results.total, attempt,
            )
            return test_results

        for failure in test_results.failure_details:
            prior_failures.append(
                f"Test '{failure.test_id}': {failure.error_message}"
            )

        logger.warning(
            "Integration %s failed %d/%d tests on attempt %d",
            parent_id, test_results.failed + test_results.errors,
            test_results.total, attempt,
        )

    return test_results


async def integrate_all(
    agent: AgentBase,
    project: ProjectManager,
    tree: DecompositionTree,
    max_attempts: int = 3,
    sops: str = "",
    parallel: bool = False,
    max_concurrent: int = 4,
    agent_factory: Callable[[], AgentBase] | None = None,
) -> dict[str, TestResults]:
    """Integrate all non-leaf components, deepest first.

    When parallel=True, non-leaves at the same depth are integrated
    concurrently (they're independent since their children are already done).
    Groups execute in order: deepest first, so children finish before parents.

    Returns:
        Dict of parent_id -> TestResults.
    """
    contracts = project.load_all_contracts()
    test_suites = project.load_all_test_suites()
    results: dict[str, TestResults] = {}

    # Get depth-ordered groups (deepest first)
    if parallel:
        groups = tree.non_leaf_parallel_groups()
    else:
        # Sequential: use topological order, non-leaves only
        order = tree.topological_order()
        groups = [[cid] for cid in order
                  if tree.nodes.get(cid) and tree.nodes[cid].children]

    async def _integrate_one(component_id: str) -> tuple[str, TestResults] | None:
        node = tree.nodes.get(component_id)
        if not node or not node.children:
            return None

        if component_id not in contracts:
            logger.warning("No contract for parent %s", component_id)
            return None

        child_contracts = {
            cid: contracts[cid]
            for cid in node.children
            if cid in contracts
        }

        test_suite = test_suites.get(component_id)
        if not test_suite:
            logger.warning("No test suite for parent %s", component_id)
            return None

        int_agent = agent_factory() if (parallel and agent_factory) else agent
        try:
            test_results = await integrate_component(
                int_agent, project, component_id,
                contracts[component_id],
                test_suite,
                child_contracts,
                max_attempts=max_attempts,
                sops=sops,
            )
        finally:
            if parallel and agent_factory and int_agent is not agent:
                await int_agent.close()

        node.implementation_status = (
            "tested" if test_results.all_passed else "failed"
        )
        node.test_results = test_results
        return component_id, test_results

    for group in groups:
        if parallel and len(group) > 1:
            sem = asyncio.Semaphore(max_concurrent)

            async def _limited(cid: str) -> tuple[str, TestResults] | None:
                async with sem:
                    return await _integrate_one(cid)

            gather_results = await asyncio.gather(*[_limited(cid) for cid in group])
            for result in gather_results:
                if result:
                    results[result[0]] = result[1]
        else:
            for component_id in group:
                result = await _integrate_one(component_id)
                if result:
                    results[result[0]] = result[1]

    project.save_tree(tree)
    return results
