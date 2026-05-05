"""Baton deployment directive generator for pact-managed projects.

Reads a project's contracts and decomposition tree to generate a baton.yaml
topology config with one node per component, observability, canary thresholds,
and health check endpoints.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def _load_tree(project_dir: Path) -> dict | None:
    """Load decomposition tree from project."""
    tree_path = project_dir / "decomposition" / "tree.json"
    if tree_path.exists():
        with open(tree_path) as f:
            return json.load(f)
    return None


def _load_contract(project_dir: Path, component_id: str) -> dict | None:
    """Load a component's contract from the project."""
    contract_path = project_dir / "contracts" / component_id / "interface.json"
    if contract_path.exists():
        with open(contract_path) as f:
            return json.load(f)
    return None


def _load_access_graph(project_dir: Path) -> dict | None:
    """Load access_graph.json if present."""
    ag_path = project_dir / "access_graph.json"
    if ag_path.exists():
        with open(ag_path) as f:
            return json.load(f)
    return None


_DDL_ONLY_EFFECTS = {
    "ddl_execution", "trigger_creation", "constraint_creation",
    "catalog_introspection", "schema_migration", "database_migration",
}


def _has_http_interface(contract: dict) -> bool:
    """Return True if the component exposes an HTTP server interface.

    Components whose side-effects are exclusively DDL/catalog operations
    (schema init, migrations) have no HTTP server and should not get a
    health-check URL.
    """
    side_effects = contract.get("data_access", {}).get("side_effects", [])
    if not side_effects:
        return True  # unknown — assume HTTP
    effect_types = {se["type"] for se in side_effects}
    return not effect_types.issubset(_DDL_ONLY_EFFECTS)


def _get_leaf_components(tree: dict) -> list[dict]:
    """Extract leaf components (no children) from the decomposition tree."""
    nodes = tree.get("nodes", {})
    leaves = []
    for node_id, node in nodes.items():
        if not node.get("children"):
            leaves.append(node)
    return leaves


def _get_all_components(tree: dict) -> list[dict]:
    """Extract all components from the decomposition tree."""
    nodes = tree.get("nodes", {})
    return list(nodes.values())


def _build_edges(tree: dict, components: list[dict], project_dir: Path) -> list[dict]:
    """Build edges from the decomposition tree's dependency structure.

    Priority order:
    1. Explicit contract dependency declarations between deployed nodes.
    2. Inferred edges from access_graph side-effects:
       - database_* effects → component depends on the database node
       - http_* effects → component depends on the HTTP API node
    3. Parent-child fallback for any remaining deployed-node pairs.
    """
    component_ids = {c["component_id"] for c in components}
    edges = []
    seen = set()

    # 1. Contract dependency declarations
    for comp in components:
        comp_id = comp["component_id"]
        contract = _load_contract(project_dir, comp_id) or comp.get("contract") or {}
        for dep in contract.get("dependencies", []):
            if dep in component_ids and (comp_id, dep) not in seen:
                edges.append({"source": comp_id, "target": dep})
                seen.add((comp_id, dep))

    # 2. Infer from access_graph side-effect types
    if not edges:
        # Classify each deployed component by the side-effect categories it produces
        db_nodes: set[str] = set()    # components with DDL-only effects (schema init)
        api_nodes: set[str] = set()   # components with database_* effects (HTTP APIs)
        client_nodes: set[str] = set()  # components with http_* effects (HTTP clients)

        for comp in components:
            comp_id = comp["component_id"]
            contract = _load_contract(project_dir, comp_id) or {}
            effects = {
                se["type"]
                for se in contract.get("data_access", {}).get("side_effects", [])
            }
            if effects and effects.issubset(_DDL_ONLY_EFFECTS):
                db_nodes.add(comp_id)
            elif any(e.startswith("database_") for e in effects):
                api_nodes.add(comp_id)
            elif any(e.startswith("http_") for e in effects):
                client_nodes.add(comp_id)

        for api in api_nodes:
            for db in db_nodes:
                if (api, db) not in seen:
                    edges.append({"source": api, "target": db})
                    seen.add((api, db))

        for client in client_nodes:
            for api in api_nodes:
                if (client, api) not in seen:
                    edges.append({"source": client, "target": api})
                    seen.add((client, api))

    # 3. Parent-child fallback
    if not edges:
        for comp in components:
            parent_id = comp.get("parent_id", "")
            if parent_id and parent_id in component_ids:
                pair = (parent_id, comp["component_id"])
                if pair not in seen:
                    edges.append({"source": parent_id, "target": comp["component_id"]})
                    seen.add(pair)

    return edges


