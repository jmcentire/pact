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
```

**Entry point**: `pact = "pact.cli:main"` (pyproject.toml)

**Python**: >=3.12 | **Dependencies**: pydantic>=2.0, pyyaml>=6.0 | **Optional**: anthropic>=0.40

## Architecture Overview

### Research-First Agent Protocol

Every agent follows 3 phases: Research -> Plan+Evaluate -> Execute. Research and plan outputs are persisted alongside work products.

### Core Workflow

1. **Interview** -- System reads task+SOPs, identifies risks/ambiguities, asks user clarifying questions
2. **Decompose** -- Task -> DecompositionNode tree (2-7 components)
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

### Casual-Pace Scheduling

Poll-based, not event-loop. Agents invoked for focused bursts, state fully persisted between bursts.

## Source Layout

```
src/pact/
  schemas.py           # All Pydantic models
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
  budget.py            # Per-project spend tracking
  lifecycle.py         # Run state machine
  daemon.py            # Event-driven FIFO-based coordinator
  interface_stub.py    # Interface stub generation
  cli.py               # CLI entry points

  agents/
    base.py            # AgentBase (reuses Backend protocol)
    research.py        # Best-practices research + plan evaluation
    contract_author.py # Generates interface contracts
    test_author.py     # Generates functional tests from contracts
    code_author.py     # Implements black boxes
    trace_analyst.py   # I/O tracing for diagnosis

  backends/
    __init__.py        # Backend protocol + factory
    anthropic.py       # Direct API backend
    claude_code.py     # Claude Code CLI backend
    claude_code_team.py # Tmux-based full Claude Code agent sessions

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
    compositions/      # Integration glue
    learnings/         # Accumulated learnings
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

## Testing

```bash
make test          # 260 tests, ~0.5s
make test-quick    # Stop on first failure
```
