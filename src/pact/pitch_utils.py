"""Shared pitch summary utilities for CLI, design doc, and handoff brief.

format_pitch_summary() is the single source of truth for pitch display.
"""

from __future__ import annotations

from dataclasses import dataclass

from pact.schemas_shaping import ShapingPitch


@dataclass
class PitchSummary:
    """Structured summary of a ShapingPitch for display."""
    appetite: str
    breadboard_place_count: int
    rabbit_hole_count: int
    no_go_count: int
    problem_statement: str
    has_region_map: bool
    has_fit_check: bool


def extract_pitch_summary(pitch: ShapingPitch) -> PitchSummary:
    """Extract a display summary from a ShapingPitch."""
    return PitchSummary(
        appetite=str(pitch.appetite),
        breadboard_place_count=(
            len(pitch.solution_breadboard.places)
            if pitch.solution_breadboard else 0
        ),
        rabbit_hole_count=len(pitch.rabbit_holes),
        no_go_count=len(pitch.no_gos),
        problem_statement=pitch.problem,
        has_region_map=pitch.solution_region_map is not None,
        has_fit_check=pitch.fit_check is not None,
    )


def format_pitch_summary(pitch: ShapingPitch) -> str:
    """Format a ShapingPitch as a human-readable multi-line summary."""
    summary = extract_pitch_summary(pitch)
    lines = [
        f"Appetite: {summary.appetite}",
        f"Problem: {summary.problem_statement[:120]}",
        f"Breadboard Places: {summary.breadboard_place_count}",
        f"Rabbit Holes: {summary.rabbit_hole_count}",
        f"No-Gos: {summary.no_go_count}",
    ]
    if summary.has_region_map:
        lines.append("Region Map: yes")
    if summary.has_fit_check:
        lines.append("Fit Check: yes")
    return "\n".join(lines)


def build_pitch_context_for_handoff(pitch: ShapingPitch) -> str:
    """Build pitch context string for render_handoff_brief()."""
    sections = [f"Appetite: {pitch.appetite}"]

    if pitch.solution_breadboard and pitch.solution_breadboard.places:
        sections.append("")
        sections.append("Breadboard:")
        for place in pitch.solution_breadboard.places:
            desc = f" — {place.description}" if place.description else ""
            sections.append(f"  - {place.name}{desc}")
        if pitch.solution_breadboard.connections:
            sections.append("Flows:")
            for conn in pitch.solution_breadboard.connections:
                sections.append(
                    f"  - {conn.from_place} -> {conn.to_place} ({conn.affordance})"
                )

    if pitch.rabbit_holes:
        sections.append("")
        sections.append("Rabbit Holes:")
        for rh in pitch.rabbit_holes:
            status = f" [{rh.status}]" if rh.status else ""
            sections.append(f"  - {rh.description}{status}")

    if pitch.no_gos:
        sections.append("")
        sections.append("No-Gos:")
        for ng in pitch.no_gos:
            sections.append(f"  - {ng}")

    return "\n".join(sections)
