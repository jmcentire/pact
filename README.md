# Pact

**Contracts before code. Tests as law. Agents that can't cheat.**

Pact is a multi-agent software engineering framework where the architecture is decided before a single line of implementation is written. Tasks are decomposed into components, each component gets a typed interface contract, and each contract gets executable tests. Only then do agents implement -- independently, in parallel, even competitively -- with no way to ship code that doesn't honor its contract.

The insight: LLMs are unreliable reviewers but tests are perfectly reliable judges. So make the tests first, make them mechanical, and let agents iterate until they pass. No advisory coordination. No "looks good to me." Pass or fail.

## Quick Start

```bash
git clone https://github.com/jmcentire/pact.git
cd pact
make
source .venv/bin/activate
```

That's it. Now try:

```bash
pact init my-project
# Edit my-project/task.md with your task
# Edit my-project/sops.md with your standards
pact --help
```

## How It Works

```
Task
  |
  v
Interview -----> Decompose -----> Contract -----> Test
                    |                 |              |
                    v                 v              v
              Component Tree    Interfaces     Executable Tests
                                                    |
                                                    v
                              Implement (parallel, competitive)
                                                    |
                                                    v
                              Integrate (glue + parent tests)
                                                    |
                                                    v
                                              Diagnose (on failure)
```

**Eight phases, all mechanical gates:**

1. **Interview** -- Identify risks, ambiguities, ask clarifying questions
2. **Decompose** -- Task into 2-7 component tree
3. **Contract** -- Each component gets a typed interface contract
4. **Test** -- Each contract gets executable tests (the enforcement)
5. **Validate** -- Mechanical gate: refs resolve, no cycles, tests parse
6. **Implement** -- Each component built independently by a code agent
7. **Integrate** -- Parent components composed via glue code
8. **Diagnose** -- On failure: I/O tracing, root cause, recovery

## Two Execution Levers

| Lever | Config Key | Effect |
|-------|-----------|--------|
| **Parallel Components** | `parallel_components: true` | Independent components implement concurrently |
| **Competitive Implementations** | `competitive_implementations: true` | N agents implement the SAME component; best wins |

Either, neither, or both. Defaults: both off (sequential, single-attempt).

## Plan-Only Mode

Set `plan_only: true` to stop after contracts and tests are generated. Then target specific components:

```bash
pact components my-project              # See what was decomposed
pact build my-project sync_tracker      # Build one component
pact build my-project sync_tracker --competitive --agents 3
```

## CLI Commands

| Command | Purpose |
|---------|---------|
| `pact init <project>` | Scaffold a new project |
| `pact run <project>` | Run the pipeline |
| `pact daemon <project>` | Event-driven mode (recommended) |
| `pact status <project>` | Show current state |
| `pact components <project>` | List components with status |
| `pact build <project> <id>` | Build/rebuild a specific component |
| `pact interview <project>` | Run interview phase only |
| `pact answer <project>` | Answer interview questions |
| `pact approve <project>` | Approve with defaults |
| `pact validate <project>` | Re-run contract validation |
| `pact design <project>` | Regenerate design.md |
| `pact signal <project>` | Resume a paused daemon |

## Configuration

**Global** (`config.yaml` at repo root):

```yaml
model: claude-opus-4-6
default_budget: 10.00
parallel_components: false
competitive_implementations: false
competitive_agents: 2
max_concurrent_agents: 4
plan_only: false
```

**Per-project** (`pact.yaml` in project directory):

```yaml
budget: 25.00
parallel_components: true
competitive_implementations: true
competitive_agents: 3
```

Project config overrides global. Both are optional.

## Project Structure

Each project is a self-contained directory:

```
my-project/
  task.md              # What to build
  sops.md              # How to build it (standards, stack, preferences)
  pact.yaml            # Budget and execution config
  design.md            # Auto-maintained design document
  .pact/
    state.json         # Run lifecycle
    audit.jsonl        # Full audit trail
    decomposition/     # Tree + decisions
    contracts/         # Per-component interfaces + tests
    implementations/   # Per-component code
    compositions/      # Integration glue
    learnings/         # Accumulated learnings
```

## Development

```bash
make dev          # Install with LLM backend support
make test         # Run full test suite (260 tests)
make test-quick   # Stop on first failure
make clean        # Remove venv and caches
```

Requires Python 3.12+. Core has two dependencies: `pydantic` and `pyyaml`. LLM backends require `anthropic`.

## Architecture

See [CLAUDE.md](CLAUDE.md) for the full technical reference.

## License

MIT
