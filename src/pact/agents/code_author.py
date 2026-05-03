"""Code author agent — implements black boxes against contracts.

Follows the Research-First Protocol:
1. Research algorithmic approaches, libraries, security best practices
2. Plan implementation approach and self-evaluate
3. Write implementation code
"""

from __future__ import annotations

import logging

from pact.agents.base import AgentBase
from pact.agents.research import plan_and_evaluate, research_phase
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    PlanEvaluation,
    ResearchReport,
    TestResults,
)

logger = logging.getLogger(__name__)

CODE_SYSTEM = """You are starting fresh on this implementation with no prior context.

You are implementing a component against its contract. The contract defines
WHAT to build, the tests define HOW to verify. Produce an implementation
that passes all contract tests.

All type, function, and error class names must match the contract stub exactly.
Check the REQUIRED EXPORTS list at the bottom of the stub — tests import these
names directly. Standalone functions are module-level, not class methods.
Enum member names match variant names exactly (no UPPERCASE conversion).
All log statements include the PACT log key. If using Pydantic, use v2 API
(model_validator, field_validator, model_dump, ConfigDict).

When the contract defines types with validators, implement them as canonical data
structures with runtime validation (Pydantic models, dataclasses with __post_init__
checks, or equivalent). Invalid inputs should raise clear errors. Prefer bespoke
types over raw primitives for fields with domain semantics.

Every class must accept optional event_handler and log_handler kwargs:
  def __init__(self, ..., event_handler=None, log_handler=None):
      self._emit = event_handler or (lambda event: None)
      self._log = log_handler or (lambda level, msg, ctx: None)
Call self._emit() at start and end of every public method with:
  {"pact_key": "PACT:<component_id>:<method_name>", "event": "invoked"|"completed",
   "input_classification": [...], "output_classification": [...],
   "side_effects": [...], "ts": time.time_ns()}
The PACT key must be a string literal in the source. When no handler is provided,
emission must be a silent no-op — no stdout, no errors."""

CODE_SYSTEM_TS = """You are starting fresh on this implementation with no prior context.

You are implementing a TypeScript component against its contract. All type,
function, and error class names must match the contract stub exactly. Check
the REQUIRED EXPORTS list — tests import these names directly. Standalone
functions are module-level named exports. Use strict mode, unknown instead
of any. Error classes extend Error. Named exports only, no defaults.
All log statements include the PACT log key.

When the contract defines types with validators, implement them as canonical data
structures with runtime validation (Zod schemas, branded types, or class constructors
with checks). Invalid inputs should throw clear errors. Prefer bespoke types over
raw primitives for fields with domain semantics.

Effect v3 CRITICAL: Data.tagged is curried. WRONG: Data.tagged('Tag', {fields}).
CORRECT: Data.tagged('Tag')({fields}) or Data.TaggedError('Tag')({fields}).
The second positional argument is silently ignored — this is the #1 Effect v3 mistake.
Similarly, Layer.fail() takes a value, not a constructor — pass the constructed error.
Every class must accept optional eventHandler in the constructor.
Emit structured events at start/end of each public method with pact_key, event type,
classification arrays, and side_effects. Null handler must be a silent no-op."""

CODE_SYSTEM_JS = """You are starting fresh on this implementation with no prior context.

You are implementing a JavaScript component against its contract. All
function and error class names must match the contract stub exactly. Check
the REQUIRED EXPORTS list — tests import these names directly. Standalone
functions are module-level named exports. Use ESM imports with .js extensions.
Error classes extend Error. Named exports only, no defaults. No TypeScript
syntax. JSDoc for documentation. All log statements include the PACT log key.

When the contract defines types with validators, implement them as canonical data
structures with runtime validation (class constructors with checks, or factory
functions that throw on invalid input). Prefer bespoke types over raw primitives
for fields with domain semantics.

Every class must accept optional eventHandler in the constructor.
Emit structured events at start/end of each public method with pact_key, event type,
classification arrays, and side_effects. Null handler must be a silent no-op.
Effect v3 CRITICAL: Data.tagged is curried. WRONG: Data.tagged('Tag', {fields}).
CORRECT: Data.tagged('Tag')({fields}) or Data.TaggedError('Tag')({fields}).
The second positional argument is silently ignored — this is the #1 Effect v3 mistake.
Similarly, Layer.fail() takes a value, not a constructor — pass the constructed error."""

