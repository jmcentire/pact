"""Interactive project setup wizard for Pact.

Walks users through project setup with clear guidance, producing
annotated sample files with inline instructions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import BaseModel, Field

from pact.schemas import InterviewQuestion, QuestionType, validate_answer


class WizardConfig(BaseModel):
    """Collected wizard answers -- input to file generation."""

    project_name: str = ""
    description: str = ""
    language: str = "python"
    test_framework: str = ""
    build_mode: str = "auto"
    shaping: bool = False
    budget: float = 10.0
    parallel_components: bool = False
    max_file_lines: int = 300
    prefer_stdlib: bool = True
    run_interview: bool = False


def build_wizard_questions() -> list[InterviewQuestion]:
    """Return the ordered list of wizard questions."""
    return [
        InterviewQuestion(
            id="project_name",
            text="Project name",
            question_type=QuestionType.FREETEXT,
        ),
        InterviewQuestion(
            id="description",
            text="Describe your project in 1-3 sentences",
            question_type=QuestionType.FREETEXT,
        ),
        InterviewQuestion(
            id="language",
            text="Primary language",
            question_type=QuestionType.ENUM,
            options=["python", "typescript", "javascript"],
            default="python",
        ),
        InterviewQuestion(
            id="test_framework",
            text="Test framework (leave blank for auto-detect)",
            question_type=QuestionType.FREETEXT,
            default="auto",
        ),
        InterviewQuestion(
            id="build_mode",
            text=(
                "Build mode\n"
                "  unary     - Single agent, no decomposition (simple tasks)\n"
                "  auto      - LLM decides whether to decompose (recommended)\n"
                "  hierarchy - Always decompose into components (complex systems)"
            ),
            question_type=QuestionType.ENUM,
            options=["unary", "auto", "hierarchy"],
            default="auto",
        ),
        InterviewQuestion(
            id="shaping",
            text="Enable shaping phase? (appetite, breadboard, rabbit holes -- helps with ambiguous tasks)",
            question_type=QuestionType.BOOLEAN,
            default="no",
        ),
        InterviewQuestion(
            id="budget",
            text="Budget cap in dollars",
            question_type=QuestionType.NUMERIC,
            default="10",
            range_min=0.5,
            range_max=1000,
        ),
        InterviewQuestion(
            id="parallel_components",
            text="Enable parallel component builds? (faster, uses more concurrent API calls)",
            question_type=QuestionType.BOOLEAN,
            default="no",
        ),
        InterviewQuestion(
            id="max_file_lines",
            text="Maximum lines per generated file",
            question_type=QuestionType.NUMERIC,
            default="300",
            range_min=50,
            range_max=2000,
        ),
        InterviewQuestion(
            id="prefer_stdlib",
            text="Prefer standard library over third-party packages?",
            question_type=QuestionType.BOOLEAN,
            default="yes",
        ),
        InterviewQuestion(
            id="run_interview",
            text="Run interview phase immediately after setup?",
            question_type=QuestionType.BOOLEAN,
            default="no",
        ),
    ]


def run_wizard_interactive(
    questions: list[InterviewQuestion],
    input_fn: Callable[[str], str] | None = None,
    print_fn: Callable[..., None] | None = None,
) -> WizardConfig:
    """Run wizard questions interactively. Returns collected config."""
    if input_fn is None:
        input_fn = input
    if print_fn is None:
        print_fn = print
    answers: dict[str, str] = {}

    for q in questions:
        # Check conditional dependency
        if q.depends_on and q.depends_on in answers:
            if answers[q.depends_on].lower() != (q.depends_value or "").lower():
                answers[q.id] = q.default
                continue

        # Build prompt
        prompt = f"\n{q.text}"
        if q.question_type == QuestionType.ENUM:
            prompt += f"  ({', '.join(q.options)})"
        if q.default:
            prompt += f"  [default: {q.default}]"
        prompt += "\n> "

        while True:
            raw = input_fn(prompt).strip()
            value = raw if raw else q.default

            if not value:
                if q.question_type == QuestionType.FREETEXT:
                    print_fn("  Please provide an answer.")
                    continue
                # For other types without defaults, re-prompt
                print_fn("  Please provide an answer.")
                continue

            error = validate_answer(q, value)
            if error:
                print_fn(f"  {error}")
                continue

            answers[q.id] = value
            break

    return answers_to_config(answers)


def answers_to_config(answers: dict[str, str]) -> WizardConfig:
    """Convert raw answer strings to typed WizardConfig."""

    def to_bool(s: str) -> bool:
        return s.lower() in ("yes", "true")

    test_fw = answers.get("test_framework", "auto")
    if test_fw == "auto":
        test_fw = ""

    return WizardConfig(
        project_name=answers.get("project_name", ""),
        description=answers.get("description", ""),
        language=answers.get("language", "python"),
        test_framework=test_fw,
        build_mode=answers.get("build_mode", "auto"),
        shaping=to_bool(answers.get("shaping", "no")),
        budget=float(answers.get("budget", "10")),
        parallel_components=to_bool(answers.get("parallel_components", "no")),
        max_file_lines=int(float(answers.get("max_file_lines", "300"))),
        prefer_stdlib=to_bool(answers.get("prefer_stdlib", "yes")),
        run_interview=to_bool(answers.get("run_interview", "no")),
    )


def resolve_test_framework(config: WizardConfig) -> str:
    """Auto-detect test framework from language if not explicitly set."""
    if config.test_framework:
        return config.test_framework
    defaults = {"python": "pytest", "typescript": "vitest", "javascript": "jest"}
    return defaults.get(config.language, "pytest")


# ── File generators ──────────────────────────────────────────────────


def _task_example(language: str) -> str:
    """Return a language-appropriate task example."""
    if language == "python":
        return """\
