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


def validate_type_references(contract: ComponentContract) -> list[str]:
    """Check that all type_ref values in fields resolve to a defined type."""
    defined_types = {t.name for t in contract.types}
    # Add primitives
    defined_types |= {"str", "int", "float", "bool", "None", "bytes", "dict", "list", "any"}
    errors = []

    for func in contract.functions:
        # Check output type
        if func.output_type and func.output_type not in defined_types:
            errors.append(
                f"Function '{func.name}' output_type '{func.output_type}' "
                f"not defined in component '{contract.component_id}'"
            )
        # Check input types
        for field in func.inputs:
            if field.type_ref not in defined_types:
                errors.append(
                    f"Function '{func.name}' input '{field.name}' type_ref "
                    f"'{field.type_ref}' not defined in component '{contract.component_id}'"
                )

    # Check struct field types
    for type_spec in contract.types:
        for field in type_spec.fields:
            if field.type_ref not in defined_types:
                errors.append(
                    f"Type '{type_spec.name}' field '{field.name}' type_ref "
                    f"'{field.type_ref}' not defined in component '{contract.component_id}'"
                )
        # Check item_type for list types
        if type_spec.kind == "list" and type_spec.item_type:
            if type_spec.item_type not in defined_types:
                errors.append(
                    f"Type '{type_spec.name}' item_type '{type_spec.item_type}' "
                    f"not defined in component '{contract.component_id}'"
                )

    return errors


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
    return errors


def validate_test_suite(suite: ContractTestSuite) -> list[str]:
    """Check that a test suite is valid — has cases and generated code parses."""
    errors = []
    if not suite.component_id:
        errors.append("Test suite missing component_id")
    if not suite.test_cases:
        errors.append(f"Test suite for '{suite.component_id}' has no test cases")
    if suite.generated_code:
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

    # Check dependency contracts — distinguish internal vs external
    tree_component_ids = set(tree.nodes.keys()) if tree else set()

    for cid, contract in contracts.items():
        for dep_id in contract.dependencies:
            if dep_id in contracts:
                continue  # Internal dependency with contract — OK

            if dep_id in tree_component_ids:
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

    if all_errors:
        return GateResult(
            passed=False,
            reason=f"Contract validation failed with {len(all_errors)} error(s)",
            details=all_errors,
        )

    return GateResult(
        passed=True,
        reason="All contracts validated successfully",
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
