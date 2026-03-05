# CLAUDE.md -- Pact

Contract-first multi-agent software engineering. Decomposition produces contracts and tests, not code. Black-box implementations verified by functional tests at boundaries. Recursive composition.

## Quick Reference

```bash
cd ~/Code/pact
python3 -m pytest tests/ -v        # Run all tests
pact init <project-dir>            # Initialize project
pact status <project-dir>          # Show state
pact components <project-dir>      # List components
pact build <project-dir> <id>      # Build specific component
pact run <project-dir>             # Execute pipeline
pact tasks <project-dir>           # Generate/display task list
pact analyze <project-dir>         # Cross-artifact analysis
pact checklist <project-dir>       # Requirements quality checklist
pact export-tasks <project-dir>    # Export TASKS.md
pact directive <project-dir> <json> # Send structured directive to daemon
```

**Entry point**: `pact = "pact.cli:main"` (pyproject.toml)

**Python**: >=3.12 | **Dependencies**: pydantic>=2.0, pyyaml>=6.0 | **Optional**: anthropic>=0.40

## Architecture Overview

### Research-First Agent Protocol

Every agent follows 3 phases: Research -> Plan+Evaluate -> Execute. Research and plan outputs are persisted alongside work products.

### Core Workflow

1. **Interview** -- Establishes processing register (cognitive mode), then identifies risks/ambiguities, asks user clarifying questions
2. **Shape** -- (Optional) Produce a Shape Up pitch: appetite, breadboard, rabbit holes, no-gos
3. **Decompose** -- Task -> DecompositionNode tree (2-7 components), guided by shaping context
3. **Contract** -- For each component (leaves first), generate ComponentContract
4. **Test** -- For each contract, generate ContractTestSuite with executable tests + hidden Goodhart tests
5. **Validate** -- Mechanical gate: all refs resolve, no cycles, test code parses
6. **Implement** -- Each component independently by code_author agent, verified by contract tests
7. **Integrate** -- Parent components: glue code wiring children, parent-level tests
8. **Diagnose** -- On failure: I/O tracing, systematic error recovery

### Execution Modes

Two independent levers:
- `parallel_components: true` -- Independent leaves implement concurrently (semaphore-limited)
- `competitive_implementations: true` -- N agents implement same component, best wins
- `plan_only: true` -- Stop after contracts, use `pact build` to target specific nodes
- `max_concurrent_agents: 4` -- Concurrency limit for parallel modes

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

Standards are injected into every agent's handoff brief and persisted at `.pact/standards.json`.

### Processing Register

Research (Papers 35-39) established that LLM representations follow a hierarchy: register (processing mode) > domain > structural shape. Register is the hub that domain anchors to. Pact makes this explicit:

- **Establishment**: Interview phase determines the processing register before any domain analysis (e.g., "rigorous-analytical", "exploratory-generative", "systematic-verification")
- **Propagation**: Register is a first-class field on `ComponentContract` and flows through the handoff protocol: reset → prime register → prime domain
- **Override**: Set `processing_register` in `pact.yaml` to skip LLM establishment
- **Monitoring**: Health system tracks register drift — agents departing from their established processing mode — as an early indicator of coordination failure

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

Goodhart tests are adversarial — they probe for hardcoded returns, boundary-adjacent inputs, invariant generalization, and postcondition universality. They live in `.pact/contracts/<cid>/goodhart/` and are never loaded by `load_all_test_suites()` or `render_handoff_brief()`.

During the **polish phase**, Goodhart tests run against all implementations. Failures trigger **graduated-disclosure remediation**: the component is re-implemented with behavioral hints (not exact errors) that get more specific on each attempt (max 2 by default). The agent never sees the actual test code.

- **Level 1**: Vague behavioral hint from test description (e.g., "your add() function may not correctly handle the commutative property")
- **Level 2**: Specific contract invariant (e.g., "The contract requires: add(a,b) == add(b,a). Your implementation appears to violate this.")

Config: `max_goodhart_attempts` in pact.yaml (default: 2). Cost: ~$0.07/component for generation (1 LLM call, no research/plan).

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

  # Spec-kit capabilities (task list, analysis, checklist)
  schemas_tasks.py     # Task list, analysis, checklist Pydantic models
  task_list.py         # Task list generation + rendering (mechanical, no LLM)
  analyzer.py          # Cross-artifact consistency analysis (mechanical)
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

  human/
    __init__.py        # Human integration facade
    linear.py          # Linear issue tracking
    slack.py           # Slack notifications
    git.py             # Git/PR management
```

## Per-Project Directory

```
<project>/
  task.md              # Task description
  sops.md              # Operating procedures
  pact.yaml            # Per-project config
  design.md            # Living design document
  .pact/
    state.json         # Run lifecycle state
    audit.jsonl        # All actions + decisions
    decomposition/     # Tree + decisions
    contracts/         # Per-component contracts + tests + goodhart/
    implementations/   # Per-component code + attempts/
    standards.json     # Global standards (auto-generated after decomposition)
    tasks.json         # Phased task list (auto-generated after decomposition)
    analysis.json      # Cross-artifact analysis report
    checklist.json     # Requirements quality checklist
    compositions/      # Integration glue
    learnings/         # Accumulated learnings
    monitoring/        # Incidents, budget state, diagnostic reports
      incidents.json   # All incidents with lifecycle state
      budget.json      # Running budget totals per window
      reports/         # Per-incident diagnostic reports (markdown)
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
```

The task list is auto-generated after decomposition and auto-updated after each implementation/integration phase.

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
make test          # 1617 tests, ~7s
make test-quick    # Stop on first failure
```

## Kindex Knowledge Graph

A persistent knowledge graph (`kin`) indexes conversations, projects, and intellectual work across all repos. It hooks into Claude Code automatically (SessionStart, PreCompact). Docs: https://jmcentire.github.io/kindex/

```bash
kin search "pact contracts"      # Hybrid search (FTS + graph)
kin context contracts             # Pull related context
kin add "<insight>"               # Capture discoveries
```

Legacy Conv vault (459 nodes, richer historical data): `~/Personal/Projects/Conv/`
