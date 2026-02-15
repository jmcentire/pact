"""Wavefront scheduling — dependency-driven execution.

Instead of phase-locked execution (all contracts, then all tests, then all implementations),
wavefront scheduling advances each component through its own phase pipeline as soon as
its dependencies are satisfied.

Example for tree with components A(root), B(leaf), C(leaf), D(depends on B):
  Wave 1: Contract B, Contract C (parallel - both are leaves, no deps)
  Wave 2: Test B, Test C, Contract D (parallel - B,C contracts done; D deps satisfied)
  Wave 3: Implement B, Implement C, Test D (parallel)
  Wave 4: Implement D (B done, D tests done)
  Wave 5: Integrate A (all children done)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pact.schemas import DecompositionTree


class ComponentPhase(StrEnum):
    """Phases a component progresses through."""
    PENDING = "pending"
    CONTRACT = "contract"
    TEST = "test"
    IMPLEMENT = "implement"
    INTEGRATE = "integrate"
    COMPLETE = "complete"


# Phase ordering for progression
PHASE_ORDER = [
    ComponentPhase.PENDING,
    ComponentPhase.CONTRACT,
    ComponentPhase.TEST,
    ComponentPhase.IMPLEMENT,
    ComponentPhase.INTEGRATE,
    ComponentPhase.COMPLETE,
]


@dataclass
class ComponentState:
    """Tracks a single component's phase progress."""
    component_id: str
    current_phase: ComponentPhase = ComponentPhase.PENDING
    is_leaf: bool = True
    dependencies: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)


class WavefrontScheduler:
    """Dependency-driven execution scheduler.

    Fan out independent work, serialize dependencies.
    """

    def __init__(
        self,
        tree: DecompositionTree,
        max_concurrent: int = 4,
    ):
        self.tree = tree
        self.max_concurrent = max_concurrent
        self.states: dict[str, ComponentState] = {}

        # Initialize component states from tree
        for node_id, node in tree.nodes.items():
            # Dependencies = components this one depends on (from contract.dependencies)
            # For tree-based deps: leaves depend on nothing, parents depend on children
            self.states[node_id] = ComponentState(
                component_id=node_id,
                current_phase=ComponentPhase.PENDING,
                is_leaf=len(node.children) == 0,
                dependencies=[],  # Set externally via set_dependencies
                children=list(node.children),
            )

    def set_dependencies(self, component_id: str, deps: list[str]) -> None:
        """Set contract-level dependencies for a component."""
        if component_id in self.states:
            self.states[component_id].dependencies = deps

    def compute_ready_set(self) -> list[tuple[str, str]]:
        """Return list of (component_id, phase) pairs ready to execute.

        A component is ready for its next phase when:
          - CONTRACT: component is PENDING (leaves can start immediately)
          - TEST: component's CONTRACT phase is complete
          - IMPLEMENT: component's TEST phase is complete AND
                       all dependencies have completed IMPLEMENT
          - INTEGRATE: component's IMPLEMENT is complete (for leaves) OR
                       all children have completed IMPLEMENT (for parents)
          - COMPLETE: INTEGRATE is done

        Postconditions:
          - No two entries have a blocking dependency relationship
          - Max concurrency respects max_concurrent_agents
          - Result is sorted by dependency depth (leaves first)
        """
        ready: list[tuple[str, str]] = []

        for cid, state in self.states.items():
            if state.current_phase == ComponentPhase.COMPLETE:
                continue

            next_phase = self._next_phase(state)
            if next_phase is None:
                continue

            if self._can_advance(cid, next_phase):
                ready.append((cid, next_phase.value))

        # Sort by depth (leaves first = higher depth first in tree, or just by phase priority)
        # Sort leaves before parents, then alphabetically for stability
        ready.sort(key=lambda x: (
            0 if self.states[x[0]].is_leaf else 1,
            PHASE_ORDER.index(ComponentPhase(x[1])),
            x[0],
        ))

        # Respect max_concurrent
        if len(ready) > self.max_concurrent:
            ready = ready[:self.max_concurrent]

        return ready

    def advance(self, component_id: str, completed_phase: str) -> None:
        """Record phase completion for a component.

        Side effects:
          - Updates component state
          - May unblock downstream components
        """
        state = self.states.get(component_id)
        if not state:
            return

        phase = ComponentPhase(completed_phase)
        state.current_phase = phase

        # If implementation is complete for a leaf, jump to COMPLETE
        # (leaves don't need integration)
        if phase == ComponentPhase.IMPLEMENT and state.is_leaf and not state.children:
            state.current_phase = ComponentPhase.COMPLETE

    def is_complete(self) -> bool:
        """Check if all components have completed."""
        return all(
            s.current_phase == ComponentPhase.COMPLETE
            for s in self.states.values()
        )

    def _next_phase(self, state: ComponentState) -> ComponentPhase | None:
        """Determine the next phase for a component."""
        current_idx = PHASE_ORDER.index(state.current_phase)
        if current_idx >= len(PHASE_ORDER) - 1:
            return None

        next_phase = PHASE_ORDER[current_idx + 1]

        # Skip INTEGRATE for leaves (they go straight to COMPLETE after IMPLEMENT)
        if next_phase == ComponentPhase.INTEGRATE and state.is_leaf and not state.children:
            next_phase = ComponentPhase.COMPLETE

        return next_phase

    def _can_advance(self, component_id: str, target_phase: ComponentPhase) -> bool:
        """Check if a component can advance to target_phase."""
        state = self.states[component_id]

        if target_phase == ComponentPhase.CONTRACT:
            # Any PENDING component can start contracting
            return state.current_phase == ComponentPhase.PENDING

        if target_phase == ComponentPhase.TEST:
            # Must have completed CONTRACT
            return state.current_phase == ComponentPhase.CONTRACT

        if target_phase == ComponentPhase.IMPLEMENT:
            # Must have completed TEST, and all deps must be IMPLEMENT or beyond
            if state.current_phase != ComponentPhase.TEST:
                return False
            for dep_id in state.dependencies:
                dep = self.states.get(dep_id)
                if dep and PHASE_ORDER.index(dep.current_phase) < PHASE_ORDER.index(ComponentPhase.IMPLEMENT):
                    return False
            return True

        if target_phase == ComponentPhase.INTEGRATE:
            # For parents: all children must be COMPLETE
            if state.current_phase != ComponentPhase.IMPLEMENT:
                return False
            for child_id in state.children:
                child = self.states.get(child_id)
                if child and child.current_phase != ComponentPhase.COMPLETE:
                    return False
            return True

        if target_phase == ComponentPhase.COMPLETE:
            # Leaves: after IMPLEMENT. Parents: after INTEGRATE.
            if state.is_leaf and not state.children:
                return state.current_phase == ComponentPhase.IMPLEMENT
            return state.current_phase == ComponentPhase.INTEGRATE

        return False
