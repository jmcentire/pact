"""Cross-artifact analysis — purely mechanical consistency checks.

Validates consistency across decomposition tree, contracts, and test suites.
No LLM calls required.
"""

from __future__ import annotations

from collections import Counter

from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionTree,
    EngineeringDecision,
)
from pact.schemas_shaping import ShapingPitch
from pact.schemas_tasks import (
    AnalysisFinding,
    AnalysisReport,
    FindingCategory,
    FindingSeverity,
)


# Vague words that indicate ambiguity in descriptions
VAGUE_WORDS = frozenset({
    "etc", "maybe", "various", "somehow", "stuff", "things",
    "probably", "possibly", "whatever", "something", "somehow",
    "tbd", "todo", "fixme", "hack",
})

# Minimum description length (word count) before flagging
MIN_DESCRIPTION_WORDS = 10


def _next_id(counter: list[int]) -> str:
    counter[0] += 1
    return f"F{counter[0]:03d}"


def _check_coverage(
    tree: DecompositionTree,
    contracts: dict[str, ComponentContract],
    test_suites: dict[str, ContractTestSuite],
    counter: list[int],
) -> list[AnalysisFinding]:
    """Check for coverage gaps: components without contracts, contracts without tests."""
    findings: list[AnalysisFinding] = []

    for node_id, node in tree.nodes.items():
        # Component without contract
        if node_id not in contracts:
            findings.append(AnalysisFinding(
                id=_next_id(counter),
                severity=FindingSeverity.error,
                category=FindingCategory.coverage_gap,
                component_id=node_id,
                description=f"Component '{node.name}' ({node_id}) has no contract",
                suggestion="Generate a contract for this component",
            ))
            continue

        # Contract without test suite
        if node_id not in test_suites:
            findings.append(AnalysisFinding(
                id=_next_id(counter),
                severity=FindingSeverity.error,
                category=FindingCategory.coverage_gap,
                component_id=node_id,
                description=f"Component '{node.name}' ({node_id}) has no test suite",
                suggestion="Generate a test suite from the contract",
            ))
            continue

        # Functions without test cases
        contract = contracts[node_id]
        suite = test_suites[node_id]
        tested_functions = {tc.function for tc in suite.test_cases}
        for fn in contract.functions:
            if fn.name not in tested_functions:
                findings.append(AnalysisFinding(
                    id=_next_id(counter),
                    severity=FindingSeverity.warning,
                    category=FindingCategory.coverage_gap,
                    component_id=node_id,
                    description=f"Function '{fn.name}' in '{node.name}' has no test cases",
                    suggestion=f"Add test cases for {fn.name}",
                ))

    return findings


def _check_ambiguity(
    contracts: dict[str, ComponentContract],
    counter: list[int],
) -> list[AnalysisFinding]:
    """Check for ambiguous or vague descriptions."""
    findings: list[AnalysisFinding] = []

    for cid, contract in contracts.items():
        # Short contract description
        words = contract.description.split()
        if len(words) < MIN_DESCRIPTION_WORDS:
            findings.append(AnalysisFinding(
                id=_next_id(counter),
                severity=FindingSeverity.warning,
                category=FindingCategory.ambiguity,
                component_id=cid,
                description=f"Contract description for '{contract.name}' is too short ({len(words)} words)",
                suggestion="Expand the description to at least 10 words",
            ))

        # Vague words in description
        lower_words = {w.lower().strip(".,!?;:()") for w in words}
        vague_found = lower_words & VAGUE_WORDS
        if vague_found:
            findings.append(AnalysisFinding(
                id=_next_id(counter),
                severity=FindingSeverity.warning,
                category=FindingCategory.ambiguity,
                component_id=cid,
                description=f"Contract '{contract.name}' contains vague words: {', '.join(sorted(vague_found))}",
                suggestion="Replace vague terms with specific requirements",
            ))

        # Check function descriptions too
        for fn in contract.functions:
            fn_words = fn.description.split()
            fn_lower = {w.lower().strip(".,!?;:()") for w in fn_words}
            fn_vague = fn_lower & VAGUE_WORDS
            if fn_vague:
                findings.append(AnalysisFinding(
                    id=_next_id(counter),
                    severity=FindingSeverity.info,
                    category=FindingCategory.ambiguity,
                    component_id=cid,
                    description=f"Function '{fn.name}' in '{contract.name}' contains vague words: {', '.join(sorted(fn_vague))}",
                    suggestion="Replace vague terms with specific requirements",
                ))

    return findings


