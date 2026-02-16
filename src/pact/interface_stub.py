"""Interface stub generation — the agent's mental model.

Renders a ComponentContract into a code-shaped reference document that
LLMs consume dramatically better than raw JSON schemas. Research shows
AI performs significantly better when given interface stubs vs schema dumps.

The stub looks like actual code — type definitions, function signatures,
docstrings with pre/postconditions, error specifications, and validators.
This is the "header file" that every agent receives as their mental model
of the component they're working with (or working against).

Even for dynamically-typed target languages, the stub gives agents a
precise conceptual model. We don't need the language to be strongly typed;
we just need agents to know the valid shapes, constraints, and expectations.

Four output formats:
  1. render_stub()           — Python-style interface stub (.pyi-like)
  2. render_dependency_map() — compact reference for all dependencies
  3. render_compact_deps()   — function signatures + type shapes (~80% smaller)
  4. render_handoff_brief()  — complete context for agent handoff
"""

from __future__ import annotations

import hashlib

from pact.schemas import (
    ComponentContract,
    ComponentTask,
    ContractTestSuite,
    DecompositionTree,
    ErrorCase,
    FieldSpec,
    FunctionContract,
    RunState,
    TestResults,
    TypeSpec,
    ValidatorSpec,
)


# ── Interface Stub Rendering ─────────────────────────────────────────


def get_required_exports(contract: ComponentContract) -> list[str]:
    """Extract the list of names that an implementation MUST export.

    These are the type names, function names, and error class names
    from the contract. Tests import these by name and fail at collection
    if any are missing.
    """
    exports: list[str] = []
    for t in contract.types:
        exports.append(t.name)
    for func in contract.functions:
        exports.append(func.name)
        for err in func.error_cases:
            if err.error_type and err.error_type not in exports:
                exports.append(err.error_type)
    return exports


def render_stub(contract: ComponentContract) -> str:
    """Render a contract as a Python-style interface stub.

    This is the primary "mental model" artifact. It looks like code,
    not like a JSON schema. Agents consume this format far more accurately.

    Example output:
        # === Pricing Engine (pricing) v1 ===
        # Dependencies: inventory, tax_calculator

        class PriceResult:
            \"\"\"Final price calculation result.\"\"\"
            base_price: float          # required
            tax_amount: float          # required
            total: float               # required, postcondition: total == base_price + tax_amount
            currency: str = "USD"      # optional, validators: regex(^[A-Z]{3}$)

        class PricingError(Enum):
            UNIT_NOT_FOUND = "unit_not_found"
            INVALID_DATES = "invalid_dates"

        def calculate_price(
            unit_id: str,              # required, precondition: non-empty
            check_in: str,             # required, validators: regex(^\\d{4}-\\d{2}-\\d{2}$)
            check_out: str,            # required
            guest_count: int = 1,      # optional, validators: range(1, 20)
        ) -> PriceResult:
            \"\"\"Calculate the nightly price for a unit stay.

            Preconditions:
              - check_in < check_out
              - unit_id exists in inventory

            Postconditions:
              - result.total > 0
              - result.currency is valid ISO 4217

            Errors:
              - UNIT_NOT_FOUND: when unit_id not in inventory
              - INVALID_DATES: when check_in >= check_out

            Side effects: none
            Idempotent: yes
            \"\"\"
            ...
    """
    lines: list[str] = []

    # Header
    dep_str = f"  Dependencies: {', '.join(contract.dependencies)}" if contract.dependencies else ""
    lines.append(f"# === {contract.name} ({contract.component_id}) v{contract.version} ===")
    if dep_str:
        lines.append(f"#{dep_str}")
    if contract.description:
        lines.append(f"# {contract.description}")
    lines.append("")

    # Invariants (module-level)
    if contract.invariants:
        lines.append("# Module invariants:")
        for inv in contract.invariants:
            lines.append(f"#   - {inv}")
        lines.append("")

    # Type definitions
    for type_spec in contract.types:
        lines.extend(_render_type(type_spec))
        lines.append("")

    # Function signatures
    for func in contract.functions:
        lines.extend(_render_function(func))
        lines.append("")

    # Required exports checklist — ensures implementations export exact names
    exports = get_required_exports(contract)
    if exports:
        lines.append("# ── REQUIRED EXPORTS ──────────────────────────────────")
        lines.append("# Your implementation module MUST export ALL of these names")
        lines.append("# with EXACTLY these spellings. Tests import them by name.")
        lines.append(f"# __all__ = {exports}")
        lines.append("")

    return "\n".join(lines)


