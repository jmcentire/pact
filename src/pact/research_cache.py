"""Research cache — persist and reuse research results.

Avoids redundant research phases when a run is resumed and the
component context (description, dependencies, SOPs) hasn't changed.
Results are saved to .pact/research/{cache_key}.json.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from pact.schemas import ResearchReport

logger = logging.getLogger(__name__)

_MIN_CACHE_AGE_SECONDS = 0  # No minimum — freshness is context-based


def cache_key(component_id: str, role: str, context_hash: str) -> str:
    """Deterministic key from component + role + hash of inputs.

    Args:
        component_id: The component being researched.
        role: The agent role (contract_author, test_author, code_author).
        context_hash: Hash of the inputs that would change research findings.

    Returns:
        A string key like "pricing_engine__contract_author__a1b2c3d4".
    """
    return f"{component_id}__{role}__{context_hash[:16]}"


def context_hash(component_desc: str, deps: list[str], sops: str) -> str:
    """SHA256 of the inputs that would change research findings.

    If any of these change, the cached research is stale.
    """
    content = f"{component_desc}\n---\n{','.join(sorted(deps))}\n---\n{sops}"
    return hashlib.sha256(content.encode()).hexdigest()


def save_research(project_dir: Path, key: str, report: ResearchReport) -> None:
    """Save research report to .pact/research/{key}.json."""
    research_dir = project_dir / ".pact" / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    path = research_dir / f"{key}.json"
    path.write_text(report.model_dump_json(indent=2))
    logger.debug("Cached research: %s", key)


def load_research(project_dir: Path, key: str) -> ResearchReport | None:
    """Load cached research if it exists. Returns None if missing."""
    path = project_dir / ".pact" / "research" / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return ResearchReport.model_validate(data)
    except Exception:
        logger.warning("Failed to load cached research: %s", key)
        return None


def invalidate(project_dir: Path, component_id: str) -> int:
    """Invalidate all cached research for a component.

    Returns the number of cache entries removed.
    """
    research_dir = project_dir / ".pact" / "research"
    if not research_dir.exists():
        return 0
    count = 0
    for path in research_dir.glob(f"{component_id}__*.json"):
        path.unlink()
        count += 1
    return count
