"""All Pydantic models — rigid boundaries for agent outputs.

Every data structure in the pact architecture is a Pydantic model.
Schemas shape LLM output via tool_choice enforcement and serve as the
single source of truth for all inter-agent communication.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


# ── Contract Models ──────────────────────────────────────────────────


class ValidatorSpec(BaseModel):
    """A validation rule for a field."""
    kind: Literal["range", "regex", "length", "custom"]
    expression: str
    error_message: str = ""


class FieldSpec(BaseModel):
    """A typed field within a struct or function signature."""
    name: str
    type_ref: str
    required: bool = True
    default: str = ""
    description: str = ""
    validators: list[ValidatorSpec] = []


class TypeSpec(BaseModel):
    """A type definition within a contract."""
    name: str
    kind: Literal["primitive", "struct", "enum", "list", "optional", "union"]
    fields: list[FieldSpec] = []
    item_type: str = ""
    variants: list[str] = []
    inner_types: list[str] = []
    description: str = ""


class ErrorCase(BaseModel):
    """An error condition a function can produce."""
    name: str
    condition: str
    error_type: str
    error_data: dict[str, str] = {}


class SideEffectKind(StrEnum):
    """Categorized side effect types for contract functions."""
    NONE = "none"
    READS_FILE = "reads_file"
    WRITES_FILE = "writes_file"
    NETWORK_CALL = "network_call"
    MUTATES_STATE = "mutates_state"
    LOGGING = "logging"


class SideEffect(BaseModel):
    """Structured side effect declaration."""
    kind: SideEffectKind
    target: str = Field(default="", description="What is read/written/called")
    description: str = Field(default="", description="Additional context")



class PerformanceBudget(BaseModel):
    """Optional performance constraints on a function."""
    p95_latency_ms: int | None = Field(default=None, ge=1, description="95th percentile latency cap in ms")
    max_memory_mb: int | None = Field(default=None, ge=1, description="Peak memory cap in MB")
    complexity: str | None = Field(default=None, description="Big-O complexity, e.g. 'O(n log n)'")


class FunctionContract(BaseModel):
    """Contract for a single function — inputs, output, errors, invariants."""
    name: str
    description: str
    inputs: list[FieldSpec]
    output_type: str
    error_cases: list[ErrorCase] = []
    preconditions: list[str] = []
    postconditions: list[str] = []
    idempotent: bool = False
    side_effects: list[str] = []
    structured_side_effects: list[SideEffect] = []
    performance_budget: PerformanceBudget | None = None


class ComponentContract(BaseModel):
    """The interface contract for a single component — the artifact."""
    component_id: str
    name: str
    description: str
    version: int = 1
    types: list[TypeSpec] = []
    functions: list[FunctionContract] = []
    dependencies: list[str] = []
    dependency_contracts: dict[str, list[str]] = {}
    invariants: list[str] = []
    requires: list[str] = []


# ── Test Models ──────────────────────────────────────────────────────


class TestCase(BaseModel):
    """A single test case derived from a contract."""
    __test__ = False  # Prevent pytest collection
    id: str
    description: str
    function: str
    category: Literal["happy_path", "edge_case", "error_case", "invariant"]
    setup_description: str = ""
    mock_dependencies: dict[str, str] = {}
    input_values: dict[str, str] = {}
    input_description: str = ""
    expected_output_description: str = ""
    expected_error: str = ""
    assertions: list[str] = []


class ContractTestSuite(BaseModel):
    """Executable test suite generated from a contract."""
    component_id: str
    contract_version: int
    test_cases: list[TestCase] = []
    test_language: str = "python"  # Valid values: "python", "typescript"
    generated_code: str = ""


class TestFailure(BaseModel):
    """Details of a single test failure."""
    __test__ = False  # Prevent pytest collection
    test_id: str
    test_description: str = ""
    error_message: str = ""
    stdout: str = ""
    stderr: str = ""


class TestResults(BaseModel):
    """Aggregated test run results."""
    __test__ = False  # Prevent pytest collection
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    failure_details: list[TestFailure] = []
    timestamp: str = ""

    @property
    def all_passed(self) -> bool:
        return self.total > 0 and self.failed == 0 and self.errors == 0


# ── Interview Models ─────────────────────────────────────────────────


class InterviewResult(BaseModel):
    """Output of the interview phase — risks, ambiguities, questions."""
    risks: list[str] = []
    ambiguities: list[str] = []
    questions: list[str] = []
    assumptions: list[str] = []
    user_answers: dict[str, str] = {}
    approved: bool = False


# ── Interview V2 Models ─────────────────────────────────────────────


class QuestionType(StrEnum):
    """Types of interview questions with validation semantics."""
    FREETEXT = "freetext"
    BOOLEAN = "boolean"
    ENUM = "enum"
    NUMERIC = "numeric"


class InterviewQuestion(BaseModel):
    """A typed interview question with validation."""
    id: str = Field(description="Unique question identifier, e.g. q_001")
    text: str = Field(description="The question text")
    question_type: QuestionType = QuestionType.FREETEXT
    options: list[str] = Field(default_factory=list, description="Valid options for enum type")
    default: str = Field(default="", description="Default answer if auto-approved")
    range_min: float | None = Field(default=None, description="Min value for numeric type")
    range_max: float | None = Field(default=None, description="Max value for numeric type")
    depends_on: str | None = Field(default=None, description="Question ID this depends on")
    depends_value: str | None = Field(default=None, description="Required answer on depends_on to show this question")


def validate_answer(question: InterviewQuestion, answer: str) -> str | None:
    """Validate an answer against question type constraints.

    Returns None if valid, error message string if invalid.

    Rules:
      - BOOLEAN: answer in ("yes", "no", "true", "false")
      - ENUM: answer in question.options (case-insensitive)
      - NUMERIC: parseable as float, within range if specified
      - FREETEXT: non-empty string
    """
    if question.question_type == QuestionType.BOOLEAN:
        if answer.lower() not in ("yes", "no", "true", "false"):
            return f"Boolean question requires yes/no/true/false, got '{answer}'"
        return None

    if question.question_type == QuestionType.ENUM:
        lower_options = [o.lower() for o in question.options]
        if answer.lower() not in lower_options:
            return f"Answer '{answer}' not in valid options: {question.options}"
        return None

    if question.question_type == QuestionType.NUMERIC:
        try:
            val = float(answer)
        except ValueError:
            return f"Numeric question requires a number, got '{answer}'"
        if question.range_min is not None and val < question.range_min:
            return f"Value {val} below minimum {question.range_min}"
        if question.range_max is not None and val > question.range_max:
            return f"Value {val} above maximum {question.range_max}"
        return None

    # FREETEXT
    if not answer.strip():
        return "Freetext answer cannot be empty"
    return None


class AnswerSource(StrEnum):
    """Where an answer came from — provenance tracking."""
    USER_INTERACTIVE = "user_interactive"
    AUTO_ASSUMPTION = "auto_assumption"
    INTEGRATION_SLACK = "integration_slack"
    INTEGRATION_LINEAR = "integration_linear"
    CLI_APPROVE = "cli_approve"


class AuditedAnswer(BaseModel):
    """An answer with full provenance."""
    question_id: str
    answer: str
    source: AnswerSource
    confidence: float = Field(ge=0.0, le=1.0, description="Match confidence for auto-filled")
    timestamp: str = Field(default="", description="ISO 8601 timestamp")
    matched_assumption: str | None = Field(default=None, description="Which assumption was matched, if any")


# ── Research & Planning Models ───────────────────────────────────────


class ResearchFinding(BaseModel):
    """A single finding from best-practices research."""
    topic: str
    finding: str
    source: str
    relevance: str
    confidence: float = Field(ge=0.0, le=1.0)


class ResearchReport(BaseModel):
    """Output of the research phase — before any work begins."""
    task_summary: str
    findings: list[ResearchFinding] = []
    recommended_approach: str = ""
    alternatives_considered: list[str] = []
    risks: list[str] = []
    compliance_notes: list[str] = []


class PlanEvaluation(BaseModel):
    """Self-evaluation of a plan before execution."""
    plan_summary: str
    efficiency_assessment: str = ""
    compliance_assessment: str = ""
    risk_assessment: str = ""
    decision: Literal["proceed", "revise", "escalate"] = "proceed"
    revision_notes: str = ""


# ── Decomposition Models ─────────────────────────────────────────────


class EngineeringDecision(BaseModel):
    """A decision made during decomposition."""
    ambiguity: str
    decision: str
    rationale: str


class DecompositionNode(BaseModel):
    """A node in the decomposition tree — recursive structure."""
    component_id: str
    name: str
    description: str
    depth: int = 0
    parent_id: str = ""
    children: list[str] = []
    contract: ComponentContract | None = None
    implementation_status: Literal[
        "pending", "contracted", "implemented", "tested", "failed"
    ] = "pending"
    test_results: TestResults | None = None


class DecompositionTree(BaseModel):
    """Full decomposition tree — all nodes indexed by component_id."""
    root_id: str
    nodes: dict[str, DecompositionNode] = {}

    def leaves(self) -> list[DecompositionNode]:
        """Return leaf nodes (no children)."""
        return [n for n in self.nodes.values() if not n.children]

    def children_of(self, node_id: str) -> list[DecompositionNode]:
        """Return child nodes of a given node."""
        node = self.nodes.get(node_id)
        if not node:
            return []
        return [self.nodes[c] for c in node.children if c in self.nodes]

    def parent_of(self, node_id: str) -> DecompositionNode | None:
        """Return parent node."""
        node = self.nodes.get(node_id)
        if not node or not node.parent_id:
            return None
        return self.nodes.get(node.parent_id)

    def topological_order(self) -> list[str]:
        """Return component IDs in dependency order (leaves first).

        Visits ALL nodes, not just those reachable from root,
        to handle trees with orphaned subtrees.
        """
        visited: set[str] = set()
        order: list[str] = []

        def visit(node_id: str) -> None:
            if node_id in visited:
                return
            visited.add(node_id)
            node = self.nodes.get(node_id)
            if node:
                for child_id in node.children:
                    visit(child_id)
                order.append(node_id)

        # Start from root, then sweep any orphaned nodes
        visit(self.root_id)
        for node_id in self.nodes:
            if node_id not in visited:
                visit(node_id)
        return order

    def leaf_parallel_groups(self) -> list[list[str]]:
        """All leaves can run simultaneously (they're independent in a tree).

        Returns a single group containing all leaf component IDs.
        """
        leaf_ids = [n.component_id for n in self.leaves()]
        return [leaf_ids] if leaf_ids else []

    def non_leaf_parallel_groups(self) -> list[list[str]]:
        """Non-leaves at same depth can integrate in parallel. Deepest first.

        Returns groups ordered deepest-first so children finish before parents.
        """
        depth_map: dict[int, list[str]] = {}
        for node in self.nodes.values():
            if node.children:  # non-leaf only
                depth_map.setdefault(node.depth, []).append(node.component_id)

        # Deepest first
        return [depth_map[d] for d in sorted(depth_map, reverse=True)]

    def subtree(self, node_id: str) -> list[str]:
        """Return all node IDs in the subtree rooted at node_id (inclusive)."""
        result: list[str] = []

        def collect(nid: str) -> None:
            result.append(nid)
            node = self.nodes.get(nid)
            if node:
                for child_id in node.children:
                    collect(child_id)

        collect(node_id)
        return result


# ── I/O Tracing Models ───────────────────────────────────────────────


class IOTrace(BaseModel):
    """Captured I/O at a component boundary."""
    component_id: str
    function: str
    inputs: dict[str, str] = {}
    output: str = ""
    error: str = ""
    duration_ms: float = 0.0
    sub_traces: list[IOTrace] = []


class TraceDiagnosis(BaseModel):
    """Diagnosis from I/O trace analysis."""
    failing_test: str
    root_cause: Literal[
        "implementation_bug", "contract_bug", "glue_bug", "design_bug"
    ]
    component_id: str
    explanation: str
    suggested_fix: str = ""


# ── Failure Tracking ─────────────────────────────────────────────────


class FailureRecord(BaseModel):
    """Record of a failure for the design document."""
    component_id: str
    failure_type: str
    description: str
    resolution: str = ""
    timestamp: str = ""


# ── Design Document ──────────────────────────────────────────────────


class DesignDocument(BaseModel):
    """Living design document — auto-maintained throughout the run."""
    project_id: str
    title: str
    summary: str = ""
    decomposition_tree: DecompositionTree | None = None
    engineering_decisions: list[EngineeringDecision] = []
    failure_history: list[FailureRecord] = []
    lessons_learned: list[str] = []
    version: int = 1


# ── Run State ────────────────────────────────────────────────────────


class ComponentTask(BaseModel):
    """Tracks a single component's progress through the pipeline."""
    component_id: str
    status: Literal[
        "pending", "researching", "contracting", "testing",
        "implementing", "integrating", "completed", "failed"
    ] = "pending"
    attempts: int = 0
    last_error: str = ""


class RunState(BaseModel):
    """Mutable lifecycle state for a pact run."""
    id: str
    project_dir: str
    status: Literal["active", "paused", "completed", "failed", "budget_exceeded"] = "active"
    phase: Literal[
        "interview", "shape", "decompose", "contract", "implement",
        "integrate", "diagnose", "complete"
    ] = "interview"
    component_tasks: list[ComponentTask] = []
    interview_result: InterviewResult | None = None
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    last_check_in: str = ""
    created_at: str = ""
    completed_at: str = ""
    pause_reason: str = ""
    phase_cycles: int = 0

    def record_tokens(self, input_tokens: int, output_tokens: int, cost: float) -> None:
        self.total_tokens += input_tokens + output_tokens
        self.total_cost_usd += cost

    def pause(self, reason: str) -> None:
        self.status = "paused"
        self.pause_reason = reason

    def fail(self, reason: str) -> None:
        self.status = "failed"
        self.pause_reason = reason
        self.completed_at = datetime.now().isoformat()

    def complete(self) -> None:
        self.status = "completed"
        self.completed_at = datetime.now().isoformat()


# ── Gate Result ──────────────────────────────────────────────────────


class GateResult(BaseModel):
    """Whether a validation gate passed or failed."""
    passed: bool
    reason: str
    details: list[str] = []


# ── Learning ─────────────────────────────────────────────────────────


class LearningEntry(BaseModel):
    """A single learning from a project run."""
    id: str
    lesson: str
    category: Literal[
        "contract_pattern", "test_pattern", "implementation_pattern",
        "integration_pattern", "failure_mode", "domain_convention",
    ]
    component_id: str = ""
    source_project_id: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    created_at: str = ""


# ── Artifact Metadata (PBOM) ─────────────────────────────────────


class ArtifactMetadata(BaseModel):
    """Provenance metadata for a generated artifact."""
    pact_version: str = "0.1.0"
    model: str = Field(default="", description="Model ID that generated this artifact")
    component_id: str = ""
    artifact_type: Literal["contract", "test_suite", "implementation", "composition"] = "contract"
    contract_version: int = 1
    cost_input_tokens: int = 0
    cost_output_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: str = Field(default="", description="ISO 8601 generation timestamp")
    run_id: str = Field(default="", description="Unique run identifier")


# ── Spec-Compliance Audit ─────────────────────────────────────────


class RequirementCoverage(BaseModel):
    """Coverage assessment for a single spec requirement."""
    requirement: str = Field(description="The requirement extracted from the spec")
    status: Literal["covered", "partial", "gap"] = Field(description="Coverage status")
    evidence: str = Field(default="", description="Which component/code covers this requirement")
    notes: str = Field(default="", description="Explanation of partial coverage or gap")


class SpecAuditResult(BaseModel):
    """Result of comparing spec requirements against implementations."""
    requirements: list[RequirementCoverage] = Field(default_factory=list)
    covered_count: int = 0
    partial_count: int = 0
    gap_count: int = 0
    total_count: int = 0
    summary: str = Field(default="", description="Human-readable summary")
