# PACT Key Standard

**Canonical reference for PACT key format and usage across the stack.**

Cross-referenced from: [Sentinel](https://github.com/jmcentire/sentinel), [Cartographer](https://github.com/jmcentire/cartographer)

## Format

```
PACT:<component_id>:<method_name>
```

## Rules

| Field | Pattern | Example |
|-------|---------|---------|
| Prefix | `PACT:` (uppercase, literal) | `PACT:` |
| component_id | `[a-zA-Z0-9_]+` | `auth_module` |
| method_name | `[a-zA-Z0-9_]+` | `validate_token` |

**Regex**: `PACT:[a-zA-Z0-9_]+:[a-zA-Z0-9_]+`

## Examples

```
PACT:auth_module:validate_token
PACT:payment_processor:charge_card
PACT:user_service:create_account
PACT:notification_handler:send_email
```

## Embedding

PACT keys are embedded in generated source code by Pact at implementation time. They must be **string literals** (not computed) so that Sentinel and Cartographer can discover them via static analysis (AST walking or regex).

### Python

```python
self._emit({
    "pact_key": "PACT:auth_module:validate_token",
    "event": "completed",
    "output_classification": ["PII"],
    "side_effects": ["database_read"],
    "duration_ms": elapsed,
})
```

### TypeScript

```typescript
this.emit({
    pact_key: "PACT:auth_module:validate_token",
    event: "completed",
    output_classification: ["PII"],
    side_effects: ["database_read"],
    duration_ms: elapsed,
});
```

## Who Produces

**Pact** embeds PACT keys during the Implement phase (phase 5). The `code_author` agent includes keys as string literals in every public method of every generated component. Emission compliance tests are auto-generated from the contract interface to verify all expected keys are present.

## Who Consumes

| Tool | How | Purpose |
|------|-----|---------|
| **Sentinel** | Regex extraction from production logs | Error attribution to component + method |
| **Cartographer** | AST walking + regex during discovery | Detect existing instrumentation in brownfield codebases |
| **Arbiter** | Via access_graph.json (component_id field) | Trust scoring per component |

## Validation

Cartographer's Pact compatibility checker validates:
- All substantive source modules contain at least one PACT key
- Keys match the canonical regex format
- component_id in keys matches registered manifest entries
- No malformed or partial keys

## Emission Payload

The full emission payload alongside a PACT key should include:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `pact_key` | string | yes | The PACT key literal |
| `event` | string | yes | `"started"`, `"completed"`, `"error"` |
| `output_classification` | list[string] | no | Data tiers touched: `PUBLIC`, `PII`, `FINANCIAL`, `AUTH`, `COMPLIANCE` |
| `side_effects` | list[string] | no | Side effects performed: `database_read`, `database_write`, `api_call`, etc. |
| `duration_ms` | number | no | Execution time in milliseconds |
| `error` | string | no | Error message (when event is `"error"`) |

## History

- **v1** (2026-03-15): Initial standard. Format: `PACT:<component_id>:<method_name>`.
