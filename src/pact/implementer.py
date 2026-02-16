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

from pathlib import Path

from pact.agents.base import AgentBase
from pact.agents.code_author import author_code
from pact.interface_stub import get_required_exports
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


def _fix_pydantic_v1_patterns(source: str) -> tuple[str, list[str]]:
    """Mechanically fix common Pydantic v1 patterns that crash on v2.

    Targets patterns that cause ImportError or PydanticUserError at
    class/module definition time (not runtime logic errors).

    Returns (fixed_source, list_of_changes_made).
    """
    import re as _re
    changes: list[str] = []

    # 1. Remove imports of removed symbols
    removed_imports = {
        r"from pydantic\.main import [^\n]+": "removed pydantic.main import",
        r"from pydantic\.error_wrappers import [^\n]+": "removed pydantic.error_wrappers import",
    }
    for pattern, desc in removed_imports.items():
        if _re.search(pattern, source):
            source = _re.sub(pattern, "# (removed v1 import)", source)
            changes.append(desc)

    # 2. Remove 'Extra' from pydantic imports and replace usage
    if "Extra" in source:
        # Replace Extra.forbid / Extra.ignore with string literals
        for attr, replacement in [
            ("Extra.forbid", "'forbid'"),
            ("Extra.ignore", "'ignore'"),
            ("Extra.allow", "'allow'"),
        ]:
            if attr in source:
                source = source.replace(attr, replacement)
                changes.append(f"replaced {attr} with {replacement}")
        # Remove Extra from import line
        source = _re.sub(
            r",\s*Extra\b", "", source,
        )
        source = _re.sub(
            r"\bExtra\s*,\s*", "", source,
        )

    # 3. Replace regex= with pattern= in Field() calls
    if "regex=" in source:
        source = source.replace("regex=", "pattern=")
        changes.append("replaced regex= with pattern= in Field()")

    # 4. Remove always=True from validator decorators
    if "always=True" in source:
        source = _re.sub(r",\s*always\s*=\s*True", "", source)
        source = _re.sub(r"always\s*=\s*True\s*,\s*", "", source)
        changes.append("removed always=True from validators")

    # 5. Fix BaseModelMetaclass usage in enum definitions
    if "BaseModelMetaclass" in source:
        source = source.replace(
            "BaseModelMetaclass", "Enum"
        )
        # Ensure Enum is imported
        if "from enum import" not in source and "import enum" not in source:
            source = "from enum import Enum\n" + source
        changes.append("replaced BaseModelMetaclass with Enum")

    # 6. Fix @root_validator usage (crashes in v2 without parens/mode)
    if "@root_validator" in source:
        # Replace @root_validator(pre=True) with @model_validator(mode='before')
        source = _re.sub(
            r"@root_validator\s*\(\s*pre\s*=\s*True\s*\)",
            "@model_validator(mode='before')",
            source,
        )
        # Replace bare @root_validator or @root_validator() with @model_validator(mode='after')
        source = _re.sub(
            r"@root_validator\s*(?:\(\s*\))?(?=\s*\n)",
            "@model_validator(mode='after')",
            source,
        )
        # Fix imports: replace root_validator with model_validator
        source = _re.sub(
            r"\broot_validator\b(?=\s*[,\)])",
            "model_validator",
            source,
            count=1,  # Only fix the import line
        )
        changes.append("replaced @root_validator with @model_validator")

    # 7. Auto-add missing imports for commonly used names
    _common_imports = {
        "ConfigDict": ("pydantic", "ConfigDict"),
        "field_validator": ("pydantic", "field_validator"),
        "model_validator": ("pydantic", "model_validator"),
        "deepcopy": ("copy", "deepcopy"),
    }
    for name, (module, symbol) in _common_imports.items():
        if name in source:
            # Check if it's actually imported
            import_patterns = [
                f"from {module} import",
                f"import {module}",
            ]
            if not any(p in source for p in import_patterns if name in source.split(p)[-1].split("\n")[0] if p in source):
                # More robust check: is the name used but not in any import line?
                lines = source.split("\n")
                imported = False
                for line in lines:
                    if ("import" in line and name in line and
                            not line.strip().startswith("#")):
                        imported = True
                        break
                if not imported:
                    # Add the import at the top (after any __future__ imports)
                    future_end = 0
                    for i, line in enumerate(lines):
                        if "from __future__" in line:
                            future_end = i + 1
                    lines.insert(future_end, f"from {module} import {symbol}")
                    source = "\n".join(lines)
                    changes.append(f"added missing import: from {module} import {symbol}")

    return source, changes


