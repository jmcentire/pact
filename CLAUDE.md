# CLAUDE.md -- Pact

Contract-first multi-agent software engineering. Decomposition produces contracts and tests, not code. Black-box implementations verified by functional tests at boundaries. Recursive composition.

## Quick Reference

```bash
cd path/to/pact
python3 -m pytest tests/ -v        # Run all tests
pact init <project-dir>            # Initialize project
pact init <project-dir> --spec <file> # Initialize from AI-authored JSON/YAML build spec
pact spec apply <project-dir> <file> # Apply AI-authored JSON/YAML build spec
pact status <project-dir>          # Show state
pact components <project-dir>      # List components
pact build <project-dir> <id>      # Build specific component
pact run <project-dir>             # Execute pipeline
pact tasks <project-dir>           # Generate/display task list
pact analyze <project-dir>         # Cross-artifact analysis
pact checklist <project-dir>       # Requirements quality checklist
pact production init <project-dir> # Scaffold optional production-readiness pack
pact production fingerprint <project-dir> # Print the source fingerprint for evidence
pact production validate <project-dir> # Validate production-readiness gate for a Pact-managed project
pact assess <directory>            # Architectural assessment (any codebase)
pact export-tasks <project-dir>    # Export TASKS.md
pact handoff <project-dir> <id>    # Render/validate handoff brief
pact review <target> --claim <text> # Advocate + Simulacrum review
pact agent spec-author [--apply]    # Run one constrained spec-author agent
pact agent repair --source-root <dir> [--apply] # Run one constrained repair agent
pact directive <project-dir> <json> # Send structured directive to daemon
pact mcp-server [--project-dir <dir>] # Run MCP server (stdio)
pact-mcp                              # MCP server entry point
```

**Entry point**: `pact = "pact.cli:main"`, `pact-mcp = "pact.mcp_server:main"` (pyproject.toml)

**Python**: >=3.12 | **Dependencies**: pydantic>=2.0, pytest>=8.2, pyyaml>=6.0 | **Optional**: anthropic>=0.40, mcp>=1.0

## Architecture Overview

### Research-First Agent Protocol

Every agent follows 3 phases: Research -> Plan+Evaluate -> Execute. Research and plan outputs are persisted alongside work products.

### Core Workflow

1. **Interview** -- Establishes processing register (cognitive mode), then identifies risks/ambiguities, asks user clarifying questions
   and confirms the readiness profile for operational maturity, security,
   privacy, compliance, gating, testing, and monitoring.
2. **Shape** -- (Optional) Produce a Shape Up pitch: appetite, breadboard, rabbit holes, no-gos
3. **Decompose** -- Task -> DecompositionNode tree (2-7 components), guided by shaping context
3. **Contract** -- For each component (leaves first), generate ComponentContract
4. **Test** -- For each contract, generate ContractTestSuite with executable tests + hidden Goodhart tests
5. **Validate** -- Mechanical gate: all refs resolve, no cycles, test code parses
6. **Preflight** -- Establish red lines and contingencies before implementation (iterative coding-shell backends). Queries Kindex for lessons from previous runs. Stores PreflightPlan per component in `.pact/preflight/`. Skipped for direct API backends.
7. **Implement** -- Each component independently by code_author agent, verified by contract tests
8. **Integrate** -- Parent components: glue code wiring children, parent-level tests
9. **Retrospective** -- Post-run analysis: cost, failure patterns, lessons learned (mechanical, no LLM)
10. **Diagnose** -- On failure: I/O tracing, systematic error recovery

### Production-Readiness Pack

`pact production` is an explicit opt-in layer for higher-bar builds. It does
not alter the default planner or implementation flow. Instead it scaffolds
file-backed artifacts under `production/` and validates them against Pact's
existing outputs:

