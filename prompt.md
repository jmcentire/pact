# Pact — System Context

## What It Is
Contract-first multi-agent software engineering framework. Decomposition produces contracts and tests, not code. Black-box implementations verified at boundaries.

## How It Works
10-phase pipeline: Interview -> Shape -> Decompose -> Contract -> Test -> Validate -> Implement -> Integrate -> Polish -> Diagnose. Event-driven via daemon.

## Key Constraints
- Contracts before code (C001)
- Mechanical validation gates (C002)
- Goodhart tests catch gaming (C003)
- Black-box verification only (C004)
- Research before execute (C006)
- State survives crashes (C008)
- Budget enforcement (C009)
- Adopt is non-destructive (C010)

## Architecture
Core: daemon (orchestration), decomposer, contractor, test_author, code_author, validator, integrator, diagnoser. Support: state_manager, mcp_server, wizard, adoption.

## Done Checklist
- [ ] Contract generation precedes all implementation
- [ ] Goodhart tests present for every contract suite
- [ ] Mechanical validation passes (refs, cycles, parsing)
- [ ] State persists across daemon restart
- [ ] Budget tracked and enforced