def _check_duplication(
    contracts: dict[str, ComponentContract],
    counter: list[int],
) -> list[AnalysisFinding]:
    """Check for duplicate type names and similar function signatures."""
    findings: list[AnalysisFinding] = []

    # Duplicate type names across contracts
    type_locations: dict[str, list[str]] = {}
    for cid, contract in contracts.items():
        for t in contract.types:
            type_locations.setdefault(t.name, []).append(cid)

    for type_name, locations in type_locations.items():
        if len(locations) >= 2:
            findings.append(AnalysisFinding(
                id=_next_id(counter),
                severity=FindingSeverity.warning,
                category=FindingCategory.duplication,
                description=f"Type '{type_name}' is defined in multiple contracts: {', '.join(locations)}",
                suggestion="Consider extracting to a shared types module",
                artifacts=[f"contracts/{cid}/interface.json" for cid in locations],
            ))

    # Similar function signatures across contracts
    sig_locations: dict[str, list[tuple[str, str]]] = {}
    for cid, contract in contracts.items():
        for fn in contract.functions:
            input_types = tuple(i.type_ref for i in fn.inputs)
            sig = f"{fn.output_type}({','.join(input_types)})"
            sig_locations.setdefault(sig, []).append((cid, fn.name))

    for sig, locations in sig_locations.items():
        if len(locations) >= 2:
            names = [f"{cid}.{fn}" for cid, fn in locations]
            findings.append(AnalysisFinding(
                id=_next_id(counter),
                severity=FindingSeverity.info,
                category=FindingCategory.duplication,
                description=f"Similar function signatures: {', '.join(names)} (signature: {sig})",
                suggestion="Check if these could share a common implementation",
            ))

    return findings


def _check_consistency(
    tree: DecompositionTree,
    contracts: dict[str, ComponentContract],
    counter: list[int],
) -> list[AnalysisFinding]:
    """Check consistency between tree structure and contracts."""
    findings: list[AnalysisFinding] = []

    for cid, contract in contracts.items():
        node = tree.nodes.get(cid)
        if not node:
            continue

        # Contract dependencies should match tree children (for non-leaf nodes)
        if node.children:
            child_set = set(node.children)
            dep_set = set(contract.dependencies)
            missing_deps = child_set - dep_set
            extra_deps = dep_set - child_set

            # Only flag missing children — extra deps might be valid cross-references
            if missing_deps:
                findings.append(AnalysisFinding(
                    id=_next_id(counter),
                    severity=FindingSeverity.error,
                    category=FindingCategory.consistency,
                    component_id=cid,
                    description=f"Contract for '{contract.name}' is missing dependencies on tree children: {', '.join(sorted(missing_deps))}",
                    suggestion="Add missing children as contract dependencies",
                ))

        # Cross-boundary type references should resolve
        for fn in contract.functions:
            for inp in fn.inputs:
                if "." in inp.type_ref:
                    ref_component = inp.type_ref.split(".")[0]
                    if ref_component not in contracts:
                        findings.append(AnalysisFinding(
                            id=_next_id(counter),
                            severity=FindingSeverity.warning,
                            category=FindingCategory.consistency,
                            component_id=cid,
                            description=f"Cross-boundary type reference '{inp.type_ref}' in '{fn.name}' references unknown component",
                            suggestion=f"Ensure component '{ref_component}' exists and has the referenced type",
                        ))

    return findings


def analyze_project(
    tree: DecompositionTree,
    contracts: dict[str, ComponentContract],
    test_suites: dict[str, ContractTestSuite],
    decisions: list[EngineeringDecision] | None = None,
    pitch: ShapingPitch | None = None,
) -> AnalysisReport:
    """Run cross-artifact analysis on a project.

    Performs purely mechanical checks:
    - Coverage: components without contracts, functions without tests
    - Ambiguity: short/vague descriptions
    - Duplication: identical type names, similar function signatures
    - Consistency: contract deps vs tree children, cross-boundary refs
    """
    counter = [0]
    findings: list[AnalysisFinding] = []

    findings.extend(_check_coverage(tree, contracts, test_suites, counter))
    findings.extend(_check_ambiguity(contracts, counter))
    findings.extend(_check_duplication(contracts, counter))
    findings.extend(_check_consistency(tree, contracts, counter))

    # Summary
    errors = sum(1 for f in findings if f.severity == FindingSeverity.error)
    warnings = sum(1 for f in findings if f.severity == FindingSeverity.warning)
    infos = sum(1 for f in findings if f.severity == FindingSeverity.info)
    summary = f"{errors} error(s), {warnings} warning(s), {infos} info(s)"

    return AnalysisReport(
        project_id=tree.root_id,
        findings=findings,
        summary=summary,
    )


def render_analysis_markdown(report: AnalysisReport) -> str:
    """Render an analysis report as markdown."""
    lines: list[str] = []
    lines.append(f"# Cross-Artifact Analysis — {report.project_id}")
    lines.append(f"Summary: {report.summary}")
    lines.append("")

    if not report.findings:
        lines.append("No findings. All artifacts are consistent.")
        lines.append("")
        return "\n".join(lines)

    # Group by severity
    for severity in (FindingSeverity.error, FindingSeverity.warning, FindingSeverity.info):
        items = [f for f in report.findings if f.severity == severity]
        if not items:
            continue

        lines.append(f"## {severity.value.upper()}S ({len(items)})")
        lines.append("")
        for f in items:
            component_tag = f" [{f.component_id}]" if f.component_id else ""
            lines.append(f"- **{f.id}**{component_tag} [{f.category}] {f.description}")
            if f.suggestion:
                lines.append(f"  - Suggestion: {f.suggestion}")
        lines.append("")

    return "\n".join(lines)
