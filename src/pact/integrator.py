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

GLUE_SYSTEM_TS = """You are an integration engineer wiring child components together.
Given a parent contract and its children's contracts, produce TypeScript glue code
that composes child implementations into the parent's interface.

Key principles:
- Import children using ESM imports (e.g., `import { fn } from './child_module';`)
- Export parent interface implementation using named exports
- Glue code handles data transformation between components
- Glue code handles routing (which child to call when)
- Glue code does NOT add business logic
- All parent functions must be implemented by delegating to children
- Error propagation must match the parent contract
- Use TypeScript strict mode; use `unknown` instead of `any`
- Use TypeScript module composition patterns (re-export, type narrowing)"""

GLUE_SYSTEM_JS = """You are an integration engineer wiring child components together.
Given a parent contract and its children's contracts, produce JavaScript glue code
that composes child implementations into the parent's interface.

Key principles:
- Import children using ESM imports with .js extensions (e.g., `import { fn } from './child_module.js';`)
- Export parent interface implementation using named exports
- Glue code handles data transformation between components
- Glue code handles routing (which child to call when)
- Glue code does NOT add business logic
- All parent functions must be implemented by delegating to children
- Error propagation must match the parent contract
- Do NOT use TypeScript syntax — plain JavaScript ES6+ modules only
- Use JSDoc comments for documentation"""


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

    language = project.language
    is_ts = language == "typescript"
    is_js = language == "javascript"
    glue_ext = ".js" if is_js else (".ts" if is_ts else ".py")

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

        if is_ts:
            lang_label = "TypeScript"
            import_hint = (
                "- Import from each child using ESM imports "
                "(e.g., `import { fn } from './child_module';`)"
            )
        elif is_js:
            lang_label = "JavaScript"
            import_hint = (
                "- Import from each child using ESM imports with .js extensions "
                "(e.g., `import { fn } from './child_module.js';`)"
            )
        else:
            lang_label = "Python"
            import_hint = "- Import from each child's module"

        # Build full child contract JSON
        child_contracts_json = "\n\n".join(
            f'"{cid}":\n{c.model_dump_json(indent=2)}'
            for cid, c in child_contracts.items()
        )

        # Load child implementation source code
        child_impls = ""
        for cid in child_contracts:
            impl_src = project.impl_src_dir(cid)
            if impl_src.exists():
                for src_file in impl_src.rglob("*"):
                    if src_file.is_file() and src_file.suffix in (".py", ".ts", ".js"):
                        child_impls += f"\n\n=== {cid} implementation ({src_file.name}) ===\n{src_file.read_text()}"

        prompt = f"""Generate glue code to compose children into the parent interface.

Parent: {parent_contract.name} (id: {parent_id})
Parent functions:
{parent_funcs}

Children:
{children_summary}

Parent contract (JSON):
{parent_contract.model_dump_json(indent=2)}

Child contracts (full JSON):
{child_contracts_json}
{f'{chr(10)}Child implementations:{child_impls}' if child_impls else ''}
{failure_context}

Generate:
1. glue_code: {lang_label} module that imports children and implements parent interface
2. composition_test: Optional additional integration tests

The glue code should:
{import_hint}
- Implement each parent function by delegating to appropriate children
- Handle data transformation between child interfaces
- Propagate errors according to parent contract"""

        system = GLUE_SYSTEM_TS if is_ts else (GLUE_SYSTEM_JS if is_js else GLUE_SYSTEM)
        response, _, _ = await agent.assess(GlueResponse, prompt, system)

        # Save glue code
        comp_dir = project.composition_dir(parent_id)
        glue_path = comp_dir / f"glue{glue_ext}"
        glue_path.write_text(response.glue_code)

        if response.composition_test:
            test_ext = ".test.ts" if is_ts else ".py"
            test_path = comp_dir / f"composition_test{test_ext}"
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

        # Include child implementation src/ dirs so glue code can import them
        child_paths = [
            project.impl_src_dir(cid) for cid in child_contracts
        ]

        test_results = await run_contract_tests(
            test_file, comp_dir, extra_paths=child_paths,
            language=language,
            project_dir=project.project_dir,
        )

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


