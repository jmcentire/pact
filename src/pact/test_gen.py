"""Test generation pipeline — reverse-engineer contracts and generate tests for any codebase.

Ties together mechanical analysis (codebase_analyzer) with LLM agents
(contract + test authoring) and security audit output.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pact.agents.base import AgentBase
from pact.budget import BudgetTracker
from pact.codebase_analyzer import analyze_codebase
from pact.schemas import ComponentContract, ContractTestSuite, FunctionContract, FieldSpec
from pact.schemas_testgen import (
    CodebaseAnalysis,
    SecurityAuditReport,
    SecurityFinding,
    SecurityRiskLevel,
    TestGenPlan,
    TestGenPlanEntry,
    TestGenResult,
)

logger = logging.getLogger(__name__)

# ── System Prompts ─────────────────────────────────────────────────

REVERSE_ENGINEER_SYSTEM = """You are a contract reverse-engineer. Given source code for a module,
you produce a ComponentContract that precisely describes what the code ACTUALLY does.

Key principles:
- Describe actual behavior, not aspirational behavior
- Extract real preconditions and postconditions from the code
- Identify actual error cases (exceptions raised, error returns)
- List real dependencies (imports used)
- Types should reflect the actual parameter and return types
- Be precise about side effects"""


# ── Plan Generation ────────────────────────────────────────────────


def plan_test_generation(
    analysis: CodebaseAnalysis,
    complexity_threshold: int = 5,
    skip_covered: bool = True,
) -> TestGenPlan:
    """Prioritize functions for test generation.

    Priority order:
    1. Security-sensitive + untested (critical)
    2. High complexity + untested
    3. Remaining uncovered functions

    Args:
        analysis: Mechanical analysis result.
        complexity_threshold: Functions at or above this are "high complexity".
        skip_covered: If True, exclude already-covered functions.

    Returns:
        TestGenPlan with prioritized entries.
    """
    # Build security-sensitive function set
    security_funcs: set[tuple[str, str]] = set()
    for finding in analysis.security.findings:
        security_funcs.add((finding.file_path, finding.function_name))

    entries: list[TestGenPlanEntry] = []

    for entry in analysis.coverage.entries:
        if skip_covered and entry.covered:
            continue

        is_security = (entry.file_path, entry.function_name) in security_funcs
        is_high_complexity = entry.complexity >= complexity_threshold

        # Priority: lower = higher priority
        if is_security and not entry.covered:
            priority = 0
        elif is_high_complexity and not entry.covered:
            priority = 1
        elif not entry.covered:
            priority = 2
        else:
            priority = 3

        # Module name from file path
        module_name = entry.file_path.replace("/", ".").replace("\\", ".")
        if module_name.endswith(".py"):
            module_name = module_name[:-3]

        entries.append(TestGenPlanEntry(
            function_name=entry.function_name,
            file_path=entry.file_path,
            module_name=module_name,
            complexity=entry.complexity,
            security_sensitive=is_security,
            priority=priority,
        ))

    # Sort by priority, then by complexity (descending)
    entries.sort(key=lambda e: (e.priority, -e.complexity))

    return TestGenPlan(entries=entries)


# ── Contract Reverse-Engineering ───────────────────────────────────


async def reverse_engineer_contract(
    agent: AgentBase,
    source_code: str,
    module_path: str,
    function_names: list[str],
) -> ComponentContract:
    """Use LLM to reverse-engineer a ComponentContract from source code.

    Unlike the standard contract_author which designs contracts from specs,
    this reads actual code and describes what it does.

    Args:
        agent: LLM agent for structured extraction.
        source_code: Full source code of the module.
        module_path: Module path (e.g., "src.auth").
        function_names: Specific functions to focus on.

    Returns:
        ComponentContract describing the module's actual behavior.
    """
    func_list = ", ".join(function_names) if function_names else "all functions"

    prompt = f"""Reverse-engineer a ComponentContract for this Python module.

Module: {module_path}
Focus on functions: {func_list}

Source code:
```python
{source_code}
```

Requirements:
- component_id should be "{module_path.replace('.', '_')}"
- name should be a clean title derived from the module name
- description should summarize what this module does
- Extract FunctionContract for each target function with:
  * Actual input types and output type from the code
  * Preconditions visible in the code (assertions, guards, validation)
  * Postconditions (what the function guarantees on success)
  * Error cases (exceptions raised, error returns)