- `trust_policy.yaml` for machine-checkable trust assertions
- `control_matrix.yaml` for control-to-evidence mapping
- `threat_model.yaml` for threat and mitigation coverage
- `architecture_laws.yaml` for hard implementation invariants
- `preflight.yaml` for red lines and fallback
- `live_validation.yaml` for running-instance evidence
- `done_gate.yaml` plus `na_register.yaml` for falsifiable readiness status

Derived evidence comes from project config, Constrain bundle, contracts,
contract tests, Goodhart tests, analysis, checklist, review, certification, and
stub scan. It also runs deterministic static checks inspired by webprobe's
mechanical audit model: likely hard-coded secrets, dependency manifest, SBOM,
and OpenAPI validity/auth/error/rate-limit shape. Static checks are never live
browser, network, or LLM probes; missing optional artifacts are reported as
`not_detected`, not as false failures. External evidence must be explicit. N/A
across the pack requires a matching structured record and review reference. Set
`production_artifact_dir` when the tracked pack should live somewhere other
than `production/`; an unset `constrain_dir` follows that configured directory.
The manifest fingerprint and bounded validation age prevent stale evidence from
passing after source or Pact artifact changes, or after the evidence ages out.
This is a deployment gate, not a runtime substitute; `live_validation.yaml`
must still prove the running environment matches the evidence being claimed.

### Production-Ready Minimalism

Pact supports a lean/simplification review lens, but it is not a readiness
profile and it is never a substitute for the production artifact pack. Before
adding code, dependencies, or layers, stop at the first rung that holds:

1. Does this need to exist at all?
2. Does stdlib solve it?
3. Does a native platform feature solve it?
4. Does an already-installed dependency solve it?
5. Can the minimum correct behavior be expressed more directly?
6. Only then add the minimum custom code.

Every intentional shortcut must name its ceiling, the trigger that makes the
ceiling real, and the upgrade path. Lean review may delete boilerplate,
wrapper-only layers, speculative configuration, and unnecessary dependencies.
It may not delete trust-boundary validation, data-loss protection, security,
accessibility, observability, rollback, migration safety, auditability, live
validation, or evidence-backed done gates. The operational test is simple:
operators must be able to answer what broke, why, and how to roll back without
deploying new code.

### Readiness Profile

Every project starts with a typed `readiness` section in `pact.yaml` and a
`build_spec.yaml` template. The profile uses `none`, `basic`, `standard`,
`strict`, and `regulated` levels per dimension, but each level expands to
concrete controls before decomposition and contract authoring. The interview
phase confirms or overrides those levels before any contract is generated.
`build_spec.yaml` is tracked planning input, not a secret store; keep tokens,
credentials, and machine-local paths out of it.
If the task scope materially changes after interview, update the spec or
`pact.yaml` and rerun interview before regenerating contracts.

An AI can provide a full build request as JSON or YAML:

```yaml
task: |
  Build a tenant-scoped booking API.
sops: |
  Use Python 3.12 and pytest.
readiness:
  security: strict
  privacy: standard
  compliance: basic
config:
  build_mode: hierarchy
  budget: 25
```

### Execution Modes

Two independent levers:
- `parallel_components: true` -- Independent leaves implement concurrently (semaphore-limited)
- `competitive_implementations: true` -- N agents implement same component, best wins
- `plan_only: true` -- Default. Stop after contracts/tests so the active Claude or Codex agent can implement
- `max_concurrent_agents: 4` -- Concurrency limit for parallel modes

Use `pact run <project> --implement` to opt into Pact-managed implementation.
If implementation exposes an infeasible contract, reconcile and regenerate the
Pact artifacts rather than silently deviating.

### Build Modes

Three build modes control decomposition behavior:

```yaml
# pact.yaml
build_mode: auto    # unary | auto | hierarchy
```

- **unary**: Single agent session. Skips LLM decomposition, creates one component with the full task. Still produces contract+tests for verification.
- **auto** (default): LLM decides whether to decompose or implement directly. Improved prompts genuinely encourage `is_trivial=true` for straightforward tasks.
- **hierarchy**: Always decompose into multiple components (previous default behavior).