# Example: DAG Task Scheduler
#
# Build a task scheduler that executes dependent tasks in topological order.
# Each task has a name, a callable, and a list of dependency task names.
#
# ## Context
# This replaces a manual script that runs data pipeline steps sequentially.
# The new scheduler should parallelize independent steps.
#
# ## Constraints
# - Must detect circular dependencies and raise CyclicDependencyError
# - Must propagate task failures to all downstream dependents (mark as SKIPPED)
# - Must validate that all dependency names reference existing tasks
# - No external dependencies beyond the standard library
#
# ## Requirements
# - register_task(name, callable, dependencies) -> None
# - run_all() -> dict[str, TaskResult]
# - TaskResult has status (SUCCESS/FAILED/SKIPPED), duration, and error fields
# - Duplicate task names raise DuplicateTaskError"""

    elif language == "typescript":
        return """\
# Example: Rate Limiter
#
# Build a token-bucket rate limiter for API endpoints.
# Each endpoint has its own bucket with configurable rate and burst.
#
# ## Context
# Replacing a simple per-second counter with proper token bucket semantics.
#
# ## Constraints
# - Must support multiple named endpoints with independent limits
# - Must be thread-safe (no race conditions on bucket state)
# - Time source must be injectable for testing
#
# ## Requirements
# - createLimiter(config: RateLimitConfig) -> RateLimiter
# - limiter.tryAcquire(endpoint: string) -> { allowed: boolean, retryAfterMs?: number }
# - limiter.reset(endpoint: string) -> void"""

    else:
        return """\
# Example: Event Emitter
#
# Build a typed event emitter with wildcard support.
#
# ## Context
# Lightweight pub/sub for a browser application, replacing a heavier library.
#
# ## Constraints
# - Must support wildcard listeners (e.g., "user.*" matches "user.login")
# - Must support once() listeners that auto-remove after first call
# - No external dependencies
#
# ## Requirements
# - on(event, handler) -> unsubscribe function
# - once(event, handler) -> unsubscribe function
# - emit(event, ...args) -> void
# - off(event, handler) -> void"""


