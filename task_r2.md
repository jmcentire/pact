# Task: Pact R2 — Operational Reliability and Contract Quality

## Overview

24 improvements observed across two concurrent Pact runs (stigmergy: 27 components, $50; pact-shape: 8 components, $50) on 2026-02-14. Both runs completed contracts and tests successfully but hit systemic issues during validation and implementation. Root causes fall into six component areas: resilience, validation, interview, scheduling, contracts, and provenance.

## What This Is

A reliability and quality overhaul of the Pact pipeline:

1. **Resilience** — Recovery from failures without manual state.json edits. Classify errors as transient vs permanent. Detect systemic patterns (all components failing identically).
2. **Validation V2** — Distinguish internal vs external dependencies. Validate incrementally (per-contract, not batch). Normalize dependency names. Enforce hierarchy alignment.
3. **Interview V2** — Structured question types. Answer audit trail with source attribution. Fix fuzzy-match answer assignment.
4. **Scheduling V2** — Wavefront execution (dependency-driven fan-out). Variable timeouts per phase/role. Per-phase budget tracking. Environment specification.
5. **Contract Quality** — Anti-cliche enforcement. Side-effect declarations. Performance budgets.
6. **Provenance** — Pipeline Bill of Materials. Drift detection. Staleness tracking. Retrospective learning. MCP server.

## Requirements

### 1. Resilience (in `src/pact/`)

#### 1.1 Resume Command (`cli.py`, `lifecycle.py`)

```python
class ResumeStrategy(BaseModel):
    """Computed strategy for resuming a failed/paused run."""
    last_checkpoint: str = Field(description="Component ID of last successful checkpoint")
    completed_components: list[str] = Field(default_factory=list)
    resume_phase: Literal["interview", "decompose", "contract", "implement", "integrate"]
    cleared_fields: list[str] = Field(description="State fields that will be reset")

def compute_resume_strategy(state: RunState, project: ProjectDir) -> ResumeStrategy:
    """Analyze failed state and determine safe resume point.

    Preconditions:
      - state.status in ("failed", "paused")
    Postconditions:
      - result.resume_phase <= state.phase (never advances past failure point)
      - result.completed_components all have contract + tests on disk
    Error cases:
      - state.status == "active" -> ValueError("Run is already active")
      - no checkpoint found -> ResumeStrategy with resume_phase="interview"
    """

def execute_resume(state: RunState, strategy: ResumeStrategy) -> RunState:
    """Apply resume strategy: reset status, clear pause_reason, log audit entry.

    Postconditions:
      - result.status == "active"
      - result.pause_reason is None
      - result.phase == strategy.resume_phase
      - audit log contains daemon_resume entry with original failure reason
    Side effects:
      - Writes state.json
      - Appends to audit.jsonl
    """
```

CLI: `pact resume <project-dir> [--from-phase PHASE]`

Invariants:
- Resume never discards completed work (contracts, tests, implementations with passing tests)
- Resume logs the original failure reason before clearing it
- Resume validates disk state matches state.json before proceeding

Test criteria:
- `test_resume_from_failed_implement` — state failed at implement, 3/7 components done. Resume sets phase=implement, completed_components=[3 IDs]
- `test_resume_from_paused` — state paused for human input. Resume sets phase=interview, status=active
- `test_resume_active_raises` — ValueError when state is already active
- `test_resume_preserves_completed_work` — no files deleted during resume
- `test_resume_audit_entry` — audit.jsonl gains a daemon_resume entry with timestamp, original error

#### 1.2 Error Classification (`lifecycle.py`)

```python
class ErrorClassification(StrEnum):
    TRANSIENT = "transient"   # API timeout, rate limit, network error -> retry
    PERMANENT = "permanent"   # Budget exceeded, invalid config, missing files -> stop
    SYSTEMIC = "systemic"     # Same error across all components -> escalate

def classify_error(error: Exception, context: dict) -> ErrorClassification:
    """Classify an error for retry/stop/escalate decision.

    Postconditions:
      - TimeoutError, ConnectionError, httpx.* -> TRANSIENT
      - BudgetExceededError, ValueError, FileNotFoundError -> PERMANENT
      - Same error type on 3+ components in same phase -> SYSTEMIC
    """
```

Invariants:
- Transient errors retry up to `max_retries` (default 3) with exponential backoff
- Permanent errors set status="failed" immediately
- Systemic errors set status="paused" with pause_reason describing the pattern
- Only PERMANENT errors mark a run as "failed"; TRANSIENT errors never do

Test criteria:
- `test_classify_timeout_as_transient` — asyncio.TimeoutError -> TRANSIENT
- `test_classify_budget_as_permanent` — BudgetExceededError -> PERMANENT
- `test_classify_repeated_same_error_as_systemic` — 3x same error type on different components -> SYSTEMIC
- `test_transient_retries_three_times` — transient error retried 3x before escalating
- `test_systemic_pauses_not_fails` — systemic detection pauses run, doesn't fail it

#### 1.3 Idle Timer Reset (`daemon.py`)

