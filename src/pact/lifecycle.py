"""Run state machine + JSONL audit log.

State transitions:
  active → paused          (human needed, budget warning)
  active → failed          (unrecoverable error)
  active → completed       (all components pass)
  active → budget_exceeded (dollar cap hit)
  paused → active          (resume)

Phase transitions:
  interview → shape → decompose → contract → implement → integrate → complete
  Any phase can transition to → diagnose → (back to prior phase)
  shape phase is skipped when shaping is disabled (default)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pact.schemas import RunState

logger = logging.getLogger(__name__)


def create_run(project_dir: str) -> RunState:
    """Create a new RunState."""
    return RunState(
        id=uuid4().hex[:12],
        project_dir=project_dir,
        status="active",
        phase="interview",
        created_at=datetime.now().isoformat(),
    )


def advance_phase(state: RunState) -> str:
    """Advance to the next phase. Returns the new phase name."""
    phase_order = [
        "interview", "shape", "decompose", "contract",
        "implement", "integrate", "complete",
    ]
    try:
        idx = phase_order.index(state.phase)
    except ValueError:
        # In diagnose or unknown phase, return to implement
        state.phase = "implement"
        return state.phase

    if idx < len(phase_order) - 1:
        state.phase = phase_order[idx + 1]
    return state.phase


def format_run_summary(state: RunState) -> str:
    """Format a run state as a human-readable summary."""
    lines = [
        f"[{state.id}] {state.status:15s} ${state.total_cost_usd:.4f}",
        f"  Phase: {state.phase}",
        f"  Project: {state.project_dir}",
    ]
    if state.component_tasks:
        completed = sum(1 for t in state.component_tasks if t.status == "completed")
        failed = sum(1 for t in state.component_tasks if t.status == "failed")
        total = len(state.component_tasks)
        lines.append(f"  Components: {completed}/{total} done, {failed} failed")
    if state.pause_reason:
        lines.append(f"  Reason: {state.pause_reason}")
    return "\n".join(lines)


@dataclass
class ResumeStrategy:
    """Computed strategy for resuming a failed/paused run."""
    last_checkpoint: str  # Component ID of last successful checkpoint
    completed_components: list[str] = field(default_factory=list)  # Components with passing tests on disk
    resume_phase: str = ""  # Phase to resume from
    cleared_fields: list[str] = field(default_factory=list)  # State fields that will be reset


def compute_resume_strategy(state: RunState, project_dir: str = "") -> ResumeStrategy:
    """Analyze failed state and determine safe resume point.

    Rules:
    - If state.status not in ("failed", "paused", "budget_exceeded") -> raise ValueError
    - resume_phase should be state.phase (resume from where it failed)
    - But if phase is "diagnose", resume_phase should be "implement"
    - completed_components: look at state.component_tasks for status=="completed"
    - cleared_fields always includes "pause_reason"
    """
    if state.status == "active":
        raise ValueError("Run is already active")
    if state.status == "completed":
        raise ValueError("Run is already completed")

    # Determine resume phase
    resume_phase = state.phase
    if resume_phase == "diagnose":
        resume_phase = "implement"

    # Identify completed components
    completed_components = [
        t.component_id for t in state.component_tasks
        if t.status == "completed"
    ]

    # Find last checkpoint (last completed component, or empty)
    last_checkpoint = completed_components[-1] if completed_components else ""

    return ResumeStrategy(
        last_checkpoint=last_checkpoint,
        completed_components=completed_components,
        resume_phase=resume_phase,
        cleared_fields=["pause_reason"],
    )


def execute_resume(state: RunState, strategy: ResumeStrategy) -> RunState:
    """Apply resume strategy.

    - Set state.status = "active"
    - Set state.pause_reason = ""
    - Set state.phase = strategy.resume_phase
    - Return the modified state
    """
    state.status = "active"
    state.pause_reason = ""
    state.phase = strategy.resume_phase
    return state


class ErrorClassification(StrEnum):
    TRANSIENT = "transient"   # API timeout, rate limit, network -> retry
    PERMANENT = "permanent"   # Budget exceeded, invalid config -> stop
    SYSTEMIC = "systemic"     # Same error across components -> escalate


def classify_error(error: Exception, context: dict | None = None) -> ErrorClassification:
    """Classify an error for retry/stop/escalate decision.

    Args:
        error: The exception to classify
        context: Optional dict with keys like "component_errors" (dict of component_id -> error type counts)

    Rules:
        - asyncio.TimeoutError, ConnectionError, OSError (network) -> TRANSIENT
        - BudgetExceeded, ValueError, FileNotFoundError, PermissionError -> PERMANENT
        - If context["component_errors"] shows 3+ components with same error type -> SYSTEMIC
        - Unknown errors default to PERMANENT (fail safe)
    """
    import asyncio

    # Check systemic first (needs context)
    if context and "component_errors" in context:
        comp_errors = context["component_errors"]
        if len(comp_errors) >= 3:
            # Check if all have the same error type
            error_types = [type(e).__name__ for e in comp_errors.values()] if isinstance(list(comp_errors.values())[0], Exception) else list(comp_errors.values())
            from collections import Counter
            counts = Counter(error_types)
            most_common_type, most_common_count = counts.most_common(1)[0]
            if most_common_count >= 3:
                return ErrorClassification.SYSTEMIC

    # Transient errors (retriable)
    transient_types = (
        asyncio.TimeoutError,
        ConnectionError,
        ConnectionResetError,
        ConnectionRefusedError,
        ConnectionAbortedError,
    )
    if isinstance(error, transient_types):
        return ErrorClassification.TRANSIENT

    # Check for OSError with network-related errno
    if isinstance(error, OSError) and not isinstance(error, (FileNotFoundError, PermissionError)):
        return ErrorClassification.TRANSIENT

    # Check for httpx errors by class name (avoid hard import dependency)
    error_class_name = type(error).__name__
    if error_class_name in ("ConnectError", "ReadTimeout", "WriteTimeout", "PoolTimeout", "ConnectTimeout"):
        return ErrorClassification.TRANSIENT

    # Permanent errors (non-retriable)
    # BudgetExceeded, ValueError, FileNotFoundError, PermissionError, etc.
    return ErrorClassification.PERMANENT


# ── Event Sourcing ───────────────────────────────────────────────────


def rebuild_state_from_audit(audit_entries: list[dict], project_dir: str) -> RunState:
    """Rebuild RunState by replaying audit entries.

    Each audit entry has: timestamp, action, detail, and optional fields.

    Action types that affect state:
      - "interview" -> phase transitions to shape/decompose
      - "shape" or "shape_error" -> phase transitions to decompose
      - "decompose" -> creates component_tasks
      - "build" -> updates component status based on test results
      - "systemic_failure" -> sets status to paused
      - "archive" -> informational only
      - "phase_start" -> informational only

    Returns a best-effort reconstructed RunState. If no entries, returns a fresh state.
    """
    from pact.schemas import ComponentTask

    state = create_run(project_dir)

    for entry in audit_entries:
        action = entry.get("action", "")
        detail = entry.get("detail", "")

        if action == "interview":
            # Interview completed — advance past interview.
            # If detail mentions questions remaining we still advance;
            # the presence of the audit entry means the phase executed.
            state.phase = "shape"

        elif action in ("shape", "shape_error"):
            # Shape completed (or errored) — advance to decompose.
            state.phase = "decompose"

        elif action == "decompose":
            # Decomposition produced component tasks.
            # Detail may list component IDs; we don't parse them here
            # because the build entries will create tasks as needed.
            state.phase = "contract"

        elif action == "build":
            # Detail format: "comp_id: N/M passed"
            comp_id, _, result_part = detail.partition(":")
            comp_id = comp_id.strip()
            result_part = result_part.strip()

            # Parse pass/total from "N/M passed"
            all_passed = False
            if "passed" in result_part:
                fraction = result_part.split("passed")[0].strip()
                if "/" in fraction:
                    passed_str, total_str = fraction.split("/", 1)
                    try:
                        passed_count = int(passed_str.strip())
                        total_count = int(total_str.strip())
                        all_passed = (passed_count == total_count and total_count > 0)
                    except ValueError:
                        pass

            # Find or create the component task
            existing = [t for t in state.component_tasks if t.component_id == comp_id]
            if existing:
                task = existing[0]
            else:
                task = ComponentTask(component_id=comp_id)
                state.component_tasks.append(task)

            task.attempts += 1
            if all_passed:
                task.status = "completed"
            else:
                task.status = "failed"
                task.last_error = detail

            # Move phase to at least implement
            if state.phase in ("interview", "shape", "decompose", "contract"):
                state.phase = "implement"

        elif action == "systemic_failure":
            state.status = "paused"
            state.pause_reason = detail

        # "archive" and "phase_start" are informational — no state change.

    return state


def compute_audit_delta(current_state: RunState, audit_entries: list[dict]) -> list[str]:
    """Compare current persisted state against audit-reconstructed state.

    Returns list of discrepancy descriptions, empty if consistent.
    Used by ``pact doctor`` to detect state corruption.
    """
    reconstructed = rebuild_state_from_audit(audit_entries, current_state.project_dir)
    discrepancies: list[str] = []

    if current_state.phase != reconstructed.phase:
        discrepancies.append(
            f"Phase mismatch: persisted={current_state.phase}, "
            f"audit-reconstructed={reconstructed.phase}"
        )

    if current_state.status != reconstructed.status:
        discrepancies.append(
            f"Status mismatch: persisted={current_state.status}, "
            f"audit-reconstructed={reconstructed.status}"
        )

    # Compare component task counts
    persisted_completed = sum(
        1 for t in current_state.component_tasks if t.status == "completed"
    )
    reconstructed_completed = sum(
        1 for t in reconstructed.component_tasks if t.status == "completed"
    )
    if persisted_completed != reconstructed_completed:
        discrepancies.append(
            f"Completed component count mismatch: persisted={persisted_completed}, "
            f"audit-reconstructed={reconstructed_completed}"
        )

    persisted_failed = sum(
        1 for t in current_state.component_tasks if t.status == "failed"
    )
    reconstructed_failed = sum(
        1 for t in reconstructed.component_tasks if t.status == "failed"
    )
    if persisted_failed != reconstructed_failed:
        discrepancies.append(
            f"Failed component count mismatch: persisted={persisted_failed}, "
            f"audit-reconstructed={reconstructed_failed}"
        )

    return discrepancies
