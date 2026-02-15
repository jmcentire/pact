# task_r3.md — Performance & Efficiency Improvements

Round 3 roadmap. Targets the core cost/latency bottleneck: Pact re-reads the
codebase and re-sends static context on every API call, paying the LLM to learn
what it already knew. These improvements cut token waste and wall-clock time.

## Diagnosis

A single component currently costs 5-6 serial API calls:

```
research → plan+eval (×2-3) → execute    [contract author]
research → plan+eval (×2-3) → execute    [test author]
research → plan+eval (×2-3) → execute    [code author]
```

Each call sends the full task context (SOPs, decomposition, dependency stubs,
research findings) cold. For 27 components, that's ~150 API calls with ~50-100K
input tokens each. The research phases alone account for ~40% of total spend.

## P0 — Prompt Caching (highest leverage, no architectural change)

### P0-1: Anthropic prompt caching for static prefixes

**File**: `src/pact/backends/anthropic.py`

The system prompt, SOPs, and task context are identical across most calls within
a phase. Anthropic's prompt caching lets us mark a prefix with
`cache_control: {"type": "ephemeral"}` — cached tokens are 10× cheaper on reads
and stay warm for 5 minutes.

**Changes**:
- Update `_stream_with_stall_detection` to accept an optional `cache_breakpoints`
  param — a list of content blocks with `cache_control` markers
- Instead of sending `system` as a plain string, support sending it as a list
  of content blocks (Anthropic's format): `[{"type": "text", "text": "...",
  "cache_control": {"type": "ephemeral"}}]`
- Add a `assess_with_cache` method that splits the prompt into cacheable prefix
  (SOPs, task description, dependency stubs) and dynamic suffix (attempt-specific
  context, prior failures)
- Keep backward compatibility: `assess()` unchanged, new `assess_with_cache()`
  is opt-in

**Interface**:
```python
async def assess_with_cache(
    self,
    schema: type[T],
    prompt: str,
    system: str,
    cache_prefix: str = "",
    max_tokens: int = 32768,
) -> tuple[T, int, int]:
    """Like assess() but marks system + cache_prefix for prompt caching.

    Args:
        system: System prompt — always cached.
        cache_prefix: Static portion of user prompt (SOPs, contracts, etc.).
                      Sent as a separate content block with cache_control.
        prompt: Dynamic portion of user prompt (attempt-specific).
    """