Project config overrides global config. Set at runtime via directive:
```bash
pact directive ./my-proj '{"type": "set_mode", "mode": "unary"}'
```

### Global Standards

After decomposition, pact automatically collects shared conventions from contracts:
- Shared types (appearing in 2+ contracts)
- Common validator patterns
- Package requirements
- Coding conventions from SOPs

Standards are injected into every agent's handoff brief and persisted at `standards.json` in the project root.

### Processing Register

Research (Papers 35-39) established that LLM representations follow a hierarchy: register (processing mode) > domain > structural shape. Register is the hub that domain anchors to. Pact makes this explicit:

- **Establishment**: Interview phase determines the processing register before any domain analysis (e.g., "rigorous-analytical", "exploratory-generative", "systematic-verification")
- **Propagation**: Register is a first-class field on `ComponentContract` and flows through the handoff protocol: reset → prime register → prime domain
- **Override**: Set `processing_register` in `pact.yaml` to skip LLM establishment
- **Monitoring**: Health system tracks register drift — agents departing from their established processing mode — as an early indicator of coordination failure
- **Context Fence**: All system prompts include a context fence ("You are starting fresh...") as the first line. `context_fence()` in `interface_stub.py` is the reusable helper. Papers XX-XXIII: 39% CE improvement.
- **Natural Format**: Handoff briefs use conversational format, not rigid markdown headers. Paper XX: +0.475 nats improvement.
- **Tiered Compression**: `render_handoff_brief(max_context_tokens=N)` applies three-tier compression. Tier 1 (fence + stub) never truncated. Paper XX: content beyond ~150 tokens of domain priming degrades performance.
- **Reduced Over-instruction**: System prompts trimmed from rule lists to concise domain activation. Paper XXII: "Director mode" -37.1%.
- **Centroid Selection**: When `competitive_implementations` enabled, `resolution.py` selects the implementation closest to ensemble centroid (code similarity). Paper XIX: 48.9% gap closure.
- **Andon Cord**: `design_bug` diagnosis routes back to decompose (not dead-end failure). Phase cycle limits prevent infinite loops.
- **Cross-Component Validation**: `validate_cross_component_interfaces()` checks shared type compatibility and dependency output/input structural compatibility across components.
- **North-Star Validation**: `validate_north_star()` checks that composed contracts plausibly fulfill the original task (verb coverage, root/leaf function presence). Runs in polish phase.
- **Early Decomposition Validation**: `validate_decomposition_coverage()` catches structural issues (orphans, empty descriptions, duplicates, low keyword coverage) before spending LLM calls on contract generation.
- **Handoff CLI**: `pact handoff <project-dir> <component-id>` renders and validates handoff briefs. `--validate` checks context fence, primer ordering, natural format, token budget. `--max-tokens` applies tiered compression.

```yaml
# pact.yaml
processing_register: rigorous-analytical  # optional override
register_check_rate: 0.1                  # drift check probability per component (0.0-1.0)
```

### Production Monitoring & Auto-Remediation

Opt-in (`monitoring_enabled: true`). Pact-generated code embeds `PACT:<project_hash>:<component_id>` log keys. The Sentinel watches log files, processes, and webhooks for errors, attributes them to components via log keys (or LLM triage), and spawns knowledge-flashed fixer agents that add a reproducer test and rebuild the black box. Multi-window budget caps (per-incident/hourly/daily/weekly/monthly) prevent runaway spend.

**Narrative retry**: On retry attempts, the remediator carries forward prior failures, test results, research reports, and plan evaluations — matching the `implementer.py` pattern. A heroic narrative reframe ("senior engineer brought in because the previous approach failed") prevents the model from falling into the same reasoning rut. `build_narrative_debrief()` is a pure, testable function.