```python
# Current: idle timer counts wall-clock since last FIFO signal
# Fixed: idle timer resets on any meaningful activity

class ActivityTracker:
    """Tracks daemon activity to prevent false idle timeouts."""

    def record_activity(self, activity_type: str) -> None:
        """Reset idle timer. Called on API calls, state transitions, audit entries.

        activity_type: "api_call" | "state_transition" | "audit_entry" | "fifo_signal"
        """

    def idle_seconds(self) -> float:
        """Seconds since last recorded activity."""

    def is_idle(self, max_idle: int) -> bool:
        """True only when no activity for max_idle seconds."""
```

Invariants:
- `is_idle()` returns False while API calls are in progress
- `is_idle()` returns False within 60s of any state transition
- Timer only counts genuine idle time (blocked on FIFO, no work in progress)

Test criteria:
- `test_api_call_resets_idle` — idle_seconds resets to 0 after record_activity("api_call")
- `test_state_transition_resets_idle` — same for state transitions
- `test_genuinely_idle_triggers` — no activity for max_idle seconds -> is_idle() == True
- `test_active_work_prevents_timeout` — continuous API calls for 3 hours -> never idle

#### 1.4 Systemic Failure Detection (`scheduler.py`)

```python
class SystemicPattern:
    """Detected pattern of identical failures across components."""
    pattern_type: str          # "zero_tests", "import_error", "timeout"
    affected_components: list[str]
    sample_error: str
    recommendation: str

def detect_systemic_failure(
    results: dict[str, TestResults],
    threshold: int = 3,
) -> SystemicPattern | None:
    """Detect when multiple components fail with the same root cause.

    Preconditions:
      - len(results) >= threshold
    Postconditions:
      - Returns None if failures are heterogeneous
      - Returns SystemicPattern if threshold+ components share identical failure signature
    Patterns detected:
      - All 0/0 (total=0, passed=0) -> environment/PATH issue
      - All same ImportError -> missing dependency
      - All same TimeoutError -> API/network issue
    """
```

Invariants:
- Detection runs after every implementation batch, not just at end
- Systemic detection triggers pause, not fail (human should diagnose)
- Pattern includes actionable recommendation, not just description

Test criteria:
- `test_detect_all_zero_zero` — 5 components with total=0, passed=0 -> SystemicPattern("zero_tests")
- `test_detect_same_import_error` — 3 components with "No module named X" -> SystemicPattern("import_error")
- `test_heterogeneous_failures_no_pattern` — 3 components with different errors -> None
- `test_below_threshold_no_pattern` — 2 components with same error, threshold=3 -> None
- `test_recommendation_is_actionable` — pattern.recommendation contains specific fix, not vague advice

#### 1.5 Event Sourcing (`project.py`)

```python
def rebuild_state(project_dir: Path) -> RunState:
    """Reconstruct RunState from audit.jsonl by replaying events.

    Preconditions:
      - project_dir / ".pact" / "audit.jsonl" exists
    Postconditions:
      - Returned state matches what state.json SHOULD contain
      - All component statuses derived from audit events, not state.json
      - Cost totals derived from logged token counts
    Side effects:
      - None (read-only reconstruction)
    Error cases:
      - Corrupt audit log -> partial reconstruction with warnings
      - Missing audit log -> ValueError
    """

def validate_state_consistency(
    state: RunState,
    project_dir: Path,
) -> list[str]:
    """Compare state.json against disk reality and audit log.

    Returns list of inconsistencies (empty = consistent).
    Checks:
      - Components marked "implemented" have code on disk
      - Components marked "tested" have test files
      - Cost totals match audit log token sums
      - Phase is consistent with component statuses
    """
```

CLI: `pact rebuild <project-dir> [--dry-run]`

Test criteria:
- `test_rebuild_from_clean_audit` — replay 20 events -> correct state
- `test_rebuild_matches_state_json` — rebuilt state == actual state.json for healthy project
- `test_rebuild_detects_drift` — manually corrupt state.json, rebuild catches discrepancy
- `test_validate_missing_implementation` — state says "implemented" but no code on disk -> inconsistency reported

---

### 2. Validation V2 (in `src/pact/contracts.py`)

#### 2.1 External vs Internal Dependencies

```python
class DependencyKind(StrEnum):
    INTERNAL = "internal"    # Must have contract in this decomposition
    EXTERNAL = "external"    # Existing codebase module, validated by file existence

class ResolvedDependency(BaseModel):
    component_id: str
    kind: DependencyKind
    resolved_path: Path | None = None  # For external: actual file path
    contract_exists: bool = False       # For internal: contract found
```

Update `ComponentContract.dependencies` from `list[str]` to support classification:

```python
# In interface.json:
{
  "dependencies": ["shaping_schemas"],           # internal (has contract)
  "external_dependencies": ["agents.base", "schemas"]  # existing modules
}
```

Validation rules:
- `internal` dependencies: must have a contract in this decomposition (existing behavior)
- `external` dependencies: must resolve to an existing file in the source tree
- Unknown dependencies (neither internal nor external match): warning, not error

Invariants:
- Validation never rejects a contract for depending on existing codebase modules
- External dependency validation checks file existence, not contract existence
- All dependency names are normalized before matching (see 2.3)

