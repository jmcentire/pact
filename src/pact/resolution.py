"""Competitive resolution — score and pick winners from parallel attempts.

When competitive_implementations is enabled, N agents implement the same
component independently. This module scores their outputs and selects a winner.

Resolution policy (updated per Papers XIX-XX):
1. Test pass rate gate — only attempts passing ≥ best_pass_rate qualify
2. Centroid selection — among qualifying attempts, select the one closest
   to the ensemble centroid (Paper XIX: closes 48.9% of coordination gap
   vs 9.1% for injection-based approaches)
3. Losing implementations saved as informational context in attempts/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from pact.schemas import TestResults

logger = logging.getLogger(__name__)


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


def _read_source(attempt: ScoredAttempt) -> str:
    """Read all source files from an attempt's directory into a single string."""
    src = Path(attempt.src_dir)
    if not src.exists():
        return ""
    parts = []
    for f in sorted(src.rglob("*")):
        if f.is_file() and f.suffix in (".py", ".ts", ".js"):
            try:
                parts.append(f.read_text())
            except Exception:
                pass
    return "\n".join(parts)


def _code_similarity(a: str, b: str) -> float:
    """Compute code similarity using SequenceMatcher (0.0 to 1.0)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def select_winner(attempts: list[ScoredAttempt]) -> ScoredAttempt | None:
    """Select the best attempt using centroid selection.

    Paper XIX showed priming selection (choosing the agent closest to
    ensemble centroid) closes 48.9% of the coordination gap, vs 9.1%
    for injection-based approaches.

    Algorithm:
    1. Gate: only attempts with the highest pass rate qualify
    2. If only one qualifier, return it
    3. Compute pairwise code similarity between all qualifiers
    4. Select the attempt with highest mean similarity to all others
       (closest to the centroid of the solution space)
    5. Ties broken by build duration (longer = more thorough)

    Returns None if no attempts provided.
    """
    if not attempts:
        return None

    if len(attempts) == 1:
        return attempts[0]

    # Gate: only top pass rate qualifiers
    best_rate = max(a.pass_rate for a in attempts)
    qualifiers = [a for a in attempts if a.pass_rate >= best_rate]

    if len(qualifiers) == 1:
        return qualifiers[0]

    # Centroid selection: read source and compute pairwise similarity
    sources = {a.attempt_id: _read_source(a) for a in qualifiers}

    # If we can't read sources, fall back to duration tiebreaker
    if all(not s for s in sources.values()):
        logger.debug("No source files readable for centroid selection, using duration tiebreaker")
        return max(qualifiers, key=lambda a: a.build_duration_seconds)

    # Compute mean similarity to all other qualifiers for each
    mean_sims: dict[str, float] = {}
    for a in qualifiers:
        sims = []
        for b in qualifiers:
            if a.attempt_id != b.attempt_id:
                sims.append(_code_similarity(sources[a.attempt_id], sources[b.attempt_id]))
        mean_sims[a.attempt_id] = sum(sims) / len(sims) if sims else 0.0

    # Select closest to centroid, break ties with duration
    winner = max(
        qualifiers,
        key=lambda a: (mean_sims[a.attempt_id], a.build_duration_seconds),
    )

    logger.info(
        "Centroid selection: %s (mean_sim=%.3f) from %d qualifiers",
        winner.attempt_id, mean_sims[winner.attempt_id], len(qualifiers),
    )

    return winner


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