**Budget hypervisor**: `estimate_tokens()` provides content-aware token estimation (symbol ratio → chars/token: 3.5 for code, 4.5 for prose). `record_tokens_validated()` cross-validates reported vs estimated counts using `max()` for conservative accounting. The `claude_code` backend no longer falls back to `len(text) // 4`. The `claude_code_team` backend now tracks spend via estimation.

### Goodhart Tests (Hidden Acceptance Criteria)

During the Test phase, Pact generates two test suites per component: **visible tests** (shown to the implementation agent) and **Goodhart tests** (hidden, never shared with agents). This counters Goodhart's Law: when agents can see all tests, they optimize for passing those specific inputs rather than truly satisfying the contract.

Goodhart tests are adversarial — they probe for hardcoded returns, boundary-adjacent inputs, invariant generalization, and postcondition universality. They live in `tests/<cid>/goodhart/` and are never loaded by `load_all_test_suites()` or `render_handoff_brief()`. Agent isolation is enforced by the orchestrator (what gets fed to agents), not by filesystem hiding.

During the **polish phase**, Goodhart tests run against all implementations. Failures trigger **graduated-disclosure remediation**: the component is re-implemented with behavioral hints (not exact errors) that get more specific on each attempt (max 2 by default). The agent never sees the actual test code.

- **Level 1**: Vague behavioral hint from test description (e.g., "your add() function may not correctly handle the commutative property")
- **Level 2**: Specific contract invariant (e.g., "The contract requires: add(a,b) == add(b,a). Your implementation appears to violate this.")

Config: `max_goodhart_attempts` in pact.yaml (default: 2). Cost: ~$0.07/component for generation (1 LLM call, no research/plan).

### Smoke Test Generation (`pact adopt`)

`pact adopt` generates mechanical smoke tests — no LLM required. AST analysis extracts all public module-level function signatures (filtering out methods, private functions, and nested functions via source line indentation check). Each signature produces an import + callable check test. Output goes to `tests/smoke/` (conventional location). v0.5.1 generates 248 smoke tests for Pact's own codebase.

### Tool Index (ctags / cscope / tree-sitter / kindex)

`analyze_codebase()` enriches analysis with external tools via `build_tool_index()`. All tools optional — skipped silently if not installed.

- **ctags** (universal-ctags): Fast multi-language symbol index. `--output-format=json` with scope, signature, kind.
- **cscope**: Cross-reference database. Call graph (callers/callees). Used for C/C++ codebases.
- **tree-sitter**: Full CST, error-tolerant, cross-language via same API. Preferred over cscope for Python/TypeScript/JavaScript. Extracts function/class definitions with parent scope via CST walking.
- **kindex**: Persistent knowledge graph. Pulls existing project context to avoid rediscovery.

Data flows into: `reverse_engineer_contract()` prompt enrichment, `render_handoff_brief()` tier 2 context. Config: `tool_index_enabled` in pact.yaml (true/false/null=auto).

Install: `brew install universal-ctags cscope` + `pip install pact-agents[analysis]` for tree-sitter.

### Casual-Pace Scheduling

Poll-based, not event-loop. Agents invoked for focused bursts, state fully persisted between bursts.

## Source Layout

