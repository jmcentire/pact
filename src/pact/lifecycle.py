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
