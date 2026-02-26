"""Codebase analyzer — mechanical AST-based analysis (no LLM).

Discovers source/test files, extracts function signatures, computes
cyclomatic complexity, maps test coverage, and detects security-sensitive
branches. All stdlib, no external dependencies.
"""

from __future__ import annotations

import ast
import logging
import os
import textwrap
from pathlib import Path

from pact.schemas_testgen import (
    CodebaseAnalysis,
    CoverageEntry,
    CoverageMap,
    ExtractedFunction,
    ExtractedParameter,
    SecurityAuditReport,
    SecurityFinding,
    SecurityRiskLevel,
    SourceFile,
    TestFile,
)

logger = logging.getLogger(__name__)

# Directories to always skip
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "venv", ".venv", "env", ".env",
    "node_modules",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".pact",
    ".tox", ".nox",
    "dist", "build", "egg-info",
})

# Security-sensitive variable names (lowercase)
SECURITY_VARIABLES = frozenset({
    "admin", "is_admin", "role", "roles", "permission", "permissions",
    "authorized", "auth", "token", "tokens", "privilege", "privileges",
    "credential", "credentials", "secret", "secrets", "password",
    "passwords", "api_key", "api_keys", "sudo", "root", "grant",
    "superuser", "is_superuser", "is_staff", "is_authenticated",
})

# Security-sensitive function call patterns (lowercase prefixes)
SECURITY_CALL_PREFIXES = (
    "grant_", "revoke_", "elevate_", "authenticate", "authorize",
    "set_role", "set_permission", "check_permission", "check_role",
    "verify_token", "validate_token", "check_auth",
)


# ── File Discovery ─────────────────────────────────────────────────


def _should_skip_dir(name: str) -> bool:
    """Check if a directory should be skipped."""
    return name in SKIP_DIRS or name.endswith(".egg-info")


def discover_source_files(root: str | Path, language: str = "python") -> list[SourceFile]:
    """Walk directory tree and find source files, skipping common non-source dirs.

    Returns list of SourceFile with path set but functions not yet extracted.
    """
    root = Path(root).resolve()
    ext = ".py" if language == "python" else ".ts"
    source_files: list[SourceFile] = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Filter out skip dirs in-place to prevent os.walk from descending
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]

        rel_dir = Path(dirpath).relative_to(root)
        # Skip test directories for source discovery
        if any(part.startswith("test") for part in rel_dir.parts):
            continue

        for fname in sorted(filenames):
            if not fname.endswith(ext):
                continue
            # Skip test files
            if fname.startswith("test_") or fname.endswith(f"_test{ext}"):
                continue
            # Skip __init__.py that are likely empty
            fpath = Path(dirpath) / fname
            rel_path = str(fpath.relative_to(root))
            source_files.append(SourceFile(path=rel_path))

    return source_files


def discover_tests(root: str | Path, language: str = "python") -> list[TestFile]:
    """Find test files in the codebase."""
    root = Path(root).resolve()
    ext = ".py" if language == "python" else ".ts"
    test_files: list[TestFile] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]

        for fname in sorted(filenames):
            if not fname.endswith(ext):
                continue
            is_test = (
                fname.startswith("test_")
                or fname.endswith(f"_test{ext}")
                or "tests" in Path(dirpath).relative_to(root).parts
            )
            if not is_test:
                continue

            fpath = Path(dirpath) / fname
            rel_path = str(fpath.relative_to(root))
            tf = TestFile(path=rel_path)

            # Parse test file for function names and imports
            if language == "python":
                try:
                    source = fpath.read_text(encoding="utf-8", errors="replace")
                    tree = ast.parse(source, filename=str(fpath))
                    tf.test_functions = _extract_test_function_names(tree)
                    tf.imported_modules = _extract_imports(tree)
                    tf.referenced_names = _extract_referenced_names(tree)
                except (SyntaxError, UnicodeDecodeError):
                    logger.debug("Failed to parse test file: %s", rel_path)

            test_files.append(tf)

    return test_files


def _extract_test_function_names(tree: ast.Module) -> list[str]:
    """Extract test function names from AST (test_ prefix or inside Test classes)."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                names.append(node.name)
    return names


def _extract_imports(tree: ast.Module) -> list[str]:
    """Extract imported module names from AST."""
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    return modules


def _extract_referenced_names(tree: ast.Module) -> list[str]:
    """Extract all Name references in the AST (for coverage matching)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return sorted(names)