def generate_task_md(config: WizardConfig) -> str:
    """Generate task.md with inline guidance and a language-appropriate example."""
    name = config.project_name or "Untitled Project"
    desc = config.description or ""
    example = _task_example(config.language)

    task_body = desc if desc else (
        "Write your task description here. Be specific about what the system "
        "should do, not how it should be built. Pact's interview phase will "
        "ask clarifying questions about ambiguities."
    )

    return f"""\
# {name}

{task_body}

## Context

<!-- Write 2-3 sentences of background. What exists today? What problem
     does this solve? What triggered this work?

     Example: "This replaces a manual CSV import script that breaks on
     malformed rows. The new version needs to handle partial failures
     and report which rows were skipped." -->

## Constraints

<!-- List hard requirements that constrain the solution. These are
     non-negotiable rules the implementation must follow.

     Good constraints are specific and testable:
       - "Must handle 10,000 records in under 5 seconds"
       - "Must not use any network calls"
       - "Must raise ValidationError (not ValueError) for bad input"

     Bad constraints are vague:
       - "Must be fast"
       - "Must be well-tested" -->

## Requirements

<!-- List what the deliverable must do. Each requirement should describe
     observable behavior, not internal structure.

     Good requirements:
       - "parse(csv_text) returns list of Record objects"
       - "Malformed rows are collected in result.errors, not raised"
       - "Empty input returns empty list, not error"

     Bad requirements:
       - "Use a class-based architecture"
       - "Follow SOLID principles" -->

{example}
"""


def generate_sops_md(config: WizardConfig) -> str:
    """Generate language-aware sops.md with inline guidance."""
    framework = resolve_test_framework(config)
    lang = config.language
    max_lines = config.max_file_lines
    stdlib_pref = (
        "Prefer standard library over third-party packages"
        if config.prefer_stdlib
        else "Third-party packages are acceptable when they simplify the solution"
    )

    # Language-specific sections
    if lang == "python":
        tech_stack = f"""\
## Tech Stack
- Language: Python 3.12+
- Testing: {framework}
- Linting: ruff
- Type checking: mypy (strict mode recommended)"""
        standards = """\
## Standards
- Type annotations on all public functions
- Prefer composition over inheritance
- Use dataclasses or Pydantic for structured data
- Follow PEP 8 naming conventions"""
    elif lang == "typescript":
        tech_stack = f"""\
## Tech Stack
- Language: TypeScript (strict mode)
- Testing: {framework}
- Linting: eslint
- Build: tsc"""
        standards = """\
## Standards
- Explicit types on all public function signatures
- Prefer interfaces over type aliases for object shapes
- Use readonly where possible
- No any -- use unknown + type guards instead"""
    else:  # javascript
        tech_stack = f"""\
## Tech Stack
- Language: JavaScript (ES2022+)
- Testing: {framework}
- Linting: eslint"""
        standards = """\
## Standards
- JSDoc type annotations on all public functions
- Prefer const over let
- Use destructuring for function parameters
- No var declarations"""

    return f"""\
# Operating Procedures

<!-- SOPs tell Pact's agents HOW to write code. Think of these as the
     coding standards you'd give a new team member on day one.

     Keep it short. Pact's research shows that ~150 tokens of domain
     context captures 98.8% of the benefit. A 500-line SOP is past
     the saturation point -- it actively degrades agent performance. -->

{tech_stack}

{standards}

## Verification
- All functions must have at least one test
- Tests must be runnable without external services
- No task is done until its contract tests pass

## Preferences
- {stdlib_pref}
- Keep files under {max_lines} lines

<!-- Add project-specific conventions below. Examples:
     - "Use UTC for all timestamps"
     - "Error messages must include the invalid value"
     - "All public functions must be async"
     - "Use snake_case for file names, PascalCase for classes"

     Don't add rules that duplicate the language defaults above.
     The agents already know standard {lang} conventions. Only add
     rules where YOUR project deviates from the norm. -->
"""


def generate_pact_yaml(config: WizardConfig) -> dict[str, Any]:
    """Generate pact.yaml config dict from wizard config."""
    cfg: dict[str, Any] = {"budget": config.budget}

    if config.language != "python":
        cfg["language"] = config.language

    framework = resolve_test_framework(config)
    if framework != "pytest":
        cfg["test_framework"] = framework

    if config.build_mode != "auto":
        cfg["build_mode"] = config.build_mode

    if config.shaping:
        cfg["shaping"] = True

    if config.parallel_components:
        cfg["parallel_components"] = True

    return cfg


def load_wizard_config_from_file(path: Path) -> WizardConfig:
    """Load wizard config from JSON or YAML file for non-interactive use."""
    text = path.read_text()
    if path.suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    return WizardConfig(**data)
