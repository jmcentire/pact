"""Post-run retrospective analysis.

Analyzes completed (or failed) runs to extract lessons for future runs.
Stores retrospectives in .pact/retrospectives/{run_id}.json.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RunRetrospective(BaseModel):
    """Post-run analysis for future improvement."""
    run_id: str
    total_cost: float = 0.0
    total_duration_seconds: float = 0.0
    components_count: int = 0
    plan_revisions: int = Field(default=0, description="How many contracts needed revision")
    largest_test_suite: list = Field(
        default_factory=lambda: ["", 0],
        description="[component_id, test_count]",
    )
    most_error_cases: list = Field(
        default_factory=lambda: ["", 0],
        description="[component_id, error_count]",
    )
    cost_distribution: dict[str, float] = Field(
        default_factory=dict,
        description="{component_id: cost}",
    )
    failure_patterns: list[str] = Field(
        default_factory=list,
        description="Detected failure patterns",
    )
    lessons: list[str] = Field(
        default_factory=list,
        description="Inferred lessons for future runs",
    )
    completed_at: str = Field(default="", description="ISO 8601 timestamp")


def generate_retrospective(project_dir: Path) -> RunRetrospective:
    """Analyze completed run and generate retrospective.

    Data sources:
      - .pact/state.json for run metadata
      - audit.jsonl for timing, cost, and events
      - contracts/ for test suite sizes
      - implementations/ for attempt counts

    Returns RunRetrospective even on partial/failed runs.
    """
    pact_dir = project_dir / ".pact"

    # Load state
    state_path = pact_dir / "state.json"
    run_id = "unknown"
    total_cost = 0.0
    total_duration = 0.0
    components_count = 0

    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            run_id = state.get("id", "unknown")
            total_cost = state.get("total_cost_usd", 0.0)
            components_count = len(state.get("component_tasks", []))
            
            created = state.get("created_at", "")
            completed = state.get("completed_at", "")
            if created and completed:
                try:
                    t_start = datetime.fromisoformat(created)
                    t_end = datetime.fromisoformat(completed)
                    total_duration = (t_end - t_start).total_seconds()
                except ValueError:
                    pass
        except (json.JSONDecodeError, KeyError):
            pass

    # Load audit entries
    audit_path = pact_dir / "audit.jsonl"
    audit_entries = []
    if audit_path.exists():
        for line in audit_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    audit_entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    # Analyze audit for patterns
    failure_patterns = []
    plan_revisions = 0
    cost_distribution: dict[str, float] = {}

    action_counts = Counter(e.get("action", "") for e in audit_entries)
    
    # Count build failures
    build_entries = [e for e in audit_entries if e.get("action") == "build"]
    failed_builds = [e for e in build_entries if "passed" in e.get("detail", "") and not _is_passing_build(e)]
    if len(failed_builds) > len(build_entries) * 0.5 and len(build_entries) > 0:
        failure_patterns.append(
            f"High failure rate: {len(failed_builds)}/{len(build_entries)} builds failed"
        )

    # Check for systemic failures
    systemic_count = action_counts.get("systemic_failure", 0)
    if systemic_count > 0:
        failure_patterns.append(
            f"Systemic failure detected {systemic_count} time(s) — likely environment issue"
        )

    # Count plan revisions (shape_error + any contract revision)
    plan_revisions = action_counts.get("shape_error", 0)

    # Find largest test suite
    largest_suite = ["", 0]
    most_errors = ["", 0]
    contracts_dir = pact_dir / "contracts"
    if contracts_dir.exists():
        for comp_dir in contracts_dir.iterdir():
            if not comp_dir.is_dir():
                continue
            cid = comp_dir.name
            suite_path = comp_dir / "tests" / "contract_test_suite.json"
            if suite_path.exists():
                try:
                    suite = json.loads(suite_path.read_text())
                    test_count = len(suite.get("test_cases", []))
                    if test_count > largest_suite[1]:
                        largest_suite = [cid, test_count]
                    # Count error_case type tests
                    error_count = sum(
                        1 for tc in suite.get("test_cases", [])
                        if tc.get("category") == "error_case"
                    )
                    if error_count > most_errors[1]:
                        most_errors = [cid, error_count]
                except (json.JSONDecodeError, KeyError):
                    pass

    # Generate lessons
    lessons = _infer_lessons(
        total_cost=total_cost,
        components_count=components_count,
        failure_patterns=failure_patterns,
        action_counts=action_counts,
        failed_builds=len(failed_builds),
        total_builds=len(build_entries),
    )

    retro = RunRetrospective(
        run_id=run_id,
        total_cost=total_cost,
        total_duration_seconds=total_duration,
        components_count=components_count,
        plan_revisions=plan_revisions,
        largest_test_suite=largest_suite,
        most_error_cases=most_errors,
        cost_distribution=cost_distribution,
        failure_patterns=failure_patterns,
        lessons=lessons,
        completed_at=datetime.now().isoformat(),
    )

    # Save retrospective
    retro_dir = pact_dir / "retrospectives"
    retro_dir.mkdir(parents=True, exist_ok=True)
    retro_path = retro_dir / f"{run_id}.json"
    retro_path.write_text(retro.model_dump_json(indent=2))

    return retro


def load_retrospective(project_dir: Path, run_id: str) -> RunRetrospective | None:
    """Load a saved retrospective."""
    path = project_dir / ".pact" / "retrospectives" / f"{run_id}.json"
    if not path.exists():
        return None
    try:
        return RunRetrospective.model_validate_json(path.read_text())
    except Exception:
        return None


def load_all_retrospectives(project_dir: Path) -> list[RunRetrospective]:
    """Load all retrospectives for a project, sorted by timestamp."""
    retro_dir = project_dir / ".pact" / "retrospectives"
    if not retro_dir.exists():
        return []
    retros = []
    for path in sorted(retro_dir.glob("*.json")):
        try:
            retros.append(RunRetrospective.model_validate_json(path.read_text()))
        except Exception:
            continue
    return retros


def _is_passing_build(entry: dict) -> bool:
    """Check if a build audit entry represents a passing build."""
    detail = entry.get("detail", "")
    # Format: "comp_a: 5/5 passed" or "comp_a: 2/5 passed"
    if "/" in detail and "passed" in detail:
        parts = detail.split("/")
        if len(parts) >= 2:
            try:
                passed = int(parts[0].split(":")[-1].strip())
                total = int(parts[1].split()[0])
                return passed == total and total > 0
            except (ValueError, IndexError):
                pass
    return False


def _infer_lessons(
    total_cost: float,
    components_count: int,
    failure_patterns: list[str],
    action_counts: Counter,
    failed_builds: int,
    total_builds: int,
) -> list[str]:
    """Infer actionable lessons from run data."""
    lessons = []

    if components_count > 0 and total_cost > 0:
        cost_per_component = total_cost / components_count
        if cost_per_component > 5.0:
            lessons.append(
                f"Average cost per component (${cost_per_component:.2f}) is high. "
                f"Consider using lighter models for contract/test authoring."
            )

    if total_builds > 0 and failed_builds > total_builds * 0.5:
        lessons.append(
            f"Build failure rate ({failed_builds}/{total_builds}) exceeds 50%. "
            f"Review contract specificity and test quality."
        )

    if any("systemic" in p.lower() for p in failure_patterns):
        lessons.append(
            "Systemic failures detected. Validate environment (PATH, dependencies) "
            "before running the pipeline."
        )

    archive_count = action_counts.get("archive", 0)
    if archive_count > 2:
        lessons.append(
            f"Multiple archives ({archive_count}) suggest frequent rebuilds. "
            f"Consider improving contract precision to reduce rework."
        )

    return lessons