# ── Function Extraction ────────────────────────────────────────────


def extract_functions(file_path: str | Path, source: str | None = None) -> list[ExtractedFunction]:
    """Parse a Python file and extract all function definitions."""
    file_path = Path(file_path)
    if source is None:
        source = file_path.read_text(encoding="utf-8", errors="replace")

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        logger.debug("Failed to parse: %s", file_path)
        return []

    source_lines = source.splitlines()
    functions: list[ExtractedFunction] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = _extract_single_function(node, source_lines)
            functions.append(func)

    return functions


def _extract_single_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
) -> ExtractedFunction:
    """Extract a single function from an AST node."""
    # Determine if this is a method (nested inside ClassDef)
    class_name = ""
    is_method = False
    # We'll check parent via the _parent attribute if set, but ast.walk doesn't set it.
    # Instead, we detect methods by checking if 'self' or 'cls' is first param.
    params = _extract_params(node.args)
    if params and params[0].name in ("self", "cls"):
        is_method = True

    # Decorators
    decorators = []
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            decorators.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            decorators.append(ast.dump(dec))
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name):
                decorators.append(dec.func.id)
            elif isinstance(dec.func, ast.Attribute):
                decorators.append(dec.func.attr)

    # Return type
    return_type = ""
    if node.returns:
        return_type = _annotation_to_str(node.returns)

    # Docstring
    docstring = ast.get_docstring(node) or ""

    # Body source
    body_source = ""
    try:
        start = node.lineno - 1
        end = node.end_lineno or start + 1
        body_source = "\n".join(source_lines[start:end])
    except (IndexError, AttributeError):
        pass

    # Complexity
    complexity = compute_complexity(node)

    return ExtractedFunction(
        name=node.name,
        params=params,
        return_type=return_type,
        complexity=complexity,
        body_source=body_source,
        line_number=node.lineno,
        is_async=isinstance(node, ast.AsyncFunctionDef),
        is_method=is_method,
        class_name=class_name,
        decorators=decorators,
        docstring=docstring,
    )


def _extract_params(args: ast.arguments) -> list[ExtractedParameter]:
    """Extract parameters from function arguments."""
    params: list[ExtractedParameter] = []

    # Defaults are right-aligned with args
    num_args = len(args.args)
    num_defaults = len(args.defaults)
    default_offset = num_args - num_defaults

    for i, arg in enumerate(args.args):
        annotation = _annotation_to_str(arg.annotation) if arg.annotation else ""
        default = ""
        if i >= default_offset:
            default_node = args.defaults[i - default_offset]
            default = _node_to_str(default_node)
        params.append(ExtractedParameter(
            name=arg.arg,
            type_annotation=annotation,
            default=default,
        ))

    # *args
    if args.vararg:
        annotation = _annotation_to_str(args.vararg.annotation) if args.vararg.annotation else ""
        params.append(ExtractedParameter(name=f"*{args.vararg.arg}", type_annotation=annotation))

    # **kwargs
    if args.kwarg:
        annotation = _annotation_to_str(args.kwarg.annotation) if args.kwarg.annotation else ""
        params.append(ExtractedParameter(name=f"**{args.kwarg.arg}", type_annotation=annotation))

    return params


def _annotation_to_str(node: ast.expr | None) -> str:
    """Convert an annotation AST node to a string."""
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return ""


def _node_to_str(node: ast.expr) -> str:
    """Convert an AST expression node to a string."""
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return "..."


# ── Cyclomatic Complexity ──────────────────────────────────────────


class _ComplexityVisitor(ast.NodeVisitor):
    """Count decision points for cyclomatic complexity."""

    def __init__(self) -> None:
        self.complexity = 1  # Base complexity

    def visit_If(self, node: ast.If) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # Each additional boolean operand adds a decision point
        self.complexity += len(node.values) - 1
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        # Each case is a decision point (minus the first which is the base)
        if hasattr(node, "cases"):
            self.complexity += len(node.cases) - 1
        self.generic_visit(node)