Test criteria:
- `test_external_dep_on_existing_module_passes` — depends on "agents.base", file exists -> pass
- `test_external_dep_on_missing_module_fails` — depends on "nonexistent_module", no file -> fail
- `test_internal_dep_without_contract_fails` — depends on sibling, no contract -> fail (existing behavior preserved)
- `test_mixed_internal_external_deps` — both types in one contract -> both validated independently

#### 2.2 Incremental Validation

```python
async def validate_contract_incremental(
    contract: ComponentContract,
    existing_contracts: dict[str, ComponentContract],
    source_tree: Path,
) -> list[ValidationError]:
    """Validate a single contract as soon as it's authored.

    Preconditions:
      - contract is freshly authored
      - existing_contracts contains all previously validated contracts
    Postconditions:
      - Type references within this contract are valid
      - Internal dependencies reference existing_contracts keys
      - External dependencies resolve in source_tree
      - Cycle detection runs against existing_contracts + this contract
    """
```

Invariants:
- Validation runs after each contract is authored, not in batch at end
- Early validation failure stops contract authoring for remaining components (fail fast)
- Incremental validation results are cached; batch validation at end is a no-op verification

Test criteria:
- `test_incremental_catches_bad_type_ref_immediately` — contract references undefined type -> error before next contract starts
- `test_incremental_catches_cycle_with_existing` — new contract creates cycle with prior contract -> error
- `test_batch_validation_matches_incremental` — batch results == union of incremental results

#### 2.3 Dependency Name Normalization

```python
def normalize_dependency_name(raw: str, known_ids: list[str]) -> str | None:
    """Normalize a dependency name to match a known component ID.

    Rules (applied in order):
      1. Exact match -> return as-is
      2. Case-insensitive match -> return known_id
      3. Underscore transposition (schemas_shaping -> shaping_schemas) -> return known_id
      4. Common prefix/suffix stripping (my_schemas -> schemas) -> return known_id if unambiguous
      5. No match -> return None

    Postconditions:
      - Result is always a member of known_ids, or None
      - Transposition detected by sorted word equality
    """
```

Invariants:
- Normalization is deterministic (same input always same output)
- Normalization never creates false matches (ambiguous matches return None)
- Normalization logs a warning when it corrects a name (visibility into LLM naming errors)

Test criteria:
- `test_exact_match` — "shaping_schemas" in known_ids -> "shaping_schemas"
- `test_transposition` — "schemas_shaping" with known "shaping_schemas" -> "shaping_schemas"
- `test_no_match_returns_none` — "totally_unknown" -> None
- `test_ambiguous_returns_none` — "schemas" matches both "shaping_schemas" and "config_schemas" -> None
- `test_case_insensitive` — "Shaping_Schemas" -> "shaping_schemas"

#### 2.4 Hierarchy Alignment

```python
def validate_hierarchy_locality(
    tree: DecompositionTree,
    contracts: dict[str, ComponentContract],
) -> list[str]:
    """Validate that dependencies follow decomposition tree locality.

    Rules:
      - A component may depend on its siblings (same parent)
      - A component may depend on its parent's siblings (uncle)
      - A component should NOT depend on distant cousins (warning)
      - A component must NOT create cross-subtree cycles

    Returns:
      List of warning strings for distant dependencies.
    """
```

Invariants:
- Sibling dependencies are always allowed
- Parent-child dependencies are always allowed
- Cross-subtree dependencies produce warnings, not errors (they may be intentional)

Test criteria:
- `test_sibling_dep_no_warning` — A depends on B, both children of C -> no warning
- `test_distant_cousin_warns` — A (child of B) depends on D (child of E, E sibling of B) -> warning
- `test_cross_subtree_warns` — deep cross-tree dependency -> warning with explanation

---

### 3. Interview V2 (in `src/pact/`)

#### 3.1 Structured Question Types (`schemas.py`)

```python
class QuestionType(StrEnum):
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

    Returns None if valid, error message if invalid.

    Rules:
      - BOOLEAN: answer in ("yes", "no", "true", "false")
      - ENUM: answer in question.options (case-insensitive)
      - NUMERIC: parseable as float, range_min <= value <= range_max
      - FREETEXT: non-empty string
    """
```

Invariants:
- Every question has a type; default is FREETEXT (backward compatible)
- ENUM questions must have >= 2 options
- NUMERIC questions with range must have range_min <= range_max
- Conditional questions (depends_on) are skipped if parent answer doesn't match

Test criteria:
- `test_boolean_accepts_yes_no` — "yes", "no", "true", "false" all valid
- `test_boolean_rejects_maybe` — "maybe" -> error message
- `test_enum_accepts_valid_option` — answer in options -> valid
- `test_enum_rejects_invalid` — answer not in options -> error
- `test_numeric_in_range` — 42 with range [0, 100] -> valid
- `test_numeric_out_of_range` — 200 with range [0, 100] -> error
- `test_conditional_skip` — depends_on="q1", depends_value="yes", q1 answered "no" -> question skipped
- `test_freetext_rejects_empty` — "" -> error

#### 3.2 Answer Audit Trail (`schemas.py`)