- Extract TypeSpec for any classes/dataclasses/named tuples defined
- List actual dependencies (imported modules used by the functions)
- List any invariants (constants, constraints maintained across calls)"""

    cache_prefix = f"Module: {module_path}\nSource:\n{source_code[:8000]}"

    contract, in_tok, out_tok = await agent.assess_cached(
        ComponentContract, prompt, REVERSE_ENGINEER_SYSTEM,
        cache_prefix=cache_prefix,
    )

    # Ensure component_id is set
    contract.component_id = module_path.replace(".", "_")

    logger.info(
        "Reverse-engineered contract for %s: %d functions (%d tokens)",
        module_path, len(contract.functions), in_tok + out_tok,
    )

    return contract


# ── Test Generation ────────────────────────────────────────────────


async def generate_tests_for_contract(
    agent: AgentBase,
    contract: ComponentContract,
    source_code: str,
    module_path: str,
    language: str = "python",
) -> ContractTestSuite:
    """Generate tests using the existing test_author infrastructure.

    Wraps author_tests() and adjusts import paths to point at the actual
    source module instead of src/<component_id>.
    """
    from pact.agents.test_author import author_tests

    suite, _research, _plan = await author_tests(
        agent, contract, language=language,
    )

    # Fix import paths in generated code to point at actual module
    if suite.generated_code:
        # Replace the default import path with the actual module
        old_import = f"from src.{contract.component_id} import"
        new_import = f"from {module_path} import"
        suite.generated_code = suite.generated_code.replace(old_import, new_import)

    return suite


# ── Security Audit Rendering ──────────────────────────────────────


def render_security_audit(report: SecurityAuditReport) -> str:
    """Render security audit report as markdown."""
    lines: list[str] = []
    lines.append("# Security Audit Report")
    lines.append("")
    lines.append(f"**Generated:** {report.analyzed_at}")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Critical: {report.critical_count}")
    lines.append(f"- High: {report.high_count}")
    lines.append(f"- Medium: {report.medium_count}")
    lines.append(f"- Low: {report.low_count}")
    lines.append(f"- Info: {report.info_count}")
    lines.append(f"- **Total: {len(report.findings)}**")
    lines.append("")

    if not report.findings:
        lines.append("No security-sensitive patterns detected.")
        return "\n".join(lines)

    # Findings grouped by risk level
    for level in (SecurityRiskLevel.critical, SecurityRiskLevel.high,
                  SecurityRiskLevel.medium, SecurityRiskLevel.low, SecurityRiskLevel.info):
        items = [f for f in report.findings if f.risk_level == level]
        if not items:
            continue

        lines.append(f"## {level.value.upper()} ({len(items)})")
        lines.append("")
        for f in items:
            covered_tag = "covered" if f.covered else "NOT COVERED"
            lines.append(f"- **{f.function_name}** ({f.file_path}:{f.line_number}) [{covered_tag}]")
            lines.append(f"  - Pattern: {f.pattern_matched}")
            lines.append(f"  - Complexity: {f.complexity}")
            if f.suggestion:
                lines.append(f"  - Suggestion: {f.suggestion}")
        lines.append("")

    return "\n".join(lines)


def render_summary(result: TestGenResult) -> str:
    """Render a console-friendly summary of test generation results."""
    lines: list[str] = []

    if result.dry_run:
        lines.append("=== Test-Gen Dry Run ===")
    else:
        lines.append("=== Test-Gen Complete ===")

    lines.append(f"Coverage before: {result.coverage_before:.0%}")
    lines.append(f"Security findings: {result.security_findings}")

    if not result.dry_run:
        lines.append(f"Contracts generated: {result.contracts_generated}")
        lines.append(f"Tests generated: {result.tests_generated}")
        lines.append(f"Cost: ${result.total_cost_usd:.4f}")

    if result.output_path:
        lines.append(f"Output: {result.output_path}")

    return "\n".join(lines)


# ── Output Writing ─────────────────────────────────────────────────


def _write_output(
    output_dir: Path,
    analysis: CodebaseAnalysis,
    plan: TestGenPlan,
    security_report: SecurityAuditReport,
    contracts: dict[str, ComponentContract],
    test_suites: dict[str, ContractTestSuite],
) -> None:
    """Write all outputs to the .pact/test-gen/ directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Analysis JSON
    (output_dir / "analysis.json").write_text(analysis.model_dump_json(indent=2))

    # Plan JSON
    (output_dir / "plan.json").write_text(plan.model_dump_json(indent=2))

    # Security audit
    (output_dir / "security_audit.md").write_text(render_security_audit(security_report))
    (output_dir / "security_audit.json").write_text(security_report.model_dump_json(indent=2))

    # Contracts and tests per module
    for module_name, contract in contracts.items():
        contract_dir = output_dir / "contracts" / module_name
        contract_dir.mkdir(parents=True, exist_ok=True)
        (contract_dir / "interface.json").write_text(contract.model_dump_json(indent=2))

    for module_name, suite in test_suites.items():
        test_dir = output_dir / "tests" / module_name
        test_dir.mkdir(parents=True, exist_ok=True)
        (test_dir / "test_suite.json").write_text(suite.model_dump_json(indent=2))
        if suite.generated_code:
            ext = ".py" if suite.test_language == "python" else ".ts"
            (test_dir / f"contract_test{ext}").write_text(suite.generated_code)