```

**Test criteria**:
- `test_cache_system_as_blocks` — system string converted to content block list
- `test_cache_prefix_as_separate_block` — cache_prefix sent as separate block with cache_control
- `test_cache_fallback_no_prefix` — when cache_prefix is empty, behaves like assess()
- `test_cache_preserves_tool_choice` — tool_choice still enforced
- `test_cache_min_threshold` — prefixes under ~300 chars skip caching (not worth it)

---

### P0-2: Thread cache_prefix through agent pipeline

**Files**: `src/pact/agents/base.py`, `src/pact/agents/research.py`,
`src/pact/agents/contract_author.py`, `src/pact/agents/test_author.py`,
`src/pact/agents/code_author.py`

Add `cache_prefix: str = ""` to `AgentBase.assess()`. If non-empty and the
backend is Anthropic, call `assess_with_cache()` instead of `assess()`.

In each agent module, identify the static vs. dynamic portions of the prompt:

**research.py**:
- Cacheable: SOPs, role context, the 5 research questions (template)
- Dynamic: task description

**contract_author.py**:
- Cacheable: SOPs, dependency stubs (unchanged within a phase), CONTRACT_SYSTEM
- Dynamic: component description, research findings, plan

**test_author.py**:
- Cacheable: SOPs, contract JSON, dependency mock info, TEST_SYSTEM
- Dynamic: research findings, plan summary

**code_author.py**:
- Cacheable: SOPs, handoff brief (contract stub + tests + dependency map), CODE_SYSTEM
- Dynamic: research findings, plan summary, prior failures

**Changes per agent**:
- Split prompt construction into `_build_cache_prefix()` + dynamic portion
- Pass `cache_prefix` through `agent.assess()`

**Test criteria**:
- `test_contract_author_cache_prefix_contains_sops` — SOPs in cache_prefix
- `test_contract_author_cache_prefix_contains_dep_stubs` — dep stubs in cache_prefix
- `test_test_author_cache_prefix_contains_contract` — contract JSON in cache_prefix
- `test_code_author_cache_prefix_contains_handoff` — handoff brief in cache_prefix
- `test_no_cache_prefix_when_no_sops` — empty when nothing to cache

---

### P0-3: Cache usage metrics in budget tracking

**Files**: `src/pact/budget.py`, `src/pact/backends/anthropic.py`

Track cache hit/miss rates to measure the actual savings.

**Changes to `BudgetTracker`**:
- Add `cache_creation_tokens: int = 0`
- Add `cache_read_tokens: int = 0`
- Add `record_cache_tokens(creation, read)` method
- Add `cache_hit_rate` property (read / (read + creation + uncached))
- Include cache stats in `summary()` and `as_dict()`

**Changes to `AnthropicBackend`**:
- After streaming completes, check `message.usage` for `cache_creation_input_tokens`
  and `cache_read_input_tokens` fields
- Call `self._budget.record_cache_tokens(creation, read)`

**Test criteria**:
- `test_cache_token_tracking` — budget records cache stats
- `test_cache_hit_rate_calculation` — correct ratio
- `test_cache_stats_in_summary` — appears in summary output

---

## P1 — Consolidated Research (reduces per-component overhead)

### P1-1: Group research by subtree

**File**: `src/pact/agents/research.py` (new function)

Instead of one research phase per component per agent type, do one research
phase per subtree. Components sharing a parent share 90%+ of relevant context.

**New function**:
```python
async def research_for_group(
    agent: AgentBase,
    group_description: str,
    components: list[dict],  # [{id, name, description}]
    role_context: str,
    sops: str = "",
) -> ResearchReport:
    """One research phase covering multiple related components.

    The prompt lists all components in the group so the LLM can produce
    findings that apply broadly. Per-component specifics come later in
    plan evaluation.
    """
```

**Test criteria**:
- `test_group_research_prompt_includes_all_components` — all component names in prompt
- `test_group_research_returns_research_report` — valid ResearchReport
- `test_group_research_with_sops` — SOPs included

---

### P1-2: Share research across contract+test+code phases

**Files**: `src/pact/agents/contract_author.py`, `src/pact/agents/test_author.py`,
`src/pact/agents/code_author.py`, `src/pact/implementer.py`

Currently each agent (contract_author, test_author, code_author) does its own
research phase independently. The contract_author's research findings are
discarded before the test_author runs.

**Changes**:
- `author_contract()` returns `ResearchReport` (already does)
- `author_tests()` accepts optional `prior_research: ResearchReport = None`.
  If provided, skips its own research phase and uses the prior findings,
  supplemented with test-specific focus
- `author_code()` similarly accepts `prior_research: ResearchReport = None`
- `implement_component()` threads the contract research through to test
  and code authoring

**New function in research.py**:
```python
async def augment_research(
    agent: AgentBase,
    base_research: ResearchReport,
    supplemental_focus: str,
    sops: str = "",
) -> ResearchReport:
    """Augment existing research with additional role-specific findings.

    Much cheaper than full research — sends base findings as context
    and asks for supplemental findings only.
    """
```

**Test criteria**:
- `test_tests_skip_research_with_prior` — no research call when prior_research provided
- `test_tests_augment_research` — augmented findings merged with base
- `test_code_skip_research_with_prior` — same for code author
- `test_augment_research_preserves_base` — base findings retained

---

### P1-3: Research cache persistence

**Files**: `src/pact/project.py`, new: `src/pact/research_cache.py`

Persist research results to `.pact/research/` so that resumed runs don't repeat
research for components whose context hasn't changed.

**New module `research_cache.py`**:
```python
def cache_key(component_id: str, role: str, context_hash: str) -> str:
    """Deterministic key from component + role + hash of inputs."""

def save_research(project_dir: Path, key: str, report: ResearchReport) -> None:
    """Save to .pact/research/{key}.json"""

