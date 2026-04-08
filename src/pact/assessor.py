"""Architectural assessment engine — mechanical codebase analysis.

Analyzes a Python codebase for structural friction: shallow modules,
hub dependencies, tight coupling, scattered logic, and test coverage
gaps. No LLM calls required. Uses stdlib ast for parsing.

Usage:
    from pact.assessor import assess_codebase, render_assessment_markdown
    report = assess_codebase(Path("src/myproject"))
    print(render_assessment_markdown(report))
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from pact.schemas_assess import (
    AssessmentCategory,
    AssessmentFinding,
    AssessmentReport,
    ModuleMetrics,
)
from pact.schemas_tasks import FindingSeverity

logger = logging.getLogger(__name__)

# Directories to skip during discovery
SKIP_DIRS = frozenset({
    "__pycache__", ".git", ".hg", ".svn", "node_modules", "venv", ".venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    "egg-info", ".eggs", ".pact",
})

# Default thresholds for findings
DEFAULT_THRESHOLDS: dict[str, float] = {
    "shallow_depth_ratio": 5.0,
    "shallow_min_interface": 3,
    "hub_fan_in_warning": 8,
    "hub_fan_in_error": 15,
    "scattered_import_info": 5,
    "scattered_import_warning": 10,
}


def _next_id(counter: list[int]) -> str:
    counter[0] += 1
    return f"A{counter[0]:03d}"


# ── Module Discovery ───────────────────────────────────────────────


def _discover_modules(root: Path) -> list[Path]:
    """Walk directory tree and return Python source files."""
    modules: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        # Skip files in excluded directories
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        # Skip empty __init__.py files (they're structural, not modules)
        if path.name == "__init__.py" and path.stat().st_size < 50:
            continue
        modules.append(path)
    return modules


# ── Module Parsing ─────────────────────────────────────────────────


def _count_loc(source: str) -> int:
    """Count non-empty, non-comment lines."""
    count = 0
    for line in source.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


def _extract_imports(tree: ast.Module) -> list[tuple[str, list[str]]]:
    """Extract import statements from an AST.

    Returns list of (module_name, [imported_names]) tuples.
    For `import X`, imported_names is empty.
    For `from X import a, b`, imported_names is ['a', 'b'].
    """
    imports: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, []))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names = [alias.name for alias in node.names]
                imports.append((node.module, names))
    return imports


def _parse_module(path: Path, root: Path) -> ModuleMetrics:
    """Parse a Python module and compute metrics."""
    rel_path = str(path.relative_to(root))
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ModuleMetrics(path=rel_path)

    loc = _count_loc(source)

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ModuleMetrics(path=rel_path, loc=loc)

    public_functions = 0
    public_classes = 0

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                public_functions += 1
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                public_classes += 1

    interface_size = public_functions + public_classes
    depth_ratio = loc / max(1, interface_size)

    return ModuleMetrics(
        path=rel_path,
        loc=loc,
        public_functions=public_functions,
        public_classes=public_classes,
        interface_size=interface_size,
        depth_ratio=depth_ratio,
    )


# ── Import Graph ───────────────────────────────────────────────────


def _module_path_to_import_name(path: Path, root: Path) -> str:
    """Convert a file path to a dotted module name relative to root.

    e.g., src/pact/health.py relative to src/pact -> health
    """
    rel = path.relative_to(root)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_import(
    module_name: str,
    known_modules: dict[str, str],
) -> str | None:
    """Resolve a dotted import name to a file path if it's intra-project.

    known_modules maps dotted module names to relative file paths.
    Returns the file path if found, None if it's external.
    """
    # Direct match
    if module_name in known_modules:
        return known_modules[module_name]

    # Try progressively shorter prefixes (e.g., pact.health -> pact)
    parts = module_name.split(".")
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in known_modules:
            return known_modules[prefix]

    return None


def _build_import_graph(
    root: Path,
    modules: list[Path],
    metrics: dict[str, ModuleMetrics],
) -> dict[str, set[str]]:
    """Build intra-project import adjacency list.

    Returns dict mapping relative file path -> set of imported file paths.
    Only includes imports that resolve to files within the project.
    """
    # Build lookup: dotted name -> relative path
    # Register both the bare name (e.g., "health") and with root dir as
    # package prefix (e.g., "pact.health") so we match either style.
    known_modules: dict[str, str] = {}
    root_pkg = root.name  # e.g., "pact" if root is src/pact

    for mod_path in modules:
        dotted = _module_path_to_import_name(mod_path, root)
        rel = str(mod_path.relative_to(root))
        known_modules[dotted] = rel
        # Also register with parent package prefix
        if dotted:
            known_modules[f"{root_pkg}.{dotted}"] = rel

    # Register the root package itself
    init = root / "__init__.py"
    if init.exists():
        known_modules[root_pkg] = "__init__.py"

    graph: dict[str, set[str]] = {m.path: set() for m in metrics.values()}

    for mod_path in modules:
        rel = str(mod_path.relative_to(root))
        try:
            source = mod_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (SyntaxError, Exception):
            continue

        imports = _extract_imports(tree)
        for module_name, _names in imports:
            resolved = _resolve_import(module_name, known_modules)
            if resolved and resolved != rel:
                graph.setdefault(rel, set()).add(resolved)

    return graph


def _compute_fan_metrics(
    graph: dict[str, set[str]],
    metrics: dict[str, ModuleMetrics],
) -> None:
    """Compute fan-in and fan-out for each module, mutating metrics in place."""
    # Fan-out: how many modules this one imports
    for path, imports in graph.items():
        if path in metrics:
            metrics[path].fan_out = len(imports)
            metrics[path].imports = sorted(imports)

    # Fan-in: how many modules import this one
    fan_in_counts: dict[str, int] = {}
    for _source, targets in graph.items():
        for target in targets:
            fan_in_counts[target] = fan_in_counts.get(target, 0) + 1

    for path, count in fan_in_counts.items():
        if path in metrics:
            metrics[path].fan_in = count


# ── Check Functions ────────────────────────────────────────────────


def _check_shallow_modules(
    metrics: dict[str, ModuleMetrics],
    counter: list[int],
    thresholds: dict[str, float],
) -> list[AssessmentFinding]:
    """Flag modules where interface complexity rivals implementation complexity."""
    findings: list[AssessmentFinding] = []
    depth_threshold = thresholds.get("shallow_depth_ratio", 5.0)
    min_interface = int(thresholds.get("shallow_min_interface", 3))

    for m in metrics.values():
        if m.interface_size < min_interface:
            continue
        if m.depth_ratio < depth_threshold:
            findings.append(AssessmentFinding(
                id=_next_id(counter),
                severity=FindingSeverity.warning,
                category=AssessmentCategory.shallow_module,
                module_path=m.path,
                description=(
                    f"{m.path}: depth ratio {m.depth_ratio:.1f} "
                    f"({m.loc} LOC / {m.interface_size} public names). "
                    f"Interface is nearly as complex as implementation."
                ),
                suggestion=(
                    "Consider deepening this module by consolidating related "
                    "functionality or hiding complexity behind fewer entry points."
                ),
                metric_value=m.depth_ratio,
            ))

    return findings


def _check_hub_dependencies(
    metrics: dict[str, ModuleMetrics],
    counter: list[int],
    thresholds: dict[str, float],
) -> list[AssessmentFinding]:
    """Flag modules with excessively high fan-in (many dependents)."""
    findings: list[AssessmentFinding] = []
    warn_threshold = int(thresholds.get("hub_fan_in_warning", 8))
    error_threshold = int(thresholds.get("hub_fan_in_error", 15))

    for m in metrics.values():
        if m.fan_in >= error_threshold:
            findings.append(AssessmentFinding(
                id=_next_id(counter),
                severity=FindingSeverity.error,
                category=AssessmentCategory.hub_dependency,
                module_path=m.path,
                description=(
                    f"{m.path}: fan-in of {m.fan_in} — "
                    f"{m.fan_in} other modules depend on this one. "
                    f"Changes here have wide blast radius."
                ),
                suggestion=(
                    "Consider splitting into focused submodules or defining "
                    "a narrower public interface to reduce coupling."
                ),
                metric_value=float(m.fan_in),
            ))
        elif m.fan_in >= warn_threshold:
            findings.append(AssessmentFinding(
                id=_next_id(counter),
                severity=FindingSeverity.warning,
                category=AssessmentCategory.hub_dependency,
                module_path=m.path,
                description=(
                    f"{m.path}: fan-in of {m.fan_in}. "
                    f"Becoming a hub dependency."
                ),
                suggestion=(
                    "Monitor growth. If fan-in increases, consider splitting "
                    "responsibilities."
                ),
                metric_value=float(m.fan_in),
            ))

    return findings


def _check_tight_coupling(
    graph: dict[str, set[str]],
    counter: list[int],
    thresholds: dict[str, float],
) -> list[AssessmentFinding]:
    """Detect mutual imports and strongly connected components."""
    findings: list[AssessmentFinding] = []
    seen_pairs: set[tuple[str, str]] = set()

    # Detect mutual imports (A imports B AND B imports A)
    for source, targets in graph.items():
        for target in targets:
            if target in graph and source in graph[target]:
                pair = tuple(sorted([source, target]))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    findings.append(AssessmentFinding(
                        id=_next_id(counter),
                        severity=FindingSeverity.warning,
                        category=AssessmentCategory.tight_coupling,
                        module_path=pair[0],
                        description=(
                            f"Mutual import: {pair[0]} <-> {pair[1]}. "
                            f"These modules are tightly coupled."
                        ),
                        suggestion=(
                            "Extract shared concepts into a third module, or "
                            "merge if they represent the same responsibility."
                        ),
                        related_modules=[pair[1]],
                    ))

    # Detect strongly connected components (cycles of 3+)
    # Simple iterative DFS-based SCC detection
    all_nodes = set(graph.keys())
    for targets in graph.values():
        all_nodes.update(targets)

    sccs = _find_sccs(graph, all_nodes)
    for scc in sccs:
        if len(scc) >= 3:
            sorted_scc = sorted(scc)
            findings.append(AssessmentFinding(
                id=_next_id(counter),
                severity=FindingSeverity.error,
                category=AssessmentCategory.tight_coupling,
                module_path=sorted_scc[0],
                description=(
                    f"Circular dependency cluster ({len(scc)} modules): "
                    f"{', '.join(sorted_scc[:5])}"
                    f"{'...' if len(scc) > 5 else ''}. "
                    f"These modules cannot be understood or tested independently."
                ),
                suggestion=(
                    "Break the cycle by extracting shared types/interfaces "
                    "into a separate module with no intra-cluster imports."
                ),
                related_modules=sorted_scc[1:],
            ))

    return findings


def _find_sccs(
    graph: dict[str, set[str]],
    all_nodes: set[str],
) -> list[list[str]]:
    """Find strongly connected components using iterative Tarjan's algorithm."""
    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    result: list[list[str]] = []

    def strongconnect(v: str) -> None:
        # Iterative version using an explicit work stack
        work: list[tuple[str, list[str], int]] = []
        work.append((v, sorted(graph.get(v, set())), 0))
        indices[v] = lowlinks[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)

        while work:
            node, neighbors, ni = work[-1]

            if ni < len(neighbors):
                work[-1] = (node, neighbors, ni + 1)
                w = neighbors[ni]
                if w not in indices:
                    indices[w] = lowlinks[w] = index_counter[0]
                    index_counter[0] += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, sorted(graph.get(w, set())), 0))
                elif w in on_stack:
                    lowlinks[node] = min(lowlinks[node], indices[w])
            else:
                # Done with this node's neighbors
                if lowlinks[node] == indices[node]:
                    scc: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        scc.append(w)
                        if w == node:
                            break
                    if len(scc) > 1:
                        result.append(scc)
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlinks[parent] = min(lowlinks[parent], lowlinks[node])

    for node in sorted(all_nodes):
        if node not in indices:
            strongconnect(node)

    return result


