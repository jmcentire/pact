"""Remediator — knowledge-flashed fixer for production incidents.

When the Sentinel detects a production error, the remediator:
1. Loads the affected component's contract, tests, and dependencies
2. Generates a reproducer test from the error signal
3. Adds it to the test suite
4. Rebuilds the component via the standard implementation pipeline
5. Verifies all tests pass (original + new reproducer)

The fixer receives the full handoff brief plus the error signal as
external_context — this is the "knowledge flash".
"""

from __future__ import annotations

import logging
from typing import Callable

from pydantic import BaseModel, Field

from pact.agents.base import AgentBase
from pact.events import EventBus
from pact.project import ProjectManager
from pact.schemas import ComponentContract, ContractTestSuite, TestResults
from pact.schemas_monitoring import Incident, Signal

logger = logging.getLogger(__name__)

REPRODUCER_SYSTEM = """You are a test engineer for Pact, a contract-first software system.
Your job is to write a pytest test case that reproduces a production error.
The test should fail with the reported error and pass once the bug is fixed.

Write a single test function that can be appended to the existing test suite.
Use pytest conventions. Import from the component module."""


class ReproducerResult(BaseModel):
    """Generated reproducer test code."""
    test_code: str = Field(description="Python test code (single function)")
    test_name: str = Field(description="Name of the test function")
    description: str = Field(description="What this test verifies")


async def generate_reproducer_test(
    agent: AgentBase,
    signal: Signal,
    contract: ComponentContract,
    test_suite: ContractTestSuite,
) -> str:
    """Generate a test case that reproduces the production error.

    Returns Python test code to be appended to the component's test suite.
    """
    # Build context from contract
    func_summary = "\n".join(
        f"  - {f.name}({', '.join(i.name + ': ' + i.type_ref for i in f.inputs)}) -> {f.output_type}"
        for f in contract.functions
    )
    type_summary = "\n".join(
        f"  - {t.name} ({t.kind})" for t in contract.types
    )

    existing_tests = ""
    if test_suite.generated_code:
        existing_tests = f"\nExisting test code (for reference):\n```python\n{test_suite.generated_code}\n```"

    prompt = f"""Write a pytest test that reproduces this production error.

Component: {contract.name} ({contract.component_id})
Functions:
{func_summary}
Types:
{type_summary}

Production error:
  Source: {signal.source}
  Error text: {signal.raw_text}

{existing_tests}

Write a single test function that:
1. Sets up the conditions that trigger the error
2. Calls the relevant function
3. Asserts the correct behavior (the test should FAIL with the current buggy code)

The test should be named test_reproducer_<brief_description>.
Include necessary imports. Use mocks for dependencies if needed."""

    try:
        result, _, _ = await agent.assess(
            ReproducerResult, prompt, REPRODUCER_SYSTEM, max_tokens=2048,
        )
        return result.test_code
    except Exception as e:
        logger.debug("Reproducer test generation failed: %s", e)
        # Return a minimal placeholder test
        return (
            f"def test_reproducer_production_error():\n"
            f'    """Reproducer for: {signal.raw_text[:80]}"""\n'
            f"    # Auto-generation failed: {e}\n"
            f"    assert False, 'Production error reproducer — needs manual implementation'\n"
        )


async def remediate_incident(
    incident: Incident,
    project: ProjectManager,
    agent_or_factory: AgentBase | Callable[[], AgentBase],
    event_bus: EventBus | None = None,
    max_attempts: int = 2,
    use_interactive: bool = False,
) -> tuple[bool, str]:
    """Attempt to fix an incident by rebuilding the affected component.

    Steps:
    1. Load component's contract, tests, dependency contracts
    2. Generate a new test case that reproduces the production error
    3. Add it to the component's test suite
    4. Rebuild the component (via implement_component)
    5. Verify ALL tests pass (original + new)
    6. If pass: return (True, summary)
    7. If fail: return (False, diagnostic_summary) for escalation

    Returns:
        (success, summary) — True if fixed, False if needs escalation
    """
    component_id = incident.component_id
    if not component_id:
        return False, "No component identified for this incident"

    # Load component artifacts
    contract = project.load_contract(component_id)
    if not contract:
        return False, f"No contract found for component {component_id}"

    test_suite = project.load_test_suite(component_id)
    if not test_suite:
        return False, f"No test suite found for component {component_id}"

    # Get or create agent
    if callable(agent_or_factory):
        agent = agent_or_factory()
    else:
        agent = agent_or_factory

    # Step 1: Generate reproducer test
    signal = incident.signals[0] if incident.signals else Signal(
        source="manual",
        raw_text="Unknown error",
        timestamp=incident.created_at,
    )

    reproducer_code = await generate_reproducer_test(
        agent, signal, contract, test_suite,
    )

    # Step 2: Append reproducer to test suite
    if test_suite.generated_code:
        augmented_code = test_suite.generated_code + "\n\n" + reproducer_code
    else:
        augmented_code = reproducer_code

    augmented_suite = ContractTestSuite(
        component_id=test_suite.component_id,
        contract_version=test_suite.contract_version,
        test_cases=test_suite.test_cases,
        test_language=test_suite.test_language,
        generated_code=augmented_code,
    )

    # Step 3: Rebuild component with augmented tests
    from pact.agents.code_author import author_code

    all_contracts = project.load_all_contracts()

    # Build error context for the handoff
    error_context = (
        f"## PRODUCTION ERROR (incident {incident.id})\n"
        f"Source: {signal.source}\n"
        f"Error: {signal.raw_text}\n"
        f"Component: {component_id}\n"
        f"A reproducer test has been added. Fix the implementation so ALL tests pass.\n"
    )

    for attempt in range(1, max_attempts + 1):
        incident.remediation_attempts = attempt

        try:
            result = await author_code(
                agent=agent,
                contract=contract,
                test_suite=augmented_suite,
                dependency_contracts={
                    dep: all_contracts[dep]
                    for dep in contract.dependencies
                    if dep in all_contracts
                },
                attempt=attempt,
                external_context=error_context,
            )

            # Save the implementation
            src_dir = project.impl_src_dir(component_id)
            for filename, content in result.files.items():
                (src_dir / filename).write_text(content)

            # Run tests
            from pact.test_harness import run_contract_tests
            test_results = await run_contract_tests(
                project, component_id, augmented_suite,
            )

            if test_results.all_passed:
                summary = (
                    f"Auto-fixed incident {incident.id}: "
                    f"{test_results.passed}/{test_results.total} tests pass "
                    f"(including reproducer)"
                )
                return True, summary

        except Exception as e:
            logger.debug(
                "Remediation attempt %d failed for %s: %s",
                attempt, component_id, e,
            )

    return False, (
        f"Remediation failed after {max_attempts} attempts for "
        f"incident {incident.id} (component: {component_id})"
    )
