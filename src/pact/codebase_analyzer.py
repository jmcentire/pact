"""Codebase analyzer — mechanical AST-based analysis (no LLM).

Discovers source/test files, extracts function signatures, computes
cyclomatic complexity, maps test coverage, and detects security-sensitive
branches. All stdlib, no external dependencies.
"""

from __future__ import annotations

import ast
import logging
import os
import re
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
    ext_map = {"python": ".py", "typescript": ".ts", "javascript": ".js", "rust": ".rs"}
    ext = ext_map.get(language, ".py")
    source_files: list[SourceFile] = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Filter out skip dirs in-place to prevent os.walk from descending
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        # Rust builds into target/ — always skip
        if language == "rust":
            dirnames[:] = [d for d in dirnames if d != "target"]

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
    ext_map = {"python": ".py", "typescript": ".ts", "javascript": ".js", "rust": ".rs"}
    ext = ext_map.get(language, ".py")
    test_files: list[TestFile] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        if language == "rust":
            dirnames[:] = [d for d in dirnames if d != "target"]

        for fname in sorted(filenames):
            if not fname.endswith(ext):
                continue

            fpath = Path(dirpath) / fname
            rel_parts = Path(dirpath).relative_to(root).parts

            if language == "rust":
                # Rust tests: files in tests/ dir, or any .rs file containing #[test]
                in_tests_dir = "tests" in rel_parts
                has_test_attr = False
                if not in_tests_dir:
                    try:
                        source = fpath.read_text(encoding="utf-8", errors="replace")
                        has_test_attr = "#[test]" in source or "#[cfg(test)]" in source
                    except (OSError, UnicodeDecodeError):
                        pass
                is_test = in_tests_dir or has_test_attr
            else:
                is_test = (
                    fname.startswith("test_")
                    or fname.endswith(f"_test{ext}")
                    or "tests" in rel_parts
                )
            if not is_test:
                continue

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
            elif language == "rust":
                try:
                    source = fpath.read_text(encoding="utf-8", errors="replace")
                    tf.test_functions = _extract_rust_test_function_names(source)
                    tf.referenced_names = _extract_rust_referenced_names(source)
                except (OSError, UnicodeDecodeError):
                    logger.debug("Failed to parse Rust test file: %s", rel_path)

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


# ── Rust Helpers (regex-based, no external parser) ─────────────────


# Regex for Rust function definitions: `fn name(` or `pub fn name(`
_RUST_FN_RE = re.compile(
    r"^[ \t]*(?:pub(?:\(crate\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)\s*[<(]",
    re.MULTILINE,
)
# Regex for Rust struct/enum/trait/impl definitions
_RUST_STRUCT_RE = re.compile(r"^[ \t]*(?:pub(?:\(crate\))?\s+)?struct\s+(\w+)", re.MULTILINE)
_RUST_ENUM_RE = re.compile(r"^[ \t]*(?:pub(?:\(crate\))?\s+)?enum\s+(\w+)", re.MULTILINE)
_RUST_TRAIT_RE = re.compile(r"^[ \t]*(?:pub(?:\(crate\))?\s+)?trait\s+(\w+)", re.MULTILINE)
_RUST_IMPL_RE = re.compile(r"^[ \t]*impl(?:<[^>]*>)?\s+(\w+)", re.MULTILINE)

# Regex to find #[test] annotated functions
_RUST_TEST_FN_RE = re.compile(
    r"#\[test\]\s*(?:#\[.*?\]\s*)*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)",
    re.DOTALL,
)

# Regex for Rust use statements (for reference matching)
_RUST_USE_RE = re.compile(r"^[ \t]*use\s+([\w:]+)", re.MULTILINE)


def _extract_rust_test_function_names(source: str) -> list[str]:
    """Extract test function names from Rust source (functions with #[test] attribute)."""
    return _RUST_TEST_FN_RE.findall(source)


def _extract_rust_referenced_names(source: str) -> list[str]:
    """Extract referenced names from Rust source for coverage matching."""
    names: set[str] = set()
    # Collect function calls and identifiers
    for m in _RUST_FN_RE.finditer(source):
        names.add(m.group(1))
    # Collect use paths — take the last segment as the referenced name
    for m in _RUST_USE_RE.finditer(source):
        path = m.group(1)
        last_segment = path.rsplit("::", 1)[-1]
        if last_segment != "*":
            names.add(last_segment)
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