```
src/pact/
  schemas.py           # All Pydantic models
  schemas_shaping.py   # Shaping phase models (ShapingPitch, Breadboard, etc.)
  pitch_utils.py       # Pitch summary, formatting, handoff context
  contracts.py         # Contract validation (mechanical gates)
  test_harness.py      # Functional test execution
  design_doc.py        # Living design document
  decomposer.py        # Task -> Contracts workflow
  implementer.py       # Contract -> Code workflow (parallel + competitive)
  integrator.py        # Composition + I/O tracing (parallel depth groups)
  resolution.py        # Competitive resolution (score, pick winner)
  diagnoser.py         # Error recovery
  scheduler.py         # Casual-pace polling + component targeting
  project.py           # Project directory lifecycle + attempt storage
  config.py            # GlobalConfig + ProjectConfig + ParallelConfig
  budget.py            # Per-project spend tracking + content-aware token estimation
  lifecycle.py         # Run state machine
  daemon.py            # Event-driven FIFO-based coordinator
  interface_stub.py    # Interface stub generation + log key preamble
  standards.py         # Global standards collection + rendering
  cli.py               # CLI entry points
  mcp_server.py        # MCP server (FastMCP transport + PactMCPServer handlers)
  tool_index.py        # External tool enrichment (ctags, cscope, tree-sitter, kindex)
  production.py        # Optional production-readiness pack scaffold + validation
  schemas_production.py # Production-readiness artifact schemas

  # Spec-kit capabilities (task list, analysis, checklist)
  schemas_tasks.py     # Task list, analysis, checklist Pydantic models
  task_list.py         # Task list generation + rendering (mechanical, no LLM)
  analyzer.py          # Cross-artifact consistency analysis (mechanical)
  assessor.py          # Architectural assessment engine (any codebase, mechanical)
  schemas_assess.py    # Assessment models (AssessmentReport, ModuleMetrics, etc.)
  checklist_gen.py     # Requirements quality checklist generation (mechanical)

  # Monitoring subsystem
  schemas_monitoring.py # Monitoring models (Signal, Incident, MonitoringBudget, etc.)
  signals.py           # Signal ingestion (LogTailer, ProcessWatcher, WebhookReceiver)
  incidents.py         # Incident lifecycle + multi-window budget enforcement
  remediator.py        # Knowledge-flashed fixer (reproducer test + rebuild + narrative retry)
  sentinel.py          # Long-running monitor coordinator

  agents/
    base.py            # AgentBase (reuses Backend protocol)
    research.py        # Best-practices research + plan evaluation
    contract_author.py # Generates interface contracts
    test_author.py     # Generates functional tests from contracts
    code_author.py     # Implements black boxes (embeds PACT log keys)
    shaper.py          # Shape Up pitch generation agent
    trace_analyst.py   # I/O tracing for diagnosis
    triage.py          # Error-to-component mapping + diagnostic reports

  backends/
    __init__.py        # Backend protocol + factory
    anthropic.py       # Direct API backend
    claude_code.py     # Claude Code CLI backend (validated token tracking)
    claude_code_team.py # Tmux-based full Claude Code agent sessions (budget-aware)
    codex_code.py      # Codex CLI backend
    agent_runtime.py   # Shared Claude/Codex execution envelope

  human/
    __init__.py        # Human integration facade
    linear.py          # Linear issue tracking
    slack.py           # Slack notifications
    git.py             # Git/PR management
```

## Per-Project Directory

All project knowledge is visible in the project tree. Only ephemeral per-run state lives in `.pact/`:

```
<project>/
  task.md              # Task description
  sops.md              # Operating procedures
  pact.yaml            # Per-project config
  design.md            # Living design document (markdown)
  design.json          # Structured design document
  standards.json       # Global standards (auto-generated after decomposition)
  tasks.json           # Phased task list (auto-generated after decomposition)
  analysis.json        # Cross-artifact analysis report
  checklist.json       # Requirements quality checklist
  production/          # Optional production-readiness pack
    manifest.yaml
    prompt.md
    constraints.yaml
    component_map.yaml
    trust_policy.yaml
    control_matrix.yaml
    threat_model.yaml
    architecture_laws.yaml
    preflight.yaml
    live_validation.yaml
    done_gate.yaml
    na_register.yaml
    build_charter.md
    reports/
  TASKS.md             # Rendered task list
  decomposition/       # Decomposition artifacts
    tree.json
    decisions.json
    interview.json
    pitch.json
  contracts/<cid>/     # Interface specs + history
    interface.json
    interface.py (or .ts)
    history/           # Contract version history
  src/<cid>/           # Implementations + glue code
    <cid>.py (or .ts)
  tests/<cid>/         # Contract tests + Goodhart tests
    contract_test.py (or .test.ts)
    contract_test_suite.json
    goodhart/          # Adversarial acceptance tests
      goodhart_test_suite.json
      goodhart_test.py (or .test.ts)
  learnings/           # Accumulated learnings
    learnings.jsonl
  .pact/               # Ephemeral run state only (gitignored)
    state.json         # Run lifecycle state
    audit.jsonl        # All actions + decisions
    contracts/<cid>/   # Contract research (ephemeral)
      research.json
    implementations/<cid>/ # Research, plans, metadata, attempts
    compositions/      # Integration test results
    monitoring/        # Incidents, budget state, diagnostic reports
```

