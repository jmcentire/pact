"""Tests for design document rendering."""

from __future__ import annotations

from pact.design_doc import render_design_doc, update_design_doc
from pact.schemas import (
    DecompositionNode,
    DecompositionTree,
    DesignDocument,
    EngineeringDecision,
    FailureRecord,
    TestResults,
)


class TestRenderDesignDoc:
    def test_minimal(self):
        doc = DesignDocument(
            project_id="test",
            title="Test Design",
        )
        md = render_design_doc(doc)
        assert "# Test Design" in md
        assert "Version 1" in md

    def test_with_summary(self):
        doc = DesignDocument(
            project_id="test",
            title="Test",
            summary="A pricing engine",
        )
        md = render_design_doc(doc)
        assert "A pricing engine" in md

    def test_with_tree(self):
        tree = DecompositionTree(
            root_id="root",
            nodes={
                "root": DecompositionNode(
                    component_id="root", name="Root",
                    description="Root component",
                    children=["a"],
                    implementation_status="pending",
                ),
                "a": DecompositionNode(
                    component_id="a", name="Component A",
                    description="First component",
                    parent_id="root",
                    implementation_status="tested",
                    test_results=TestResults(total=5, passed=5),
                ),
            },
        )
        doc = DesignDocument(
            project_id="test",
            title="Test",
            decomposition_tree=tree,
        )
        md = render_design_doc(doc)
        assert "Root" in md
        assert "Component A" in md
        assert "[+]" in md  # tested status
        assert "5/5 passed" in md

    def test_with_decisions(self):
        doc = DesignDocument(
            project_id="test",
            title="Test",
            engineering_decisions=[
                EngineeringDecision(
                    ambiguity="Auth method",
                    decision="Use JWT",
                    rationale="Simpler for API",
                ),
            ],
        )
        md = render_design_doc(doc)
        assert "Engineering Decisions" in md
        assert "Auth method" in md
        assert "JWT" in md

    def test_with_failures(self):
        doc = DesignDocument(
            project_id="test",
            title="Test",
            failure_history=[
                FailureRecord(
                    component_id="pricing",
                    failure_type="implementation_bug",
                    description="Off by one error",
                    resolution="Fixed boundary check",
                ),
            ],
        )
        md = render_design_doc(doc)
        assert "Failure History" in md
        assert "implementation_bug" in md

    def test_with_lessons(self):
        doc = DesignDocument(
            project_id="test",
            title="Test",
            lessons_learned=["Always validate inputs", "Mock external services"],
        )
        md = render_design_doc(doc)
        assert "Lessons Learned" in md
        assert "validate inputs" in md


class TestUpdateDesignDoc:
    def test_updates_tree(self):
        doc = DesignDocument(project_id="test", title="Test")
        tree = DecompositionTree(
            root_id="root",
            nodes={"root": DecompositionNode(
                component_id="root", name="Root", description="r",
            )},
        )
        updated = update_design_doc(doc, tree=tree)
        assert updated.decomposition_tree is not None
        assert updated.version == 2

    def test_increments_version(self):
        doc = DesignDocument(project_id="test", title="Test", version=3)
        updated = update_design_doc(doc)
        assert updated.version == 4