def _sanitize_filename(filename: str) -> str:
    """Strip redundant src/ prefix from code author filenames.

    Code authors often return {"src/module.py": "..."} but the file is
    already written under impl_src_dir (which IS the src/ directory).
    Writing src/module.py under src/ creates src/src/module.py.
    """
    if filename.startswith("src/"):
        return filename[4:]
    return filename


def _find_defined_names(source: str) -> set[str]:
    """Extract top-level defined names from Python source code.

    Finds class definitions, function definitions, and top-level
    assignments (including type aliases).
    """
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _to_snake_case(name: str) -> str:
    """Convert PascalCase/camelCase to snake_case for comparison."""
    import re as _re
    # Insert underscore before uppercase letters that follow lowercase
    s = _re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return s.lower()


def _fuzzy_match(missing_name: str, available: set[str]) -> str | None:
    """Find a likely match for a missing export name.

    Checks: exact case-insensitive match, PascalCase↔snake_case match,
    then word-boundary substring match.
    Returns the best matching defined name, or None.

    Requires the shorter name to be at least 4 characters and cover at least
    40% of the longer name to avoid false positives (e.g., "str" in "constraints").
    """
    missing_lower = missing_name.lower()

    # Exact case-insensitive match
    for name in available:
        if name.lower() == missing_lower:
            return name

    # PascalCase ↔ snake_case match
    missing_snake = _to_snake_case(missing_name)
    for name in available:
        if _to_snake_case(name) == missing_snake:
            return name

    # Underscore-stripped match (PascalCase vs snake_case without underscores)
    missing_no_underscore = missing_lower.replace("_", "")
    for name in available:
        if name.lower().replace("_", "") == missing_no_underscore:
            return name

    # Substring matching with quality threshold
    if len(missing_name) < 4:
        return None  # Too short for reliable substring matching

    candidates = []
    for name in available:
        name_lower = name.lower()
        # Check containment in either direction
        if missing_lower in name_lower or name_lower in missing_lower:
            # Quality check: shorter must cover ≥40% of longer
            shorter = min(len(missing_lower), len(name_lower))
            longer = max(len(missing_lower), len(name_lower))
            if shorter / longer >= 0.4:
                candidates.append(name)

    if len(candidates) == 1:
        return candidates[0]

    return None


