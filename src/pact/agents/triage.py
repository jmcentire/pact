"""Triage agent — maps error signals to components, generates diagnostic reports.

When a signal doesn't have a PACT log key, or deeper analysis is needed,
the triage agent uses an LLM to match errors to known components by
comparing the error text against all component contracts.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from pact.agents.base import AgentBase
from pact.project import ProjectManager
from pact.schemas import ComponentContract, ContractTestSuite, DecompositionTree, TestResults
from pact.schemas_monitoring import DiagnosticReport, Incident, Signal

logger = logging.getLogger(__name__)

TRIAGE_SYSTEM = """You are a triage agent for Pact, a contract-first software system.
Your job is to analyze a production error signal and determine which component
most likely produced it. You'll be shown all component contracts and the error text.

Respond with the component_id of the most likely source, or "unknown" if you
cannot determine the source with reasonable confidence."""


class TriageResult(BaseModel):
    """Result of LLM-based triage."""
    component_id: str = Field(description="Component ID or 'unknown'")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in the mapping")
    reasoning: str = Field(description="Brief explanation of why this component was selected")


DIAGNOSTIC_SYSTEM = """You are a diagnostic analyst for Pact, a contract-first software system.
Your job is to analyze a production incident and produce a detailed diagnostic report
that helps humans understand the root cause and next steps.

Be specific, actionable, and honest about uncertainty."""


class DiagnosticResult(BaseModel):
    """LLM-generated diagnostic report."""
    summary: str = Field(description="1-2 sentence summary")
    error_analysis: str = Field(description="Root cause hypothesis")
    component_context: str = Field(description="How the component's contract relates to the error")
    recommended_direction: str = Field(description="What a human should do next")
    severity: str = Field(description="low, medium, high, or critical")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in diagnosis")


async def triage_signal(
    agent: AgentBase,
    signal: Signal,
    project: ProjectManager,
    tree: DecompositionTree,
    contracts: dict[str, ComponentContract],
) -> str | None:
    """Use LLM to map an error signal to a specific component.

    Presents the error text alongside all component contracts and asks
    which component most likely produced this error. Returns component_id
    or None if it can't be determined.
    """
    if not contracts:
        return None

    # Build context: compact summary of all components
    component_summaries = []
    for comp_id, contract in contracts.items():
        funcs = ", ".join(f.name for f in contract.functions)
        types = ", ".join(t.name for t in contract.types)
        deps = ", ".join(contract.dependencies) if contract.dependencies else "none"
        component_summaries.append(
            f"- {comp_id} ({contract.name}): functions=[{funcs}], "
            f"types=[{types}], dependencies=[{deps}]"
        )

    components_text = "\n".join(component_summaries)

    prompt = f"""Error signal from production:
Source: {signal.source}
Raw text: {signal.raw_text}
File: {signal.file_path}
Process: {signal.process_name}

Known components:
{components_text}

Which component most likely produced this error? Analyze the error text,
function names, type names, and error patterns to determine the source."""

    try:
        result, _, _ = await agent.assess(
            TriageResult, prompt, TRIAGE_SYSTEM, max_tokens=1024,
        )
        if result.component_id == "unknown" or result.confidence < 0.3:
            return None
        if result.component_id in contracts:
            return result.component_id
        return None
    except Exception as e:
        logger.debug("Triage failed: %s", e)
        return None


async def generate_diagnostic_report(
    agent: AgentBase,
    incident: Incident,
    project: ProjectManager,
    contract: ComponentContract | None,
    test_results: TestResults | None,
    attempted_fixes: list[str],
) -> DiagnosticReport:
    """Generate a detailed escalation report with diagnostics, direction, and insight.

    Even when Pact can't fix the issue, this report gives humans:
    - Root cause analysis
    - Component context (contract, deps, test history)
    - What was attempted and why it failed
    - Recommended next steps
    """
    # Build context
    signals_text = "\n".join(
        f"  [{s.source}] {s.raw_text[:200]}" for s in incident.signals[:5]
    )

    contract_text = "No contract available"
    if contract:
        funcs = "\n".join(
            f"  - {f.name}: {f.description}" for f in contract.functions
        )
        contract_text = (
            f"Component: {contract.name} ({contract.component_id})\n"
            f"Functions:\n{funcs}\n"
            f"Dependencies: {', '.join(contract.dependencies)}\n"
            f"Invariants: {', '.join(contract.invariants)}"
        )

    test_text = "No test results available"
    if test_results:
        test_text = (
            f"Tests: {test_results.passed}/{test_results.total} passed, "
            f"{test_results.failed} failed, {test_results.errors} errors"
        )
        if test_results.failure_details:
            for fd in test_results.failure_details[:3]:
                test_text += f"\n  FAIL: {fd.test_id} — {fd.error_message[:100]}"

    fixes_text = "No fixes attempted"
    if attempted_fixes:
        fixes_text = "\n".join(f"  {i}. {fix}" for i, fix in enumerate(attempted_fixes, 1))

    prompt = f"""Analyze this production incident and produce a diagnostic report.

Incident ID: {incident.id}
Status: {incident.status}
Total spend: ${incident.spend_usd:.2f}
Remediation attempts: {incident.remediation_attempts}

Error signals:
{signals_text}

Component contract:
{contract_text}

Test results:
{test_text}

Attempted fixes:
{fixes_text}

Produce a thorough diagnostic report including root cause hypothesis,
component context analysis, and recommended next steps for a human."""

    try:
        result, _, _ = await agent.assess(
            DiagnosticResult, prompt, DIAGNOSTIC_SYSTEM, max_tokens=2048,
        )
        return DiagnosticReport(
            incident_id=incident.id,
            summary=result.summary,
            error_analysis=result.error_analysis,
            component_context=result.component_context,
            attempted_fixes=attempted_fixes,
            recommended_direction=result.recommended_direction,
            severity=result.severity if result.severity in ("low", "medium", "high", "critical") else "medium",
            confidence=result.confidence,
        )
    except Exception as e:
        logger.debug("Diagnostic report generation failed: %s", e)
        return DiagnosticReport(
            incident_id=incident.id,
            summary=f"Diagnostic generation failed: {e}",
            error_analysis="Unable to analyze — LLM call failed",
            component_context=contract_text,
            attempted_fixes=attempted_fixes,
            recommended_direction="Manual investigation required",
            severity="medium",
            confidence=0.0,
        )