async def integrate_component_iterative(
    project: ProjectManager,
    parent_id: str,
    parent_contract: ComponentContract,
    parent_test_suite: ContractTestSuite,
    child_contracts: dict[str, ComponentContract],
    budget: object,  # BudgetTracker
    model: str = "claude-opus-4-6",
    sops: str = "",
    external_context: str = "",
    learnings: str = "",
    max_turns: int = 30,
    timeout: int = 600,
) -> TestResults:
    """Integrate child components via iterative Claude Code (write glue -> test -> fix).

    Instead of asking the API to produce a JSON blob of glue code, gives Claude Code
    full tool access to read child implementations, write glue code, run parent tests,
    read errors, and iterate within a single session.

    Returns:
        TestResults from running parent-level tests.
    """
    from pact.backends.claude_code import ClaudeCodeBackend

    language = project.language
    is_ts = language == "typescript"
    is_js = language == "javascript"
    file_ext = ".js" if is_js else (".ts" if is_ts else ".py")

    children_summary = "\n".join(
        f"  - {cid}: {c.name} — {', '.join(f.name for f in c.functions)}"
        for cid, c in child_contracts.items()
    )

    parent_funcs = "\n".join(
        f"  - {f.name}({', '.join(i.name + ': ' + i.type_ref for i in f.inputs)}) -> {f.output_type}"
        for f in parent_contract.functions
    )

    # Gather child implementation paths
    child_src_dirs = {
        cid: project.impl_src_dir(cid) for cid in child_contracts
    }
    child_impl_listing = "\n".join(
        f"  - {cid}: {path}/"
        for cid, path in child_src_dirs.items()
    )

    # Write test file so the agent can run it
    test_file = project.test_code_path(parent_id)
    if not test_file.exists() and parent_test_suite.generated_code:
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(parent_test_suite.generated_code)

    comp_dir = project.composition_dir(parent_id)

    module_name = parent_id.replace("-", "_")

    if is_ts or is_js:
        # Build NODE_PATH for TypeScript/JavaScript module resolution
        node_path_parts = [str(comp_dir), str(comp_dir.parent)]
        node_path_parts.extend(str(p) for p in child_src_dirs.values())
        env_path_str = ":".join(node_path_parts)

        ts_specific = ""
        if is_ts:
            ts_specific = "   - Use named exports only (no default exports)\n"
        js_specific = ""
        if is_js:
            js_specific = (
                "   - Use ESM imports with .js file extensions\n"
                "   - Do NOT use TypeScript syntax — plain JavaScript only\n"
            )

        prompt = f"""You are an integration engineer. Wire child components together into the parent interface.

## Parent Component: {parent_contract.name} (id: {parent_id})

Parent functions:
{parent_funcs}

Parent contract (JSON):
{parent_contract.model_dump_json(indent=2)}

## Children

{children_summary}

Child contracts:
{chr(10).join(f'{cid}: {c.model_dump_json(indent=2)}' for cid, c in child_contracts.items())}

## Child Implementation Locations

{child_impl_listing}

## CRITICAL: Module Structure Convention

The test file imports from: `./src/{module_name}`

You MUST write your glue module at: {comp_dir}/src/{module_name}{file_ext}

Create the directory if needed: mkdir -p {comp_dir}/src/

Children are importable using ESM imports. For example:
{chr(10).join(f'  import {{ ... }} from "./{cid.replace("-", "_")}";' for cid in child_contracts)}

## Your Task

1. Read each child implementation to understand their actual APIs:
{chr(10).join(f'   - {path}/{cid.replace("-", "_")}{file_ext}' for cid, path in child_src_dirs.items())}
2. Read the parent test file: {test_file}
3. Write your glue module at: {comp_dir}/src/{module_name}{file_ext}
   - Import from each child module using ESM imports
   - Re-export ALL types and functions that the test file imports
   - Implement each parent function by delegating to appropriate children
   - Match the exact type names, function signatures, and enum values from the contract
{ts_specific}{js_specific}   - Use named exports only (no default exports)
   - Do NOT add business logic — only wiring and delegation
4. Run tests:
   NODE_PATH="{env_path_str}" npx vitest run {test_file}
5. If tests fail, read the errors, fix your glue code, and re-run
6. Keep iterating until ALL tests pass

{f'SOPs: {sops}' if sops else ''}
{f'Context: {external_context}' if external_context else ''}
{f'Learnings: {learnings}' if learnings else ''}
"""
    else:
        # Build PYTHONPATH that includes child implementation dirs
        pythonpath_parts = [str(comp_dir), str(comp_dir.parent)]
        pythonpath_parts.extend(str(p) for p in child_src_dirs.values())
        pythonpath_str = ":".join(pythonpath_parts)

        prompt = f"""You are an integration engineer. Wire child components together into the parent interface.

## Parent Component: {parent_contract.name} (id: {parent_id})

Parent functions:
{parent_funcs}

Parent contract (JSON):
{parent_contract.model_dump_json(indent=2)}

## Children

{children_summary}

Child contracts:
{chr(10).join(f'{cid}: {c.model_dump_json(indent=2)}' for cid, c in child_contracts.items())}

## Child Implementation Locations

{child_impl_listing}

## CRITICAL: Module Structure Convention

The test file imports: `from src.{module_name} import ...`

You MUST write your glue module at: {comp_dir}/src/{module_name}.py

Create the directory if needed: mkdir -p {comp_dir}/src/

Children are importable directly by name (they're on PYTHONPATH). For example:
{chr(10).join(f'  import {cid.replace("-", "_")}' for cid in child_contracts)}

Do NOT use sys.path manipulation. Just import children by their module name.

## Your Task

1. Read each child implementation to understand their actual APIs:
{chr(10).join(f'   - {path}/{cid.replace("-", "_")}.py' for cid, path in child_src_dirs.items())}
2. Read the parent test file: {test_file}
3. Write your glue module at: {comp_dir}/src/{module_name}.py
   - Create {comp_dir}/src/__init__.py if needed
   - Import from each child module by name (e.g. `import <child_module_name>`)
   - Re-export ALL types and functions that the test file imports
   - Implement each parent function by delegating to appropriate children
   - Match the exact type names, function signatures, and enum values from the contract
   - Do NOT add business logic — only wiring and delegation
4. Run tests with correct PYTHONPATH:
   PYTHONPATH="{pythonpath_str}" python3 -m pytest {test_file} -v
5. If tests fail, read the errors, fix your glue code, and re-run
6. Keep iterating until ALL tests pass

{f'SOPs: {sops}' if sops else ''}
{f'Context: {external_context}' if external_context else ''}
{f'Learnings: {learnings}' if learnings else ''}
"""

    logger.info("Integrating %s iteratively via Claude Code (%s)", parent_id, model)

    backend = ClaudeCodeBackend(
        budget=budget,
        model=model,
        repo_path=project.project_dir,
        timeout=timeout,
    )

    try:
        await backend.implement(
            prompt=prompt,
            working_dir=project.project_dir,
            max_turns=max_turns,
            timeout=timeout,
        )
    except Exception as e:
        logger.error("Iterative integration failed for %s: %s", parent_id, e)

    project.append_audit(
        "integration",
        f"{parent_id} iterative claude_code ({model})",
    )

    # Run parent tests for official results — include child src/ dirs
    child_paths = [
        project.impl_src_dir(cid) for cid in child_contracts
    ]
    test_results = await run_contract_tests(
        test_file, comp_dir, extra_paths=child_paths,
        language=language,
        project_dir=project.project_dir,
    )

    # Save results
    results_path = comp_dir / "test_results.json"
    results_path.write_text(test_results.model_dump_json(indent=2))

    project.append_audit(
        "test_run",
        f"integration {parent_id}: {test_results.passed}/{test_results.total} passed",
    )

    logger.info(
        "Integration %s iterative result: %d/%d passed",
        parent_id, test_results.passed, test_results.total,
    )

    return test_results


async def integrate_all_iterative(
    project: ProjectManager,
    tree: DecompositionTree,
    budget: object,  # BudgetTracker
    model: str = "claude-opus-4-6",
    sops: str = "",
    parallel: bool = False,
    max_concurrent: int = 4,
    external_context: str = "",
    learnings: str = "",
    max_turns: int = 30,
    timeout: int = 600,
) -> dict[str, TestResults]:
    """Integrate all non-leaf components via iterative Claude Code, deepest first.

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

        test_results = await integrate_component_iterative(
            project, component_id,
            contracts[component_id],
            test_suite,
            child_contracts,
            budget=budget,
            model=model,
            sops=sops,
            external_context=external_context,
            learnings=learnings,
            max_turns=max_turns,
            timeout=timeout,
        )

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
