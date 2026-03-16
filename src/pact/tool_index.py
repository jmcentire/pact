"""Tool index — enrich codebase analysis with ctags, cscope, tree-sitter, and kindex.

Detects available external tools, runs them against a codebase, and
returns structured data that augments the AST-based analysis. All tools
are optional: if not installed, their sections are silently skipped.

ctags:       Symbol definitions with scope, signature, kind. Multi-language. Fast.
cscope:      Cross-reference database. Call graph for C/C++ codebases.
tree-sitter: Full CST, error-tolerant, cross-language. Preferred for Python/TS/JS.
kindex:      Persistent knowledge graph. Pulls existing project context.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from pact.schemas_testgen import (
    CallGraphEntry,
    CscopeRef,
    CtagsSymbol,
    ToolAvailability,
    ToolIndex,
    TreeSitterSymbol,
)

logger = logging.getLogger(__name__)

# Directories to skip during indexing
_EXCLUDE_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "venv", ".venv", "env", ".env",
    "node_modules",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".pact",
    ".tox", ".nox",
    "dist", "build",
})

# Language mapping for ctags --languages flag
_CTAGS_LANG_MAP = {
    "python": "Python",
    "typescript": "TypeScript",
    "javascript": "JavaScript",
    "rust": "Rust",
    "go": "Go",
    "java": "Java",
    "c": "C",
    "cpp": "C++",
}

# Languages where tree-sitter is preferred over cscope
_TREE_SITTER_PREFERRED = {"python", "typescript", "javascript", "rust", "go", "java"}

# Try to import tree-sitter (optional dependency)
try:
    from tree_sitter import Language, Parser
    _HAS_TREE_SITTER = True
except ImportError:
    _HAS_TREE_SITTER = False


# ── Tool Detection ────────────────────────────────────────────────


def _run_quiet(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr). Swallow errors."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return -1, "", ""


def detect_tools() -> ToolAvailability:
    """Check which external analysis tools are available on PATH."""
    avail = ToolAvailability()

    # ctags — must be universal-ctags (not BSD ctags)
    rc, out, _ = _run_quiet(["ctags", "--version"])
    if rc == 0 and "Universal Ctags" in out:
        avail.ctags = True
        first_line = out.splitlines()[0] if out else ""
        for part in first_line.split(","):
            part = part.strip()
            if part.startswith("Universal Ctags"):
                avail.ctags_version = part.replace("Universal Ctags ", "").strip()
                break

    # cscope — prints version to stderr
    rc, out, err = _run_quiet(["cscope", "--version"])
    version_text = out or err
    if "cscope" in version_text.lower():
        avail.cscope = True
        for line in version_text.splitlines():
            if "version" in line.lower():
                avail.cscope_version = line.split("version")[-1].strip().rstrip(")")
                break

    # tree-sitter — Python library, not CLI
    if _HAS_TREE_SITTER:
        avail.tree_sitter = True
        try:
            import tree_sitter
            avail.tree_sitter_version = getattr(tree_sitter, "__version__", "unknown")
        except Exception:
            avail.tree_sitter_version = "installed"

    # kindex
    rc, out, _ = _run_quiet(["kin", "--version"])
    if rc == 0:
        avail.kindex = True
        avail.kindex_version = out.strip()
    else:
        kin_mcp = shutil.which("kin-mcp")
        if kin_mcp:
            avail.kindex = True
            avail.kindex_version = "mcp-only"

    return avail


# ── ctags ─────────────────────────────────────────────────────────


def run_ctags(root: Path, language: str = "python") -> list[CtagsSymbol]:
    """Run universal-ctags and parse JSON output.

    Returns list of CtagsSymbol. Empty list if ctags unavailable or fails.
    """
    ctags_bin = shutil.which("ctags")
    if not ctags_bin:
        return []

    # Verify it's universal-ctags
    rc, out, _ = _run_quiet([ctags_bin, "--version"])
    if rc != 0 or "Universal Ctags" not in out:
        logger.debug("ctags is not universal-ctags, skipping")
        return []

    root = Path(root).resolve()

    # Build exclude args (must use --exclude=DIR syntax, not --exclude DIR)
    exclude_args = [f"--exclude={d}" for d in _EXCLUDE_DIRS]

    # Language filter
    ctags_lang = _CTAGS_LANG_MAP.get(language, "")
    lang_args = ["--languages=" + ctags_lang] if ctags_lang else []

    cmd = [
        ctags_bin,
        "--output-format=json",
        "--fields=+nKSs",        # line number, Kind (long), Signature, scope
        "--recurse",
        *exclude_args,
        *lang_args,
        "-f", "-",               # output to stdout
        str(root),
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, cwd=str(root),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.debug("ctags failed: %s", e)
        return []

    if result.returncode != 0:
        logger.debug("ctags returned %d: %s", result.returncode, result.stderr[:200])
        return []

    symbols: list[CtagsSymbol] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        if obj.get("_type") != "tag":
            continue

        # Make path relative to root
        file_path = obj.get("path", "")
        try:
            file_path = str(Path(file_path).relative_to(root))
        except ValueError:
            pass

        symbols.append(CtagsSymbol(
            name=obj.get("name", ""),
            file_path=file_path,
            line_number=obj.get("line", 0),
            kind=obj.get("kind", ""),
            scope=obj.get("scope", ""),
            scope_kind=obj.get("scopeKind", ""),
            signature=obj.get("signature", ""),
            language=obj.get("language", ""),
        ))

    logger.info("ctags: %d symbols from %s", len(symbols), root)
    return symbols


# ── cscope ────────────────────────────────────────────────────────


def _build_cscope_file_list(root: Path, language: str = "python") -> list[str]:
    """Build a list of source files for cscope to index."""
    ext_map = {
        "python": {".py"},
        "typescript": {".ts", ".tsx"},
        "javascript": {".js", ".jsx"},
        "c": {".c", ".h"},
        "cpp": {".cpp", ".hpp", ".cc", ".hh", ".h"},
        "rust": {".rs"},
        "go": {".go"},
        "java": {".java"},
    }
    exts = ext_map.get(language, {".py"})
    files: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS and not d.endswith(".egg-info")]
        for fname in filenames:
            if any(fname.endswith(ext) for ext in exts):
                files.append(os.path.join(dirpath, fname))

    return files


def _cscope_query(db_path: str, query_type: int, symbol: str) -> list[CscopeRef]:
    """Run a cscope line-mode query.

    Query types: 0=symbol, 1=definition, 2=callees, 3=callers.
    """
    cscope_bin = shutil.which("cscope")
    if not cscope_bin:
        return []

    cmd = [cscope_bin, "-d", "-f", db_path, f"-L{query_type}", symbol]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []

    if result.returncode != 0:
        return []

    refs: list[CscopeRef] = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue
        refs.append(CscopeRef(
            symbol=parts[1] if len(parts) > 1 else symbol,
            file_path=parts[0],
            line_number=int(parts[2]) if parts[2].isdigit() else 0,
            context=parts[3] if len(parts) > 3 else "",
        ))

    return refs


def run_cscope(
    root: Path,
    function_names: list[str],
    language: str = "python",
) -> list[CallGraphEntry]:
    """Build cscope database and query call graph.

    Returns call graph entries. Empty list if cscope unavailable or fails.
    """
    cscope_bin = shutil.which("cscope")
    if not cscope_bin:
        return []

    root = Path(root).resolve()
    files = _build_cscope_file_list(root, language)
    if not files:
        return []

    tmpdir = tempfile.mkdtemp(prefix="pact_cscope_")
    db_path = os.path.join(tmpdir, "cscope.out")

    try:
        # Write file list
        file_list_path = os.path.join(tmpdir, "cscope.files")
        with open(file_list_path, "w") as f:
            f.write("\n".join(files))

        # Build database
        build_cmd = [cscope_bin, "-b", "-q", "-i", file_list_path, "-f", db_path]
        try:
            result = subprocess.run(
                build_cmd, capture_output=True, text=True, timeout=120, cwd=str(root),
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug("cscope build failed: %s", e)
            return []

        if result.returncode != 0:
            logger.debug("cscope build returned %d", result.returncode)
            return []

        # Query each function
        entries: list[CallGraphEntry] = []
        for func_name in function_names:
            callers = _cscope_query(db_path, 3, func_name)
            callees = _cscope_query(db_path, 2, func_name)

            # Make paths relative
            for ref in callers + callees:
                try:
                    ref.file_path = str(Path(ref.file_path).relative_to(root))
                except ValueError:
                    pass

            if callers or callees:
                defs = _cscope_query(db_path, 1, func_name)
                file_path = ""
                if defs:
                    try:
                        file_path = str(Path(defs[0].file_path).relative_to(root))
                    except ValueError:
                        file_path = defs[0].file_path

                entries.append(CallGraphEntry(
                    function=func_name,
                    file_path=file_path,
                    callers=callers,
                    callees=callees,
                ))

        logger.info("cscope: %d call graph entries", len(entries))
        return entries

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── tree-sitter ───────────────────────────────────────────────────


def _get_tree_sitter_language(language: str):
    """Get a tree-sitter Language object for the given language.

    Returns None if the grammar is not installed.
    """
    if not _HAS_TREE_SITTER:
        return None

    try:
        if language == "python":
            import tree_sitter_python
            return Language(tree_sitter_python.language())
        elif language == "javascript":
            import tree_sitter_javascript
            return Language(tree_sitter_javascript.language())
        elif language == "typescript":
            import tree_sitter_typescript
            return Language(tree_sitter_typescript.language_typescript())
    except (ImportError, Exception) as e:
        logger.debug("tree-sitter grammar for %s not available: %s", language, e)

    return None


# Node types that represent definitions worth extracting
_FUNC_DEF_TYPES = {"function_definition", "function_declaration", "method_definition"}
_CLASS_DEF_TYPES = {"class_definition", "class_declaration"}


def _walk_tree_sitter(node, rel_path: str, symbols: list[TreeSitterSymbol]) -> None:
    """Recursively walk a tree-sitter AST and extract definitions."""
    if node.type in _FUNC_DEF_TYPES:
        name = ""
        for child in node.children:
            if child.type == "identifier":
                name = child.text.decode("utf-8", errors="replace")
                break
            # TypeScript/JS: property_identifier for methods
            if child.type == "property_identifier":
                name = child.text.decode("utf-8", errors="replace")
                break

        if name:
            # Determine parent scope (class)
            parent_name = ""
            parent_kind = ""
            p = node.parent
            if p and p.type == "block":
                p = p.parent
            if p and p.type in _CLASS_DEF_TYPES:
                for child in p.children:
                    if child.type == "identifier":
                        parent_name = child.text.decode("utf-8", errors="replace")
                        parent_kind = "class"
                        break

            symbols.append(TreeSitterSymbol(
                name=name,
                file_path=rel_path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                kind="function_definition",
                parent=parent_name,
                parent_kind=parent_kind,
            ))

    elif node.type in _CLASS_DEF_TYPES:
        name = ""
        for child in node.children:
            if child.type == "identifier":
                name = child.text.decode("utf-8", errors="replace")
                break

        if name:
            symbols.append(TreeSitterSymbol(
                name=name,
                file_path=rel_path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                kind="class_definition",
            ))

    # Recurse into children
    for child in node.children:
        _walk_tree_sitter(child, rel_path, symbols)


def run_tree_sitter(root: Path, language: str = "python") -> list[TreeSitterSymbol]:
    """Parse source files with tree-sitter and extract symbol definitions.

    Uses CST walking (not query API) for compatibility across tree-sitter versions.
    Returns list of TreeSitterSymbol. Empty list if tree-sitter unavailable.
    """
    if not _HAS_TREE_SITTER:
        return []

    ts_lang = _get_tree_sitter_language(language)
    if ts_lang is None:
        return []

    root = Path(root).resolve()
    ext_map = {
        "python": {".py"},
        "typescript": {".ts", ".tsx"},
        "javascript": {".js", ".jsx"},
    }
    exts = ext_map.get(language, set())
    if not exts:
        return []

    parser = Parser(ts_lang)
    symbols: list[TreeSitterSymbol] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS and not d.endswith(".egg-info")]

        for fname in filenames:
            if not any(fname.endswith(ext) for ext in exts):
                continue

            fpath = Path(dirpath) / fname
            try:
                source = fpath.read_bytes()
            except OSError:
                continue

            try:
                tree = parser.parse(source)
            except Exception:
                continue

            try:
                rel_path = str(fpath.relative_to(root))
            except ValueError:
                rel_path = str(fpath)

            _walk_tree_sitter(tree.root_node, rel_path, symbols)

    logger.info("tree-sitter: %d symbols from %s", len(symbols), root)
    return symbols


# ── kindex ────────────────────────────────────────────────────────


def query_kindex(project_path: Path) -> str:
    """Query kindex for existing context about a project.

    Returns text summary, or empty string if kindex unavailable.
    """
    kin_bin = shutil.which("kin")
    if not kin_bin:
        return ""

    project_name = project_path.name

    # Try context first (richer), fall back to search
    rc, context, _ = _run_quiet([kin_bin, "context", project_name], timeout=15)
    if rc == 0 and context:
        return context

    rc, out, _ = _run_quiet([kin_bin, "search", project_name, "--top-k", "10"], timeout=15)
    if rc == 0 and out:
        return out

    return ""


# ── Main Orchestrator ─────────────────────────────────────────────


def build_tool_index(
    root: str | Path,
    language: str = "python",
    function_names: list[str] | None = None,
) -> ToolIndex:
    """Build a ToolIndex by running all available external tools.

    Detects tools, runs whichever are available, returns unified index.
    Gracefully degrades: no tools installed -> empty ToolIndex.

    Args:
        root: Project root directory.
        language: Programming language for file filtering.
        function_names: Functions to query call graph for.
            If None, call graph is skipped (cscope DB build is expensive).

    Returns:
        ToolIndex with data from available tools.
    """
    root = Path(root).resolve()
    tools = detect_tools()

    logger.info(
        "Tool availability — ctags: %s, cscope: %s, tree-sitter: %s, kindex: %s",
        tools.ctags, tools.cscope, tools.tree_sitter, tools.kindex,
    )

    symbols: list[CtagsSymbol] = []
    ts_symbols: list[TreeSitterSymbol] = []
    call_graph: list[CallGraphEntry] = []
    kindex_context = ""

    # ctags — fast symbol index (always run if available)
    if tools.ctags:
        symbols = run_ctags(root, language)

    # tree-sitter — rich AST analysis (preferred for non-C languages)
    if tools.tree_sitter and language in _TREE_SITTER_PREFERRED:
        ts_symbols = run_tree_sitter(root, language)

    # cscope — call graph (for C/C++, or fallback if tree-sitter unavailable)
    if tools.cscope and function_names:
        use_cscope = language in ("c", "cpp") or not tools.tree_sitter
        if use_cscope:
            call_graph = run_cscope(root, function_names, language)

    # kindex — existing project knowledge
    if tools.kindex:
        kindex_context = query_kindex(root)

    return ToolIndex(
        tools=tools,
        symbols=symbols,
        tree_sitter_symbols=ts_symbols,
        call_graph=call_graph,
        kindex_context=kindex_context,
    )


# ── Rendering for Agent Context ───────────────────────────────────


def render_tool_index_context(
    tool_index: ToolIndex | None,
    file_path: str | None = None,
    function_name: str | None = None,
    max_symbols: int = 50,
    max_callers: int = 10,
) -> str:
    """Render tool index data as text for agent prompts.

    Can be scoped to a specific file or function for focused context.

    Args:
        tool_index: The ToolIndex to render.
        file_path: If set, show symbols for this file only.
        function_name: If set, show call graph for this function.
        max_symbols: Limit symbol output per section.
        max_callers: Limit caller/callee output.

    Returns:
        Text block for agent context, or empty string if no data.
    """
    if not tool_index:
        return ""

    has_data = (
        tool_index.symbols
        or tool_index.tree_sitter_symbols
        or tool_index.call_graph
        or tool_index.kindex_context
    )
    if not has_data:
        return ""

    sections: list[str] = []

    # tree-sitter symbols (preferred — richer data)
    if tool_index.tree_sitter_symbols:
        ts_syms = tool_index.tree_sitter_symbols
        if file_path:
            ts_syms = tool_index.tree_sitter_for_file(file_path)

        if ts_syms:
            lines = ["## Codebase Structure (tree-sitter)"]
            by_kind: dict[str, list[TreeSitterSymbol]] = {}
            for s in ts_syms[:max_symbols]:
                by_kind.setdefault(s.kind, []).append(s)

            for kind, syms in sorted(by_kind.items()):
                label = kind.replace("_", " ") if kind else "other"
                lines.append(f"\n### {label}")
                for s in syms:
                    parent_info = f" (in {s.parent})" if s.parent else ""
                    span = f"L{s.start_line}-{s.end_line}" if s.end_line > s.start_line else f"L{s.start_line}"
                    lines.append(f"- {s.name}{parent_info} [{s.file_path}:{span}]")

            if len(tool_index.tree_sitter_symbols) > max_symbols and not file_path:
                lines.append(f"\n... and {len(tool_index.tree_sitter_symbols) - max_symbols} more")
            sections.append("\n".join(lines))

    # ctags symbols (fallback if no tree-sitter, or supplemental)
    elif tool_index.symbols:
        symbols = tool_index.symbols
        if file_path:
            symbols = tool_index.symbols_for_file(file_path)

        if symbols:
            lines = ["## Symbol Index (ctags)"]
            by_kind: dict[str, list[CtagsSymbol]] = {}
            for s in symbols[:max_symbols]:
                by_kind.setdefault(s.kind, []).append(s)

            for kind, syms in sorted(by_kind.items()):
                lines.append(f"\n### {kind}s" if kind else "\n### other")
                for s in syms:
                    scope_info = f" (in {s.scope})" if s.scope else ""
                    sig_info = s.signature if s.signature else ""
                    lines.append(f"- {s.name}{sig_info}{scope_info} [{s.file_path}:{s.line_number}]")

            if len(tool_index.symbols) > max_symbols and not file_path:
                lines.append(f"\n... and {len(tool_index.symbols) - max_symbols} more")
            sections.append("\n".join(lines))

    # Call graph
    if function_name and tool_index.call_graph:
        callers = tool_index.callers_of(function_name)
        callees = tool_index.callees_of(function_name)

        if callers or callees:
            lines = [f"## Call Graph for {function_name}"]
            if callers:
                lines.append(f"\nCallers ({len(callers)}):")
                for ref in callers[:max_callers]:
                    lines.append(f"  <- {ref.symbol} [{ref.file_path}:{ref.line_number}]")
                if len(callers) > max_callers:
                    lines.append(f"  ... and {len(callers) - max_callers} more")
            if callees:
                lines.append(f"\nCallees ({len(callees)}):")
                for ref in callees[:max_callers]:
                    lines.append(f"  -> {ref.symbol} [{ref.file_path}:{ref.line_number}]")
                if len(callees) > max_callers:
                    lines.append(f"  ... and {len(callees) - max_callers} more")
            sections.append("\n".join(lines))

    # Kindex context
    if tool_index.kindex_context:
        sections.append(f"## Existing Project Knowledge\n\n{tool_index.kindex_context}")

    return "\n\n".join(sections)