def validate_and_fix_exports(
    src_dir: Path,
    contract: ComponentContract,
) -> list[str]:
    """Check implementation files for missing required exports and auto-fix.

    For each missing export, attempts to find a fuzzy match among defined
    names and injects an alias (e.g., `Phase = TaskPhase`).

    Returns list of exports that could NOT be fixed (truly missing).
    """
    required = get_required_exports(contract)
    if not required:
        return []

    # Collect all defined names across ALL implementation files
    py_files = list(src_dir.rglob("*.py"))
    if not py_files:
        return required  # No files at all

    # Scan all files for defined names (the component may use packages)
    all_defined: set[str] = set()
    for f in py_files:
        all_defined |= _find_defined_names(f.read_text())

    # Find the main module file for alias injection
    main_file = None
    component_module = contract.component_id.replace("-", "_")

    # Priority: component_name/__init__.py > component_name.py > first non-init > first
    pkg_init = src_dir / component_module / "__init__.py"
    if pkg_init.exists():
        main_file = pkg_init
    if not main_file:
        for f in py_files:
            if f.name == f"{component_module}.py":
                main_file = f
                break
    if not main_file:
        non_init = [f for f in py_files if f.name != "__init__.py"]
        main_file = non_init[0] if non_init else py_files[0]

    defined = all_defined

    # Check what's missing
    missing = [name for name in required if name not in defined]
    if not missing:
        return []

    # Try to auto-fix with aliases
    aliases: list[str] = []
    still_missing: list[str] = []

    for name in missing:
        match = _fuzzy_match(name, defined)
        if match:
            aliases.append(f"{name} = {match}")
            logger.info(
                "Auto-aliased missing export: %s = %s", name, match,
            )
        else:
            still_missing.append(name)

    if aliases:
        # Inject aliases at the end of the main module file
        main_source = main_file.read_text()
        alias_block = (
            "\n\n# ── Auto-injected export aliases (Pact export gate) ──\n"
            + "\n".join(aliases)
            + "\n"
        )
        main_source += alias_block
        main_file.write_text(main_source)
        logger.info(
            "Injected %d export aliases into %s", len(aliases), main_file.name,
        )

    if still_missing:
        logger.warning(
            "Cannot auto-fix %d missing exports: %s",
            len(still_missing), still_missing,
        )

    return still_missing


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
    external_context: str = "",
    learnings: str = "",
) -> TestResults:
    """Implement a single component and run its contract tests.

    Returns:
        TestResults from the final attempt.
    """
    prior_failures: list[str] = []
    last_test_results: TestResults | None = None
    last_source: dict[str, str] | None = None

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
            external_context=external_context,
            learnings=learnings,
            prior_source=last_source,
        )

        # Save implementation files
        src_dir = project.impl_src_dir(component_id)
        last_source = dict(result.files)  # Save for patch mode on next attempt
        for filename, content in result.files.items():
            filepath = src_dir / _sanitize_filename(filename)
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(content)

        # Pydantic v1→v2 mechanical fixes (before running tests)
        for py_file in src_dir.rglob("*.py"):
            original = py_file.read_text()
            fixed, fixes = _fix_pydantic_v1_patterns(original)
            if fixes:
                py_file.write_text(fixed)
                logger.info(
                    "Auto-fixed Pydantic v1 patterns in %s: %s",
                    py_file.name, "; ".join(fixes),
                )

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

        # Export validation gate — check and fix before running tests
        unfixable = validate_and_fix_exports(src_dir, contract)
        if unfixable:
            # Add specific missing-export feedback for the next attempt
            for name in unfixable:
                prior_failures.append(
                    f"MISSING EXPORT: Your module does not define '{name}'. "
                    f"The contract requires this exact name to be importable."
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
            detail = f"Test '{failure.test_id}': {failure.error_message}"
            # Include actual error from stderr when message is generic
            # (e.g., import crashes that show "Failed to collect tests")
            if failure.stderr and failure.error_message in (
                "Failed to collect tests", "ERROR", "FAILED",
            ):
                # Extract the most relevant error lines from stderr
                error_lines = [
                    line for line in failure.stderr.splitlines()
                    if any(kw in line for kw in (
                        "Error", "error", "Import", "Module", "cannot",
                        "No module", "Traceback", "raise", "invalid",
                    ))
                ]
                if error_lines:
                    detail += "\n  Actual error:\n  " + "\n  ".join(error_lines[-5:])
            prior_failures.append(detail)

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


async def implement_component_iterative(
    project: ProjectManager,
    component_id: str,
    contract: ComponentContract,
    test_suite: ContractTestSuite,
    budget: object,  # BudgetTracker
    model: str = "claude-opus-4-6",
    dependency_contracts: dict[str, ComponentContract] | None = None,
    sops: str = "",
    external_context: str = "",
    learnings: str = "",
    max_turns: int = 30,
    timeout: int = 600,
) -> TestResults:
    """Implement a component using iterative Claude Code (write -> test -> fix).

    Instead of the API-based research->plan->code pipeline with blind retries,
    gives Claude Code full tool access to write code, run tests, read errors,
    and iterate within a single session. This is how a human developer works.

    Args:
        project: ProjectManager for file paths.
        component_id: Component to implement.
        contract: The ComponentContract.
        test_suite: Tests to pass.
        budget: BudgetTracker for spend accounting.
        model: Model to use for the Claude Code session.
        dependency_contracts: Contracts of dependencies.
        sops: Standard operating procedures.
        external_context: Context from integrations.
        learnings: Learnings from prior runs.
        max_turns: Maximum agentic turns for the session.
        timeout: Maximum wall-clock seconds.

    Returns:
        TestResults from running contract tests after implementation.
    """
    from pact.interface_stub import render_handoff_brief
    from pact.backends.claude_code import ClaudeCodeBackend

    # Build the handoff brief
    all_contracts = dict(dependency_contracts or {})
    all_contracts[contract.component_id] = contract

    handoff = render_handoff_brief(
        component_id=contract.component_id,
        contract=contract,
        contracts=all_contracts,
        test_suite=test_suite,
        sops=sops,
        external_context=external_context,
        learnings=learnings,
    )

    # Write test file so the agent can run it
    test_file = project.test_code_path(component_id)
    if not test_file.exists() and test_suite.generated_code:
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(test_suite.generated_code)

    src_dir = project.impl_src_dir(component_id)
    src_dir.mkdir(parents=True, exist_ok=True)

    module_name = contract.component_id.replace("-", "_")

    prompt = f"""You are implementing a software component. Here is your complete handoff brief:

{handoff}

## Your Task

Implement this component so ALL tests pass. You have full tool access.

1. Read the test file: {test_file}
2. Write your implementation in: {src_dir}/
3. The main module should be: {src_dir}/{module_name}.py
4. Run tests: python3 -m pytest {test_file} -v
5. If tests fail, read the errors, fix your code, and re-run
6. Keep iterating until ALL tests pass

Rules:
- All type names and function signatures must match the contract EXACTLY
- Use Pydantic v2 API (model_validator, field_validator, ConfigDict, pattern= not regex=)
- Handle all error cases from the contract
- Do NOT use __all__ — just define the names at module level
- The test file imports from src/{module_name} — your module must be importable from there
"""

    logger.info("Implementing %s iteratively via Claude Code (%s)", component_id, model)

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
        logger.error("Iterative implementation failed for %s: %s", component_id, e)

    # Belt-and-suspenders: apply Pydantic v1→v2 post-processing
    for py_file in src_dir.rglob("*.py"):
        try:
            original = py_file.read_text()
            fixed, fixes = _fix_pydantic_v1_patterns(original)
            if fixes:
                py_file.write_text(fixed)
                logger.info(
                    "Post-fixed Pydantic patterns in %s: %s",
                    py_file.name, "; ".join(fixes),
                )
        except Exception:
            pass

    # Export validation gate
    validate_and_fix_exports(src_dir, contract)

    # Save metadata
    project.save_impl_metadata(component_id, {
        "attempt": 1,
        "timestamp": datetime.now().isoformat(),
        "method": "iterative_claude_code",
        "model": model,
    })

    project.append_audit(
        "implementation",
        f"{component_id} iterative claude_code ({model})",
    )

    # Run contract tests for official results
    test_results = await run_contract_tests(test_file, src_dir)
    project.save_test_results(component_id, test_results)

    project.append_audit(
        "test_run",
        f"{component_id}: {test_results.passed}/{test_results.total} passed",
    )

    logger.info(
        "Component %s iterative result: %d/%d passed",
        component_id, test_results.passed, test_results.total,
    )

    return test_results


async def implement_component_interactive(
    team_backend: object,  # ClaudeCodeTeamBackend
    project: ProjectManager,
    component_id: str,
    contract: ComponentContract,
    test_suite: ContractTestSuite,
    dependency_contracts: dict[str, ComponentContract] | None = None,
    sops: str = "",
    external_context: str = "",
    learnings: str = "",
) -> TestResults:
    """Implement a component using a Claude Code interactive session.

    Instead of the API-based research->plan->code pipeline, spawns a full
    Claude Code session that can read files, write code, run tests, and
    iterate -- all within one persistent context window.

    Args:
        team_backend: A ClaudeCodeTeamBackend instance.
        project: ProjectManager for file paths.
        component_id: Component to implement.
        contract: The ComponentContract.
        test_suite: Tests to pass.
        dependency_contracts: Contracts of dependencies.
        sops: Standard operating procedures.
        external_context: Context from integrations.
        learnings: Learnings from prior runs.

    Returns:
        TestResults from running contract tests after implementation.
    """
    from pact.interface_stub import render_handoff_brief
    from pact.backends.claude_code_team import AgentTask

    all_contracts = dict(dependency_contracts or {})
    all_contracts[contract.component_id] = contract

    handoff = render_handoff_brief(
        component_id=contract.component_id,
        contract=contract,
        contracts=all_contracts,
        test_suite=test_suite,
        sops=sops,
        external_context=external_context,
        learnings=learnings,
    )

    # Write test file so the agent can run it
    test_file = project.test_code_path(component_id)
    if not test_file.exists() and test_suite.generated_code:
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(test_suite.generated_code)

    src_dir = project.impl_src_dir(component_id)
    src_dir.mkdir(parents=True, exist_ok=True)

    prompt = f"""You are implementing a software component. Here is your handoff brief:

{handoff}

## Instructions

1. Read the test file at: {test_file}
2. Implement the component in: {src_dir}/
3. Create a Python module that implements all types and functions from the contract
4. Run the tests with: python3 -m pytest {test_file} -v
5. If tests fail, read the errors, fix your implementation, and re-run
6. Iterate until ALL tests pass
7. When done, write a file at {src_dir}/DONE.txt containing "PASSED" if all tests passed

Important:
- All type names and function signatures must match the interface stub EXACTLY
- Handle all error cases as specified
- Dependencies should be accepted as constructor/function parameters (dependency injection)
"""

    output_file = str(src_dir / ".agent_output.json")
    task = AgentTask(
        prompt=prompt,
        output_file=output_file,
        pane_name=f"impl-{component_id[:12]}",
        working_dir=str(project.project_dir),
        max_turns=30,
    )

    try:
        await team_backend.spawn_agent(task)
        await team_backend.wait_for_completion(output_file, timeout=600)
    except Exception as e:
        logger.error("Interactive implementation failed for %s: %s", component_id, e)

    # Save metadata
    project.save_impl_metadata(component_id, {
        "attempt": 1,
        "timestamp": datetime.now().isoformat(),
        "method": "interactive",
    })

    project.append_audit(
        "implementation",
        f"{component_id} interactive implementation",
    )

    # Run contract tests to get results
    test_results = await run_contract_tests(test_file, src_dir)
    project.save_test_results(component_id, test_results)

    project.append_audit(
        "test_run",
        f"{component_id}: {test_results.passed}/{test_results.total} passed",
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
    external_context: str = "",
    learnings: str = "",
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
            external_context=external_context,
            learnings=learnings,
        )

        # Save to attempt directory (not main src)
        src_dir = project.attempt_src_dir(component_id, attempt_id)
        for filename, content in result.files.items():
            filepath = src_dir / _sanitize_filename(filename)
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(content)

        project.save_attempt_metadata(component_id, attempt_id, {
            "attempt": attempt,
            "timestamp": datetime.now().isoformat(),
            "files": list(result.files.keys()),
            "type": "competitive",
        })

        # Export validation gate
        validate_and_fix_exports(src_dir, contract)

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
    external_context: str = "",
    learnings: str = "",
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
                external_context=external_context,
                learnings=learnings,
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


async def implement_all_iterative(
    project: ProjectManager,
    tree: DecompositionTree,
    budget: object,  # BudgetTracker
    model: str = "claude-opus-4-6",
    sops: str = "",
    parallel: bool = False,
    max_concurrent: int = 4,
    target_components: set[str] | None = None,
    external_context: str = "",
    learnings: str = "",
    max_turns: int = 30,
    timeout: int = 600,
) -> dict[str, TestResults]:
    """Implement all leaf components using iterative Claude Code sessions.

    Each component gets its own Claude Code session with full tool access
    that can write code, run tests, read errors, and fix — iteratively.

    Args:
        project: ProjectManager.
        tree: Decomposition tree.
        budget: BudgetTracker for spend accounting.
        model: Model for Claude Code sessions.
        sops: Standard operating procedures text.
        parallel: If True, implement independent leaves concurrently.
        max_concurrent: Maximum concurrent Claude Code sessions.
        target_components: If set, only implement these component IDs.
        external_context: Context from integrations.
        learnings: Learnings from prior runs.
        max_turns: Max agentic turns per component session.
        timeout: Max wall-clock seconds per component.

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

    implementable: list[str] = []
    for cid in leaf_ids:
        if cid not in contracts:
            logger.warning("No contract for %s, skipping", cid)
            continue
        if cid not in test_suites:
            logger.warning("No test suite for %s, skipping", cid)
            continue
        implementable.append(cid)

    async def _impl_one(component_id: str) -> tuple[str, TestResults]:
        contract = contracts[component_id]
        dep_contracts = {
            dep_id: contracts[dep_id]
            for dep_id in contract.dependencies
            if dep_id in contracts
        }
        test_results = await implement_component_iterative(
            project=project,
            component_id=component_id,
            contract=contract,
            test_suite=test_suites[component_id],
            budget=budget,
            model=model,
            dependency_contracts=dep_contracts or None,
            sops=sops,
            external_context=external_context,
            learnings=learnings,
            max_turns=max_turns,
            timeout=timeout,
        )
        return component_id, test_results

    if parallel and len(implementable) > 1:
        sem = asyncio.Semaphore(max_concurrent)

        async def _guarded(cid: str) -> tuple[str, TestResults]:
            async with sem:
                return await _impl_one(cid)

        gather_results = await asyncio.gather(
            *[_guarded(cid) for cid in implementable]
        )
        for component_id, test_results in gather_results:
            results[component_id] = test_results
    else:
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

    project.save_tree(tree)
    return results


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
    external_context: str = "",
    learnings: str = "",
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
                external_context=external_context,
                learnings=learnings,
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
                    external_context=external_context,
                    learnings=learnings,
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