```python
class AnswerSource(StrEnum):
    USER_INTERACTIVE = "user_interactive"   # Human typed it
    AUTO_ASSUMPTION = "auto_assumption"     # Matched from assumptions
    INTEGRATION_SLACK = "integration_slack"
    INTEGRATION_LINEAR = "integration_linear"
    CLI_APPROVE = "cli_approve"             # pact approve (bulk)

class AuditedAnswer(BaseModel):
    """An answer with full provenance."""
    question_id: str
    answer: str
    source: AnswerSource
    confidence: float = Field(ge=0.0, le=1.0, description="Match confidence for auto-filled")
    timestamp: str = Field(description="ISO 8601 timestamp")
    matched_assumption: str | None = Field(default=None, description="Which assumption was matched, if any")
```

Invariants:
- USER_INTERACTIVE always has confidence=1.0
- AUTO_ASSUMPTION includes the matched assumption text
- confidence < 0.5 triggers a warning in `pact status`
- All answers are append-only (later answers for same question_id supersede, but history preserved)

Test criteria:
- `test_user_answer_confidence_one` — source=USER_INTERACTIVE -> confidence=1.0
- `test_auto_answer_includes_assumption` — source=AUTO_ASSUMPTION -> matched_assumption is not None
- `test_low_confidence_flagged` — confidence=0.3 -> appears in status warnings
- `test_answer_supersede_preserves_history` — two answers for same question -> latest used, both stored

#### 3.3 Fix Approve Matching (`cli.py`)

```python
def match_answer_to_question(
    question: str,
    assumptions: list[str],
    existing_answers: dict[str, str],
) -> tuple[str, float]:
    """Match a question to the best assumption for auto-approval.

    Algorithm (in order):
      1. Index-based pairing: if question index < len(assumptions), use assumptions[index]
         Confidence: 0.7
      2. Keyword overlap (>= 3 significant words shared): use best match
         Confidence: word_overlap / max(len_q_words, len_a_words)
      3. No match: return ("Accepted as stated", 0.0)

    Significant words: exclude stopwords (the, a, an, is, are, for, to, in, of, etc.)

    Postconditions:
      - Confidence is between 0.0 and 1.0
      - Result never uses assumptions[0] as universal fallback
    """
```

Invariants:
- Stopwords are never used for matching
- Confidence accurately reflects match quality
- No question receives the same assumption answer unless genuinely matching

Test criteria:
- `test_index_pairing_correct_order` — question[0] paired with assumption[0] at confidence 0.7
- `test_keyword_overlap_beats_index` — strong keyword match overrides index pairing
- `test_stopwords_excluded` — "What is the best approach for..." doesn't match on "is", "the", "for"
- `test_no_match_returns_accepted` — unrelated question and assumptions -> ("Accepted as stated", 0.0)
- `test_no_universal_fallback` — 5 different questions, 2 assumptions -> at most 2 questions get matched

---

### 4. Scheduling V2 (in `src/pact/`)

#### 4.1 Wavefront Scheduling (`scheduler.py`)

```python
class WavefrontScheduler:
    """Dependency-driven execution: fan out independent work, serialize dependencies.

    Instead of phase-locked execution (all contracts, then all tests, then all implementations),
    wavefront scheduling advances each component through its own phase pipeline as soon as
    its dependencies are satisfied.

    Example for tree with components A(root), B(leaf), C(leaf), D(depends on B):
      Wave 1: Contract B, Contract C (parallel - both are leaves, no deps)
      Wave 2: Test B, Test C, Contract D (parallel - B,C contracts done; D deps satisfied)
      Wave 3: Implement B, Implement C, Test D (parallel)
      Wave 4: Implement D (B done, D tests done)
      Wave 5: Integrate A (all children done)
    """

    def compute_ready_set(
        self,
        tree: DecompositionTree,
        component_states: dict[str, ComponentState],
    ) -> list[tuple[str, str]]:
        """Return list of (component_id, phase) pairs ready to execute.

        A component is ready for phase P when:
          - Its prerequisite phase (P-1) is complete
          - All its dependencies have completed their phase P (for contract/test)
          - All its dependencies have completed implementation (for implement)

        Postconditions:
          - No two entries have a dependency relationship (would deadlock)
          - Result is topologically sorted by dependency depth
          - Max concurrency respects max_concurrent_agents
        """

    def advance(
        self,
        component_id: str,
        completed_phase: str,
        result: Any,
    ) -> None:
        """Record phase completion and recompute ready set.

        Side effects:
          - Updates component_states
          - May unblock downstream components
          - Logs phase completion to audit
        """
```

