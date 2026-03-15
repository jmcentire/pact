"""Access graph generation — produces access_graph.json from contracts.

The access graph captures data access patterns, authority declarations, and
component relationships. It is consumed by Arbiter at phase 8.5 for blast
radius analysis and trust scoring.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from pact.project import ProjectManager
from pact.schemas import ComponentContract

logger = logging.getLogger(__name__)


def _hash_file(path: Path) -> str:
    """SHA-256 hex digest of a file's contents."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_access_graph(
    project: ProjectManager,
    trust_policy: dict | None = None,
    classification_registry: dict | None = None,
) -> dict:
    """Build access_graph.json from contracts and dependency edges.

    Components section: one entry per component from contracts.
    Edges section: inferred from ComponentContract.dependencies.
    """
    contracts = project.load_all_contracts()
    if not contracts:
        return {"version": "1.0", "generated_by": "pact", "components": [], "edges": []}

    components = []
    for cid, contract in contracts.items():
        contract_path = project.contract_dir(cid) / "interface.json"
        test_path = project._visible_tests_dir / cid / "contract_test_suite.json"

        entry = {
            "id": cid,
            "name": contract.name,
            "data_access": {
                "reads": contract.data_access.reads,
                "writes": contract.data_access.writes,
                "side_effects": [
                    se.model_dump() for se in contract.data_access.side_effects
                ],
            },
            "authority": {
                "domains": contract.authority.domains,
                "rationale": contract.authority.rationale,
            },
            "contract_hash": _hash_file(contract_path),
            "test_hash": _hash_file(test_path),
        }
        components.append(entry)

    # Build edges from dependency declarations
    edges = []
    for cid, contract in contracts.items():
        for dep_id in contract.dependencies:
            if dep_id in contracts:
                # Determine data tiers in flight from dependency's data_access
                dep_contract = contracts[dep_id]
                tiers = list(set(contract.data_access.reads) & set(dep_contract.data_access.writes))
                if not tiers:
                    tiers = list(set(contract.data_access.reads))
                edges.append({
                    "from": cid,
                    "to": dep_id,
                    "data_tiers_in_flight": tiers,
                })

    graph = {
        "version": "1.0",
        "generated_by": "pact",
        "project": project.project_dir.name,
        "timestamp": datetime.now().isoformat(),
        "components": components,
        "edges": edges,
    }

    if trust_policy:
        graph["trust_policy"] = trust_policy
    if classification_registry:
        graph["classification_registry"] = classification_registry

    return graph


def save_access_graph(project: ProjectManager, graph: dict) -> Path:
    """Save access_graph.json to project root."""
    path = project.project_dir / "access_graph.json"
    path.write_text(json.dumps(graph, indent=2))
    logger.info("Saved access_graph.json with %d components", len(graph.get("components", [])))
    return path


def load_access_graph(project: ProjectManager) -> dict | None:
    """Load access_graph.json from project root."""
    path = project.project_dir / "access_graph.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())