def _check_scattered_logic(
    root: Path,
    modules: list[Path],
    known_modules: set[str],
    counter: list[int],
    thresholds: dict[str, float],
) -> list[AssessmentFinding]:
    """Flag specific intra-project imports that appear in many files."""
    findings: list[AssessmentFinding] = []
    info_threshold = int(thresholds.get("scattered_import_info", 5))
    warn_threshold = int(thresholds.get("scattered_import_warning", 10))

    # Track: (module, name) -> set of files that import it
    import_sites: dict[tuple[str, str], set[str]] = {}

    for mod_path in modules:
        rel = str(mod_path.relative_to(root))
        try:
            source = mod_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (SyntaxError, Exception):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                # Only track intra-project imports
                top_pkg = node.module.split(".")[0]
                if top_pkg not in known_modules:
                    continue
                for alias in node.names:
                    key = (node.module, alias.name)
                    import_sites.setdefault(key, set()).add(rel)

    for (module_name, name), files in sorted(import_sites.items()):
        count = len(files)
        if count >= warn_threshold:
            severity = FindingSeverity.warning
        elif count >= info_threshold:
            severity = FindingSeverity.info
        else:
            continue

        findings.append(AssessmentFinding(
            id=_next_id(counter),
            severity=severity,
            category=AssessmentCategory.scattered_logic,
            module_path=f"{module_name}",
            description=(
                f"`from {module_name} import {name}` appears in {count} files. "
                f"This concept may be scattered rather than centralized."
            ),
            suggestion=(
                "If these callers share logic around this import, consider "
                "creating a higher-level abstraction they can use instead."
            ),
            metric_value=float(count),
            related_modules=sorted(files)[:10],
        ))

    return findings


