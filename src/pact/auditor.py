"""Spec-compliance audit — compare task.md requirements against implementations.

Uses an LLM to extract requirements from the spec and check whether each
requirement is covered by the implementation source code.
"""

from __future__ import annotations

from pact.agents.base import AgentBase
from pact.project import ProjectManager
from pact.schemas import RequirementCoverage, SpecAuditResult


AUDIT_SYSTEM = """\
You are a spec-compliance auditor. You will be given a project specification and \
implementation source code. Your job is to:

1. Extract all functional requirements from the specification.
2. For each requirement, determine if the implementation covers it:
   - "covered": Clearly implemented with matching code
   - "partial": Partially implemented or missing edge cases
   - "gap": Not implemented at all
3. Provide evidence (component/file names) for covered requirements.
4. Provide notes explaining what's missing for partial/gap requirements.

Be thorough but fair. Look for semantic coverage, not just keyword matching.
"""


async def audit_spec_compliance(
    agent: AgentBase,
    project: ProjectManager,
) -> SpecAuditResult:
    """Compare task.md requirements against implementations."""
    task_md = project.task_path.read_text() if project.task_path.exists() else ""
    if not task_md:
        return SpecAuditResult(summary="No task.md found — nothing to audit.")

    # Gather all implementation source code
    tree = project.load_tree()
    implementations: dict[str, str] = {}
    if tree:
        for comp_id in tree.nodes:
            impl_src = project.impl_src_dir(comp_id)
            if impl_src.exists():
                for src_file in impl_src.rglob("*"):
                    if src_file.is_file() and src_file.suffix in (".py", ".ts", ".js"):
                        key = f"{comp_id}/{src_file.name}"
                        implementations[key] = src_file.read_text()

    if not implementations:
        return SpecAuditResult(summary="No implementations found — nothing to audit against.")

    # Build implementation summary for the prompt
    impl_text = ""
    for path, code in implementations.items():
        impl_text += f"\n\n=== {path} ===\n{code}"

    prompt = f"""\
## Specification (task.md)

{task_md}

## Implementation Source Code

{impl_text}

Extract all functional requirements from the specification and assess coverage.
"""

    result, _in_tokens, _out_tokens = await agent.assess(
        SpecAuditResult, prompt, AUDIT_SYSTEM, max_tokens=16384,
    )

    # Recompute counts from requirements list
    result.total_count = len(result.requirements)
    result.covered_count = sum(1 for r in result.requirements if r.status == "covered")
    result.partial_count = sum(1 for r in result.requirements if r.status == "partial")
    result.gap_count = sum(1 for r in result.requirements if r.status == "gap")

    if result.total_count > 0:
        pct = result.covered_count / result.total_count * 100
        result.summary = (
            f"{result.covered_count}/{result.total_count} requirements fully covered ({pct:.0f}%)"
        )

    return result


def render_audit_markdown(result: SpecAuditResult) -> str:
    """Render a SpecAuditResult as human-readable markdown."""
    lines = ["Spec Compliance Audit", "=" * 21]

    if not result.requirements:
        lines.append(result.summary or "No requirements found.")
        return "\n".join(lines)

    for req in result.requirements:
        icon = {"covered": "COVERED", "partial": "PARTIAL", "gap": "GAP"}[req.status]
        lines.append(f"  [{icon}]  {req.requirement}")
        if req.evidence:
            lines.append(f"           Evidence: {req.evidence}")
        if req.notes:
            lines.append(f"           Note: {req.notes}")

    lines.append("")
    lines.append(f"Coverage: {result.summary}")
    return "\n".join(lines)