## Key Schemas

| Schema | Purpose |
|--------|---------|
| `DecompositionTree` | Tree of components with traversal (leaves, parallel groups, subtree) |
| `ComponentContract` | Typed interface: functions, types, invariants, dependencies, processing_register |
| `ContractTestSuite` | Executable tests generated from contract |
| `TestResults` | Aggregated pass/fail with failure details |
| `ScoredAttempt` | Competitive attempt with pass rate + duration scoring |
| `RunState` | Mutable lifecycle: phase, status, component tasks, spend |
| `Incident` | Tracked production error with lifecycle (detected→triaging→remediating→resolved/escalated) |
| `MonitoringBudget` | Multi-window spend caps (per-incident, hourly, daily, weekly, monthly) |
| `Signal` | Raw error signal from log file, process, webhook, or manual report |
| `GlobalStandards` | Shared packages, types, conventions distributed to all agents |
| `BuildMode` | StrEnum: unary, auto, hierarchy |
| `Directive` | Structured FIFO command with type + payload |
| `TaskList` | Phased task list with dependency-aware ready_tasks() |
| `AnalysisReport` | Cross-artifact consistency findings (errors, warnings, info) |
| `RequirementsChecklist` | Quality validation questions with tri-state answers |

## Task List & Analysis Commands

```bash
pact tasks <project-dir>                   # Generate/display phased task list
pact tasks <project-dir> --regenerate      # Force regeneration
pact tasks <project-dir> --phase setup     # Filter by phase
pact tasks <project-dir> --component auth  # Filter by component
pact tasks <project-dir> --complete T001   # Mark task as completed
pact tasks <project-dir> --json            # Output as JSON
pact analyze <project-dir>                 # Run cross-artifact analysis
pact analyze <project-dir> --json          # Output as JSON
pact checklist <project-dir>               # Generate requirements checklist
pact checklist <project-dir> --json        # Output as JSON
pact export-tasks <project-dir>            # Export TASKS.md
pact assess <directory>                    # Assess codebase architecture
pact assess <directory> --json             # Output as JSON
pact assess <directory> --threshold K=V    # Override threshold
```

The task list is auto-generated after decomposition and auto-updated after each implementation/integration phase.

### Architectural Assessment

`pact assess` performs mechanical analysis of any Python codebase for structural friction. No LLM required. Detects: shallow modules (low depth ratio), hub dependencies (high fan-in), tight coupling (mutual imports, SCCs), scattered logic (same import in many files), and test coverage gaps.

## Directive Commands

```bash
pact directive <project-dir> resume                                        # Backward-compatible simple string
pact directive <project-dir> '{"type": "set_mode", "mode": "unary"}'       # Change build mode at runtime
pact directive <project-dir> '{"type": "set_config", "key": "value"}'      # Update config keys
pact directive <project-dir> '{"type": "inject_context", "context": "..."}'# Inject context for next agent
```

## Monitoring Commands

```bash
pact watch <project-dir>...           # Start Sentinel monitor (Ctrl+C to stop)
pact report <project-dir> <error>     # Manually report a production error
pact incidents <project-dir>          # List active/recent incidents
pact incident <project-dir> <id>      # Show incident details + diagnostic report
```