# ── Main Pipeline ──────────────────────────────────────────────────


async def run_test_gen(
    project_path: str | Path,
    language: str = "python",
    budget: float = 10.0,
    model: str = "claude-sonnet-4-5-20250929",
    backend: str = "anthropic",
    complexity_threshold: int = 5,
    skip_covered: bool = True,
    dry_run: bool = False,
) -> TestGenResult:
    """Run the full test-gen pipeline.

    Phases 1-6 are purely mechanical (AST, no LLM).
    Phases 7-8 use LLM agents (skipped in dry_run mode).
    Phase 9-10: Security audit report and output writing.

    Args:
        project_path: Root directory of the codebase to analyze.
        language: Programming language ("python" or "typescript").
        budget: Maximum LLM spend in dollars.
        model: LLM model to use for contract/test generation.
        backend: LLM backend to use.
        complexity_threshold: Functions at or above this complexity get priority.
        skip_covered: Skip functions that already have test coverage.
        dry_run: If True, only run mechanical analysis (no LLM calls).

    Returns:
        TestGenResult with counts and output path.
    """
    project_path = Path(project_path).resolve()
    output_dir = project_path / ".pact" / "test-gen"

    # Phases 1-5: Mechanical analysis
    logger.info("Analyzing codebase at %s...", project_path)
    analysis = analyze_codebase(project_path, language)
    logger.info(
        "Found %d source files, %d functions, %d test files",
        analysis.total_source_files, analysis.total_functions, analysis.total_test_files,
    )

    # Phase 6: Plan
    plan = plan_test_generation(analysis, complexity_threshold, skip_covered)
    logger.info(
        "Plan: %d functions to generate (%d security-sensitive)",
        plan.total, plan.security_sensitive_count,
    )

    result = TestGenResult(
        coverage_before=analysis.coverage.coverage_ratio,
        security_findings=len(analysis.security.findings),
        output_path=str(output_dir),
        dry_run=dry_run,
    )

    if dry_run:
        # Write analysis and plan only
        _write_output(output_dir, analysis, plan, analysis.security, {}, {})
        return result

    # Phases 7-8: LLM-powered contract + test generation
    budget_tracker = BudgetTracker(per_project_cap=budget)
    budget_tracker.set_model_pricing(model)
    budget_tracker.start_project()

    agent = AgentBase(budget_tracker, model=model, backend=backend)

    contracts: dict[str, ComponentContract] = {}
    test_suites: dict[str, ContractTestSuite] = {}

    # Group plan entries by module for batch processing
    modules: dict[str, list[TestGenPlanEntry]] = {}
    for entry in plan.entries:
        modules.setdefault(entry.module_name, []).append(entry)

    try:
        for module_name, entries in modules.items():
            if budget_tracker.is_exceeded():
                logger.warning("Budget exceeded, stopping generation")
                break

            # Read the source file
            file_path = entries[0].file_path
            full_path = project_path / file_path
            if not full_path.exists():
                logger.warning("Source file not found: %s", full_path)
                continue

            source_code = full_path.read_text(encoding="utf-8", errors="replace")
            function_names = [e.function_name for e in entries]

            # Phase 7: Reverse-engineer contract
            logger.info("Reverse-engineering contract for %s...", module_name)
            contract = await reverse_engineer_contract(
                agent, source_code, module_name, function_names,
            )
            contracts[module_name] = contract
            result.contracts_generated += 1

            if budget_tracker.is_exceeded():
                logger.warning("Budget exceeded after contract generation")
                break

            # Phase 8: Generate tests
            logger.info("Generating tests for %s...", module_name)
            suite = await generate_tests_for_contract(
                agent, contract, source_code, module_name, language,
            )
            test_suites[module_name] = suite
            result.tests_generated += 1

    finally:
        await agent.close()
        result.total_cost_usd = budget_tracker.project_spend

    # Phases 9-10: Write output
    _write_output(output_dir, analysis, plan, analysis.security, contracts, test_suites)

    return result