def extract_functions_rust(file_path: str | Path, source: str | None = None) -> list[ExtractedFunction]:
    """Parse a Rust file and extract function/struct/enum/trait/impl definitions via regex.

    Since Python's ast module only handles Python, this uses regex patterns
    to find Rust definitions. Less precise than a real parser but sufficient
    for signature extraction and coverage mapping.
    """
    file_path = Path(file_path)
    if source is None:
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.debug("Failed to read: %s", file_path)
            return []

    source_lines = source.splitlines()
    functions: list[ExtractedFunction] = []

    # Extract fn definitions
    for m in _RUST_FN_RE.finditer(source):
        name = m.group(1)
        line_number = source[:m.start()].count("\n") + 1
        # Determine visibility
        line_text = source_lines[line_number - 1] if line_number <= len(source_lines) else ""
        is_pub = line_text.lstrip().startswith("pub")
        is_async = "async" in line_text.split("fn")[0] if "fn" in line_text else False

        # Try to extract simple parameter list
        params = _extract_rust_params(source, m.end() - 1)

        # Try to extract return type
        return_type = _extract_rust_return_type(source, m.end() - 1)

        functions.append(ExtractedFunction(
            name=name,
            params=params,
            return_type=return_type,
            complexity=1,  # No AST-based complexity for Rust
            body_source="",
            line_number=line_number,
            is_async=is_async,
            is_method="self" in line_text.split(")")[0] if ")" in line_text else False,
            class_name="",
            decorators=["pub"] if is_pub else [],
            docstring="",
        ))

    # Extract struct definitions
    for m in _RUST_STRUCT_RE.finditer(source):
        name = m.group(1)
        line_number = source[:m.start()].count("\n") + 1
        functions.append(ExtractedFunction(
            name=name,
            params=[],
            return_type="struct",
            complexity=1,
            body_source="",
            line_number=line_number,
            is_async=False,
            is_method=False,
            class_name="",
            decorators=["struct"],
            docstring="",
        ))

    # Extract enum definitions
    for m in _RUST_ENUM_RE.finditer(source):
        name = m.group(1)
        line_number = source[:m.start()].count("\n") + 1
        functions.append(ExtractedFunction(
            name=name,
            params=[],
            return_type="enum",
            complexity=1,
            body_source="",
            line_number=line_number,
            is_async=False,
            is_method=False,
            class_name="",
            decorators=["enum"],
            docstring="",
        ))

    # Extract trait definitions
    for m in _RUST_TRAIT_RE.finditer(source):
        name = m.group(1)
        line_number = source[:m.start()].count("\n") + 1
        functions.append(ExtractedFunction(
            name=name,
            params=[],
            return_type="trait",
            complexity=1,
            body_source="",
            line_number=line_number,
            is_async=False,
            is_method=False,
            class_name="",
            decorators=["trait"],
            docstring="",
        ))

    # Extract impl blocks
    for m in _RUST_IMPL_RE.finditer(source):
        name = m.group(1)
        line_number = source[:m.start()].count("\n") + 1
        functions.append(ExtractedFunction(
            name=name,
            params=[],
            return_type="impl",
            complexity=1,
            body_source="",
            line_number=line_number,
            is_async=False,
            is_method=False,
            class_name="",
            decorators=["impl"],
            docstring="",
        ))

    return functions


def _extract_rust_params(source: str, paren_start: int) -> list[ExtractedParameter]:
    """Extract parameters from a Rust function starting at the opening paren."""
    # Find matching closing paren
    if paren_start >= len(source) or source[paren_start] != "(":
        return []

    depth = 0
    end = paren_start
    for i in range(paren_start, min(paren_start + 500, len(source))):
        if source[i] == "(":
            depth += 1
        elif source[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    else:
        return []

    param_text = source[paren_start + 1:end].strip()
    if not param_text:
        return []

    params: list[ExtractedParameter] = []
    # Split on commas (naive — doesn't handle commas inside generics)
    # but good enough for signature extraction
    for part in param_text.split(","):
        part = part.strip()
        if not part:
            continue
        # Handle &self, &mut self, self
        if part in ("self", "&self", "&mut self"):
            params.append(ExtractedParameter(name=part, type_annotation="", default=""))
            continue
        # name: Type pattern
        if ":" in part:
            name, type_ann = part.split(":", 1)
            params.append(ExtractedParameter(
                name=name.strip(),
                type_annotation=type_ann.strip(),
                default="",
            ))
        else:
            params.append(ExtractedParameter(name=part.strip(), type_annotation="", default=""))

    return params


def _extract_rust_return_type(source: str, paren_start: int) -> str:
    """Extract return type from a Rust function (the -> Type before the opening brace)."""
    # Find closing paren first
    if paren_start >= len(source) or source[paren_start] != "(":
        return ""

    depth = 0
    close_paren = paren_start
    for i in range(paren_start, min(paren_start + 500, len(source))):
        if source[i] == "(":
            depth += 1
        elif source[i] == ")":
            depth -= 1
            if depth == 0:
                close_paren = i
                break
    else:
        return ""

    # Look for -> between close_paren and opening brace or newline
    rest = source[close_paren + 1:close_paren + 200]
    arrow_match = re.search(r"\s*->\s*([^{]+)", rest)
    if arrow_match:
        return arrow_match.group(1).strip().rstrip("{").strip()
    return ""


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
        if not full_path.exists():
            continue

        if language == "python":
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

        elif language == "rust":
            source = full_path.read_text(encoding="utf-8", errors="replace")
            sf.functions = extract_functions_rust(full_path, source)

            # Extract struct/enum/trait names as "classes" for coverage mapping
            sf.classes = [
                f.name for f in sf.functions
                if f.decorators and f.decorators[0] in ("struct", "enum", "trait")
            ]
            # Extract use statements as imports
            sf.imports = [m.group(1) for m in _RUST_USE_RE.finditer(source)]

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

    # Step 7: Build tool index (optional enrichment from ctags/cscope/tree-sitter/kindex)
    tool_idx = None
    try:
        from pact.tool_index import build_tool_index
        all_function_names = [f.name for sf in source_files for f in sf.functions]
        tool_idx = build_tool_index(root, language, function_names=all_function_names)
    except Exception:
        logger.debug("Tool index build failed, continuing without it", exc_info=True)

    return CodebaseAnalysis(
        root_path=str(root),
        language=language,
        source_files=source_files,
        test_files=test_files,
        coverage=coverage,
        security=security,
        tool_index=tool_idx,
    )
