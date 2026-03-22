"""Contract validation logic — mechanical gates, no LLM.

Validates that contracts are well-formed: all type references resolve,
dependency graphs are acyclic, test suites exist and parse.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionTree,
    GateResult,
)

logger = logging.getLogger(__name__)

# Built-in primitives and wrappers that should never produce validation errors.
_BUILTIN_TYPES = frozenset({
    "str", "int", "float", "bool", "None", "bytes", "dict", "list", "any",
    "Optional", "Union", "tuple", "set", "frozenset", "Callable",
    "Iterator", "Generator", "Sequence", "Mapping", "Iterable",
    "Any", "AsyncCallable", "Awaitable", "Coroutine", "AsyncIterator",
    "Path", "datetime",
})


def extract_base_types(type_ref: str) -> list[str]:
    """Extract all base type names from a parameterized type reference.

    Handles bracket syntax (list[str], dict[str, int], Optional[Foo]),
    pipe unions (str | None), and nested combinations.

    Examples:
        "str" -> ["str"]
        "list[str]" -> ["list", "str"]
        "dict[str, int]" -> ["dict", "str", "int"]
        "Optional[Foo]" -> ["Optional", "Foo"]
        "list[dict[str, Bar]]" -> ["list", "dict", "str", "Bar"]
        "str | None" -> ["str", "None"]
    """
    type_ref = type_ref.strip()
    if not type_ref:
        return []

    # Handle pipe unions: "X | Y | Z"
    if "|" in type_ref and "[" not in type_ref:
        result = []
        for part in type_ref.split("|"):
            result.extend(extract_base_types(part.strip()))
        return result

    # Handle bracket syntax: "X[Y, Z]"
    bracket_pos = type_ref.find("[")
    if bracket_pos > 0 and type_ref.endswith("]"):
        base = type_ref[:bracket_pos].strip()
        inner = type_ref[bracket_pos + 1:-1]

        result = [base]

        # Split inner on commas at depth 0
        parts = _split_at_depth_zero(inner)
        for part in parts:
            result.extend(extract_base_types(part.strip()))
        return result

    # Handle pipe unions inside parameterized types: "X[A | B]" is handled
    # by the bracket case above; standalone "A | B" needs depth-aware splitting
    if "|" in type_ref:
        # Pipe inside brackets already handled — this is a top-level pipe
        result = []
        parts = _split_at_depth_zero(type_ref, delimiter="|")
        if len(parts) > 1:
            for part in parts:
                result.extend(extract_base_types(part.strip()))
            return result

    # Handle bare bracket groups like "[str, str]" from nested generics
    if type_ref.startswith("[") and type_ref.endswith("]"):
        inner = type_ref[1:-1]
        result = []
        for part in _split_at_depth_zero(inner):
            result.extend(extract_base_types(part.strip()))
        return result

    # Simple name — no brackets, no pipes
    return [type_ref]


def _split_at_depth_zero(s: str, delimiter: str = ",") -> list[str]:
    """Split a string on delimiter only at bracket depth 0."""
    parts = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch in ("[", "("):
            depth += 1
            current.append(ch)
        elif ch in ("]", ")"):
            depth -= 1
            current.append(ch)
        elif ch == delimiter and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def _check_type_ref(type_ref: str, defined_types: set[str]) -> bool:
    """Return True if all base types in type_ref resolve to defined_types."""
    base_types = extract_base_types(type_ref)
    return all(bt in defined_types for bt in base_types)


def validate_type_references(contract: ComponentContract) -> list[str]:
    """Check that all type_ref values in fields resolve to a defined type."""
    defined_types = {t.name for t in contract.types}
    # Add primitives and built-in wrappers
    defined_types |= _BUILTIN_TYPES
    errors = []

    for func in contract.functions:
        # Check output type
        if func.output_type and not _check_type_ref(func.output_type, defined_types):
            errors.append(
                f"Function '{func.name}' output_type '{func.output_type}' "
                f"not defined in component '{contract.component_id}'"
            )
        # Check input types
        for field in func.inputs:
            if not _check_type_ref(field.type_ref, defined_types):
                errors.append(
                    f"Function '{func.name}' input '{field.name}' type_ref "
                    f"'{field.type_ref}' not defined in component '{contract.component_id}'"
                )

    # Check struct field types
    for type_spec in contract.types:
        for field in type_spec.fields:
            if not _check_type_ref(field.type_ref, defined_types):
                errors.append(
                    f"Type '{type_spec.name}' field '{field.name}' type_ref "
                    f"'{field.type_ref}' not defined in component '{contract.component_id}'"
                )
        # Check item_type for list types
        if type_spec.kind == "list" and type_spec.item_type:
            if not _check_type_ref(type_spec.item_type, defined_types):
                errors.append(
                    f"Type '{type_spec.name}' item_type '{type_spec.item_type}' "
                    f"not defined in component '{contract.component_id}'"
                )

    return errors


def auto_stub_undefined_types(contract: ComponentContract) -> tuple[ComponentContract, list[str]]:
    """Auto-generate stub TypeSpec entries for undefined type_refs.

    Scans all type_ref values in the contract (function inputs, outputs,
    struct fields, list item_types).  Any non-builtin type name that doesn't
    match an existing TypeSpec gets a stub entry (kind="struct", no fields).

    This is a mechanical repair step — call after type registry enforcement
    and before validation.  The stubs prevent validation failures when the
    LLM references types it didn't define.  Stubbed types are logged as
    warnings so the user knows they may need manual attention.

    Returns:
        Tuple of (updated contract, list of warning strings for stubbed types).
    """
    from pact.schemas import TypeSpec

    defined_types = {t.name for t in contract.types}
    defined_types |= _BUILTIN_TYPES

    # Collect all referenced type names
    all_refs: set[str] = set()
    for func in contract.functions:
        if func.output_type:
            all_refs.update(extract_base_types(func.output_type))
        for field in func.inputs:
            all_refs.update(extract_base_types(field.type_ref))
    for type_spec in contract.types:
        for field in type_spec.fields:
            all_refs.update(extract_base_types(field.type_ref))
        if type_spec.kind == "list" and type_spec.item_type:
            all_refs.update(extract_base_types(type_spec.item_type))
        for inner in type_spec.inner_types:
            all_refs.update(extract_base_types(inner))

    # Find undefined types
    undefined = all_refs - defined_types
    if not undefined:
        return contract, []

    # Generate stubs
    warnings: list[str] = []
    new_types = list(contract.types)
    for type_name in sorted(undefined):
        stub = TypeSpec(
            name=type_name,
            kind="struct",
            description=f"Auto-stubbed type — referenced but not defined in contract '{contract.component_id}'",
        )
        new_types.append(stub)
        warnings.append(
            f"Auto-stubbed undefined type '{type_name}' in component "
            f"'{contract.component_id}' — consider defining it explicitly"
        )
        logger.warning(
            "Auto-stubbed undefined type '%s' in component '%s'",
            type_name, contract.component_id,
        )

    return contract.model_copy(update={"types": new_types}), warnings


def validate_dependency_graph(tree: DecompositionTree) -> list[str]:
    """Check that the dependency graph is acyclic."""
    errors = []

    # Check for cycles using DFS
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {nid: WHITE for nid in tree.nodes}

    def dfs(node_id: str, path: list[str]) -> None:
        color[node_id] = GRAY
        node = tree.nodes.get(node_id)
        if not node:
            return
        for child_id in node.children:
            if child_id not in color:
                errors.append(f"Child '{child_id}' of '{node_id}' not found in tree")
                continue
            if color[child_id] == GRAY:
                cycle_path = " -> ".join(path + [child_id])
                errors.append(f"Dependency cycle detected: {cycle_path}")
            elif color[child_id] == WHITE:
                dfs(child_id, path + [child_id])
        color[node_id] = BLACK

    for node_id in tree.nodes:
        if color[node_id] == WHITE:
            dfs(node_id, [node_id])

    return errors


_VAGUE_RATIONALE_PATTERNS = [
    "handles data", "manages stuff", "processes information",
    "for various purposes", "as needed", "general use",
    "handles all", "manages all", "processes all",
    "data handling", "data management", "data processing",
    "needed for functionality", "required for operation",
    "used internally", "for internal use",
]


def validate_rationale_quality(text: str, field_path: str) -> list[str]:
    """Check a rationale string for vague/cliche content.

    Returns list of rejection reasons.
    """
    if not text:
        return [f"{field_path}: rationale is empty"]

    if len(text) < 20:
        return [f"{field_path}: rationale too short ({len(text)} chars, minimum 20)"]

    errors = []
    lower = text.lower()
    for pattern in _VAGUE_RATIONALE_PATTERNS:
        if pattern in lower:
            errors.append(
                f"{field_path}: rationale contains vague phrase '{pattern}' — "
                f"be specific about what data is accessed and why"
            )
    return errors


def validate_authority_overlap(
    contracts: dict[str, "ComponentContract"],
) -> list[str]:
    """Check that no two components claim authority over overlapping domains."""
    domain_owners: dict[str, str] = {}
    warnings = []
    for cid, contract in contracts.items():
        for domain in contract.authority.domains:
            if domain in domain_owners:
                warnings.append(
                    f"Domain '{domain}' claimed by both '{domain_owners[domain]}' "
                    f"and '{cid}'"
                )
            else:
                domain_owners[domain] = cid
    return warnings


def validate_contract_completeness(contract: ComponentContract) -> list[str]:
    """Check that a contract is minimally complete."""
    errors = []
    if not contract.component_id:
        errors.append("Contract missing component_id")
    if not contract.name:
        errors.append("Contract missing name")
    if not contract.functions:
        errors.append(f"Contract '{contract.component_id}' has no functions defined")
    for func in contract.functions:
        if not func.name:
            errors.append(f"Function in '{contract.component_id}' missing name")
        if not func.output_type:
            errors.append(f"Function '{func.name}' in '{contract.component_id}' missing output_type")

    # data_access validation — only enforced when data_access has been populated
    # (default empty DataAccessDeclaration is accepted for backward compat)
    cid = contract.component_id or "unknown"
    da = contract.data_access
    if da.reads or da.writes or da.side_effects:
        # data_access was populated — rationale is required
        if not da.rationale:
            errors.append(f"Contract '{cid}' missing data_access.rationale")
        else:
            errors.extend(validate_rationale_quality(
                da.rationale, f"Contract '{cid}' data_access.rationale",
            ))

    # authority validation
    if contract.authority.domains and contract.authority.rationale is None:
        errors.append(
            f"Contract '{cid}' has authority.domains but missing authority.rationale"
        )
    if contract.authority.domains and contract.authority.rationale:
        errors.extend(validate_rationale_quality(
            contract.authority.rationale, f"Contract '{cid}' authority.rationale",
        ))

    return errors


def validate_test_suite(suite: ContractTestSuite) -> list[str]:
    """Check that a test suite is valid — has cases and generated code parses."""
    errors = []
    if not suite.component_id:
        errors.append("Test suite missing component_id")
    if not suite.test_cases:
        errors.append(f"Test suite for '{suite.component_id}' has no test cases")
    if suite.generated_code:
        # Only validate Python syntax; TypeScript is validated by the TS compiler
        if getattr(suite, "test_language", "python") == "python":
            try:
                ast.parse(suite.generated_code)
            except SyntaxError as e:
                errors.append(
                    f"Test suite for '{suite.component_id}' has syntax error "
                    f"in generated code: {e}"
                )
    return errors


def normalize_dependency_name(raw: str, known_ids: list[str]) -> str | None:
    """Normalize a dependency name to match a known component ID.

    Rules (applied in order):
      1. Exact match -> return as-is
      2. Case-insensitive match -> return known_id
      3. Underscore transposition (schemas_shaping -> shaping_schemas) -> return known_id
      4. No match -> return None

    Postconditions:
      - Result is always a member of known_ids, or None
      - Transposition detected by sorted word equality
    """
    # 1. Exact match
    if raw in known_ids:
        return raw

    # 2. Case-insensitive match
    raw_lower = raw.lower()
    for kid in known_ids:
        if kid.lower() == raw_lower:
            logger.warning("Normalized dependency '%s' -> '%s' (case mismatch)", raw, kid)
            return kid

    # 3. Underscore transposition
    raw_parts = sorted(raw_lower.split("_"))
    for kid in known_ids:
        kid_parts = sorted(kid.lower().split("_"))
        if raw_parts == kid_parts and raw_lower != kid.lower():
            logger.warning("Normalized dependency '%s' -> '%s' (word transposition)", raw, kid)
            return kid

    # 4. No match
    return None


def validate_all_contracts(
    tree: DecompositionTree,
    contracts: dict[str, ComponentContract],
    test_suites: dict[str, ContractTestSuite],
) -> GateResult:
    """Full mechanical validation gate. No LLM, no persuasion.

    Checks:
    1. All component IDs in tree have contracts
    2. All type references resolve within each contract
    3. Dependency graph is acyclic
    4. All contracts have test suites
    5. Test code parses
    """
    all_errors: list[str] = []

    # Check dependency graph
    graph_errors = validate_dependency_graph(tree)
    all_errors.extend(graph_errors)

    # Check each component has a contract
    for node_id, node in tree.nodes.items():
        if node_id not in contracts:
            all_errors.append(f"Component '{node_id}' missing contract")
            continue

        contract = contracts[node_id]

        # Validate contract completeness
        all_errors.extend(validate_contract_completeness(contract))

        # Validate type references
        all_errors.extend(validate_type_references(contract))

        # Check test suite exists
        if node_id not in test_suites:
            all_errors.append(f"Component '{node_id}' missing test suite")
        else:
            all_errors.extend(validate_test_suite(test_suites[node_id]))

        # Quality audit — non-blocking warnings for vague language
        from pact.quality import audit_contract_specificity
        quality_warnings = audit_contract_specificity(contract)
        for w in quality_warnings:
            logger.warning("Quality: %s", w)

    # Check authority domain overlap across all contracts
    overlap_warnings = validate_authority_overlap(contracts)
    all_errors.extend(overlap_warnings)

    # Check dependency contracts — distinguish internal vs external
    tree_component_ids = set(tree.nodes.keys()) if tree else set()

    for cid, contract in contracts.items():
        for dep_id in contract.dependencies:
            # Apply name normalization before resolution
            resolved = normalize_dependency_name(dep_id, list(contracts.keys()))
            effective_id = resolved or dep_id

            if effective_id in contracts:
                continue  # Internal dependency with contract — OK

            if effective_id in tree_component_ids:
                # Internal (in decomposition tree) but missing contract — error
                all_errors.append(
                    f"Contract '{cid}' depends on '{dep_id}' which is in the "
                    f"decomposition tree but has no contract"
                )
            else:
                # External dependency (not in tree) — just log, don't error
                logger.debug(
                    "Contract '%s' has external dependency '%s' "
                    "(not in decomposition tree, skipping validation)",
                    cid, dep_id,
                )

    # Cross-component interface compatibility (warnings, not blocking)
    interface_warnings = validate_cross_component_interfaces(contracts)
    for w in interface_warnings:
        logger.warning("Interface: %s", w)

    if all_errors:
        return GateResult(
            passed=False,
            reason=f"Contract validation failed with {len(all_errors)} error(s)",
            details=all_errors + [f"[warning] {w}" for w in interface_warnings],
        )

    return GateResult(
        passed=True,
        reason="All contracts validated successfully",
        details=[f"[warning] {w}" for w in interface_warnings] if interface_warnings else [],
    )


def validate_contract_incremental(
    contract: ComponentContract,
    existing_contracts: dict[str, ComponentContract],
) -> list[str]:
    """Validate a single contract incrementally against existing contracts.

    Checks:
    1. Type references within this contract are valid
    2. Internal dependencies reference existing_contracts keys
    3. No cycles with existing contracts

    Returns list of error strings (empty = valid).
    """
    errors = []

    # 1. Type references
    errors.extend(validate_type_references(contract))

    # 2. Contract completeness
    errors.extend(validate_contract_completeness(contract))

    # 3. Dependency resolution with normalization
    known_ids = list(existing_contracts.keys()) + [contract.component_id]
    for dep_id in contract.dependencies:
        normalized = normalize_dependency_name(dep_id, list(existing_contracts.keys()))
        if normalized is None:
            # Could be external - just debug log, don't error
            logger.debug(
                "Contract '%s' dependency '%s' not found in existing contracts (may be external)",
                contract.component_id, dep_id,
            )

    # 4. Simple cycle check: if A depends on B and B depends on A
    for dep_id in contract.dependencies:
        dep_contract = existing_contracts.get(dep_id)
        if dep_contract and contract.component_id in dep_contract.dependencies:
            errors.append(
                f"Circular dependency: '{contract.component_id}' <-> '{dep_id}'"
            )

    return errors


def validate_external_dependencies(
    contract: ComponentContract,
    source_tree: Path | None = None,
) -> list[str]:
    """Validate that external dependencies (in contract.requires) resolve to existing modules.

    Args:
        contract: The contract to validate
        source_tree: Root of source tree to check file existence. If None, skip file checks.

    Returns:
        List of warning strings (not errors — external deps are advisory).
    """
    warnings = []
    if not source_tree or not contract.requires:
        return warnings

    for req in contract.requires:
        # Convert dotted path to file path: "agents.base" -> "agents/base.py"
        parts = req.split(".")
        possible_paths = [
            source_tree / "/".join(parts) / "__init__.py",
            source_tree / ("/".join(parts) + ".py"),
        ]
        if not any(p.exists() for p in possible_paths):
            warnings.append(
                f"Contract '{contract.component_id}' requires '{req}' "
                f"but no matching file found in source tree"
            )

    return warnings


def validate_hierarchy_locality(
    tree: DecompositionTree,
    contracts: dict[str, ComponentContract],
) -> list[str]:
    """Validate that dependencies follow decomposition tree locality.

    Rules:
      - A component may depend on its siblings (same parent) — OK
      - A component may depend on its parent's siblings (uncle) — OK
      - A component may depend on its parent — OK
      - A component should NOT depend on distant cousins (warning)
      - Cross-subtree dependencies produce warnings, not errors

    Returns:
      List of warning strings for distant/cross-subtree dependencies.
    """
    warnings = []
    
    for cid, contract in contracts.items():
        node = tree.nodes.get(cid)
        if not node:
            continue
            
        # Build set of "nearby" nodes: siblings, parent, uncles
        nearby = set()
        
        # Self
        nearby.add(cid)
        
        # Parent
        if node.parent_id:
            nearby.add(node.parent_id)
            parent = tree.nodes.get(node.parent_id)
            if parent:
                # Siblings (other children of same parent)
                for sib_id in parent.children:
                    nearby.add(sib_id)
                # Uncles (siblings of parent = other children of grandparent)
                if parent.parent_id:
                    grandparent = tree.nodes.get(parent.parent_id)
                    if grandparent:
                        for uncle_id in grandparent.children:
                            nearby.add(uncle_id)
        
        # Children
        for child_id in node.children:
            nearby.add(child_id)
        
        # Check each dependency
        for dep_id in contract.dependencies:
            if dep_id not in tree.nodes:
                continue  # External dep, skip
            if dep_id not in nearby:
                dep_node = tree.nodes.get(dep_id)
                dep_path = _node_path(tree, dep_id)
                src_path = _node_path(tree, cid)
                warnings.append(
                    f"Distant dependency: '{cid}' ({src_path}) depends on "
                    f"'{dep_id}' ({dep_path}) — consider restructuring to "
                    f"keep dependencies within sibling/parent scope"
                )
    
    return warnings


def _node_path(tree: DecompositionTree, node_id: str) -> str:
    """Build a path string for a node like 'root > parent > node'."""
    parts = []
    current = node_id
    visited = set()
    while current and current not in visited:
        visited.add(current)
        node = tree.nodes.get(current)
        if not node:
            break
        parts.append(node.name or current)
        current = node.parent_id
    return " > ".join(reversed(parts))


def validate_cross_component_interfaces(
    contracts: dict[str, ComponentContract],
) -> list[str]:
    """Check interface compatibility between dependent components.

    FINDINGS.md showed that contracts generated in isolation had interface
    mismatches (e.g., template_engine expecting {{title}} but templates
    using {{site_title}}). This validation catches those mismatches.

    Checks:
    1. When component A depends on B, A's input types referencing B's
       output types must use matching field names and types
    2. Shared type names across components must have compatible definitions
    3. Function output types from one component used as input types in
       another must be structurally compatible

    Returns list of warning strings (not errors — interface mismatches
    are warnings that inform the user, not hard gates).
    """
    warnings: list[str] = []

    # Build index of all type definitions across components
    type_defs: dict[str, list[tuple[str, "ComponentContract"]]] = {}
    for cid, contract in contracts.items():
        for t in contract.types:
            if t.name not in type_defs:
                type_defs[t.name] = []
            type_defs[t.name].append((cid, contract))

    # Check 1: Shared type names must have compatible definitions
    for type_name, sources in type_defs.items():
        if len(sources) <= 1:
            continue
        # Compare field sets between all definitions of the same type
        field_sets: dict[str, set[str]] = {}
        for cid, contract in sources:
            t = next(t for t in contract.types if t.name == type_name)
            if t.fields:
                field_sets[cid] = {f.name for f in t.fields}

        if len(field_sets) >= 2:
            cids = list(field_sets.keys())
            for i in range(len(cids)):
                for j in range(i + 1, len(cids)):
                    a_fields = field_sets[cids[i]]
                    b_fields = field_sets[cids[j]]
                    if a_fields != b_fields:
                        only_a = a_fields - b_fields
                        only_b = b_fields - a_fields
                        detail_parts = []
                        if only_a:
                            detail_parts.append(
                                f"only in {cids[i]}: {', '.join(sorted(only_a))}"
                            )
                        if only_b:
                            detail_parts.append(
                                f"only in {cids[j]}: {', '.join(sorted(only_b))}"
                            )
                        warnings.append(
                            f"Type '{type_name}' has different fields in "
                            f"'{cids[i]}' vs '{cids[j]}': {'; '.join(detail_parts)}"
                        )

    # Check 2: Dependency output/input type compatibility
    for cid, contract in contracts.items():
        for dep_id in contract.dependencies:
            dep = contracts.get(dep_id)
            if not dep:
                continue
            # Check that types referenced by dep's function outputs are
            # defined consistently if also referenced by this contract
            dep_output_types = {f.output_type for f in dep.functions}
            my_input_types = set()
            for func in contract.functions:
                for inp in func.inputs:
                    my_input_types.add(inp.type_ref)

            shared_refs = dep_output_types & my_input_types
            for ref in shared_refs:
                # If the type is defined in both contracts, check compatibility
                dep_type = next((t for t in dep.types if t.name == ref), None)
                my_type = next((t for t in contract.types if t.name == ref), None)
                if dep_type and my_type and dep_type.fields and my_type.fields:
                    dep_fields = {f.name for f in dep_type.fields}
                    my_fields = {f.name for f in my_type.fields}
                    if dep_fields != my_fields:
                        warnings.append(
                            f"Interface mismatch: '{dep_id}' outputs type "
                            f"'{ref}' with fields {sorted(dep_fields)} but "
                            f"'{cid}' expects fields {sorted(my_fields)}"
                        )

    return warnings


def validate_north_star(
    task_text: str,
    tree: DecompositionTree,
    contracts: dict[str, ComponentContract],
    acceptance_criteria: list[str] | None = None,
) -> list[str]:
    """Validate that decomposed components can plausibly fulfill the original task.

    This is the north-star validation: do the contracts, when composed, actually
    cover what the task asked for? The SEO agent case showed that 9 components
    can pass 796 tests while the assembled service can't actually do the job.

    Checks (mechanical, no LLM):
    1. Task requirements mentioned in task.md should map to at least one
       contract function (keyword overlap heuristic)
    2. The root component should have functions covering the top-level verbs
    3. Leaf components without functions are dead weight
    4. Acceptance criteria keywords should appear somewhere in contracts

    Returns list of warning strings (advisory, not blocking).
    """
    warnings: list[str] = []
    if not task_text or not contracts:
        return warnings

    task_lower = task_text.lower()
    task_words = set(task_lower.split())

    # Common task verbs that should map to contract functions
    action_verbs = {
        "parse", "validate", "generate", "create", "build", "search",
        "find", "filter", "sort", "transform", "convert", "calculate",
        "compute", "render", "format", "send", "receive", "store",
        "save", "load", "read", "write", "delete", "update", "check",
        "verify", "authenticate", "authorize", "encrypt", "decrypt",
        "compress", "serialize", "deserialize", "fetch",
        "audit", "analyze", "report", "export", "import", "process",
        "handle", "route", "dispatch", "schedule", "execute", "run",
        "monitor", "notify", "publish", "subscribe", "index",
        "crawl", "scrape", "extract", "merge", "split", "join",
        "connect", "register", "login",
    }
    task_verbs = task_words & action_verbs

    # Collect all contract function names and descriptions
    all_functions: set[str] = set()
    all_func_descriptions: list[str] = []
    for contract in contracts.values():
        for func in contract.functions:
            all_functions.add(func.name.lower())
            all_func_descriptions.append(func.description.lower())

    # Check 1: Task verbs should appear in at least one function
    uncovered_verbs: list[str] = []
    for verb in sorted(task_verbs):
        found = any(verb in fn for fn in all_functions) or any(
            verb in fd for fd in all_func_descriptions
        )
        if not found:
            uncovered_verbs.append(verb)

    if uncovered_verbs and len(uncovered_verbs) > len(task_verbs) * 0.5:
        warnings.append(
            f"Task mentions actions [{', '.join(uncovered_verbs)}] but no "
            f"contract function name or description covers them. The "
            f"composed system may not fulfill the stated goal."
        )

    # Check 2: Root component should have functions
    root_contract = contracts.get(tree.root_id)
    if root_contract and not root_contract.functions:
        warnings.append(
            f"Root component '{tree.root_id}' has no functions. "
            f"The top-level interface is empty."
        )

    # Check 3: Leaf components without functions
    for node_id, node in tree.nodes.items():
        if not node.children:
            contract = contracts.get(node_id)
            if contract and not contract.functions:
                warnings.append(
                    f"Leaf component '{node_id}' has no functions. "
                    f"It contributes nothing to the composed system."
                )

    # Check 4: Acceptance criteria coverage
    if acceptance_criteria:
        # Build a searchable corpus from all contract content
        corpus = " ".join(
            f"{c.name} {c.description} " + " ".join(
                f"{f.name} {f.description}" for f in c.functions
            ) + " ".join(
                f"{t.name} {t.description}" for t in c.types
            ) + " ".join(c.invariants)
            for c in contracts.values()
        ).lower()

        uncovered = []
        for criterion in acceptance_criteria:
            # Extract significant words (3+ chars, not stopwords)
            stopwords = {"the", "and", "for", "are", "but", "not", "you",
                         "all", "can", "has", "her", "was", "one", "our",
                         "out", "had", "this", "that", "with", "from",
                         "they", "been", "have", "its", "will", "each",
                         "make", "when", "must", "should", "shall"}
            words = {w for w in criterion.lower().split() if len(w) >= 3 and w not in stopwords}
            # Check if at least some criterion keywords appear in contracts
            matched = sum(1 for w in words if w in corpus)
            if words and matched / len(words) < 0.3:
                uncovered.append(criterion[:100])

        if uncovered:
            warnings.append(
                f"{len(uncovered)} acceptance criteria have low coverage in contracts: "
                + "; ".join(uncovered)
            )

    return warnings


def validate_decomposition_coverage(
    task_text: str,
    tree: DecompositionTree,
) -> list[str]:
    """Early validation: check decomposition structure before contract generation.

    Runs immediately after decomposition, before spending LLM calls on
    contract/test generation. Catches structural issues early.

    Checks:
    1. Root has children (unless unary)
    2. No orphan nodes
    3. Component descriptions aren't empty
    4. Component descriptions aren't duplicates
    5. Task keywords appear in component descriptions

    Returns list of warning strings.
    """
    warnings: list[str] = []
    if not tree or not tree.nodes:
        warnings.append("Decomposition produced no components.")
        return warnings

    root = tree.nodes.get(tree.root_id)
    if not root:
        warnings.append(f"Root node '{tree.root_id}' not found in tree.")
        return warnings

    # Check 1: Root has children (unless unary mode)
    if len(tree.nodes) > 1 and not root.children:
        warnings.append(
            "Root component has no children despite multiple nodes. "
            "Decomposition may be malformed."
        )

    # Check 2: Orphan nodes
    for nid, node in tree.nodes.items():
        if nid != tree.root_id and not node.parent_id:
            warnings.append(f"Component '{nid}' has no parent — orphaned node.")

    # Check 3: Empty descriptions
    for nid, node in tree.nodes.items():
        if not node.description or not node.description.strip():
            warnings.append(
                f"Component '{nid}' has empty description. "
                f"Contract generation will lack context."
            )

    # Check 4: Duplicate descriptions
    descriptions: dict[str, str] = {}
    for nid, node in tree.nodes.items():
        desc = (node.description or "").strip().lower()
        if desc and len(desc) > 20:
            if desc in descriptions:
                warnings.append(
                    f"Components '{descriptions[desc]}' and '{nid}' have "
                    f"identical descriptions — likely a decomposition error."
                )
            else:
                descriptions[desc] = nid

    # Check 5: Task keyword coverage
    if task_text:
        task_lower = task_text.lower()
        task_significant = {
            w for w in task_lower.split()
            if len(w) > 4 and w.isalpha()
        }
        stop_words = {
            "should", "would", "could", "about", "these", "their",
            "which", "where", "there", "other", "every", "after",
            "before", "being", "between", "through", "under",
            "above", "below", "while", "during", "until", "since",
        }
        task_significant -= stop_words

        if task_significant:
            all_desc_text = " ".join(
                (n.description or "").lower() for n in tree.nodes.values()
            )
            missing = {w for w in task_significant if w not in all_desc_text}
            coverage = 1.0 - (len(missing) / len(task_significant))
            if coverage < 0.3:
                warnings.append(
                    f"Only {coverage:.0%} of task keywords appear in component "
                    f"descriptions. The decomposition may not cover the full task."
                )

    return warnings