Invariants:
- Contracts serialize per-node (a component's contract must complete before its tests start)
- Independent components (no dependency relationship) always run in parallel
- A component never starts implementation before ALL its dependencies have passing implementations
- Wavefront scheduling produces the same final result as phase-locked, just faster

Test criteria:
- `test_leaves_start_in_parallel` — tree with 3 independent leaves -> all 3 in first ready set
- `test_dependent_waits_for_dependency` — D depends on B -> D not in ready set until B's contract done
- `test_integration_waits_for_all_children` — parent not ready until all children implemented
- `test_wavefront_matches_phased_result` — same tree, same contracts -> identical final artifacts
- `test_respects_max_concurrent` — max_concurrent=2 -> ready set never exceeds 2
- `test_no_deadlock` — circular dependency detected and rejected at validation, not at scheduling

#### 4.2 Variable Timeouts (`config.py`, `backends/`)

```python
class ImpatienceLevel(StrEnum):
    PATIENT = "patient"       # 600s stall timeout
    NORMAL = "normal"         # 300s stall timeout
    IMPATIENT = "impatient"   # 150s stall timeout

class TimeoutConfig(BaseModel):
    """Per-role and per-phase timeout configuration."""
    impatience: ImpatienceLevel = ImpatienceLevel.NORMAL
    role_timeouts: dict[str, int] = Field(
        default_factory=lambda: {
            "decomposer": 300,
            "contract_author": 300,
            "test_author": 300,
            "code_author": 300,
            "trace_analyst": 180,
        },
        description="Stall timeout in seconds per agent role",
    )

    def get_timeout(self, role: str) -> int:
        """Return effective timeout for a role, scaled by impatience level.

        Postconditions:
          - PATIENT: role_timeout * 2
          - NORMAL: role_timeout * 1
          - IMPATIENT: role_timeout * 0.5
          - Result is always >= 30 (floor)
        """
```

Config (pact.yaml):
```yaml
impatience: normal          # patient | normal | impatient
role_timeouts:
  test_author: 450          # test suites are largest outputs
  code_author: 300
  trace_analyst: 120
```

Invariants:
- Timeout floor is 30 seconds (nothing below)
- Impatience multiplier applies uniformly to all roles
- role_timeouts override defaults but are still scaled by impatience

Test criteria:
- `test_patient_doubles_timeout` — role_timeout=300, impatience=patient -> 600
- `test_impatient_halves_timeout` — role_timeout=300, impatience=impatient -> 150
- `test_floor_at_30` — role_timeout=20, impatience=impatient -> 30 (not 10)
- `test_role_override` — custom role_timeout=450 for test_author -> 450 at normal
- `test_unknown_role_uses_default` — role not in role_timeouts -> 300 at normal

#### 4.3 Per-Phase Budget Tracking (`budget.py`)

```python
class PhaseBudget(BaseModel):
    """Budget tracking broken down by pipeline phase."""
    phase_spend: dict[str, float] = Field(
        default_factory=dict,
        description="Spend per phase: {'interview': 0.50, 'decompose': 1.20, ...}",
    )
    phase_caps: dict[str, float] = Field(
        default_factory=dict,
        description="Max spend per phase as fraction of total: {'shaping': 0.15}",
    )

    def record_spend(self, phase: str, amount: float) -> None:
        """Record spending for a specific phase."""

    def check_phase_budget(self, phase: str, total_budget: float) -> bool:
        """Check if phase has budget remaining under its cap.

        Postconditions:
          - Returns True if phase_spend[phase] < phase_caps[phase] * total_budget
          - Returns True if phase has no cap (uncapped phases)
          - Returns False if cap exceeded
        """

    def phase_summary(self) -> dict[str, dict[str, float]]:
        """Return {phase: {spent, cap, remaining}} for all phases."""
```

Invariants:
- Uncapped phases have no spending limit (only total budget matters)
- Phase spend is tracked independently from total project spend
- `shaping_budget_pct` maps to `phase_caps["shaping"]` (backward compatible)
- Phase budget check uses phase-specific spend, not total project spend

Test criteria:
- `test_phase_under_cap_passes` — shaping spent $2, cap=15% of $100 -> True
- `test_phase_over_cap_fails` — shaping spent $20, cap=15% of $100 -> False
- `test_uncapped_phase_always_passes` — implement has no cap, spent $40 -> True
- `test_total_budget_still_enforced` — phase under cap but total budget exceeded -> caught by total check
- `test_backward_compat_shaping_budget_pct` — old config with shaping_budget_pct=0.15 -> phase_caps["shaping"]=0.15

#### 4.4 Environment Specification (`config.py`, `test_harness.py`)

```python
class EnvironmentSpec(BaseModel):
    """Standardized execution environment for test harness and agents."""
    python_path: str = Field(default="python3", description="Python interpreter command or path")
    inherit_path: bool = Field(default=True, description="Inherit PATH from parent process")
    extra_path_dirs: list[str] = Field(default_factory=list, description="Additional PATH directories")
    required_tools: list[str] = Field(
        default_factory=lambda: ["pytest"],
        description="Tools that must be available (validated at startup)",
    )
    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="Additional environment variables for subprocess execution",
    )

    def build_env(self, pythonpath: str) -> dict[str, str]:
        """Build the subprocess environment dict.

        Postconditions:
          - PYTHONPATH is set to pythonpath parameter
          - PATH includes parent PATH if inherit_path=True
          - PATH includes all extra_path_dirs
          - All env_vars are included
          - python_path resolves to an actual executable
        Error cases:
          - python_path not found -> EnvironmentError with resolution suggestions
          - required_tool not found -> EnvironmentError listing missing tools
        """

    def validate_environment(self) -> list[str]:
        """Check that all required tools are available.

        Returns list of missing tools (empty = all present).
        """
```

Config (pact.yaml):
```yaml
environment:
  python_path: python3
  inherit_path: true
  extra_path_dirs:
    - /opt/homebrew/bin
  required_tools:
    - pytest
    - mypy
```

Invariants:
- Default behavior (no config) inherits full parent PATH (fixes the root cause bug)
- `pact doctor` validates environment and reports missing tools
- Environment validation runs once at daemon startup, not per-test

Test criteria:
- `test_inherit_path_includes_parent` — inherit_path=True -> PATH contains os.environ["PATH"]
- `test_inherit_path_false_minimal` — inherit_path=False -> PATH is only extra_path_dirs + /usr/bin
- `test_missing_tool_detected` — required_tools=["nonexistent"] -> validate returns ["nonexistent"]
- `test_build_env_includes_pythonpath` — build_env("src:lib") -> PYTHONPATH="src:lib"
- `test_doctor_shows_environment` — pact doctor output includes environment validation results

---

### 5. Contract Quality (in `src/pact/agents/`)

#### 5.1 Anti-Cliche Enforcement (`contract_author.py`)

```python
VAGUE_PATTERNS: list[re.Pattern] = [
    re.compile(r"entire class of", re.IGNORECASE),
    re.compile(r"best practice", re.IGNORECASE),
    re.compile(r"industry standard", re.IGNORECASE),
    re.compile(r"works on my machine", re.IGNORECASE),
    re.compile(r"scalable and maintainable", re.IGNORECASE),
    re.compile(r"robust and reliable", re.IGNORECASE),
    re.compile(r"clean architecture", re.IGNORECASE),
    re.compile(r"properly handle", re.IGNORECASE),
    re.compile(r"as needed", re.IGNORECASE),
    re.compile(r"and more", re.IGNORECASE),
    re.compile(r"etc\.?\s*$", re.IGNORECASE),
]

def audit_contract_specificity(contract: ComponentContract) -> list[str]:
    """Flag vague language in contract descriptions, invariants, and error messages.

    Returns list of warnings with location and flagged phrase.

    Postconditions:
      - Every flagged phrase includes the field path where it was found
      - Warnings are suggestions, not validation errors
    """
```

System prompt addition for contract authoring:
```
Every claim must be specific and testable. Do not use phrases like "prevents an
entire class of failures" without naming the failure class and the prevention
mechanism. If you cannot specify a concrete mechanism, omit the claim. Prefer
"raises ValidationError when confidence_score > 1.0" over "properly handles
invalid input." Invariants must be machine-verifiable, not aspirational.
```

Test criteria:
- `test_flags_entire_class_of` — description containing "entire class of failures" -> warning
- `test_flags_best_practice` — invariant containing "follows best practices" -> warning
- `test_clean_contract_no_warnings` — specific, testable descriptions -> empty list
- `test_warning_includes_field_path` — warning contains "functions[0].description" or similar

#### 5.2 Side-Effect Declarations (`schemas.py`, `contract_author.py`)

```python
class SideEffectKind(StrEnum):
    NONE = "none"
    READS_FILE = "reads_file"
    WRITES_FILE = "writes_file"
    NETWORK_CALL = "network_call"
    MUTATES_STATE = "mutates_state"
    LOGGING = "logging"

class SideEffect(BaseModel):
    kind: SideEffectKind
    target: str = Field(description="What is read/written/called, e.g. 'state.json' or 'anthropic API'")
    description: str = Field(default="", description="Additional context")
```

Update `ContractFunction.side_effects` from `list[str]` to `list[SideEffect]`.

System prompt addition: "Every function must declare its side effects. Pure functions declare `[{kind: 'none'}]`. Functions that read files, make network calls, or mutate state must declare each effect with a target."

Invariants:
- Every function has at least one side_effect entry (even if `kind=none`)
- `idempotent=True` is incompatible with `kind=writes_file` (validation warning)
- Side effects are used by code_author to understand impact scope

Test criteria:
- `test_pure_function_declares_none` — function with no effects -> side_effects=[{kind: "none"}]
- `test_file_writer_declares_writes` — function writing state.json -> side_effects includes writes_file
- `test_idempotent_with_write_warns` — idempotent=True + writes_file -> validation warning
- `test_empty_side_effects_rejected` — side_effects=[] -> validation error

#### 5.3 Performance Budgets (`schemas.py`)

```python
class PerformanceBudget(BaseModel):
    """Optional performance constraints on a function."""
    p95_latency_ms: int | None = Field(default=None, ge=1, description="95th percentile latency cap in ms")
    max_memory_mb: int | None = Field(default=None, ge=1, description="Peak memory cap in MB")
    complexity: str | None = Field(default=None, description="Big-O complexity, e.g. 'O(n log n)'")

# Added to ContractFunction:
class ContractFunction(BaseModel):
    # ... existing fields ...
    performance_budget: PerformanceBudget | None = None
```

Invariants:
- Performance budgets are optional (None means unconstrained)
- When specified, test_author generates corresponding assertions (timing tests)
- Complexity is documentation-only (not automatically verified)

Test criteria:
- `test_performance_budget_optional` — function with no budget -> performance_budget is None
- `test_latency_budget_generates_test` — p95_latency_ms=100 -> test suite includes timing assertion
- `test_complexity_stored_but_not_verified` — complexity="O(n)" -> stored, no test generated

---

### 6. Provenance (in `src/pact/`)

#### 6.1 Pipeline Bill of Materials (`project.py`)

```python
class ArtifactMetadata(BaseModel):
    """Provenance metadata for a generated artifact."""
    pact_version: str
    model: str = Field(description="Model ID that generated this artifact")
    component_id: str
    artifact_type: Literal["contract", "test_suite", "implementation", "composition"]
    contract_version: int = 1
    cost_input_tokens: int = 0
    cost_output_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: str = Field(description="ISO 8601 generation timestamp")
    run_id: str = Field(description="Unique run identifier")

def write_artifact_metadata(
    artifact_path: Path,
    metadata: ArtifactMetadata,
) -> None:
    """Write sidecar metadata file alongside generated artifact.

    Sidecar path: artifact_path.with_suffix('.meta.json')

    Postconditions:
      - .meta.json exists alongside the artifact
      - Metadata is valid JSON matching ArtifactMetadata schema
    """

def read_artifact_metadata(artifact_path: Path) -> ArtifactMetadata | None:
    """Read sidecar metadata for an artifact. Returns None if no metadata."""
```

Invariants:
- Every generated file has a corresponding .meta.json sidecar
- Metadata is written atomically (no partial writes)
- run_id is consistent across all artifacts in a single Pact run

Test criteria:
- `test_metadata_written_alongside_artifact` — generate contract -> .meta.json exists
- `test_metadata_contains_model` — metadata.model matches configured model for role
- `test_metadata_contains_cost` — cost fields populated from API response
- `test_metadata_roundtrip` — write then read -> identical ArtifactMetadata

#### 6.2 Drift Detection (`contracts.py`)

```python
class ArtifactBaseline(BaseModel):
    """Hash baseline for drift detection."""
    component_id: str
    contract_hash: str = Field(description="SHA256 of interface.json")
    test_hash: str = Field(description="SHA256 of contract_test.py")
    impl_hash: str = Field(description="SHA256 of implementation files concatenated")
    captured_at: str = Field(description="ISO 8601 timestamp")
    test_results: TestResults | None = None

def capture_baseline(component_id: str, project_dir: Path) -> ArtifactBaseline:
    """Capture current hashes for a component's artifacts."""

def detect_drift(
    baseline: ArtifactBaseline,
    project_dir: Path,
) -> list[str]:
    """Compare current file hashes against baseline.

    Returns:
      List of drift descriptions, e.g.:
        ["implementation changed (hash mismatch) but contract version unchanged"]
    """
```

Storage: `.pact/baselines/{component_id}.json`

Invariants:
- Baselines captured after successful implementation (all tests pass)
- Drift detection runs on `pact validate` and `pact status`
- Implementation drift without contract version bump is a warning
- Contract drift without test update is an error

Test criteria:
- `test_no_drift_clean` — baseline matches current files -> empty list
- `test_impl_drift_detected` — modify implementation after baseline -> drift reported
- `test_contract_drift_without_test_update` — modify contract, don't update tests -> error
- `test_baseline_capture_after_passing_tests` — baseline only captured when tests pass

#### 6.3 Staleness Tracking

```python
class StalenessCheck(BaseModel):
    component_id: str
    status: Literal["fresh", "aging", "stale"]
    reason: str
    days_since_verification: int
    dependency_updates_since: int = 0

def check_staleness(
    component_id: str,
    baseline: ArtifactBaseline,
    dependency_baselines: dict[str, ArtifactBaseline],
    staleness_window_days: int = 90,
) -> StalenessCheck:
    """Determine if a component's contract is stale.

    Rules:
      - fresh: verified within staleness_window, no dependency changes
      - aging: verified within staleness_window, but dependencies have changed
      - stale: not verified within staleness_window OR dependencies changed + not re-verified
    """
```

Config: `staleness_window_days: 90` in pact.yaml

Test criteria:
- `test_fresh_within_window` — verified 30 days ago, no dep changes -> fresh
- `test_aging_dep_changed` — verified 30 days ago, dependency updated since -> aging
- `test_stale_past_window` — verified 100 days ago -> stale
- `test_staleness_in_status` — `pact status` includes staleness warnings for stale components

#### 6.4 Retrospective Learning (`project.py`)

```python
class RunRetrospective(BaseModel):
    """Post-run analysis for future improvement."""
    run_id: str
    total_cost: float
    total_duration_seconds: float
    components_count: int
    plan_revisions: int = Field(description="How many contracts needed revision")
    largest_test_suite: tuple[str, int] = Field(description="(component_id, test_count)")
    most_error_cases: tuple[str, int] = Field(description="(component_id, error_count)")
    cost_distribution: dict[str, float] = Field(description="{component_id: cost}")
    failure_patterns: list[str] = Field(default_factory=list, description="Detected failure patterns")
    lessons: list[str] = Field(default_factory=list, description="Inferred lessons for future runs")

def generate_retrospective(project_dir: Path) -> RunRetrospective:
    """Analyze completed run and generate retrospective.

    Preconditions:
      - Run is complete (status=complete or status=failed with partial work)
    Data sources:
      - audit.jsonl for timing and cost
      - .pact/contracts/ for test suite sizes
      - .pact/implementations/ for attempt counts
      - state.json for final status
    """
```

Storage: `.pact/retrospectives/{run_id}.json`

Invariants:
- Retrospective generated automatically after every run (success or failure)
- Lessons are specific and actionable, not vague
- Future runs can load retrospectives from prior runs for context

Test criteria:
- `test_retrospective_captures_cost` — total_cost matches sum of audit entries
- `test_retrospective_identifies_largest_suite` — correct component identified
- `test_retrospective_after_failure` — partial retrospective generated even on failed runs
- `test_lessons_are_specific` — lessons don't contain vague patterns (same anti-cliche rules)

#### 6.5 MCP Server (`mcp_server.py` — NEW)

```python
# MCP resources:
# pact://status          -> RunState summary
# pact://contracts       -> list of contracts with summaries
# pact://contract/{id}   -> full contract for a component
# pact://budget          -> budget summary with phase breakdown
# pact://retrospective   -> latest retrospective

# MCP tools:
# pact_validate          -> run validation, return errors
# pact_resume            -> resume failed/paused run
# pact_status            -> detailed status with staleness
```

Invariants:
- MCP server is optional (Pact works without it)
- Read-only resources (no mutations via MCP resources)
- Tools require confirmation for state-changing operations (resume)
- Server discovers project directory from cwd or explicit path

Test criteria:
- `test_status_resource_returns_json` — valid RunState JSON
- `test_contract_resource_returns_interface` — returns contract for given component_id
- `test_validate_tool_returns_errors` — validation errors returned as structured response
- `test_mcp_server_starts_without_project` — graceful error when no .pact/ directory

#### 6.6 Context Compression (`interface_stub.py`)

```python
def build_code_agent_context(
    contract: ComponentContract,
    test_suite: ContractTestSuite,
    decisions: list[str] | None = None,
    research: list[dict] | None = None,
    max_tokens: int = 8000,
) -> str:
    """Build tiered context for code generation agent.

    Tier 1 (always included): interface.py + contract_test.py
    Tier 2 (if room): decisions.json relevant to this component
    Tier 3 (if room): research findings summary (not full findings)

    Postconditions:
      - Result fits within max_tokens (estimated)
      - Tier 1 is never truncated
      - Tier 2 and 3 are truncated gracefully if needed
    """
```

Invariants:
- Contract and tests are never omitted (they define the work)
- Research is excluded by default (valuable for writing contract, not for satisfying it)
- Decisions are summarized, not included verbatim

Test criteria:
- `test_always_includes_contract_and_tests` — even at max_tokens=100 -> contract present
- `test_excludes_research_by_default` — no research in output unless explicitly included
- `test_includes_decisions_if_room` — sufficient max_tokens -> decisions present
- `test_truncates_gracefully` — very low max_tokens -> tier 1 only, no crash

---

## Constraints

- All changes are backward compatible. Existing pact.yaml files work without modification.
- New config fields have sensible defaults that preserve current behavior.
- No new required dependencies. MCP server is optional.
- All new code follows existing patterns: Pydantic v2 models, async where appropriate, type hints throughout.
- Environment specification defaults to `inherit_path: true` (the fix for the root cause PATH bug).
- Wavefront scheduling is opt-in via `scheduling: wavefront` in pact.yaml. Default remains phase-locked.
- Every new public function has at least 3 test cases covering: happy path, edge case, error case.
- Generated metadata (.meta.json) files are gitignored by default.

## Success Criteria

- `pact resume` recovers a failed run without manual state.json editing
- Daemon never times out during active API processing
- Systemic failures (all-zero test results) are detected and paused within 2 component completions
- External dependencies on existing codebase modules pass validation
- Incremental validation catches errors before the next contract is authored
- `pact doctor` validates environment, reports missing tools, shows all integration statuses
- Wavefront scheduling reduces wall-clock time by >= 30% on trees with 5+ independent leaves
- All 24 improvements have corresponding tests that pass
- Existing 387 Pact tests continue to pass with zero regressions

## Priority

### P0 — Caused failures this session
- 1.1 Resume command (both runs required manual state.json edits)
- 1.3 Idle timer reset (killed active work)
- 1.4 Systemic failure detection (0/0 pattern undetected across all components)
- 2.1 External dependency validation (rejected valid contracts, wasted $24.53)
- 4.4 Environment specification (PATH bug: root cause of all 0/0 test failures)

### P1 — Quality and correctness
- 1.2 Error classification (transient vs permanent)
- 2.2 Incremental validation
- 2.3 Dependency name normalization
- 3.3 Fix approve matching
- 4.3 Per-phase budget tracking
- 5.1 Anti-cliche enforcement
- 5.2 Side-effect declarations
- 6.1 PBOM metadata

### P2 — Capability expansion
- 1.5 Event sourcing / rebuild
- 2.4 Hierarchy alignment
- 3.1 Structured question types
- 3.2 Answer audit trail
- 4.1 Wavefront scheduling
- 4.2 Variable timeouts
- 5.3 Performance budgets
- 6.2 Drift detection
- 6.3 Staleness tracking
- 6.4 Retrospective learning
- 6.5 MCP server
- 6.6 Context compression
