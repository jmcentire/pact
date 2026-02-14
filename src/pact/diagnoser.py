"""Diagnoser — Error recovery workflow.

Four systematic error recovery cases:
1. Implementation Bug: Component fails its contract tests after max attempts
2. Glue Bug: Parent tests fail, children satisfy their contracts
3. Contract Bug: Child satisfies contract but parent still fails
4. Design Bug: Decomposition itself was wrong

All failures recorded in the design document.
"""

from __future__ import annotations

import logging
from datetime import datetime

from pact.agents.base import AgentBase
from pact.agents.trace_analyst import analyze_trace
from pact.project import ProjectManager
from pact.schemas import (
    ComponentContract,
    DesignDocument,
    FailureRecord,
    IOTrace,
    TestFailure,
    TestResults,
    TraceDiagnosis,
)

logger = logging.getLogger(__name__)


async def diagnose_failure(
    agent: AgentBase,
    project: ProjectManager,
    component_id: str,
    test_results: TestResults,
    parent_id: str = "",
    sops: str = "",
) -> TraceDiagnosis | None:
    """Diagnose a component failure and record it.

    For leaf components: implementation_bug or contract_bug.
    For parent components: glue_bug, contract_bug, or design_bug.

    Returns:
        TraceDiagnosis if diagnosis was performed, None if no failures.
    """
    if test_results.all_passed:
        return None

    contracts = project.load_all_contracts()
    tree = project.load_tree()

    if not tree:
        logger.error("No decomposition tree found for diagnosis")
        return None

    node = tree.nodes.get(component_id)
    if not node:
        logger.error("Component %s not found in tree", component_id)
        return None

    # Get the failing test
    failing_test = TestFailure(
        test_id="aggregate",
        test_description=f"{test_results.failed} tests failed",
        error_message="; ".join(
            f.error_message for f in test_results.failure_details[:3]
        ),
    )
    if test_results.failure_details:
        failing_test = test_results.failure_details[0]

    contract = contracts.get(component_id)
    if not contract:
        logger.error("No contract for %s", component_id)
        return None

    # For leaf nodes, check if it's an implementation vs contract bug
    if not node.children:
        # Simple diagnosis: implementation bug (contract is assumed correct first)
        diagnosis = TraceDiagnosis(
            failing_test=failing_test.test_id,
            root_cause="implementation_bug",
            component_id=component_id,
            explanation=(
                f"Component '{component_id}' failed {test_results.failed} of "
                f"{test_results.total} contract tests. Since this is a leaf "
                f"component, the implementation does not match the contract."
            ),
            suggested_fix="Re-implement with fresh context, focusing on the failing tests.",
        )
    else:
        # Parent node — need trace analysis
        child_contracts = {
            cid: contracts[cid]
            for cid in node.children
            if cid in contracts
        }

        # Build I/O traces (placeholder — real traces from instrumentation)
        io_traces = [
            IOTrace(
                component_id=cid,
                function="(all)",
                inputs={},
                output="",
                error="" if tree.nodes.get(cid, node).implementation_status == "tested" else "failed",
            )
            for cid in node.children
        ]

        diagnosis, _, _ = await analyze_trace(
            agent, contract, child_contracts,
            failing_test, io_traces,
            sops=sops,
        )

    # Record failure
    failure_record = FailureRecord(
        component_id=component_id,
        failure_type=diagnosis.root_cause,
        description=diagnosis.explanation,
        resolution=diagnosis.suggested_fix,
        timestamp=datetime.now().isoformat(),
    )

    # Update design document
    doc = project.load_design_doc() or DesignDocument(
        project_id=project.project_dir.name,
        title=f"Design: {project.project_dir.name}",
    )
    doc.failure_history.append(failure_record)
    project.save_design_doc(doc)

    # Record learning
    project.append_learning({
        "lesson": f"{diagnosis.root_cause} in {component_id}: {diagnosis.explanation}",
        "category": "failure_mode",
        "component_id": component_id,
        "timestamp": datetime.now().isoformat(),
    })

    project.append_audit(
        "diagnosis",
        f"{component_id}: {diagnosis.root_cause} — {diagnosis.explanation[:100]}",
    )

    logger.info(
        "Diagnosed %s: %s in %s",
        component_id, diagnosis.root_cause, diagnosis.component_id,
    )

    return diagnosis


def determine_recovery_action(
    diagnosis: TraceDiagnosis,
) -> str:
    """Determine the recovery action based on diagnosis.

    Returns:
        Action string: "reimplement", "reglue", "update_contract", "redesign"
    """
    return {
        "implementation_bug": "reimplement",
        "glue_bug": "reglue",
        "contract_bug": "update_contract",
        "design_bug": "redesign",
    }.get(diagnosis.root_cause, "reimplement")