## Testing

```bash
make test          # full suite, ~30s
make test-quick    # Stop on first failure
```

## Release Checklist

When asked to release, follow these steps exactly. Do NOT install twine or attempt manual PyPI upload — it's fully automated.

1. Run full test suite: `python3 -m pytest tests/ -v`
2. Bump version in `pyproject.toml` and `src/pact/__init__.py`
3. Commit version bump and all changes
4. Push to main: `git push origin main`
5. Tag: `git tag vX.Y.Z && git push origin vX.Y.Z`
6. Create GitHub release: `gh release create vX.Y.Z --title "..." --notes "..." -R jmcentire/pact`
7. Watch the workflow: `gh run watch <id> -R jmcentire/pact` -- publish job must pass
8. Verify on PyPI: `pip index versions pact-agents 2>/dev/null | head -1` or check https://pypi.org/project/pact-agents/

**Definition of done:** The release is complete when (a) the publish workflow is green, (b) the new version appears on PyPI, and (c) `pip install pact-agents==X.Y.Z` succeeds. If the workflow fails, fix the issue, bump to a new patch version, and repeat from step 1 -- do not re-tag or force-push an existing tag.

## Kindex Knowledge Graph

A persistent knowledge graph (`kin`) indexes conversations, projects, and intellectual work across all repos. It hooks into Claude Code automatically (SessionStart, PreCompact). Docs: https://wandercom.github.io/kindex/

```bash
kin search "pact contracts"      # Hybrid search (FTS + graph)
kin context contracts             # Pull related context
kin add "<insight>"               # Capture discoveries
```

Kindex is an optional knowledge graph for carrying agent decisions, tasks, and
project context across sessions. When Kindex is available, use its persistent
task and knowledge surfaces rather than host-session-only scratch state. Start
or resume a tag, search before adding, use `task_add` / `task_list` /
`task_done` for work that must survive the current conversation, and prefer
`edit` or `supersede` over duplicate nodes. Pact's post-run publishing writes
durable task nodes, not concept-only task stand-ins. Kindex remains optional:
Pact keeps its own run state under `.pact/` and still runs when Kindex is
unavailable.

This closes three concrete failure modes: task-shaped concepts were not durable
Kindex tasks, noninteractive `pact init` could block or EOF on the indexing
prompt, and an approved interview could remain paused until a daemon-specific
recovery path intervened. Existing automation should set `auto_index: true` or
`auto_index: false` in `.kin/config`; `PACT_INTERACTIVE=true|false` overrides
TTY detection when a shell's interactivity is ambiguous.

Tracked `.kin/` files are part of the repository contract:
- `.kin/config` — project metadata, voice, domains, and work policy
- `.kin/index.json` — tracked graph snapshot generated by `kin index`
- `.kin/code-map.json` — repo-relative code map generated by `kin export code-map`
- `.kin/.gitignore` — local/private runtime exclusions only

Tracked artifacts must be self-contained and machine-portable. Never commit a
developer-local path, local-only report pointer, secret, private transcript,
or host-specific scratch reference. Regenerate snapshots from source rather
than hand-editing generated JSON. In noninteractive shells, `pact init`
skips the Kindex indexing prompt unless `.kin/config` explicitly sets
`auto_index: true`.

Refresh tracked Kindex artifacts with:

```bash
kin ingest code --directory . --project-path .
kin index --project-path . --output-dir .
kin export code-map --directory . --project-path . --output .kin/code-map.json
```

## Cross-Agent Engineering Skill

`skills/pact-engineer/SKILL.md` packages the plan-first workflow for Claude and
Codex: Pact planning, active-agent implementation, contract reconciliation,
Advocate review, Simulacrum validation, Kindex capture, and the done gate.
`skills/simulacrum/SKILL.md` exposes Pact's packaged Simulacrum without relying
on a user-specific home-directory installation.