def compute_complexity(node: ast.AST) -> int:
    """Compute McCabe cyclomatic complexity for a function AST node."""
    visitor = _ComplexityVisitor()
    visitor.visit(node)
    return visitor.complexity


# ── Coverage Mapping ───────────────────────────────────────────────


def map_test_coverage(
    source_files: list[SourceFile],
    test_files: list[TestFile],
) -> CoverageMap:
    """Map test coverage using dual heuristic: import analysis + name matching.

    Heuristic 1: Import analysis — test file imports source module -> coverage link
    Heuristic 2: Name matching — strip test_ prefix from test names -> match source functions
    """
    # Build lookup: module_name -> list of function names
    # e.g. "src/auth.py" -> module "src.auth" -> functions ["login", "logout"]
    module_to_functions: dict[str, set[str]] = {}
    func_to_file: dict[str, str] = {}
    func_to_complexity: dict[str, int] = {}

    for sf in source_files:
        module = sf.path.replace("/", ".").replace("\\", ".")
        if module.endswith(".py"):
            module = module[:-3]
        func_names = {f.name for f in sf.functions}
        module_to_functions[module] = func_names
        for f in sf.functions:
            key = f"{sf.path}::{f.name}"
            func_to_file[key] = sf.path
            func_to_complexity[key] = f.complexity

    # Build set of covered function names from test files
    covered_functions: set[str] = set()  # "path::func_name"

    for tf in test_files:
        # Heuristic 1: Which source modules does this test import?
        imported_source_modules: set[str] = set()
        for imp in tf.imported_modules:
            for module_name in module_to_functions:
                # Check if import matches or is a sub-import
                if imp == module_name or module_name.endswith(f".{imp}") or imp.endswith(f".{module_name.split('.')[-1]}"):
                    imported_source_modules.add(module_name)

        # Heuristic 2: Name matching — strip test_ prefix
        tested_names: set[str] = set()
        for test_name in tf.test_functions:
            if test_name.startswith("test_"):
                bare_name = test_name[5:]  # strip "test_"
                tested_names.add(bare_name)

        # Also check referenced names from the AST
        referenced = set(tf.referenced_names)

        # Mark functions as covered
        for sf in source_files:
            module = sf.path.replace("/", ".").replace("\\", ".")
            if module.endswith(".py"):
                module = module[:-3]

            for func in sf.functions:
                key = f"{sf.path}::{func.name}"
                # Covered if: test imports the module AND (test name matches OR function name is referenced)
                if module in imported_source_modules:
                    if func.name in tested_names or func.name in referenced:
                        covered_functions.add(key)
                # Also covered if test name directly matches (even without import analysis)
                if func.name in tested_names:
                    covered_functions.add(key)

    # Build coverage entries
    entries: list[CoverageEntry] = []
    for sf in source_files:
        for func in sf.functions:
            key = f"{sf.path}::{func.name}"
            is_covered = key in covered_functions
            test_count = 0
            if is_covered:
                # Count how many test functions reference this name
                for tf in test_files:
                    for tn in tf.test_functions:
                        if tn.startswith("test_") and tn[5:] == func.name:
                            test_count += 1
                        elif func.name in tf.referenced_names:
                            test_count += 1
                            break
                test_count = max(test_count, 1)

            entries.append(CoverageEntry(
                function_name=func.name,
                file_path=sf.path,
                complexity=func.complexity,
                test_count=test_count,
                covered=is_covered,
            ))

    return CoverageMap(entries=entries)


# ── Security Pattern Detection ─────────────────────────────────────


