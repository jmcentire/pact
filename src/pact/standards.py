"""Global standards — shared conventions distributed to every agent.

Collects packages, shared types, coding conventions, and mock definitions
from contracts and SOPs. Every agent (code_author, integrator) receives the
same standards brief so implementations stay consistent.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field

from pact.schemas import ComponentContract


@dataclass
class GlobalStandards:
    """Shared conventions distributed to every agent."""
    packages: list[str] = field(default_factory=list)
    shared_types: dict[str, str] = field(default_factory=dict)
    conventions: list[str] = field(default_factory=list)
    validators: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    mocks: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict."""
        return {
            "packages": self.packages,
            "shared_types": self.shared_types,
            "conventions": self.conventions,
            "validators": self.validators,
            "tools": self.tools,
            "mocks": self.mocks,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GlobalStandards:
        """Deserialize from a dict."""
        return cls(
            packages=data.get("packages", []),
            shared_types=data.get("shared_types", {}),
            conventions=data.get("conventions", []),
            validators=data.get("validators", []),
            tools=data.get("tools", []),
            mocks=data.get("mocks", {}),
        )


def collect_standards(
    contracts: dict[str, ComponentContract],
    sops: str = "",
    config_env: dict | None = None,
) -> GlobalStandards:
    """Extract global standards from project artifacts.

    Args:
        contracts: All component contracts.
        sops: Standard operating procedures text.
        config_env: Raw environment config dict (from pact.yaml).

    Returns:
        GlobalStandards with shared types, packages, conventions.
    """
    standards = GlobalStandards()

    # Extract shared types (appearing in 2+ contracts)
    type_usage: Counter[str] = Counter()
    type_defs: dict[str, str] = {}
    for contract in contracts.values():
        for t in contract.types:
            type_usage[t.name] += 1
            if t.name not in type_defs:
                if t.fields:
                    fields_str = ", ".join(
                        f"{f.name}: {f.type_ref}" for f in t.fields
                    )
                    type_defs[t.name] = f"struct({fields_str})"
                elif t.kind == "enum" and t.variants:
                    type_defs[t.name] = f"enum({', '.join(t.variants)})"
                else:
                    type_defs[t.name] = t.kind

    standards.shared_types = {
        name: type_defs.get(name, "")
        for name, count in type_usage.items()
        if count >= 2
    }

    # Extract shared validator patterns
    validator_patterns: Counter[str] = Counter()
    for contract in contracts.values():
        for func in contract.functions:
            for inp in func.inputs:
                for v in inp.validators:
                    pattern = f"{v.kind}({v.expression})"
                    validator_patterns[pattern] += 1
        for t in contract.types:
            for f_spec in t.fields:
                for v in f_spec.validators:
                    pattern = f"{v.kind}({v.expression})"
                    validator_patterns[pattern] += 1

    standards.validators = sorted(
        pattern for pattern, count in validator_patterns.items()
        if count >= 2
    )

    # Extract packages from environment config
    if config_env:
        required_tools = config_env.get("required_tools", [])
        if required_tools:
            standards.tools = list(required_tools)

    # Extract conventions from SOPs
    if sops:
        standards.conventions = _extract_conventions(sops)

    # Default packages
    standards.packages = ["pydantic>=2.0", "pytest"]

    return standards


def _extract_conventions(sops: str) -> list[str]:
    """Extract actionable convention rules from SOPs text.

    Looks for bullet points and numbered items that describe
    coding patterns, naming conventions, or requirements.
    """
    conventions: list[str] = []
    for line in sops.splitlines():
        stripped = line.strip()
        # Match bullet points or numbered items that look like rules
        if re.match(r"^[-*•]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            # Clean prefix
            rule = re.sub(r"^[-*•\d.]+\s+", "", stripped).strip()
            if rule and len(rule) > 10:
                conventions.append(rule)
    return conventions


def render_standards_brief(standards: GlobalStandards) -> str:
    """Format standards for injection into agent prompts.

    Returns a markdown section that can be inserted into handoff briefs.
    """
    if not any([
        standards.packages, standards.shared_types, standards.conventions,
        standards.validators, standards.tools,
    ]):
        return ""

    lines = ["## GLOBAL STANDARDS", ""]

    if standards.packages:
        lines.append("### Required Packages")
        for pkg in standards.packages:
            lines.append(f"- {pkg}")
        lines.append("")

    if standards.shared_types:
        lines.append("### Shared Types (used across components)")
        lines.append("These types appear in multiple contracts. Use identical definitions.")
        for name, definition in sorted(standards.shared_types.items()):
            lines.append(f"- `{name}`: {definition}")
        lines.append("")

    if standards.conventions:
        lines.append("### Coding Conventions")
        for conv in standards.conventions:
            lines.append(f"- {conv}")
        lines.append("")

    if standards.validators:
        lines.append("### Common Validators")
        for v in standards.validators:
            lines.append(f"- {v}")
        lines.append("")

    if standards.tools:
        lines.append("### Available Tools")
        for tool in standards.tools:
            lines.append(f"- {tool}")
        lines.append("")

    return "\n".join(lines)
