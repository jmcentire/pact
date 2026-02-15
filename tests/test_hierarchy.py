"""Tests for hierarchy locality validation."""
from pact.contracts import validate_hierarchy_locality
from pact.schemas import (
    ComponentContract,
    DecompositionNode,
    DecompositionTree,
)


def _make_tree(nodes_spec: list[tuple[str, str, list[str]]]) -> DecompositionTree:
    """Helper: nodes_spec = [(id, parent_id, [children])]"""
    nodes = {}
    root_id = nodes_spec[0][0] if nodes_spec else "root"
    for nid, parent, children in nodes_spec:
        depth = 0
        if parent:
            # Count depth by walking up
            p = parent
            while p:
                depth += 1
                pnode = next((s for s in nodes_spec if s[0] == p), None)
                p = pnode[1] if pnode else ""
        nodes[nid] = DecompositionNode(
            component_id=nid, name=nid, description=f"Component {nid}",
            depth=depth, parent_id=parent, children=children,
        )
    return DecompositionTree(root_id=root_id, nodes=nodes)


def _make_contract(cid: str, deps: list[str]) -> ComponentContract:
    return ComponentContract(
        component_id=cid, name=cid, description=f"Contract {cid}",
        dependencies=deps,
    )


class TestHierarchyLocality:
    def test_sibling_dep_no_warning(self):
        """A depends on B, both children of root -> no warning."""
        tree = _make_tree([
            ("root", "", ["a", "b"]),
            ("a", "root", []),
            ("b", "root", []),
        ])
        contracts = {
            "a": _make_contract("a", ["b"]),
            "b": _make_contract("b", []),
        }
        warnings = validate_hierarchy_locality(tree, contracts)
        assert warnings == []

    def test_parent_dep_no_warning(self):
        """Child depends on parent -> no warning."""
        tree = _make_tree([
            ("root", "", ["a"]),
            ("a", "root", ["a1"]),
            ("a1", "a", []),
        ])
        contracts = {
            "a1": _make_contract("a1", ["a"]),
            "a": _make_contract("a", []),
        }
        warnings = validate_hierarchy_locality(tree, contracts)
        assert warnings == []

    def test_uncle_dep_no_warning(self):
        """A1 (child of A) depends on B (sibling of A) -> no warning (uncle)."""
        tree = _make_tree([
            ("root", "", ["a", "b"]),
            ("a", "root", ["a1"]),
            ("a1", "a", []),
            ("b", "root", []),
        ])
        contracts = {
            "a1": _make_contract("a1", ["b"]),
            "b": _make_contract("b", []),
        }
        warnings = validate_hierarchy_locality(tree, contracts)
        assert warnings == []

    def test_distant_cousin_warns(self):
        """A1 (child of A) depends on B1 (child of B) -> warning (distant cousin)."""
        tree = _make_tree([
            ("root", "", ["a", "b"]),
            ("a", "root", ["a1"]),
            ("a1", "a", []),
            ("b", "root", ["b1"]),
            ("b1", "b", []),
        ])
        contracts = {
            "a1": _make_contract("a1", ["b1"]),
            "b1": _make_contract("b1", []),
        }
        warnings = validate_hierarchy_locality(tree, contracts)
        assert len(warnings) == 1
        assert "a1" in warnings[0]
        assert "b1" in warnings[0]

    def test_cross_subtree_warns(self):
        """Deep cross-tree dependency generates warning."""
        tree = _make_tree([
            ("root", "", ["a", "b"]),
            ("a", "root", ["a1"]),
            ("a1", "a", ["a1x"]),
            ("a1x", "a1", []),
            ("b", "root", ["b1"]),
            ("b1", "b", ["b1y"]),
            ("b1y", "b1", []),
        ])
        contracts = {
            "a1x": _make_contract("a1x", ["b1y"]),
            "b1y": _make_contract("b1y", []),
        }
        warnings = validate_hierarchy_locality(tree, contracts)
        assert len(warnings) == 1
        assert "Distant dependency" in warnings[0]

    def test_external_dep_ignored(self):
        """Dependency not in tree (external) -> no warning."""
        tree = _make_tree([
            ("root", "", ["a"]),
            ("a", "root", []),
        ])
        contracts = {
            "a": _make_contract("a", ["external_lib"]),
        }
        warnings = validate_hierarchy_locality(tree, contracts)
        assert warnings == []

    def test_empty_tree_no_warnings(self):
        tree = DecompositionTree(root_id="root", nodes={})
        contracts = {}
        warnings = validate_hierarchy_locality(tree, contracts)
        assert warnings == []

    def test_no_dependencies_no_warnings(self):
        tree = _make_tree([
            ("root", "", ["a", "b"]),
            ("a", "root", []),
            ("b", "root", []),
        ])
        contracts = {
            "a": _make_contract("a", []),
            "b": _make_contract("b", []),
        }
        warnings = validate_hierarchy_locality(tree, contracts)
        assert warnings == []

    def test_child_dep_no_warning(self):
        """Parent depends on its child -> no warning."""
        tree = _make_tree([
            ("root", "", ["a"]),
            ("a", "root", ["a1"]),
            ("a1", "a", []),
        ])
        contracts = {
            "a": _make_contract("a", ["a1"]),
            "a1": _make_contract("a1", []),
        }
        warnings = validate_hierarchy_locality(tree, contracts)
        assert warnings == []
