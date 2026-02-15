"""Contract quality checks — mechanical audits for vague language."""

from __future__ import annotations

import re
import logging

from pact.schemas import ComponentContract

logger = logging.getLogger(__name__)

VAGUE_PATTERNS: list[re.Pattern] = [
    re.compile(r"entire class of", re.IGNORECASE),
    re.compile(r"best practice", re.IGNORECASE),
    re.compile(r"industry standard", re.IGNORECASE),
    re.compile(r"scalable and maintainable", re.IGNORECASE),
    re.compile(r"robust and reliable", re.IGNORECASE),
    re.compile(r"clean architecture", re.IGNORECASE),
    re.compile(r"properly handle", re.IGNORECASE),
    re.compile(r"as needed", re.IGNORECASE),
    re.compile(r"and more", re.IGNORECASE),
    re.compile(r"etc\.?\s*$", re.IGNORECASE),
    re.compile(r"works? on my machine", re.IGNORECASE),
    re.compile(r"appropriate\s+(error|handling|validation)", re.IGNORECASE),
]


def audit_contract_specificity(contract: ComponentContract) -> list[str]:
    """Flag vague language in contract descriptions, invariants, and error messages.

    Returns list of warnings with location and flagged phrase.

    Postconditions:
      - Every flagged phrase includes the field path where it was found
      - Warnings are suggestions, not validation errors
    """
    warnings: list[str] = []

    def check(text: str, field_path: str) -> None:
        for pattern in VAGUE_PATTERNS:
            match = pattern.search(text)
            if match:
                warnings.append(
                    f"Vague language in {field_path}: '{match.group()}' — "
                    f"be specific about what this means"
                )

    # Check contract-level fields
    check(contract.description, f"{contract.component_id}.description")

    # Check invariants
    for i, inv in enumerate(contract.invariants):
        check(inv, f"{contract.component_id}.invariants[{i}]")

    # Check functions
    for fi, func in enumerate(contract.functions):
        check(func.description, f"{contract.component_id}.functions[{fi}].description")
        for pi, pre in enumerate(func.preconditions):
            check(pre, f"{contract.component_id}.functions[{fi}].preconditions[{pi}]")
        for pi, post in enumerate(func.postconditions):
            check(post, f"{contract.component_id}.functions[{fi}].postconditions[{pi}]")
        for ei, ec in enumerate(func.error_cases):
            check(ec.condition, f"{contract.component_id}.functions[{fi}].error_cases[{ei}].condition")

    # Check type descriptions
    for ti, t in enumerate(contract.types):
        check(t.description, f"{contract.component_id}.types[{ti}].description")

    return warnings
