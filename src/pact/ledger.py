"""Ledger integration — load assertion exports and validate contracts.

When --ledger-dir is provided, Pact loads ledger_assertions_<component>.yaml
files and incorporates them into contract test suites. Ledger assertions are
treated as hard contract requirements.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from pact.schemas import ComponentContract

logger = logging.getLogger(__name__)


def load_ledger_assertions(ledger_dir: str | Path, component_id: str) -> list[dict]:
    """Load ledger assertions for a specific component.

    Looks for ledger_assertions_<component_id>.yaml in the ledger directory.
    Returns list of assertion dicts.
    """
    path = Path(ledger_dir) / f"ledger_assertions_{component_id}.yaml"
    if not path.exists():
        return []

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    assertions = data.get("assertions", [])
    if not isinstance(assertions, list):
        logger.warning("ledger_assertions_%s.yaml: 'assertions' is not a list", component_id)
        return []

    logger.info("Loaded %d ledger assertions for %s", len(assertions), component_id)
    return assertions


def load_all_ledger_assertions(ledger_dir: str | Path) -> dict[str, list[dict]]:
    """Load all ledger assertion files from a directory.

    Returns dict of {component_id: assertions}.
    """
    d = Path(ledger_dir)
    if not d.exists():
        return {}

    result: dict[str, list[dict]] = {}
    for path in d.glob("ledger_assertions_*.yaml"):
        # Extract component_id from filename
        stem = path.stem  # ledger_assertions_<component_id>
        cid = stem.replace("ledger_assertions_", "", 1)
        if cid:
            assertions = load_ledger_assertions(d, cid)
            if assertions:
                result[cid] = assertions

    return result


def validate_contract_against_ledger(
    contract: ComponentContract,
    assertions: list[dict],
) -> list[str]:
    """Check that a contract contains all methods required by ledger assertions.

    Returns list of violation messages.
    """
    violations: list[str] = []
    contract_methods = {f.name for f in contract.functions}

    for assertion in assertions:
        required_method = assertion.get("requires_method", "")
        if required_method and required_method not in contract_methods:
            violations.append(
                f"Ledger requires method '{required_method}' on '{contract.component_id}' "
                f"but contract does not define it"
            )

    return violations


def generate_ledger_test_code(
    component_id: str,
    assertions: list[dict],
    language: str = "python",
) -> str:
    """Generate test code from ledger assertions.

    Returns executable test code string to append to the test suite.
    """
    if not assertions:
        return ""

    if language in ("typescript", "javascript"):
        return _generate_ledger_test_ts(component_id, assertions)

    lines = [
        f"# Ledger assertions for {component_id} (auto-generated from ledger export)",
        "",
    ]

    for i, assertion in enumerate(assertions):
        name = assertion.get("name", f"ledger_{i}")
        desc = assertion.get("description", "Ledger assertion")
        method = assertion.get("requires_method", "")
        condition = assertion.get("condition", "")

        lines.extend([
            f"def test_ledger_{name}():",
            f'    """{desc}"""',
        ])
        if method:
            lines.append(f"    # Requires method: {method}")
        if condition:
            lines.append(f"    # Condition: {condition}")
        lines.extend([
            "    pass  # TODO: implement assertion logic from ledger spec",
            "",
        ])

    return "\n".join(lines)


def _generate_ledger_test_ts(component_id: str, assertions: list[dict]) -> str:
    """Generate TypeScript ledger test code."""
    lines = [
        f"// Ledger assertions for {component_id} (auto-generated from ledger export)",
        "",
    ]
    for assertion in assertions:
        desc = assertion.get("description", "Ledger assertion")
        lines.extend([
            f'it("ledger: {desc}", () => {{',
            "  // TODO: implement assertion logic from ledger spec",
            "});",
            "",
        ])
    return "\n".join(lines)