def _sanitize_name(name: str) -> str:
    """Convert a component name/id to a valid baton node name (lowercase, alphanumeric + hyphens)."""
    import re
    sanitized = re.sub(r"[^a-z0-9-]", "-", name.lower())
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")
    if not sanitized or not sanitized[0].isalpha():
        sanitized = "n-" + sanitized
    return sanitized


def generate_baton_yaml(
    project_dir: str | Path,
    output_path: str | None = None,
    sink: str = "jsonl",
    error_rate_threshold: float = 5.0,
    p95_ms_threshold: float = 500.0,
) -> None:
    """Generate a baton.yaml topology config for a pact-managed project.

    Args:
        project_dir: Root directory of the project.
        output_path: Override output file path. Defaults to baton.yaml
            in the project root.
        sink: Observability sink type ("jsonl" or "otel").
        error_rate_threshold: Canary error rate threshold percent.
        p95_ms_threshold: Canary p95 latency threshold in milliseconds.
    """
    project_dir = Path(project_dir).resolve()

    tree = _load_tree(project_dir)
    if not tree:
        print(f"No decomposition tree found at {project_dir / 'decomposition' / 'tree.json'}")
        print("Run 'pact run' or 'pact daemon' first to decompose your project.")
        return

    components = _get_leaf_components(tree)
    if not components:
        components = _get_all_components(tree)

    if not components:
        print("No components found in decomposition tree.")
        return

    # Assign ports starting from 3000
    base_port = 3000
    nodes = []
    name_to_port = {}

    for i, comp in enumerate(components):
        node_name = _sanitize_name(comp["component_id"])
        port = base_port + i

        node = {
            "name": node_name,
            "port": port,
        }

        # Load contract for metadata
        contract = _load_contract(project_dir, comp["component_id"])
        if not contract:
            contract = comp.get("contract")

        if contract:
            desc = contract.get("description", comp.get("description", ""))
            if desc:
                node["metadata"] = {"description": desc[:100]}

        nodes.append(node)
        name_to_port[comp["component_id"]] = (node_name, port)

    # Build edges from dependency structure
    edges = _build_edges(tree, components, project_dir)
    baton_edges = []
    for edge in edges:
        src_name = _sanitize_name(edge["source"])
        tgt_name = _sanitize_name(edge["target"])
        # Only include edges where both nodes exist
        node_names = {n["name"] for n in nodes}
        if src_name in node_names and tgt_name in node_names:
            baton_edges.append({
                "source": src_name,
                "target": tgt_name,
            })

    # Build the baton.yaml structure
    baton_config: dict = {
        "name": tree.get("root_id", "default"),
        "version": 1,
        "nodes": nodes,
    }

    if baton_edges:
        baton_config["edges"] = baton_edges

    # Observability
    baton_config["observability"] = {
        "enabled": True,
        "sink": sink,
    }
    if sink == "otel":
        baton_config["observability"]["otlp_endpoint"] = "http://localhost:4317"
        baton_config["observability"]["otlp_protocol"] = "grpc"

    # Deploy section with canary thresholds as comments/metadata
    # Baton doesn't have a top-level canary config, but we encode
    # thresholds as deploy metadata for documentation
    baton_config["deploy"] = {
        "provider": "local",
    }

    # Add health check and canary metadata — only for nodes with an HTTP interface
    for i, (node, comp) in enumerate(zip(nodes, components)):
        contract = _load_contract(project_dir, comp["component_id"]) or comp.get("contract") or {}
        node.setdefault("metadata", {})
        if _has_http_interface(contract):
            node["metadata"]["health_check"] = f"http://127.0.0.1:{node['port']}/health"
            node["metadata"]["canary_error_rate_pct"] = str(error_rate_threshold)
            node["metadata"]["canary_p95_ms"] = str(p95_ms_threshold)

    # Write output
    if output_path:
        out = Path(output_path)
    else:
        out = project_dir / "baton.yaml"

    with open(out, "w") as f:
        # Add a header comment
        f.write("# Baton topology config -- generated by pact deploy\n")
        f.write(f"# Canary thresholds: error_rate < {error_rate_threshold}%, p95 < {p95_ms_threshold}ms\n")
        f.write("#\n")
        f.write("# To use: pip install baton-orchestrator && baton up\n")
        f.write("# See: https://github.com/jmcentire/baton\n\n")
        yaml.dump(baton_config, f, default_flow_style=False, sort_keys=False)

    print(f"Generated baton.yaml: {out}")
    print(f"  Nodes: {len(nodes)}")
    print(f"  Edges: {len(baton_edges)}")
    print(f"  Observability: {sink}")
    print(f"  Canary thresholds: error_rate < {error_rate_threshold}%, p95 < {p95_ms_threshold}ms")
    print("\nNext steps:")
    print(f"  1. Review {out}")
    print("  2. baton up --mock       # Boot with mocks")
    print("  3. baton slot <node> <cmd> # Slot live services")
