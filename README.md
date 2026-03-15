# Pact

**Contracts before code. Tests as law. Agents that can't cheat.**

Pact is a multi-agent software engineering framework where the architecture is decided before a single line of implementation is written. Tasks are decomposed into components, each component gets a typed interface contract, and each contract gets executable tests. Only then do agents implement -- independently, in parallel, even competitively -- with no way to ship code that doesn't honor its contract. Generates Python, TypeScript, or JavaScript.

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

## Benchmark: ICPC World Finals

Tested on 5 ICPC World Finals competitive programming problems (212 test cases total) using Claude Opus 4.6.

| Condition | Pass Rate | Cost |
|-----------|-----------|------|
| Claude Code single-shot | 167/212 (79%) | $0.60 |
| Claude Code iterative (5 attempts) | 196/212 (92%) | $1.26 |
| Pact (solo, noshape) | **212/212 (100%)** | ~$13 |

Pact's contract-first pipeline solves problems that iterative prompting cannot. On **Trailing Digits** (2020 World Finals), Claude Code scores 31/47 even with 5 retry iterations and full test feedback -- the naive algorithm times out on large inputs. Pact's interview and decomposition phases force upfront mathematical analysis, producing the correct O(log n) approach on the first implementation attempt.

Full results: [icpc_official/RESULTS.md](https://github.com/jmcentire/pact/blob/main/benchmarks/icpc_official/RESULTS.md) in the benchmark directory.

## Philosophy: Contracts Are the Product

Pact treats **contracts as source of truth and implementations as disposable artifacts.** The code is cattle, not pets.

When a module fails in production, the response isn't "debug the implementation." It's: add a test that reproduces the failure to the contract, flush the implementation, and let an agent rebuild it. The contract got stricter. The next implementation can't have that bug. Over time, contracts accumulate the scar tissue of every production incident -- they become the real engineering artifact.

This inverts the traditional relationship between code and tests. Code is cheap (agents generate it in minutes). Contracts are expensive (they encode hard-won understanding of what the system actually needs to do). Pact makes that inversion explicit: you spend your time on contracts, agents spend their time on code.

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
Interview --> Shape (opt) --> Decompose --> Contract --> Test
                                                          |
                                                          v
                                    Implement (parallel, competitive)
                                                          |
                                                          v
                                    Integrate (glue + parent tests)
                                                          |
                                                          v
                                    Arbiter Gate (access graph + trust)
                                                          |
                                                          v
                                    Polish (Goodhart tests + regression)
                                                          |
                                                          v
                                    Certify (tamper-evident proof)
```

**Nine phases** (plus diagnose as a recovery state):

1. **Interview** -- Establish processing register, then identify risks, ambiguities, ask clarifying questions
2. **Shape** -- (Optional) Produce a Shape Up pitch: appetite, breadboard, rabbit holes, no-gos
3. **Decompose** -- Task into 2-7 component tree, guided by shaping context if present. Contract generation, test authoring (including emission compliance tests), and Goodhart adversarial tests all happen here.
4. **Implement** -- Each component built independently by a code agent with structured event emission
5. **Integrate** -- Parent components composed via glue code
6. **Arbiter** -- Generate access_graph.json, register with Arbiter for blast radius analysis. HUMAN_GATE pauses pipeline.
7. **Polish** -- Cross-component regression check + Goodhart test evaluation with graduated-disclosure remediation
8. **Complete** -- Certification with tamper-evident proof (SHA-256 hashes)

**Diagnose** is not a numbered phase — it's a recovery state. On failure at any phase, the system enters diagnose for I/O tracing, root cause analysis, and recovery routing back to implement.

## Stack Integration

Pact is the contract-first build system in a larger stack:

| Tool | Role | Pact's Relationship |
|------|------|-------------------|
| **Constrain** | Upstream policy | `--constrain-dir` seeds decomposition with constraints, component maps, trust policies |
| **Arbiter** | Trust gate | Phase 8.5 POSTs `access_graph.json` for blast radius analysis. HUMAN_GATE pauses pipeline |
| **Ledger** | Field-level audit | `--ledger-dir` loads assertions into contract test suites as hard requirements |
| **Sentinel** | Production monitoring | Separate package. Pact embeds PACT keys for attribution. `pact sentinel push-contract` accepts tightened contracts |

All integrations are optional. Without them, Pact operates as a standalone build system.

## Contract Schema

Every contract includes:

```yaml
data_access:
  reads: [PUBLIC, PII]
  writes: [PUBLIC]
  rationale: "Reads user.email for personalization, writes public analytics events"
  side_effects:
    - type: database_read
      classification: PII
      fields: ["user.email", "user.created_at"]
      rationale: "Fetch user profile for display"

authority:
  domains: ["user_profile"]
  rationale: "Authoritative source for user profile data within this service"
```

Anti-cliche enforcement rejects vague rationale strings ("handles data", "manages stuff"). Rationale must describe the specific data accessed and why.

## Audit Repo Separation

Pact supports a two-repo separation-of-privilege model where the coding agent and auditing agent operate in different repositories:

```bash
pact audit-init ./my-project --audit-dir ./my-project-audit
pact sync ./my-project          # Sync visible tests (never Goodhart) to code repo
pact certify ./my-project       # Tamper-evident certification proof
```

The coding agent cannot modify the tests that judge its work. The certification artifact includes SHA-256 hashes of all contracts, tests, and implementations with a self-integrity hash.

## Structured Event Emission

All implementations accept optional `event_handler` and `log_handler`. Every public method emits structured events:

```python
self._emit({
    "pact_key": "PACT:auth_module:validate_token",
    "event": "completed",
    "output_classification": ["PII"],
    "side_effects": ["database_read"],
    "ts": time.time_ns()
})
```

PACT keys are string literals (not computed) so Sentinel can discover them via static analysis. Emission compliance tests are auto-generated from the contract interface. See [PACT_KEY_STANDARD.md](PACT_KEY_STANDARD.md) for the canonical format specification.

## Health Monitoring

Pact monitors its own coordination health -- detecting the specific failure modes of agentic pipelines before they consume the budget.

| Metric | What It Detects |
|--------|-----------------|
| **Output/planning ratio** | Spending $50 on planning and shipping nothing |
| **Rejection rate** | Agents optimizing for each other's approval, not outcomes |
| **Budget velocity** | Coordination cost exceeding execution value |
| **Phase balance** | Any single phase consuming disproportionate budget |
| **Cascade detection** | One component's failure propagating through the tree |
| **Register drift** | Agent departing from established processing mode mid-task |

```bash
pact health my-project
```

## Two Execution Levers

| Lever | Config Key | Effect |
|-------|-----------|--------|
| **Parallel Components** | `parallel_components: true` | Independent components implement concurrently |
| **Competitive Implementations** | `competitive_implementations: true` | N agents implement the SAME component; best wins |

Either, neither, or both. Defaults: both off (sequential, single-attempt).

## CLI Commands

| Command | Purpose |
|---------|---------|
| `pact init <project>` | Scaffold a new project |
| `pact run <project>` | Run the pipeline |
| `pact daemon <project>` | Event-driven mode (recommended) |
| `pact status <project>` | Show project or component status |
| `pact components <project>` | List components with status |
| `pact build <project> <id>` | Build/rebuild a specific component |
| `pact validate <project>` | Re-run contract validation |
| `pact audit <project>` | Spec-compliance audit |
| `pact certify <project>` | Run certification (all tests, tamper-evident proof) |
| `pact audit-init <project>` | Initialize audit repo separation |
| `pact sync <project>` | Sync visible tests from audit repo |
| `pact sentinel status` | Show Sentinel/Arbiter connection config |
| `pact sentinel push-contract <id> <file>` | Accept tightened contract from Sentinel |
| `pact sentinel list-keys` | List all PACT keys in project |
| `pact health <project>` | Show health metrics and proposed remedies |
| `pact tasks <project>` | List phase tasks with status |
| `pact handoff <project> <id>` | Render/validate handoff brief |
| `pact adopt <project>` | Adopt existing codebase under pact governance |
| `pact mcp-server` | Run MCP server (stdio transport) |

Run flags: `--constrain-dir`, `--ledger-dir`, `--skip-arbiter`.

## Configuration

**Per-project** (`pact.yaml` in project directory):

```yaml
budget: 25.00
parallel_components: true
competitive_implementations: true

# Stack integration (all optional)
constrain_dir: ./constrain-output/
ledger_dir: ./ledger-export/
arbiter_endpoint: http://localhost:8080
skip_arbiter: false

# Audit repo separation
audit_dir: ../my-project-audit
audit_mode: code    # "audit" | "code" | ""

# Shaping
shaping: true
shaping_depth: standard

# Health thresholds
health_thresholds:
  output_planning_ratio_warning: 0.3
  rejection_rate_critical: 0.9
```

### Multi-Provider Configuration

Route different roles to different providers for cost optimization:

```yaml
role_models:
  decomposer: claude-opus-4-6
  contract_author: claude-opus-4-6
  test_author: claude-sonnet-4-5-20250929
  code_author: gpt-4o

role_backends:
  decomposer: anthropic
  code_author: openai
```

Available backends: `anthropic`, `openai`, `gemini`, `claude_code`, `claude_code_team`.

## Project Structure

```
my-project/
  task.md              # What to build
  sops.md              # How to build it
  pact.yaml            # Budget and config
  access_graph.json    # Data access graph (consumed by Arbiter)
  decomposition/       # Decomposition tree, decisions, interview
  contracts/<cid>/     # Interface specs with data_access + authority
  src/<cid>/           # Implementation source + glue code
  tests/<cid>/         # Contract tests + Goodhart tests
  certification/       # Tamper-evident certification proof
  .pact/               # Ephemeral run state (gitignored)
```

## MCP Server

```bash
pip install pact-agents[mcp]
pact-mcp
```

7 tools for Claude Code integration: status, contracts, budget, validate, resume.

## Development

```bash
make dev          # Install with LLM backend support
make test         # Run full test suite (1766 tests)
make test-quick   # Stop on first failure
```

Requires Python 3.12+. Core dependencies: `pydantic` and `pyyaml`.

## Architecture

See [CLAUDE.md](CLAUDE.md) for the full technical reference.

## Background

Pact is one of three systems (alongside Emergence and Apprentice) built to test
the ideas in [Beyond Code: Context, Constraints, and the New Craft of Software](https://www.amazon.com/dp/B0GNLTXVC7).

## Related

- [Baton](https://jmcentire.github.io/baton/) -- Circuit orchestration for contract-first components
- [Sentinel](https://github.com/jmcentire/sentinel) -- Production attribution and contract tightening

## License

MIT