def _check_test_gaps(
    root: Path,
    modules: list[Path],
    counter: list[int],
    thresholds: dict[str, float],
) -> list[AssessmentFinding]:
    """Flag source modules that have no corresponding test file."""
    findings: list[AssessmentFinding] = []

    # Find all test files in the project
    test_files: set[str] = set()
    for path in root.parent.rglob("test_*.py"):
        test_files.add(path.stem)  # e.g., "test_health"
    for path in root.parent.rglob("*_test.py"):
        test_files.add(path.stem)

    # Also check a tests/ directory at the same level as root
    tests_dir = root.parent / "tests"
    if tests_dir.exists():
        for path in tests_dir.rglob("test_*.py"):
            test_files.add(path.stem)

    for mod_path in modules:
        if mod_path.name.startswith("test_"):
            continue
        stem = mod_path.stem
        expected_test = f"test_{stem}"
        if expected_test not in test_files:
            rel = str(mod_path.relative_to(root))
            findings.append(AssessmentFinding(
                id=_next_id(counter),
                severity=FindingSeverity.info,
                category=AssessmentCategory.test_gap,
                module_path=rel,
                description=(
                    f"{rel}: no test file found (expected {expected_test}.py)."
                ),
                suggestion="Add tests to improve coverage and catch regressions.",
                metric_value=0.0,
            ))

    return findings


