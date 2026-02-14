"""All Pydantic models — rigid boundaries for agent outputs.

Every data structure in the pact architecture is a Pydantic model.
Schemas shape LLM output via tool_choice enforcement and serve as the
single source of truth for all inter-agent communication.
"""

from __future__ import annotations

from datetime import datetime
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
    test_language: str = "python"
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
        """Return component IDs in dependency order (leaves first)."""
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

        visit(self.root_id)
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
        "interview", "decompose", "contract", "implement",
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
