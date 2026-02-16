# CLAUDE.md -- Pact

Contract-first multi-agent software engineering. Decomposition produces contracts and tests, not code. Black-box implementations verified by functional tests at boundaries. Recursive composition.

## Quick Reference

```bash
cd ~/WanderRepos/pact
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
```

**Entry point**: `pact = "pact.cli:main"` (pyproject.toml)

**Python**: >=3.12 | **Dependencies**: pydantic>=2.0, pyyaml>=6.0 | **Optional**: anthropic>=0.40

## Architecture Overview

### Research-First Agent Protocol

Every agent follows 3 phases: Research -> Plan+Evaluate -> Execute. Research and plan outputs are persisted alongside work products.

### Core Workflow

1. **Interview** -- System reads task+SOPs, identifies risks/ambiguities, asks user clarifying questions
2. **Shape** -- (Optional) Produce a Shape Up pitch: appetite, breadboard, rabbit holes, no-gos
3. **Decompose** -- Task -> DecompositionNode tree (2-7 components), guided by shaping context
3. **Contract** -- For each component (leaves first), generate ComponentContract
4. **Test** -- For each contract, generate ContractTestSuite with executable tests
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

### Production Monitoring & Auto-Remediation

Opt-in (`monitoring_enabled: true`). Pact-generated code embeds `PACT:<project_hash>:<component_id>` log keys. The Sentinel watches log files, processes, and webhooks for errors, attributes them to components via log keys (or LLM triage), and spawns knowledge-flashed fixer agents that add a reproducer test and rebuild the black box. Multi-window budget caps (per-incident/hourly/daily/weekly/monthly) prevent runaway spend.

**Narrative retry**: On retry attempts, the remediator carries forward prior failures, test results, research reports, and plan evaluations — matching the `implementer.py` pattern. A heroic narrative reframe ("senior engineer brought in because the previous approach failed") prevents the model from falling into the same reasoning rut. `build_narrative_debrief()` is a pure, testable function.

**Budget hypervisor**: `estimate_tokens()` provides content-aware token estimation (symbol ratio → chars/token: 3.5 for code, 4.5 for prose). `record_tokens_validated()` cross-validates reported vs estimated counts using `max()` for conservative accounting. The `claude_code` backend no longer falls back to `len(text) // 4`. The `claude_code_team` backend now tracks spend via estimation.

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
    contracts/         # Per-component contracts + tests
    implementations/   # Per-component code + attempts/
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
| `ComponentContract` | Typed interface: functions, types, invariants, dependencies |
| `ContractTestSuite` | Executable tests generated from contract |
| `TestResults` | Aggregated pass/fail with failure details |
| `ScoredAttempt` | Competitive attempt with pass rate + duration scoring |
| `RunState` | Mutable lifecycle: phase, status, component tasks, spend |
| `Incident` | Tracked production error with lifecycle (detected→triaging→remediating→resolved/escalated) |
| `MonitoringBudget` | Multi-window spend caps (per-incident, hourly, daily, weekly, monthly) |
| `Signal` | Raw error signal from log file, process, webhook, or manual report |
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

## Monitoring Commands

```bash
pact watch <project-dir>...           # Start Sentinel monitor (Ctrl+C to stop)
pact report <project-dir> <error>     # Manually report a production error
pact incidents <project-dir>          # List active/recent incidents
pact incident <project-dir> <id>      # Show incident details + diagnostic report
```

## Testing

```bash
make test          # 1000 tests, ~5s
make test-quick    # Stop on first failure
```
