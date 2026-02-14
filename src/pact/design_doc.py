"""Living design document generation.

Renders the DesignDocument model into a human-readable markdown file.
Updated after each major phase transition.
"""

from __future__ import annotations

from pact.schemas import (
    DecompositionNode,
    DecompositionTree,
    DesignDocument,
    FailureRecord,
)


def render_design_doc(doc: DesignDocument) -> str:
    """Render a DesignDocument to markdown."""
    lines = [
        f"# {doc.title}",
        "",
        f"*Version {doc.version} — Auto-maintained by pact*",
        "",
    ]

    if doc.summary:
        lines.extend(["## Summary", "", doc.summary, ""])

    if doc.decomposition_tree:
        lines.extend(_render_tree(doc.decomposition_tree))

    if doc.engineering_decisions:
        lines.extend(["## Engineering Decisions", ""])
        for d in doc.engineering_decisions:
            lines.append(f"### {d.ambiguity}")
            lines.append(f"**Decision:** {d.decision}")
            lines.append(f"**Rationale:** {d.rationale}")
            lines.append("")

    if doc.failure_history:
        lines.extend(["## Failure History", ""])
        lines.extend(_render_failures(doc.failure_history))

    if doc.lessons_learned:
        lines.extend(["## Lessons Learned", ""])
        for lesson in doc.lessons_learned:
            lines.append(f"- {lesson}")
        lines.append("")

    return "\n".join(lines)


def _render_tree(tree: DecompositionTree) -> list[str]:
    """Render the decomposition tree as nested markdown."""
    lines = ["## Decomposition", ""]

    root = tree.nodes.get(tree.root_id)
    if not root:
        lines.append("*No decomposition yet*")
        lines.append("")
        return lines

    def render_node(node: DecompositionNode, indent: int = 0) -> None:
        prefix = "  " * indent
        status_icon = _status_icon(node.implementation_status)
        lines.append(f"{prefix}- {status_icon} **{node.name}** (`{node.component_id}`)")
        if node.description:
            lines.append(f"{prefix}  {node.description}")
        if node.test_results:
            tr = node.test_results
            lines.append(
                f"{prefix}  Tests: {tr.passed}/{tr.total} passed"
                + (f", {tr.failed} failed" if tr.failed else "")
            )
        for child_id in node.children:
            child = tree.nodes.get(child_id)
            if child:
                render_node(child, indent + 1)

    render_node(root)
    lines.append("")
    return lines


def _status_icon(status: str) -> str:
    return {
        "pending": "[ ]",
        "contracted": "[C]",
        "implemented": "[I]",
        "tested": "[+]",
        "failed": "[X]",
    }.get(status, "[ ]")


def _render_failures(failures: list[FailureRecord]) -> list[str]:
    lines = []
    for f in failures:
        lines.append(f"### {f.component_id} — {f.failure_type}")
        lines.append(f"{f.description}")
        if f.resolution:
            lines.append(f"**Resolution:** {f.resolution}")
        if f.timestamp:
            lines.append(f"*{f.timestamp}*")
        lines.append("")
    return lines


def update_design_doc(
    doc: DesignDocument,
    tree: DecompositionTree | None = None,
) -> DesignDocument:
    """Update the design document with current state."""
    if tree:
        doc.decomposition_tree = tree
    doc.version += 1
    return doc