def _render_type(t: TypeSpec) -> list[str]:
    """Render a single type definition."""
    lines: list[str] = []

    if t.kind == "enum":
        lines.append(f"class {t.name}(Enum):")
        if t.description:
            lines.append(f'    """{t.description}"""')
        for variant in t.variants:
            lines.append(f'    {variant.upper()} = "{variant}"')
        if not t.variants:
            lines.append("    pass")
        return lines

    if t.kind == "struct":
        lines.append(f"class {t.name}:")
        if t.description:
            lines.append(f'    """{t.description}"""')
        for field in t.fields:
            lines.append(f"    {_render_field_line(field)}")
        if not t.fields:
            lines.append("    pass")
        return lines

    if t.kind == "list":
        lines.append(f"{t.name} = list[{t.item_type}]")
        if t.description:
            lines.append(f"# {t.description}")
        return lines

    if t.kind == "optional":
        inner = t.inner_types[0] if t.inner_types else "Any"
        lines.append(f"{t.name} = {inner} | None")
        return lines

    if t.kind == "union":
        union_str = " | ".join(t.inner_types) if t.inner_types else "Any"
        lines.append(f"{t.name} = {union_str}")
        return lines

    # Primitive alias
    lines.append(f"{t.name} = {t.kind}  # {t.description}" if t.description else f"{t.name} = {t.kind}")
    return lines


def _render_field_line(field: FieldSpec) -> str:
    """Render a single field as a stub line with annotations."""
    parts = [f"{field.name}: {field.type_ref}"]

    annotations: list[str] = []
    if not field.required:
        if field.default:
            parts[0] += f" = {field.default}"
        else:
            parts[0] += " = None"
        annotations.append("optional")
    else:
        annotations.append("required")

    for v in field.validators:
        annotations.append(f"{v.kind}({v.expression})")

    if field.description:
        annotations.append(field.description)

    comment = ", ".join(annotations)
    return f"{parts[0]:40s} # {comment}"


def _render_function(func: FunctionContract) -> list[str]:
    """Render a function signature with full docstring."""
    lines: list[str] = []

    # Signature
    params: list[str] = []
    for inp in func.inputs:
        p = f"    {inp.name}: {inp.type_ref}"
        if not inp.required:
            p += f" = {inp.default}" if inp.default else " = None"
        # Add inline validator comment
        if inp.validators:
            v_str = ", ".join(f"{v.kind}({v.expression})" for v in inp.validators)
            p += f",{' ' * max(1, 30 - len(p))}# {v_str}"
        else:
            p += ","
        params.append(p)

    if params:
        lines.append(f"def {func.name}(")
        lines.extend(params)
        lines.append(f") -> {func.output_type}:")
    else:
        lines.append(f"def {func.name}() -> {func.output_type}:")

    # Docstring
    doc_lines: list[str] = []
    if func.description:
        doc_lines.append(func.description)
        doc_lines.append("")

    if func.preconditions:
        doc_lines.append("Preconditions:")
        for pre in func.preconditions:
            doc_lines.append(f"  - {pre}")
        doc_lines.append("")

    if func.postconditions:
        doc_lines.append("Postconditions:")
        for post in func.postconditions:
            doc_lines.append(f"  - {post}")
        doc_lines.append("")

    if func.error_cases:
        doc_lines.append("Errors:")
        for err in func.error_cases:
            doc_lines.append(f"  - {err.name} ({err.error_type}): {err.condition}")
            if err.error_data:
                for k, v in err.error_data.items():
                    doc_lines.append(f"      {k}: {v}")
        doc_lines.append("")

    if func.side_effects:
        doc_lines.append(f"Side effects: {', '.join(func.side_effects)}")
    else:
        doc_lines.append("Side effects: none")

    doc_lines.append(f"Idempotent: {'yes' if func.idempotent else 'no'}")

    lines.append('    """')
    for dl in doc_lines:
        lines.append(f"    {dl}" if dl else "")
    lines.append('    """')
    lines.append("    ...")

    return lines


# ── Dependency Map ───────────────────────────────────────────────────


