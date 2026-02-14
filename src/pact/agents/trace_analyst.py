"""Trace analyst agent — I/O tracing for diagnosis.

Traces I/O through composition levels to find the first divergence
point when parent-level tests fail.
"""

from __future__ import annotations

import logging

from pact.agents.base import AgentBase
from pact.agents.research import plan_and_evaluate, research_phase
from pact.schemas import (
    ComponentContract,
    IOTrace,
    PlanEvaluation,
    ResearchReport,
    TestFailure,
    TraceDiagnosis,
)

logger = logging.getLogger(__name__)

TRACE_SYSTEM = """You are a trace analyst diagnosing integration failures.
Given I/O traces at component boundaries, you find where actual behavior
diverges from expected behavior.

Diagnosis categories:
- implementation_bug: Component produces wrong output for its contract
- contract_bug: Contract is incomplete or wrong (child satisfies contract but parent fails)
- glue_bug: Integration wiring is wrong (data transformation, routing)
- design_bug: Decomposition itself is wrong (components can't compose)

Be precise. Identify the specific component and function where divergence occurs."""


async def analyze_trace(
    agent: AgentBase,
    parent_contract: ComponentContract,
    child_contracts: dict[str, ComponentContract],
    failing_test: TestFailure,
    io_traces: list[IOTrace],
    sops: str = "",
    max_plan_revisions: int = 2,
) -> tuple[TraceDiagnosis, ResearchReport, PlanEvaluation]:
    """Analyze I/O traces to diagnose an integration failure.

    Returns:
        Tuple of (diagnosis, research, plan).
    """
    trace_summary = "\n".join(
        f"  - {t.component_id}.{t.function}: "
        f"inputs={t.inputs} -> output={t.output}"
        + (f" ERROR: {t.error}" if t.error else "")
        for t in io_traces
    )

    child_summary = "\n".join(
        f"  - {cid}: {c.name} ({len(c.functions)} functions)"
        for cid, c in child_contracts.items()
    )

    task_desc = (
        f"Diagnose integration failure in '{parent_contract.name}'.\n"
        f"Failing test: {failing_test.test_description or failing_test.test_id}\n"
        f"Error: {failing_test.error_message}\n"
        f"Children:\n{child_summary}\n"
        f"I/O traces:\n{trace_summary}"
    )

    # Phase 1: Research
    research = await research_phase(
        agent, task_desc,
        role_context=(
            "Focus on debugging methodologies, common integration failure patterns, "
            "tracing strategies, contract composition issues."
        ),
        sops=sops,
    )

    # Phase 2: Plan
    plan_desc = (
        f"Diagnosis plan:\n"
        f"- Check each child's I/O against its contract\n"
        f"- Find first divergence point\n"
        f"- Classify as implementation_bug, contract_bug, glue_bug, or design_bug\n"
        f"- Approach: {research.recommended_approach}"
    )
    plan = await plan_and_evaluate(
        agent, task_desc, research, plan_desc,
        sops=sops, max_revisions=max_plan_revisions,
    )

    # Phase 3: Diagnose
    contract_json = parent_contract.model_dump_json(indent=2)
    children_json = {
        cid: c.model_dump_json(indent=2)
        for cid, c in child_contracts.items()
    }

    prompt = f"""Diagnose this integration failure.

Parent contract:
{contract_json}

Child contracts:
{children_json}

Failing test:
  ID: {failing_test.test_id}
  Description: {failing_test.test_description}
  Error: {failing_test.error_message}

I/O Traces:
{trace_summary}

Research findings: {research.recommended_approach}

Determine:
1. The root_cause category
2. Which component_id is responsible
3. A clear explanation of what went wrong
4. A suggested fix"""

    diagnosis, in_tok, out_tok = await agent.assess(
        TraceDiagnosis, prompt, TRACE_SYSTEM,
    )

    logger.info(
        "Trace diagnosis for %s: %s in %s (%d tokens)",
        parent_contract.component_id, diagnosis.root_cause,
        diagnosis.component_id, in_tok + out_tok,
    )

    return diagnosis, research, plan
