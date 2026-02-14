"""Tests for decomposer module — interview and decomposition logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from pact.schemas import (
    DecompositionNode,
    DecompositionTree,
    InterviewResult,
)


class TestDecompositionTree:
    """Test tree operations used by the decomposer."""

    def test_single_component(self):
        tree = DecompositionTree(
            root_id="main",
            nodes={
                "main": DecompositionNode(
                    component_id="main", name="Main", description="d",
                ),
            },
        )
        assert tree.topological_order() == ["main"]
        assert tree.leaves() == [tree.nodes["main"]]

    def test_multi_component_ordering(self):
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root", description="r",
                    children=["a", "b"],
                ),
                "a": DecompositionNode(
                    component_id="a", name="A", description="a",
                    parent_id="root",
                ),
                "b": DecompositionNode(
                    component_id="b", name="B", description="b",
                    parent_id="root",
                ),
            },
        )
        order = tree.topological_order()
        # Root must come after children
        assert order.index("root") > order.index("a")
        assert order.index("root") > order.index("b")

    def test_deep_tree(self):
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root", description="r",
                    children=["mid"],
                ),
                "mid": DecompositionNode(
                    component_id="mid", name="Mid", description="m",
                    parent_id="root", children=["leaf"],
                ),
                "leaf": DecompositionNode(
                    component_id="leaf", name="Leaf", description="l",
                    parent_id="mid",
                ),
            },
        )
        order = tree.topological_order()
        assert order == ["leaf", "mid", "root"]


class TestInterviewResult:
    """Test interview data model."""

    def test_no_questions_means_ready(self):
        result = InterviewResult(approved=True)
        assert result.approved

    def test_questions_need_answers(self):
        result = InterviewResult(
            questions=["How should auth work?"],
            assumptions=["Use JWT"],
        )
        assert not result.approved
        result.user_answers["How should auth work?"] = "Use OAuth2"
        result.approved = True
        assert result.approved
