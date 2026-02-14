"""Run state machine + JSONL audit log.

State transitions:
  active → paused          (human needed, budget warning)
  active → failed          (unrecoverable error)
  active → completed       (all components pass)
  active → budget_exceeded (dollar cap hit)
  paused → active          (resume)

Phase transitions:
  interview → decompose → contract → implement → integrate → complete
  Any phase can transition to → diagnose → (back to prior phase)
"""

from __future__ import annotations

import logging
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
        "interview", "decompose", "contract",
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
