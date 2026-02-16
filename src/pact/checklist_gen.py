"""Requirements quality checklist generation.

Generates validation questions from contracts and test suites.
Purely mechanical — no LLM calls required.
"""

from __future__ import annotations

from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DecompositionTree,
)
from pact.schemas_tasks import (
    ChecklistCategory,
    ChecklistItem,
    RequirementsChecklist,
)


def _next_id(counter: list[int]) -> str:
    counter[0] += 1
    return f"C{counter[0]:03d}"


def _check_functions(
    contract: ComponentContract,
    test_suite: ContractTestSuite | None,
    counter: list[int],
) -> list[ChecklistItem]:
    """Generate checklist items for contract functions."""
    items: list[ChecklistItem] = []
    tested_functions = set()
    test_categories: dict[str, set[str]] = {}

    if test_suite:
        for tc in test_suite.test_cases:
            tested_functions.add(tc.function)
            test_categories.setdefault(tc.function, set()).add(tc.category)

    for fn in contract.functions:
        # Error cases defined?
        if not fn.error_cases:
            items.append(ChecklistItem(
                id=_next_id(counter),
                category=ChecklistCategory.error_handling,
                question=f"Are error cases defined for '{fn.name}'?",
                component_id=contract.component_id,
                reference=f"contracts/{contract.component_id}/interface.json",
            ))

        # Boundary conditions for inputs?
        for inp in fn.inputs:
            if inp.type_ref in ("int", "float", "number", "str", "string"):
                items.append(ChecklistItem(
                    id=_next_id(counter),
                    category=ChecklistCategory.edge_cases,
                    question=f"Are boundary conditions defined for '{fn.name}' input '{inp.name}' ({inp.type_ref})?",
                    component_id=contract.component_id,
                    reference=f"contracts/{contract.component_id}/interface.json",
                ))

        # Preconditions testable?
        for pre in fn.preconditions:
            items.append(ChecklistItem(
                id=_next_id(counter),
                category=ChecklistCategory.testability,
                question=f"Is precondition testable for '{fn.name}': {pre}?",
                component_id=contract.component_id,
                reference=f"contracts/{contract.component_id}/interface.json",
            ))

        # Postconditions measurable?
        for post in fn.postconditions:
            items.append(ChecklistItem(
                id=_next_id(counter),
                category=ChecklistCategory.acceptance_criteria,
                question=f"Is postcondition measurable for '{fn.name}': {post}?",
                component_id=contract.component_id,
                reference=f"contracts/{contract.component_id}/interface.json",
            ))

        # Test coverage checks
        fn_categories = test_categories.get(fn.name, set())

        if fn.name not in tested_functions:
            items.append(ChecklistItem(
                id=_next_id(counter),
                category=ChecklistCategory.testability,
                question=f"Does '{fn.name}' have any test coverage?",
                component_id=contract.component_id,
                satisfied=False,
            ))
        else:
            if "happy_path" not in fn_categories:
                items.append(ChecklistItem(
                    id=_next_id(counter),
                    category=ChecklistCategory.testability,
                    question=f"Does '{fn.name}' have happy path test coverage?",
                    component_id=contract.component_id,
                    satisfied=False,
                ))

            if "error_case" not in fn_categories and fn.error_cases:
                items.append(ChecklistItem(
                    id=_next_id(counter),
                    category=ChecklistCategory.testability,
                    question=f"Does '{fn.name}' have error case test coverage?",
                    component_id=contract.component_id,
                    satisfied=False,
                ))

    return items


def _check_contract_level(
    contract: ComponentContract,
    counter: list[int],
) -> list[ChecklistItem]:
    """Generate contract-level checklist items."""
    items: list[ChecklistItem] = []

    # Invariants measurable?
    for inv in contract.invariants:
        items.append(ChecklistItem(
            id=_next_id(counter),
            category=ChecklistCategory.acceptance_criteria,
            question=f"Is invariant measurable for '{contract.name}': {inv}?",
            component_id=contract.component_id,
            reference=f"contracts/{contract.component_id}/interface.json",
        ))

    # Dependencies bidirectional?
    for dep in contract.dependencies:
        items.append(ChecklistItem(
            id=_next_id(counter),
            category=ChecklistCategory.dependencies,
            question=f"Is dependency '{dep}' from '{contract.name}' bidirectionally declared?",
            component_id=contract.component_id,
        ))

    return items


def _check_test_suite(
    component_id: str,
    test_suite: ContractTestSuite,
    counter: list[int],
) -> list[ChecklistItem]:
    """Generate test suite quality checklist items."""
    items: list[ChecklistItem] = []

    categories = {tc.category for tc in test_suite.test_cases}

    if "happy_path" not in categories:
        items.append(ChecklistItem(
            id=_next_id(counter),
            category=ChecklistCategory.testability,
            question=f"Does test suite for '{component_id}' include happy path tests?",
            component_id=component_id,
            satisfied=False,
        ))

    if "error_case" not in categories:
        items.append(ChecklistItem(
            id=_next_id(counter),
            category=ChecklistCategory.testability,
            question=f"Does test suite for '{component_id}' include error case tests?",
            component_id=component_id,
            satisfied=False,
        ))

    if "edge_case" not in categories:
        items.append(ChecklistItem(
            id=_next_id(counter),
            category=ChecklistCategory.edge_cases,
            question=f"Does test suite for '{component_id}' include edge case tests?",
            component_id=component_id,
            satisfied=False,
        ))

    return items


def generate_checklist(
    tree: DecompositionTree,
    contracts: dict[str, ComponentContract],
    test_suites: dict[str, ContractTestSuite],
    project_id: str,
) -> RequirementsChecklist:
    """Generate a requirements quality checklist from project artifacts.

    Per function: error cases defined? boundary conditions? preconditions testable?
    Per contract: invariants measurable? dependencies bidirectional?
    Per test suite: happy path? error case? edge case?
    """
    counter = [0]
    items: list[ChecklistItem] = []

    for node_id in tree.nodes:
        contract = contracts.get(node_id)
        suite = test_suites.get(node_id)

        if contract:
            items.extend(_check_functions(contract, suite, counter))
            items.extend(_check_contract_level(contract, counter))

        if suite:
            items.extend(_check_test_suite(node_id, suite, counter))

    return RequirementsChecklist(
        project_id=project_id,
        items=items,
    )


def render_checklist_markdown(checklist: RequirementsChecklist) -> str:
    """Render a requirements checklist as markdown."""
    lines: list[str] = []
    lines.append(f"# Requirements Checklist — {checklist.project_id}")
    lines.append(f"Items: {len(checklist.items)} total, {checklist.satisfied_count} satisfied, {checklist.unanswered} unanswered")
    lines.append("")

    if not checklist.items:
        lines.append("No checklist items generated.")
        lines.append("")
        return "\n".join(lines)

    # Group by category
    categories_seen: list[ChecklistCategory] = []
    for item in checklist.items:
        if item.category not in categories_seen:
            categories_seen.append(item.category)

    for category in categories_seen:
        items = [i for i in checklist.items if i.category == category]
        lines.append(f"## {category.value.replace('_', ' ').title()} ({len(items)})")
        lines.append("")
        for item in items:
            if item.satisfied is True:
                checkbox = "[x]"
            elif item.satisfied is False:
                checkbox = "[!]"
            else:
                checkbox = "[ ]"
            component_tag = f" [{item.component_id}]" if item.component_id else ""
            lines.append(f"- {checkbox} {item.id}{component_tag} {item.question}")
            if item.reference:
                lines.append(f"  - Ref: {item.reference}")
        lines.append("")

    return "\n".join(lines)