def render_dependency_map(
    component_id: str,
    contracts: dict[str, ComponentContract],
) -> str:
    """Render a compact reference of all dependencies' interfaces.

    This gives agents working on `component_id` a quick reference for
    every function they can call on their dependencies, without seeing
    the full contract details. It's a "what can I use?" cheat sheet.

    Example:
        # Available dependencies for: checkout

        ## pricing (v1)
        calculate_price(unit_id: str, dates: DateRange) -> PriceResult
          errors: UNIT_NOT_FOUND, INVALID_DATES
          types: PriceResult{base_price: float, tax: float, total: float}

        ## inventory (v1)
        check_availability(unit_id: str, dates: DateRange) -> bool
          errors: UNIT_NOT_FOUND
    """
    contract = contracts.get(component_id)
    if not contract:
        return f"# No contract found for {component_id}"

    lines = [f"# Available dependencies for: {component_id}", ""]

    for dep_id in contract.dependencies:
        dep = contracts.get(dep_id)
        if not dep:
            lines.append(f"## {dep_id} — NOT FOUND")
            lines.append("")
            continue

        lines.append(f"## {dep.name} ({dep_id}) v{dep.version}")

        # Compact type summary
        for t in dep.types:
            if t.kind == "struct" and t.fields:
                fields_str = ", ".join(f"{f.name}: {f.type_ref}" for f in t.fields)
                lines.append(f"  type {t.name} {{ {fields_str} }}")
            elif t.kind == "enum" and t.variants:
                lines.append(f"  enum {t.name} {{ {', '.join(t.variants)} }}")

        # Compact function signatures
        for func in dep.functions:
            inputs_str = ", ".join(f"{i.name}: {i.type_ref}" for i in func.inputs)
            errors_str = ""
            if func.error_cases:
                errors_str = f"\n    errors: {', '.join(e.name for e in func.error_cases)}"
            lines.append(f"  {func.name}({inputs_str}) -> {func.output_type}{errors_str}")

        lines.append("")

    return "\n".join(lines)


def render_compact_deps(contracts: dict[str, ComponentContract]) -> str:
    """Compact dependency reference: function signatures + type shapes only.

    ~80% fewer tokens than full render_stub() while preserving all type
    information needed for contract authoring.

    Example output:
        ## pricing_engine
        calculate_price(unit_id: str, dates: DateRange) -> PriceResult
        DateRange = {check_in: date, check_out: date}
        PriceResult = {total: float, breakdown: list[LineItem]}
    """
    if not contracts:
        return ""

    parts = []
    for comp_id, contract in contracts.items():
        lines = [f"## {contract.name} ({comp_id})"]

        # Function signatures
        for func in contract.functions:
            inputs = ", ".join(f"{i.name}: {i.type_ref}" for i in func.inputs)
            lines.append(f"{func.name}({inputs}) -> {func.output_type}")

        # Type shapes (compact)
        for typedef in contract.types:
            if typedef.fields:
                field_strs = ", ".join(f"{f.name}: {f.type_ref}" for f in typedef.fields)
                lines.append(f"{typedef.name} = {{{field_strs}}}")
            elif typedef.kind == "enum":
                variants = ", ".join(v for v in (typedef.variants or []))
                lines.append(f"{typedef.name} = enum({variants})")
            else:
                lines.append(f"{typedef.name} = {typedef.kind}")

        parts.append("\n".join(lines))

    return "\n\n".join(parts)


# ── Log Key Preamble ─────────────────────────────────────────────────


def render_log_key_preamble(
    project_id: str,
    component_id: str,
    prefix: str = "PACT",
) -> str:
    """Generate a logging preamble that embeds the PACT log key.

    Returns Python code that sets up a logger with the embedded key.
    The key format is PREFIX:project_hash:component_id and appears in
    every log line, enabling automatic error attribution by the Sentinel.
    """
    key = f"{prefix}:{project_id}:{component_id}"
    return f'''import logging

_PACT_KEY = "{key}"
logger = logging.getLogger(__name__)


class PactFormatter(logging.Formatter):
    """Formatter that injects the PACT log key into every record."""

    def format(self, record):
        record.pact_key = _PACT_KEY
        return super().format(record)


def _log(level: str, msg: str, **kwargs) -> None:
    """Log with PACT key embedded for production traceability."""
    getattr(logger, level)(f"[{{_PACT_KEY}}] {{msg}}", **kwargs)
'''


def project_id_hash(project_dir: str) -> str:
    """Generate a 6-char project ID hash from a project directory path."""
    return hashlib.sha256(project_dir.encode()).hexdigest()[:6]


# ── Handoff Brief ────────────────────────────────────────────────────


