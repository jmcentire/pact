"""Tests for integrator module — composition logic."""

from __future__ import annotations

from pact.schemas import (
    ComponentContract,
    DecompositionNode,
    DecompositionTree,
    FieldSpec,
    FunctionContract,
)


class TestIntegrationTree:
    """Test tree operations relevant to integration."""

    def test_non_leaves_need_integration(self):
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root", description="r",
                    children=["a", "b"],
                ),
                "a": DecompositionNode(
                    component_id="a", name="A", description="a", parent_id="root",
                ),
                "b": DecompositionNode(
                    component_id="b", name="B", description="b", parent_id="root",
                ),
            },
        )
        non_leaves = [n for n in tree.nodes.values() if n.children]
        assert len(non_leaves) == 1
        assert non_leaves[0].component_id == "root"

    def test_all_leaves_skip_integration(self):
        tree = DecompositionTree(
            root_id="main",
            nodes={
                "main": DecompositionNode(
                    component_id="main", name="Main", description="d",
                ),
            },
        )
        non_leaves = [n for n in tree.nodes.values() if n.children]
        assert len(non_leaves) == 0

    def test_child_contracts_for_integration(self):
        """Verify we can gather child contracts for a parent."""
        contracts = {
            "root": ComponentContract(
                component_id="root", name="Root", description="r",
                dependencies=["a", "b"],
                functions=[FunctionContract(
                    name="process", description="d",
                    inputs=[], output_type="str",
                )],
            ),
            "a": ComponentContract(
                component_id="a", name="A", description="a",
                functions=[FunctionContract(
                    name="do_a", description="d",
                    inputs=[], output_type="str",
                )],
            ),
            "b": ComponentContract(
                component_id="b", name="B", description="b",
                functions=[FunctionContract(
                    name="do_b", description="d",
                    inputs=[], output_type="int",
                )],
            ),
        }

        parent = contracts["root"]
        child_contracts = {
            dep: contracts[dep]
            for dep in parent.dependencies
            if dep in contracts
        }
        assert len(child_contracts) == 2
        assert "a" in child_contracts
        assert "b" in child_contracts