CODE_SYSTEM_RUST = """You are starting fresh on this implementation with no prior context.

You are implementing a Rust component against its contract. All type names,
function names, and error type names must match the contract stub exactly.
Check the REQUIRED EXPORTS list — tests import these names directly.
Standalone functions are pub module-level functions. All log statements
include the PACT log key via the pact_log! macro.

Write idiomatic Rust with proper ownership, borrowing, and lifetimes.
Use Result<T, E> for all fallible operations — never unwrap() or panic!()
in library code. Prefer &str over String for function inputs; return String
for owned values. Use Option<T> for optional fields and parameters.

Use serde (Serialize, Deserialize) for all data types that cross boundaries.
Use thiserror::Error for custom error types — derive Error with #[error("...")]
messages. Use anyhow::Result only in binary entry points, not library code.

Include all necessary `use` statements at the top of each module. Prefer
explicit imports over glob imports. Common patterns:
  use serde::{Deserialize, Serialize};
  use thiserror::Error;
  use std::collections::HashMap;

When the contract defines types with validators, implement validation in
constructor functions (e.g., fn new(...) -> Result<Self, Error>) or use the
builder pattern. Invalid inputs should return Err, not panic.

Struct fields that have domain semantics should use newtype wrappers
(e.g., pub struct UserId(pub String)) rather than raw primitives.

Every public struct must derive Debug and Clone at minimum. Data structs
should also derive Serialize and Deserialize. Error enums should derive
Error (thiserror) and Debug.

All log statements must use the pact_log! macro with the embedded PACT key
for production traceability. When no log handler is configured, logging must
be a silent no-op — no stdout, no errors."""


class ImplementationResult:
    """Result of a code author run."""

    def __init__(
        self,
        files: dict[str, str],
        research: ResearchReport,
        plan: PlanEvaluation,
    ) -> None:
        self.files = files
        self.research = research
        self.plan = plan