def render_handoff_brief(
    component_id: str,
    contract: ComponentContract,
    contracts: dict[str, ComponentContract],
    test_suite: ContractTestSuite | None = None,
    test_results: TestResults | None = None,
    prior_failures: list[str] | None = None,
    attempt: int = 1,
    sops: str = "",
    external_context: str = "",
    learnings: str = "",
    pitch_context: str = "",
    include_test_code: bool = True,
    log_key_preamble: str = "",
) -> str:
    """Render a complete handoff document for a fresh agent.

    This is the "you're picking up where someone left off" briefing.
    Designed so a brand-new agent with zero prior context can understand
    exactly what to do, what's been tried, and what constraints apply.

    The brief contains:
    1. Interface stub (mental model)
    2. Dependency map (what you can call)
    3. Test summary (what you must pass)
    4. History (what's been tried, what failed)
    5. SOPs (rules to follow)
    """
    lines: list[str] = []

    # Section 1: Mission
    lines.append("# HANDOFF BRIEF")
    lines.append(f"## Component: {contract.name} ({component_id})")
    lines.append(f"## Attempt: {attempt}")
    lines.append("")

    # Section 2: Interface (the mental model)
    lines.append("## YOUR INTERFACE CONTRACT")
    lines.append("```python")
    lines.append(render_stub(contract))
    lines.append("```")
    lines.append("")

    # Section 2b: Log key preamble (for production traceability)
    if log_key_preamble:
        lines.append("## LOG KEY PREAMBLE (include at top of every module)")
        lines.append("```python")
        lines.append(log_key_preamble)
        lines.append("```")
        lines.append("ALL log statements MUST include the PACT log key for production traceability.")
        lines.append("")

    # Section 3: Dependencies
    if contract.dependencies:
        lines.append("## AVAILABLE DEPENDENCIES")
        lines.append("```")
        lines.append(render_dependency_map(component_id, contracts))
        lines.append("```")
        lines.append("")

    # Section 4: Tests to pass
    if test_suite:
        if include_test_code:
            lines.append(f"## TESTS TO PASS ({len(test_suite.test_cases)} cases)")
            for tc in test_suite.test_cases:
                marker = ""
                if test_results and test_results.failure_details:
                    failed_ids = {f.test_id for f in test_results.failure_details}
                    if tc.id in failed_ids:
                        marker = " ** PREVIOUSLY FAILED **"
                lines.append(f"  - [{tc.category}] {tc.id}: {tc.description}{marker}")
            lines.append("")

            if test_suite.generated_code:
                lines.append("### Test code:")
                lines.append("```python")
                lines.append(test_suite.generated_code)
                lines.append("```")
                lines.append("")
        else:
            # compact listing
            if test_suite.test_cases:
                lines.append(f"## TESTS TO PASS ({len(test_suite.test_cases)} cases)")
                for tc in test_suite.test_cases:
                    desc = tc.description or ""
                    lines.append(f"- {tc.id}: {desc}")
            lines.append("")

    # Section 5: History (what's been tried)
    if prior_failures:
        lines.append("## PRIOR FAILURES (do NOT repeat these mistakes)")
        for i, failure in enumerate(prior_failures, 1):
            lines.append(f"  {i}. {failure}")
        lines.append("")

    if test_results and not test_results.all_passed:
        lines.append(f"## LAST TEST RUN: {test_results.passed}/{test_results.total} passed")
        for fd in test_results.failure_details[:5]:
            lines.append(f"  FAIL: {fd.test_id} — {fd.error_message}")
        lines.append("")

    # Section 6: Shaping context (from Shape Up phase)
    if pitch_context:
        lines.append("## SHAPING CONTEXT")
        lines.append(pitch_context)
        lines.append("")

    # Section 7: External context (from integrations)
    if external_context:
        lines.append(external_context)
        lines.append("")

    # Section 8: Learnings from previous runs
    if learnings:
        lines.append("## LEARNINGS")
        lines.append(learnings)
        lines.append("")

    # Section 9: SOPs
    if sops:
        lines.append("## OPERATING PROCEDURES (mandatory)")
        lines.append(sops)
        lines.append("")

    return "\n".join(lines)


# ── Progress Snapshot ────────────────────────────────────────────────