# ── Main Entry Point ───────────────────────────────────────────────


def assess_codebase(
    root: Path,
    language: str = "python",
    thresholds: dict[str, float] | None = None,
) -> AssessmentReport:
    """Assess a codebase for architectural friction.

    Args:
        root: Root directory of the codebase to analyze.
        language: Programming language (currently only "python" supported).
        thresholds: Override default thresholds for finding sensitivity.

    Returns:
        AssessmentReport with findings and per-module metrics.
    """
    effective_thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    root = root.resolve()

    # Discover and parse modules
    modules = _discover_modules(root)
    if not modules:
        return AssessmentReport(
            root_path=str(root),
            language=language,
            summary="No Python modules found.",
        )

    metrics: dict[str, ModuleMetrics] = {}
    for mod_path in modules:
        m = _parse_module(mod_path, root)
        metrics[m.path] = m

    # Build import graph and compute fan metrics
    graph = _build_import_graph(root, modules, metrics)
    _compute_fan_metrics(graph, metrics)

    # Collect top-level package names for filtering intra-project imports
    known_top_packages: set[str] = {root.name}
    for mod_path in modules:
        rel = mod_path.relative_to(root)
        known_top_packages.add(rel.parts[0].replace(".py", ""))

    # Run checks
    counter = [0]
    findings: list[AssessmentFinding] = []
    findings.extend(_check_shallow_modules(metrics, counter, effective_thresholds))
    findings.extend(_check_hub_dependencies(metrics, counter, effective_thresholds))
    findings.extend(_check_tight_coupling(graph, counter, effective_thresholds))
    findings.extend(_check_scattered_logic(root, modules, known_top_packages, counter, effective_thresholds))
    findings.extend(_check_test_gaps(root, modules, counter, effective_thresholds))

    # Sort by severity (error > warning > info), then by ID
    severity_order = {FindingSeverity.error: 0, FindingSeverity.warning: 1, FindingSeverity.info: 2}
    findings.sort(key=lambda f: (severity_order.get(f.severity, 9), f.id))

    # Build summary
    error_count = sum(1 for f in findings if f.severity == FindingSeverity.error)
    warn_count = sum(1 for f in findings if f.severity == FindingSeverity.warning)
    info_count = sum(1 for f in findings if f.severity == FindingSeverity.info)
    summary = (
        f"{len(modules)} modules analyzed. "
        f"{error_count} error(s), {warn_count} warning(s), {info_count} info(s)."
    )

    return AssessmentReport(
        root_path=str(root),
        language=language,
        findings=findings,
        module_metrics=sorted(metrics.values(), key=lambda m: m.path),
        summary=summary,
    )


