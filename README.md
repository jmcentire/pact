# Pact

**Contracts before code. Tests as law. Agents that can't cheat.**

Pact is a multi-agent software engineering framework where the architecture is decided before a single line of implementation is written. Tasks are decomposed into components, each component gets a typed interface contract, and each contract gets executable tests. Only then do agents implement -- independently, in parallel, even competitively -- with no way to ship code that doesn't honor its contract.

The insight: LLMs are unreliable reviewers but tests are perfectly reliable judges. So make the tests first, make them mechanical, and let agents iterate until they pass. No advisory coordination. No "looks good to me." Pass or fail.

## When to Use Pact

Pact is for projects where **getting the boundaries right matters more than getting the code written fast.** If a single Claude or Codex session can build your feature in one pass, just do that -- Pact's decomposition, contracts, and multi-agent coordination would be pure overhead.

Use Pact when:
- The task has **multiple interacting components** with non-obvious boundaries
- You need **provable correctness at interfaces** -- not "it seems to work" but "it passes 200 contract tests"
- The system will be **maintained by agents** who need contracts to understand what each piece does
- You want **competitive or parallel implementation** where multiple agents race on the same component
- The codebase is large enough that **no single context window can hold it all**

Don't use Pact when:
- A single agent can build the whole thing in one shot
- The task is a bug fix, refactor, or small feature
- You'd spend more time on contracts than on the code itself

## Philosophy: Contracts Are the Product

Pact treats **contracts as source of truth and implementations as disposable artifacts.** The code is cattle, not pets.

When a module fails in production, the response isn't "debug the implementation." It's: add a test that reproduces the failure to the contract, flush the implementation, and let an agent rebuild it. The contract got stricter. The next implementation can't have that bug. Over time, contracts accumulate the scar tissue of every production incident -- they become the real engineering artifact.

This inverts the traditional relationship between code and tests. Code is cheap (agents generate it in minutes). Contracts are expensive (they encode hard-won understanding of what the system actually needs to do). Pact makes that inversion explicit: you spend your time on contracts, agents spend their time on code.

The practical upside: when someone asks "who's debugging this at 3am?" -- agents are. The Sentinel watches production logs, detects errors, attributes them to the right component via embedded PACT log keys, spawns a knowledge-flashed fixer agent loaded with the full contract/test context, adds a reproducer test, rebuilds the module, and verifies all tests pass. The contract ensures they can't introduce regressions. The human reviews the *contract change* in the morning, not the code.

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
Interview -----> Shape (opt) -----> Decompose -----> Contract -----> Test
                    |                   |                 |              |
                    v                   v                 v              v
              Pitch: appetite,    Component Tree    Interfaces     Executable Tests
              breadboard, risks                                        |
                                                                       v
                                         Implement (parallel, competitive)
                                                                       |
                                                                       v
                                         Integrate (glue + parent tests)
                                                                       |
                                                                       v
                                                                 Diagnose (on failure)
```

**Nine phases, all mechanical gates:**

1. **Interview** -- Identify risks, ambiguities, ask clarifying questions
2. **Shape** -- (Optional) Produce a Shape Up pitch: appetite, breadboard, rabbit holes, no-gos
3. **Decompose** -- Task into 2-7 component tree, guided by shaping context if present
4. **Contract** -- Each component gets a typed interface contract
5. **Test** -- Each contract gets executable tests (the enforcement)
6. **Validate** -- Mechanical gate: refs resolve, no cycles, tests parse
7. **Implement** -- Each component built independently by a code agent
8. **Integrate** -- Parent components composed via glue code
9. **Diagnose** -- On failure: I/O tracing, root cause, recovery

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
| `pact status <project> [component]` | Show project or component status |
| `pact components <project>` | List components with status |
| `pact build <project> <id>` | Build/rebuild a specific component |
| `pact interview <project>` | Run interview phase only |
| `pact answer <project>` | Answer interview questions |
| `pact approve <project>` | Approve with defaults |
| `pact validate <project>` | Re-run contract validation |
| `pact design <project>` | Regenerate design.md |
| `pact stop <project>` | Gracefully stop a running daemon |
| `pact log <project>` | Show audit trail (`--tail N`, `--json`) |
| `pact ping` | Test API connection and show pricing |
| `pact signal <project>` | Resume a paused daemon |
| `pact watch <project>...` | Start Sentinel production monitor (Ctrl+C to stop) |
| `pact report <project> <error>` | Manually report a production error |
| `pact incidents <project>` | List active/recent incidents |
| `pact incident <project> <id>` | Show incident details + diagnostic report |

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

# Override token pricing (per million tokens: [input, output])
model_pricing:
  claude-opus-4-6: [15.00, 75.00]
  claude-sonnet-4-5-20250929: [3.00, 15.00]
  claude-haiku-4-5-20251001: [0.80, 4.00]

# Production monitoring (opt-in)
monitoring_enabled: false
monitoring_auto_remediate: true
monitoring_budget:
  per_incident_cap: 5.00
  hourly_cap: 10.00
  daily_cap: 25.00
  weekly_cap: 100.00
  monthly_cap: 300.00
```

**Per-project** (`pact.yaml` in project directory):

```yaml
budget: 25.00
parallel_components: true
competitive_implementations: true
competitive_agents: 3

# Shaping (Shape Up methodology)
shaping: true               # Enable shaping phase (default: false)
shaping_depth: standard      # light | standard | thorough
shaping_rigor: moderate      # relaxed | moderate | strict
shaping_budget_pct: 0.15    # Max budget fraction for shaping

# Production monitoring (per-project)
monitoring_log_files:
  - "/var/log/myapp/app.log"
  - "/var/log/myapp/error.log"
monitoring_process_patterns:
  - "myapp-server"
monitoring_webhook_port: 9876
monitoring_error_patterns:
  - "ERROR"
  - "CRITICAL"
  - "Traceback"
```

Project config overrides global. Both are optional.

### Multi-Provider Configuration

Route different roles to different providers for cost optimization:

```yaml
budget: 50.00

role_models:
  decomposer: claude-opus-4-6        # Strong reasoning for architecture
  contract_author: claude-opus-4-6    # Precision for interfaces
  test_author: claude-sonnet-4-5-20250929  # Fast test generation
  code_author: gpt-4o                # Cost-effective implementation

role_backends:
  decomposer: anthropic
  contract_author: anthropic
  test_author: anthropic
  code_author: openai                # Mix providers per role
```

Available backends: `anthropic`, `openai`, `gemini`, `claude_code`, `claude_code_team`.

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
    monitoring/        # Incidents, budget state, diagnostic reports
```

## Development

```bash
make dev          # Install with LLM backend support
make test         # Run full test suite (950 tests)
make test-quick   # Stop on first failure
make clean        # Remove venv and caches
```

Requires Python 3.12+. Core has two dependencies: `pydantic` and `pyyaml`. LLM backends require `anthropic`.

## Architecture

See [CLAUDE.md](CLAUDE.md) for the full technical reference.

## Background

Pact is one of three systems (alongside Emergence and Apprentice) built to test
the ideas in [Beyond Code: Context, Constraints, and the New Craft of Software](https://www.amazon.com/dp/B0GNLTXVC7).
The book covers the coordination, verification, and specification problems that
motivated Pact's design.

## License

MIT