async def author_code(
    agent: AgentBase,
    contract: ComponentContract,
    test_suite: ContractTestSuite,
    dependency_contracts: dict[str, ComponentContract] | None = None,
    prior_failures: list[str] | None = None,
    prior_test_results: TestResults | None = None,
    attempt: int = 1,
    sops: str = "",
    max_plan_revisions: int = 2,
    external_context: str = "",
    learnings: str = "",
    prior_research: ResearchReport | None = None,
    prior_source: dict[str, str] | None = None,
    language: str = "python",
    strategic_context: str = "",
    processing_register: str = "",
) -> ImplementationResult:
    """Generate implementation code following the Research-First Protocol.

    Args:
        agent: The LLM agent (typically claude_code backend).
        contract: The ComponentContract to implement.
        test_suite: The ContractTestSuite to pass.
        prior_failures: Descriptions of prior failed attempts (no reasoning).
        sops: Project SOPs.
        max_plan_revisions: Max plan revision attempts.

    Returns:
        ImplementationResult with files dict, research, and plan.
    """
    func_summary = "\n".join(
        f"  - {f.name}({', '.join(i.name + ': ' + i.type_ref for i in f.inputs)}) -> {f.output_type}"
        for f in contract.functions
    )
    type_summary = "\n".join(
        f"  - {t.name} ({t.kind})" for t in contract.types
    )

    failure_context = ""
    if prior_failures:
        failure_context = (
            "\n\nPrior attempts FAILED with these errors "
            "(do NOT repeat the same mistakes):\n"
            + "\n".join(f"  - {f}" for f in prior_failures)
        )

    task_desc = (
        f"Implement component '{contract.name}' (id: {contract.component_id}).\n"
        f"Functions:\n{func_summary}\n"
        f"Types:\n{type_summary}\n"
        f"Dependencies: {contract.dependencies}"
        f"{failure_context}"
    )

    # Phase 1: Research (or augment prior)
    if prior_research:
        from pact.agents.research import augment_research
        research = await augment_research(
            agent, prior_research,
            supplemental_focus=(
                "Focus on algorithmic approaches, existing libraries, "
                "performance considerations, security best practices for the domain."
            ),
            sops=sops,
        )
    else:
        research = await research_phase(
            agent, task_desc,
            role_context=(
                "Focus on algorithmic approaches, existing libraries, "
                "performance considerations, security best practices for the domain."
            ),
            sops=sops,
        )

    # Phase 2: Plan
    plan_desc = (
        f"Implementation plan for '{contract.name}':\n"
        f"- Approach: {research.recommended_approach}\n"
        f"- Implement all {len(contract.types)} types\n"
        f"- Implement all {len(contract.functions)} functions\n"
        f"- Handle all error cases\n"
        f"- Must pass {len(test_suite.test_cases)} contract tests"
    )
    plan = await plan_and_evaluate(
        agent, task_desc, research, plan_desc,
        sops=sops, max_revisions=max_plan_revisions,
    )

    # Phase 3: Generate code — using the handoff brief as the mental model
    from pact.interface_stub import render_handoff_brief, render_log_key_preamble, render_log_key_preamble_ts, render_log_key_preamble_rust, project_id_hash

    # Generate log key preamble for production traceability
    pid = project_id_hash(contract.component_id)  # Use component as project proxy
    if language == "typescript":
        key = f"PACT:{pid}:{contract.component_id}"
        log_preamble = render_log_key_preamble_ts(key)
    elif language == "rust":
        key = f"PACT:{pid}:{contract.component_id}"
        log_preamble = render_log_key_preamble_rust(key)
    else:
        log_preamble = render_log_key_preamble(pid, contract.component_id)

    all_contracts = dict(dependency_contracts or {})
    all_contracts[contract.component_id] = contract

    handoff = render_handoff_brief(
        component_id=contract.component_id,
        contract=contract,
        contracts=all_contracts,
        test_suite=test_suite,
        test_results=prior_test_results,
        prior_failures=prior_failures,
        attempt=attempt,
        sops=sops,
        external_context=external_context,
        learnings=learnings,
        log_key_preamble=log_preamble,
        strategic_context=strategic_context,
        processing_register=processing_register,
    )

    # The handoff brief is the largest cacheable block
    cache_prefix = handoff

    # Determine if we should use patch mode (high pass rate, targeted fixes)
    use_patch_mode = (
        prior_test_results is not None
        and prior_test_results.total > 0
        and prior_test_results.passed / prior_test_results.total >= 0.8
    )

    is_ts = language == "typescript"
    is_js = language == "javascript"
    is_rust = language == "rust"
    _ext_map = {"typescript": ".ts", "javascript": ".js", "rust": ".rs"}
    _label_map = {"typescript": "TypeScript", "javascript": "JavaScript", "rust": "Rust"}
    _fence_map = {"typescript": "typescript", "javascript": "javascript", "rust": "rust"}
    file_ext = _ext_map.get(language, ".py")
    code_fence = _fence_map.get(language, "python")
    example_file = f"module{file_ext}"

    if use_patch_mode:
        # Patch mode: preserve working code, only fix specific failures
        failing_tests = []
        if prior_test_results and prior_test_results.failure_details:
            for fd in prior_test_results.failure_details[:10]:
                failing_tests.append(f"  - {fd.test_id}: {fd.error_message}")
        failing_summary = "\n".join(failing_tests) if failing_tests else "  (see prior failures above)"

        # Include prior source in the cache prefix so the model can patch it
        if prior_source:
            prior_source_section = "\n\n## PRIOR IMPLEMENTATION (patch this, do NOT rewrite)\n"
            for fname, content in prior_source.items():
                prior_source_section += f"### {fname}\n```{code_fence}\n{content}\n```\n"
            cache_prefix += prior_source_section

        prompt = f"""The prior implementation passed {prior_test_results.passed}/{prior_test_results.total} tests.
It is MOSTLY CORRECT. Do NOT rewrite from scratch.

Your task: produce a PATCHED version that fixes ONLY the failing tests while
preserving all passing behavior. The failing tests are:
{failing_summary}

CRITICAL CONSTRAINTS:
- Keep the same overall structure and architecture
- Keep all type names, class names, and function names EXACTLY as they are
- Only modify the specific logic that causes the listed test failures
- Do NOT rename anything — the REQUIRED EXPORTS must remain unchanged
- If a test fails due to edge case handling, add the edge case handling
- If a test fails due to incorrect computation, fix the computation

Research approach: {research.recommended_approach}
Plan: {plan.plan_summary}

Respond with a JSON object containing a "files" dict where keys are filenames
and values are file contents. Include the COMPLETE file (not just the diff).

Example response format:
{{"files": {{"{example_file}": "// complete patched implementation..."}}}}"""

        logger.info(
            "Using PATCH mode for %s (%d/%d passed previously)",
            contract.component_id,
            prior_test_results.passed,
            prior_test_results.total,
        )
    else:
        # Full implementation mode
        if is_ts:
            prompt = f"""Implement the component described in the handoff brief above.

Research approach: {research.recommended_approach}
Plan: {plan.plan_summary}

Requirements:
- Produce a single TypeScript module implementing all types and functions
- CRITICAL: All type names, function names, and error class names must match
  the interface stub EXACTLY. See the REQUIRED EXPORTS list at the bottom of
  the stub — every name there MUST be a named export from your module
- Handle all error cases using typed Error subclasses with the EXACT class names from the stub
- Dependencies should be accepted as constructor/function parameters (dependency injection)
- Code must be clean, well-structured TypeScript with strict typing
- Use named exports only (no default exports)
- Use `unknown` instead of `any`; narrow with type guards
- Must pass ALL tests listed in the brief

Respond with a JSON object containing a "files" dict where keys are filenames
and values are file contents. At minimum include a main module file.

Example response format:
{{"files": {{"module.ts": "// implementation code..."}}}}"""
        elif is_js:
            prompt = f"""Implement the component described in the handoff brief above.

Research approach: {research.recommended_approach}
Plan: {plan.plan_summary}

Requirements:
- Produce a single JavaScript ES module implementing all functions
- CRITICAL: All function names and error class names must match
  the interface stub EXACTLY. See the REQUIRED EXPORTS list at the bottom of
  the stub — every name there MUST be a named export from your module
- Handle all error cases using Error subclasses with the EXACT class names from the stub
- Dependencies should be accepted as constructor/function parameters (dependency injection)
- Code must be clean, readable JavaScript (ES6+ modules)
- Use named exports only (no default exports)
- Use ESM imports with .js file extensions
- Use JSDoc comments for documentation
- Do NOT use TypeScript syntax
- Must pass ALL tests listed in the brief

Respond with a JSON object containing a "files" dict where keys are filenames
and values are file contents. At minimum include a main module file.

Example response format:
{{"files": {{"module.js": "// implementation code..."}}}}"""
        elif is_rust:
            prompt = f"""Implement the component described in the handoff brief above.

Research approach: {research.recommended_approach}
Plan: {plan.plan_summary}

Requirements:
- Produce Rust source files implementing all types and functions
- CRITICAL: All type names, function names, and error type names must match
  the interface stub EXACTLY. See the REQUIRED EXPORTS list at the bottom of
  the stub — every name there MUST be pub-exported from your module
- Use Result<T, E> for all error handling — never unwrap() or panic!() in library code
- Use thiserror::Error for custom error types with #[error("...")] messages
- Use serde (Serialize, Deserialize) for all data types that cross boundaries
- Prefer &str over String for function inputs; return String for owned values
- Include all necessary `use` statements
- Derive Debug, Clone on all public structs; Serialize, Deserialize on data structs
- Dependencies should be accepted as function parameters (dependency injection)
- Must pass ALL tests listed in the brief

Respond with a JSON object containing a "files" dict where keys are filenames
and values are file contents. At minimum include a main module file (lib.rs).

Example response format:
{{"files": {{"lib.rs": "// implementation code..."}}}}"""
        else:
            prompt = f"""Implement the component described in the handoff brief above.

Research approach: {research.recommended_approach}
Plan: {plan.plan_summary}

Requirements:
- Produce a single Python module implementing all types and functions
- CRITICAL: All type names, function names, and error class names must match
  the interface stub EXACTLY. See the REQUIRED EXPORTS list at the bottom of
  the stub — every name there MUST be importable from your module
- Handle all error cases using the EXACT exception class names from the stub
- Dependencies should be accepted as constructor/function parameters (dependency injection)
- Code must be clean, well-structured Python with type annotations
- Must pass ALL tests listed in the brief

Respond with a JSON object containing a "files" dict where keys are filenames
and values are file contents. At minimum include a main module file.

Example response format:
{{"files": {{"module.py": "# implementation code..."}}}}"""

    # Use a simple wrapper model for the response
    from pydantic import BaseModel

    class CodeResponse(BaseModel):
        """Generated implementation files."""
        files: dict[str, str]

    _system_prompts = {
        "typescript": CODE_SYSTEM_TS,
        "javascript": CODE_SYSTEM_JS,
        "rust": CODE_SYSTEM_RUST,
    }
    system_prompt = _system_prompts.get(language, CODE_SYSTEM)
    response, in_tok, out_tok = await agent.assess_cached(
        CodeResponse, prompt, system_prompt, cache_prefix=cache_prefix,
    )

    logger.info(
        "Code authored for %s: %d files (%d tokens)",
        contract.component_id, len(response.files), in_tok + out_tok,
    )

    return ImplementationResult(
        files=response.files,
        research=research,
        plan=plan,
    )
