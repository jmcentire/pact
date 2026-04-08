# Pact Capability Inventory

Machine-readable capability reference for AI agents and toolchains.
For human-readable docs, see [README.md](README.md). For full technical reference, see [CLAUDE.md](CLAUDE.md).

## Quick Selector

**I want to...** | **Use**
--- | ---
Build a multi-component project from scratch | `pact init` + `pact run`
Adopt an existing codebase under contracts | `pact adopt`
Analyze architecture for friction before building | `pact assess`
Check contract/test consistency after decomposition | `pact analyze`
Validate requirements quality | `pact checklist`
Generate tests for untested code | `pact test-gen`
Build one specific component | `pact build <project> <id>`
Monitor coordination health | `pact health`
Integrate with Claude Code | `pact mcp-server`

## Capabilities

### 1. Pipeline Orchestration

**Command:** `pact run <project-dir> [--once] [--plan-only]`
**When:** You have a task.md describing what to build and want the full pipeline.
**Requires:** LLM backend (Anthropic, OpenAI, Gemini, Claude Code).
**Phases:** Interview -> Shape -> Decompose -> Contract -> Test -> Validate -> Implement -> Integrate -> Polish -> Diagnose.
**Output:** Fully implemented, tested components in `src/<cid>/`.
**Cost:** $5-50 depending on complexity and model.

### 2. Architectural Assessment

**Command:** `pact assess <directory> [--json] [--threshold KEY=VALUE]`
**When:** You want to understand structural friction before building or refactoring. Works on any Python codebase -- no pact project required.
**Requires:** Nothing. Mechanical analysis using stdlib `ast`. No LLM.
**Detects:**
- Hub dependencies (high fan-in, modules many others depend on)
- Shallow modules (interface complexity rivals implementation)
- Tight coupling (mutual imports, circular dependency clusters via Tarjan's SCC)
- Scattered logic (same intra-project import in many files)
- Test coverage gaps (source modules without test files)
**Output:** Markdown report or JSON with per-module metrics (LOC, depth ratio, fan-in/fan-out).
**Cost:** Free. Runs in <1s on typical codebases.
**Thresholds:** `shallow_depth_ratio` (default 5.0), `hub_fan_in_warning` (8), `hub_fan_in_error` (15), `scattered_import_info` (5), `scattered_import_warning` (10).

### 3. Codebase Adoption

**Command:** `pact adopt <project-dir> [--language python|typescript|rust] [--dry-run]`
**When:** You have existing code and want smoke tests + contract scaffolding.
**Requires:** LLM backend for contract reverse-engineering. Smoke tests are mechanical (no LLM).
**Output:** `tests/smoke/` with import + callable checks for every public function. Optionally reverse-engineered contracts.
**Cost:** Free for smoke tests; ~$2-5 for contract reverse-engineering.

### 4. Cross-Artifact Analysis

**Command:** `pact analyze <project-dir> [--json]`
**When:** After decomposition, to validate consistency across contracts, tests, and tree.
**Requires:** A pact project with decomposition artifacts.
**Checks:** Coverage gaps, ambiguity, duplication, consistency, completeness.
**Output:** AnalysisReport with findings (error/warning/info).
**Cost:** Free. Mechanical.

### 5. Requirements Checklist

**Command:** `pact checklist <project-dir> [--json]`
**When:** To validate that contracts have sufficient error handling, edge cases, testability.
**Requires:** A pact project with contracts and test suites.
**Output:** RequirementsChecklist with tri-state items (satisfied/unsatisfied/unanswered).
**Cost:** Free. Mechanical.

### 6. Test Generation

**Command:** `pact test-gen <project-dir> [--language] [--dry-run] [--budget]`
**When:** You want LLM-generated tests for uncovered code, guided by complexity analysis.
**Requires:** LLM backend. Codebase with analyzable source.
**Output:** Generated test files targeting highest-complexity uncovered functions.
**Cost:** ~$1-10 depending on scope.

### 7. Health Monitoring

**Command:** `pact health <project-dir>`
**When:** During or after a run, to check coordination health.
**Requires:** A pact project with run state.
**Metrics:** Output/planning ratio, rejection rate, budget velocity, phase balance, cascade detection, register drift.
**Output:** Health report with findings and proposed remedies.
**Cost:** Free. Mechanical.

### 8. Component Building

**Command:** `pact build <project-dir> <component-id>`
**When:** You want to implement or re-implement a specific component after plan-only decomposition.
**Requires:** LLM backend. Existing decomposition with contracts.
**Output:** Implementation in `src/<cid>/` passing contract tests.
**Cost:** ~$1-5 per component.

### 9. MCP Server

**Command:** `pact mcp-server [--project-dir <dir>]` or `pact-mcp`
**When:** Integrating pact into Claude Code or other MCP-compatible editors.
**Protocol:** stdio transport, FastMCP.
**Tools:** status, contracts, budget, validate, resume, components, build.
**Install:** `pip install pact-agents[mcp]`

### 10. Task Planning

**Command:** `pact tasks <project-dir> [--phase] [--component] [--complete TASK_ID]`
**When:** After decomposition, to see phased task list with dependencies.
**Output:** Phased task list (setup -> foundational -> component -> integration -> polish).
**Cost:** Free. Mechanical.

## Integration Points

| System | Direction | Mechanism |
|--------|-----------|-----------|
| Constrain | Upstream | `--constrain-dir` seeds decomposition with constraints |
| Arbiter | Gate | POSTs `access_graph.json` for blast radius analysis |
| Ledger | Upstream | `--ledger-dir` loads field-level audit assertions |
| Sentinel | Downstream | PACT log keys for production attribution |
| Kindex | Bidirectional | Knowledge graph context for agents + post-run capture |

## Language Support

| Language | Pipeline | Assess | Adopt | Test-Gen |
|----------|----------|--------|-------|----------|
| Python | Full | Full | Full | Full |
| TypeScript | Full | Planned | Full | Planned |
| Rust | Full | Planned | Full | Planned |
| JavaScript | Full | Planned | Partial | Planned |

## Decision Guide for AI Agents

- **Starting fresh?** `pact init` + edit task.md/sops.md + `pact run`
- **Existing codebase, want governance?** `pact adopt` first, then `pact run`
- **Want to understand architecture before touching code?** `pact assess`
- **Already decomposed, want quality checks?** `pact analyze` + `pact checklist`
- **Single component needs rebuilding?** `pact build <project> <id>`
- **Pipeline stalled?** `pact health` + `pact directive <project> resume`
- **Need tests for uncovered code?** `pact test-gen`
- **Working in Claude Code?** Use `pact mcp-server` for editor integration