def load_research(project_dir: Path, key: str) -> ResearchReport | None:
    """Load cached research if fresh. Returns None if stale/missing."""

def context_hash(component_desc: str, deps: list[str], sops: str) -> str:
    """SHA256 of the inputs that would change research findings."""
```

**Test criteria**:
- `test_save_load_roundtrip` — saved research loads back identically
- `test_cache_miss_on_changed_context` — different hash returns None
- `test_cache_key_deterministic` — same inputs produce same key
- `test_load_missing_returns_none` — no file returns None

---

## P2 — Hybrid Agent Architecture (biggest wall-clock improvement)

### P2-1: Use claude_code_team for implementation phase

**File**: `src/pact/implementer.py`, `src/pact/scheduler.py`

The API-based code_author does research → plan → generate in 3 serial calls per
attempt. Claude Code sessions maintain context — one session can read the
contract, write code, run tests, and iterate on failures without re-sending
context each time.

**Changes to `implementer.py`**:
- New function `implement_component_interactive(team_backend, project, component_id, contract, test_suite, ...)`:
  - Builds a comprehensive prompt with the full handoff brief
  - Appends instructions: "Read the test file, implement the module, run tests, iterate until passing"
  - Spawns via `team_backend.spawn_agent()`
  - Waits for completion
  - Reads output files from the component's src directory
  - Returns TestResults

**Changes to `scheduler.py`**:
- In `_phase_implement()`, check config for `backend: claude_code_team`
- If so, instantiate `ClaudeCodeTeamBackend` and call `implement_component_interactive()`
  instead of the standard pipeline
- The API backend is still used for contract+test authoring (needs schema enforcement)

**Test criteria**:
- `test_interactive_prompt_contains_handoff` — handoff brief in prompt
- `test_interactive_prompt_contains_test_instructions` — tells agent to run tests
- `test_interactive_falls_back_on_no_tmux` — graceful fallback
- `test_scheduler_uses_team_for_impl` — config switches implementation mode

---

### P2-2: Shared context preamble for team agents

**File**: `src/pact/backends/claude_code_team.py`

When spawning multiple Claude Code agents for parallel components, they all
need the same project context (SOPs, decomposition tree, coding standards).
Write this once to a shared preamble file that each agent prompt references.

**Changes**:
- New method `write_shared_preamble(context: str) -> Path`:
  - Writes to `{prompt_dir}/shared_preamble.md`
  - Returns path
- `spawn_agent()` prepends `"Read {preamble_path} for project context.\n\n"`
  to the prompt if a preamble exists
- `spawn_parallel()` calls `write_shared_preamble()` once before spawning

**Test criteria**:
- `test_shared_preamble_written` — file created with context
- `test_prompt_references_preamble` — prompt includes read instruction
- `test_parallel_shares_preamble` — one file for all agents

---

### P2-3: Tiered model selection

**Files**: `src/pact/config.py`, `src/pact/agents/research.py`,
`src/pact/scheduler.py`

Not all phases need the most expensive model. Research and plan evaluation
can use a cheaper, faster model (Sonnet) while contract/test/code authoring
use the primary model (Opus).

**Changes to config**:
```python
@dataclass
class ModelConfig:
    primary: str = "claude-opus-4-6"          # contract, test, code authoring
    research: str = "claude-sonnet-4-5-20250929"  # research + plan eval
    fast: str = "claude-haiku-4-5-20251001"   # validation, formatting
```

Add `models: ModelConfig` to `GlobalConfig` and `ProjectConfig`.

**Changes to research.py**:
- `research_phase()` calls `agent.set_model(research_model)` before the call,
  restores afterward
- `plan_and_evaluate()` similarly uses the research model

**Changes to scheduler.py**:
- Read `models` config and thread appropriate model names through

**Test criteria**:
- `test_model_config_defaults` — sensible defaults
- `test_research_uses_research_model` — model switched for research
- `test_model_restored_after_research` — primary model restored
- `test_config_override` — per-project model override

---

## P3 — Prompt Optimization (reduces per-call token count)

### P3-1: Compact dependency representation

**Files**: `src/pact/interface_stub.py`, `src/pact/agents/contract_author.py`

Dependency stubs rendered via `render_stub()` are verbose (~300-500 tokens per
contract). For contract authoring, a compact signature-only format suffices.

**New function in `interface_stub.py`**:
```python
def render_compact_deps(contracts: dict[str, ComponentContract]) -> str:
    """Compact dependency reference: just function signatures + types.

    Example:
        ## pricing_engine
        calculate_price(unit_id: str, dates: DateRange) -> PriceResult
        DateRange = {check_in: date, check_out: date}
        PriceResult = {total: float, breakdown: list[LineItem]}
    """
