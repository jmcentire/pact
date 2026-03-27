"""Optional kindex integration — graceful degradation when unavailable.

Provides context enrichment on project init and artifact publishing after
phases via kindex's knowledge graph. All methods are silent no-ops when
kindex is not installed or inaccessible.

Usage from pact::

    from pact.kindex_integration import kindex

    context = kindex.fetch_context("user authentication")
    kindex.publish_task("Build login flow", task_content, tags=["auth"])
    kindex.close()
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TOOL_NAME = "pact"


# ── Availability ──────────────────────────────────────────────

_store = None
_config = None
_checked = False


def is_available() -> bool:
    """Check if kindex is installed and the store is accessible."""
    global _checked
    if _checked:
        return _store is not None
    _checked = True
    return _init_store()


def _init_store() -> bool:
    global _store, _config
    try:
        from kindex.config import load_config
        from kindex.store import Store

        _config = load_config()
        _store = Store(_config)
        return True
    except Exception as e:
        logger.debug("Kindex not available: %s", e)
        return False


def _cli_available() -> bool:
    return shutil.which("kin") is not None


# ── Context Fetch ──────────────────────────────────────────────


def fetch_context(topic: str, max_tokens: int = 1500) -> str:
    """Search kindex for context related to a topic.

    Returns a formatted markdown context block, or empty string.
    """
    if not is_available():
        return ""
    try:
        from kindex.retrieve import format_context_block, hybrid_search

        results = hybrid_search(_store, topic, top_k=10)
        if not results:
            return ""
        return format_context_block(
            _store, results, query=topic, level="abridged"
        )
    except Exception as e:
        logger.debug("Kindex context fetch failed: %s", e)
        return ""


def search(query: str, top_k: int = 10) -> list[dict]:
    """Search the knowledge graph. Returns list of result dicts."""
    if not is_available():
        return []
    try:
        from kindex.retrieve import hybrid_search

        return hybrid_search(_store, query, top_k=top_k)
    except Exception as e:
        logger.debug("Kindex search failed: %s", e)
        return []


# ── Artifact Publishing ───────────────────────────────────────


def publish_node(
    title: str,
    content: str,
    node_type: str = "concept",
    tags: list[str] | None = None,
    extra: dict | None = None,
) -> str | None:
    """Publish a single node to the graph. Returns node ID or None."""
    if not is_available():
        return None
    try:
        return _store.add_node(
            title=title,
            content=content,
            node_type=node_type,
            domains=tags or [],
            prov_source=_TOOL_NAME,
            prov_activity="artifact-publish",
            extra=extra or {},
        )
    except Exception as e:
        logger.debug("Kindex publish failed: %s", e)
        return None


def publish_task(title: str, content: str, tags: list[str] | None = None) -> str | None:
    """Publish a task description to the graph."""
    return publish_node(title, content, node_type="concept", tags=tags)


def publish_decision(title: str, rationale: str, tags: list[str] | None = None) -> str | None:
    """Publish an architectural decision."""
    return publish_node(title, rationale, node_type="decision", tags=tags)


def publish_contract(component_id: str, interface_json: str, tags: list[str] | None = None) -> str | None:
    """Publish a component contract/interface."""
    return publish_node(
        title=f"Contract: {component_id}",
        content=interface_json,
        node_type="concept",
        tags=list(tags or []) + [component_id],
    )


def publish_decomposition(tree_json: str, tags: list[str] | None = None) -> int:
    """Parse a decomposition tree and publish components as nodes.

    Returns number of nodes created.
    """
    if not is_available() or not tree_json.strip():
        return 0
    try:
        import json

        data = json.loads(tree_json)
        if not isinstance(data, dict):
            return 0
        count = 0

        def _walk(node: dict, depth: int = 0) -> None:
            nonlocal count
            name = node.get("id") or node.get("name", "?")
            desc = node.get("description", "")
            publish_node(
                title=f"Component: {name}",
                content=desc,
                node_type="concept",
                tags=list(tags or []) + [name],
            )
            count += 1
            for child in node.get("children", []):
                _walk(child, depth + 1)

        _walk(data)
        return count
    except Exception as e:
        logger.debug("Kindex decomposition publish failed: %s", e)
        return 0


def learn_text(text: str, tags: list[str] | None = None) -> int:
    """Bulk-extract knowledge from text via kindex's extraction pipeline.

    Returns number of nodes created.
    """
    if not is_available():
        return 0
    try:
        from kindex.budget import BudgetLedger
        from kindex.extract import extract

        existing = [n["title"] for n in _store.all_nodes(limit=200)]
        extraction = extract(
            text, existing, _config, BudgetLedger(_config.ledger_path)
        )
        created = 0
        for concept in extraction.get("concepts", []):
            _store.add_node(
                title=concept["title"],
                content=concept.get("content", ""),
                node_type=concept.get("type", "concept"),
                domains=concept.get("domains", tags or []),
                prov_source=_TOOL_NAME,
                prov_activity="learn",
            )
            created += 1
        return created
    except Exception as e:
        logger.debug("Kindex learn failed: %s", e)
        return 0


# ── Code Indexing ──────────────────────────────────────────────


def index_codebase(directory: Path) -> bool:
    """Run code indexing on a directory. Prefers CLI, falls back to API.

    Returns True if indexing was performed.
    """
    if _cli_available():
        try:
            result = subprocess.run(
                ["kin", "ingest", "code", "--directory", str(directory)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            return result.returncode == 0
        except Exception as e:
            logger.debug("kin CLI ingest failed: %s", e)

    if not is_available():
        return False
    try:
        from kindex.adapters.registry import get as get_adapter

        adapter = get_adapter("code")
        if adapter and adapter.is_available():
            result = adapter.ingest(
                _store, limit=500, directory=str(directory)
            )
            return result.created > 0 or result.updated > 0
    except Exception as e:
        logger.debug("Kindex code ingest failed: %s", e)
    return False


# ── .kin/config Management ────────────────────────────────────


def read_kin_config(directory: Path) -> dict:
    """Read .kin/config from a directory. Returns empty dict if not found."""
    config_path = directory / ".kin" / "config"
    if config_path.exists():
        try:
            import yaml

            return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
    legacy = directory / ".kin"
    if legacy.is_file():
        try:
            import yaml

            return yaml.safe_load(legacy.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    return {}


def write_kin_config(directory: Path, updates: dict) -> None:
    """Merge updates into .kin/config (creates if needed)."""
    import yaml

    existing = read_kin_config(directory)
    existing.update(updates)
    kin_dir = directory / ".kin"
    kin_dir.mkdir(exist_ok=True)
    config_path = kin_dir / "config"
    config_path.write_text(
        yaml.dump(existing, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def should_auto_index(directory: Path) -> bool | None:
    """Check .kin/config for auto_index setting.

    Returns True/False if configured, None if not set (caller should prompt).
    """
    config = read_kin_config(directory)
    return config.get("auto_index")


# ── Cleanup ───────────────────────────────────────────────────


def close() -> None:
    """Close the kindex store connection."""
    global _store, _checked
    if _store is not None:
        try:
            _store.close()
        except Exception:
            pass
        _store = None
    _checked = False