def detect_security_patterns(func: ExtractedFunction, source: str = "") -> list[SecurityFinding]:
    """Detect security-sensitive patterns in a function via AST conditional analysis.

    Looks for conditionals that reference security-relevant names
    (auth, role, permission, token, etc.).
    """
    findings: list[SecurityFinding] = []

    # Parse the function body
    body = source or func.body_source
    if not body:
        return findings

    try:
        tree = ast.parse(textwrap.dedent(body))
    except SyntaxError:
        return findings

    # Walk AST looking for conditionals with security-relevant names
    for node in ast.walk(tree):
        if not isinstance(node, (ast.If, ast.IfExp)):
            continue

        test_node = node.test if isinstance(node, ast.If) else node.test
        names_in_condition = _collect_names(test_node)
        calls_in_condition = _collect_calls(test_node)

        # Check for security-sensitive variable names
        for name in names_in_condition:
            if name.lower() in SECURITY_VARIABLES:
                findings.append(SecurityFinding(
                    function_name=func.name,
                    file_path="",  # Set by caller
                    line_number=func.line_number,
                    complexity=func.complexity,
                    pattern_matched=f"variable: {name}",
                    suggestion=f"Ensure branch on '{name}' is tested with both truthy and falsy values",
                ))
                break  # One finding per conditional

        # Check for security-sensitive function calls
        for call_name in calls_in_condition:
            lower_call = call_name.lower()
            if any(lower_call.startswith(prefix) for prefix in SECURITY_CALL_PREFIXES):
                findings.append(SecurityFinding(
                    function_name=func.name,
                    file_path="",  # Set by caller
                    line_number=func.line_number,
                    complexity=func.complexity,
                    pattern_matched=f"call: {call_name}",
                    suggestion=f"Ensure call to '{call_name}' in conditional is tested",
                ))
                break

    return findings


def _collect_names(node: ast.AST) -> list[str]:
    """Collect all Name and Attribute nodes from an expression."""
    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.append(child.id)
        elif isinstance(child, ast.Attribute):
            names.append(child.attr)
    return names


def _collect_calls(node: ast.AST) -> list[str]:
    """Collect all function call names from an expression."""
    calls: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                calls.append(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                calls.append(child.func.attr)
    return calls


def _assign_risk_levels(
    findings: list[SecurityFinding],
    coverage_map: CoverageMap,
) -> None:
    """Assign risk levels to security findings based on coverage and complexity."""
    covered_funcs = {
        (e.file_path, e.function_name)
        for e in coverage_map.entries if e.covered
    }
    complexity_map = {
        (e.file_path, e.function_name): e.complexity
        for e in coverage_map.entries
    }

    for finding in findings:
        key = (finding.file_path, finding.function_name)
        is_covered = key in covered_funcs
        complexity = complexity_map.get(key, finding.complexity)
        finding.covered = is_covered

        if not is_covered and complexity >= 5:
            finding.risk_level = SecurityRiskLevel.critical
        elif not is_covered:
            finding.risk_level = SecurityRiskLevel.high
        elif is_covered:
            # Has coverage but security branches may not be specifically tested
            finding.risk_level = SecurityRiskLevel.low
        else:
            finding.risk_level = SecurityRiskLevel.medium


# ── Main Orchestrator ──────────────────────────────────────────────


def analyze_codebase(root: str | Path, language: str = "python") -> CodebaseAnalysis:
    """Run full mechanical analysis on a codebase.

    Steps:
    1. Discover source files
    2. Extract functions from each source file
    3. Discover test files
    4. Map test coverage
    5. Detect security patterns
    6. Assign risk levels

    Returns a CodebaseAnalysis with everything populated.
    """
    root = Path(root).resolve()

    # Step 1: Discover source files
    source_files = discover_source_files(root, language)

    # Step 2: Extract functions
    for sf in source_files:
        full_path = root / sf.path
        if full_path.exists() and language == "python":
            source = full_path.read_text(encoding="utf-8", errors="replace")
            sf.functions = extract_functions(full_path, source)

            # Extract imports and classes
            try:
                tree = ast.parse(source, filename=str(full_path))
                sf.imports = _extract_imports(tree)
                sf.classes = [
                    node.name for node in ast.walk(tree)
                    if isinstance(node, ast.ClassDef)
                ]
            except SyntaxError:
                pass

    # Step 3: Discover tests
    test_files = discover_tests(root, language)

    # Step 4: Map coverage
    coverage = map_test_coverage(source_files, test_files)

    # Step 5: Detect security patterns
    all_findings: list[SecurityFinding] = []
    for sf in source_files:
        for func in sf.functions:
            func_findings = detect_security_patterns(func)
            for finding in func_findings:
                finding.file_path = sf.path
            all_findings.extend(func_findings)

    # Step 6: Assign risk levels
    _assign_risk_levels(all_findings, coverage)

    security = SecurityAuditReport(findings=all_findings)

    return CodebaseAnalysis(
        root_path=str(root),
        language=language,
        source_files=source_files,
        test_files=test_files,
        coverage=coverage,
        security=security,
    )