# ── Markdown Rendering ─────────────────────────────────────────────


def render_assessment_markdown(report: AssessmentReport) -> str:
    """Render an AssessmentReport as human-readable markdown."""
    lines: list[str] = []
    lines.append(f"# Architectural Assessment: {report.root_path}")
    lines.append("")
    lines.append(f"**{report.summary}**")
    lines.append("")

    if not report.findings:
        lines.append("No architectural findings.")
        return "\n".join(lines)

    # Group by severity
    for severity, label in [
        (FindingSeverity.error, "Errors"),
        (FindingSeverity.warning, "Warnings"),
        (FindingSeverity.info, "Info"),
    ]:
        group = [f for f in report.findings if f.severity == severity]
        if not group:
            continue
        lines.append(f"## {label} ({len(group)})")
        lines.append("")
        for f in group:
            lines.append(f"- **[{f.id}]** ({f.category}) {f.description}")
            if f.suggestion:
                lines.append(f"  - Suggestion: {f.suggestion}")
        lines.append("")

    # Module metrics table (top 20 by fan_in, descending)
    top_modules = sorted(report.module_metrics, key=lambda m: m.fan_in, reverse=True)[:20]
    if top_modules:
        lines.append("## Top Modules by Fan-In")
        lines.append("")
        lines.append("| Module | LOC | Public | Depth | Fan-In | Fan-Out |")
        lines.append("|--------|-----|--------|-------|--------|---------|")
        for m in top_modules:
            lines.append(
                f"| {m.path} | {m.loc} | {m.interface_size} "
                f"| {m.depth_ratio:.1f} | {m.fan_in} | {m.fan_out} |"
            )
        lines.append("")

    return "\n".join(lines)
