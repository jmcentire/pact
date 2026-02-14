"""Competitive resolution — score and pick winners from parallel attempts.

When competitive_implementations is enabled, N agents implement the same
component independently. This module scores their outputs and selects a winner.

Resolution policy:
1. Test pass rate (primary — more passing tests wins)
2. Build duration (tiebreaker — longer build favored as more thorough)
3. Losing implementations saved as informational context in attempts/
"""

from __future__ import annotations

from dataclasses import dataclass

from pact.schemas import TestResults


@dataclass
class ScoredAttempt:
    """A scored competitive attempt for a component."""
    attempt_id: str
    component_id: str
    test_results: TestResults
    build_duration_seconds: float
    src_dir: str

    @property
    def pass_rate(self) -> float:
        if self.test_results.total == 0:
            return 0.0
        return self.test_results.passed / self.test_results.total

    @property
    def score_tuple(self) -> tuple[float, float]:
        """(pass_rate, duration) — higher is better for both."""
        return (self.pass_rate, self.build_duration_seconds)


def select_winner(attempts: list[ScoredAttempt]) -> ScoredAttempt | None:
    """Select the best attempt. Highest pass rate wins; longest build breaks ties.

    Returns None if no attempts provided.
    """
    if not attempts:
        return None

    return max(attempts, key=lambda a: a.score_tuple)


def format_resolution_summary(
    winner: ScoredAttempt,
    losers: list[ScoredAttempt],
) -> str:
    """Format a human-readable summary of the competitive resolution."""
    lines = [
        f"Winner: {winner.attempt_id} "
        f"({winner.test_results.passed}/{winner.test_results.total} tests, "
        f"{winner.build_duration_seconds:.1f}s)",
    ]
    for loser in losers:
        lines.append(
            f"  Lost: {loser.attempt_id} "
            f"({loser.test_results.passed}/{loser.test_results.total} tests, "
            f"{loser.build_duration_seconds:.1f}s)"
        )
    return "\n".join(lines)