def render_progress_snapshot(
    state: RunState,
    tree: DecompositionTree | None = None,
    contracts: dict[str, ComponentContract] | None = None,
) -> str:
    """Render a compact progress snapshot for scheduler resumption.

    This is what gets read when the scheduler wakes up or when a human
    wants to understand current state at a glance.
    """
    lines = [
        "# PROGRESS SNAPSHOT",
        f"Run: {state.id} | Phase: {state.phase} | Status: {state.status}",
        f"Cost: ${state.total_cost_usd:.4f} | Tokens: {state.total_tokens:,}",
        "",
    ]

    if state.pause_reason:
        lines.append(f"PAUSED: {state.pause_reason}")
        lines.append("")

    if tree:
        lines.append("## Components:")
        for node_id in tree.topological_order():
            node = tree.nodes[node_id]
            icon = {
                "pending": "[ ]", "contracted": "[C]",
                "implemented": "[I]", "tested": "[+]", "failed": "[X]",
            }.get(node.implementation_status, "[?]")
            test_info = ""
            if node.test_results:
                tr = node.test_results
                test_info = f" ({tr.passed}/{tr.total} tests)"
            dep_info = ""
            if node.children:
                dep_info = f" -> [{', '.join(node.children)}]"
            lines.append(f"  {icon} {node.name} ({node.component_id}){dep_info}{test_info}")
        lines.append("")

    if state.component_tasks:
        active = [t for t in state.component_tasks if t.status == "implementing"]
        failed = [t for t in state.component_tasks if t.status == "failed"]
        if active:
            lines.append(f"Active: {', '.join(t.component_id for t in active)}")
        if failed:
            lines.append(f"Failed: {', '.join(f'{t.component_id} ({t.last_error[:50]})' for t in failed)}")
        lines.append("")

    return "\n".join(lines)


# ── Context Compression ─────────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text/code."""
    return len(text) // 4 + 1


def build_code_agent_context(
    contract: ComponentContract,
    test_suite: ContractTestSuite,
    decisions: list[str] | None = None,
    research: list[dict] | None = None,
    max_tokens: int = 8000,
) -> str:
    """Build tiered context for code generation agent.

    Tier 1 (always included): interface stub + test code
    Tier 2 (if room): decisions relevant to this component
    Tier 3 (if room): research findings summary (not full findings)

    Postconditions:
      - Result fits within max_tokens (estimated)
      - Tier 1 is never truncated
      - Tier 2 and 3 are truncated gracefully if needed
    """
    sections: list[str] = []
    used_tokens = 0

    # Tier 1: Always include contract stub and test code
    stub = render_stub(contract)
    tier1_parts = [
        "## CONTRACT",
        "```python",
        stub,
        "```",
    ]
    if test_suite.generated_code:
        tier1_parts.extend([
            "",
            "## TESTS TO PASS",
            "```python",
            test_suite.generated_code,
            "```",
        ])
    elif test_suite.test_cases:
        tier1_parts.append("")
        tier1_parts.append("## TEST CASES")
        for tc in test_suite.test_cases:
            tier1_parts.append(f"- [{tc.category}] {tc.id}: {tc.description}")

    tier1 = "\n".join(tier1_parts)
    used_tokens = _estimate_tokens(tier1)
    sections.append(tier1)

    remaining = max_tokens - used_tokens

    # Tier 2: Decisions (if room)
    if decisions and remaining > 100:
        decisions_text_parts = ["", "## DECISIONS"]
        for d in decisions:
            line = f"- {d}"
            line_tokens = _estimate_tokens(line)
            if used_tokens + line_tokens > max_tokens - 50:
                decisions_text_parts.append("- ... (truncated)")
                break
            decisions_text_parts.append(line)
            used_tokens += line_tokens
        decisions_text = "\n".join(decisions_text_parts)
        sections.append(decisions_text)
        remaining = max_tokens - used_tokens

    # Tier 3: Research summary (if room)
    if research and remaining > 100:
        research_parts = ["", "## RESEARCH SUMMARY"]
        for item in research:
            topic = item.get("topic", "")
            finding = item.get("finding", "")
            if topic and finding:
                # Summarize: just topic + first sentence of finding
                first_sentence = finding.split(".")[0] + "." if "." in finding else finding
                line = f"- **{topic}**: {first_sentence}"
            elif topic:
                line = f"- {topic}"
            else:
                continue
            line_tokens = _estimate_tokens(line)
            if used_tokens + line_tokens > max_tokens - 20:
                research_parts.append("- ... (truncated)")
                break
            research_parts.append(line)
            used_tokens += line_tokens
        if len(research_parts) > 1:  # More than just the header
            sections.append("\n".join(research_parts))

    return "\n".join(sections)