```

~80% fewer tokens than full stubs while preserving all type information needed
for contract authoring.

**Test criteria**:
- `test_compact_deps_includes_signatures` — all function signatures present
- `test_compact_deps_includes_types` — type definitions included
- `test_compact_deps_smaller_than_full` — significantly fewer tokens
- `test_compact_deps_empty` — empty dict returns empty string

---

### P3-2: Lazy test code in handoff brief

**File**: `src/pact/interface_stub.py`

The handoff brief includes the full test code (~2000-8000 tokens) in every
code authoring call. For the research and plan phases, only the test case
names and descriptions are needed.

**Changes to `render_handoff_brief()`**:
- Add `include_test_code: bool = True` parameter
- When False, replace the full test code section with a compact test listing:
  ```
  ## TESTS TO PASS (12 cases)
  - test_happy_path_basic: Happy path with valid inputs
  - test_edge_case_empty: Empty input handling
  ...
  ```

**Changes to `code_author.py`**:
- Research + plan phases use `include_test_code=False`
- Execute phase uses `include_test_code=True` (default)

**Test criteria**:
- `test_brief_without_test_code_smaller` — significantly fewer tokens
- `test_brief_without_test_code_has_listing` — test names still present
- `test_brief_with_test_code_unchanged` — default behavior preserved

---

### P3-3: Contract JSON deduplication

**File**: `src/pact/agents/test_author.py`

Test authoring currently sends the full contract as JSON (verbose, includes
all nested objects). Much of this is redundant with the function/type summary
already in the task description.

**Changes**:
- Replace `contract.model_dump_json(indent=2)` with a focused contract summary:
  - Function signatures with full type details
  - Type definitions with fields
  - Error cases
  - Preconditions/postconditions
- Skip metadata fields (component_id, version, etc. — already in task_desc)

**Test criteria**:
- `test_focused_contract_smaller` — fewer tokens than full JSON
- `test_focused_contract_has_error_cases` — error cases preserved
- `test_focused_contract_has_preconditions` — pre/post conditions preserved

---

## Implementation Order

| Step | Items | Depends On | Estimated Savings |
|------|-------|-----------|-------------------|
| 1 | P0-1 (cache backend) | — | 50-70% input token cost |
| 2 | P0-2 (thread cache) | P0-1 | activates P0-1 |
| 3 | P0-3 (cache metrics) | P0-1 | measurement |
| 4 | P1-1 (group research) | — | ~30% fewer research calls |
| 5 | P1-2 (share research) | P1-1 | ~40% fewer research calls |
| 6 | P1-3 (research cache) | — | skip research on resume |
| 7 | P2-1 (team impl) | — | ~60% wall-clock for impl |
| 8 | P2-2 (shared preamble) | P2-1 | reduces team prompt size |
| 9 | P2-3 (tiered models) | — | ~40% research cost |
| 10 | P3-1 (compact deps) | — | ~80% dep token savings |
| 11 | P3-2 (lazy test code) | — | ~50% research/plan savings |
| 12 | P3-3 (contract dedup) | — | ~30% test author savings |

Steps 1-3 are sequential. Steps 4-12 are largely independent.

## Expected Combined Impact

| Metric | Before | After (estimated) |
|--------|--------|-------------------|
| Input tokens per component | 50-100K | 15-30K |
| API calls per component | 12-18 | 6-10 |
| Wall-clock per component (impl) | 5-8 min | 2-3 min |
| Total cost for 27 components | ~$15-30 | ~$4-8 |
| Research cost share | 40% | 10% |
