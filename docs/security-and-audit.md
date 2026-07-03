# Security, Guardrails & Audit

This server ships with a configurable security layer so operators can limit which
tools an MCP client may use, throttle activity, and keep a durable audit trail.
Every feature is **opt-in** — with no extra configuration the server behaves
exactly as before (all tools enabled, structured logging to stderr).

## Roles

Set `BUILDIUM_ROLE` to one of the built-in roles:

| Role | Permitted tools |
| --- | --- |
| `readonly` | Only `list_*` / `get_*` (read) tools |
| `operator` | Reads plus **non-sensitive** writes |
| `admin` (default) | All tools |
| `custom` | Nothing implied by role; shaped by the allow/deny lists |

**Sensitive** tools are those touching financial resources: bills, bank
accounts, general ledger, payments, and file upload/download URL issuance.

## Guardrails

These compose with the role. The most restrictive rule wins, and the deny list
always wins.

| Env var | Effect |
| --- | --- |
| `BUILDIUM_READONLY=true` | Disables every mutating tool, regardless of role |
| `BUILDIUM_BLOCK_SENSITIVE=true` | Disables all sensitive tools |
| `BUILDIUM_ALLOW_TOOLS=a,b` | Strict whitelist of tool names (overrides the role) |
| `BUILDIUM_DENY_TOOLS=a,b` | Blacklist of tool names (always removed) |
| `BUILDIUM_RATE_LIMIT_PER_MINUTE=N` | Caps invocations per rolling 60s window (0 = off) |

Forbidden tools are **not registered**, so they are never advertised to the MCP
client. A defense-in-depth check also runs at call time and returns a
`forbidden` error envelope if a disabled tool is somehow invoked.

The effective policy is surfaced (without secrets) by the `health_check` tool
under the `policy` key.

## Audit trail

Every invocation — plus every policy denial and rate-limit rejection — emits a
structured audit event containing the timestamp, tool, operation type, role,
outcome, upstream status, retry count, duration, and **sanitized** arguments
(secrets/PII redacted, large payloads truncated).

Select a sink with `BUILDIUM_AUDIT_SINK`:

- `log` (default): structured JSON on stderr under the `audit` event name.
- `file`: newline-delimited JSON appended to `BUILDIUM_AUDIT_FILE`.
- `none`: auditing disabled.

### Reporting

With the `file` sink you can summarize activity:

```bash
# Markdown report to stdout
python scripts/generate_audit_report.py /path/to/audit.log

# CSV of per-tool counts, or write to a file
python scripts/generate_audit_report.py /path/to/audit.log --format csv
python scripts/generate_audit_report.py /path/to/audit.log --output docs/audit-report.md
```

The report includes counts by tool/outcome/operation type, the overall error
rate, recent mutations, and recent denied/rate-limited (security-relevant)
attempts.

The admin-only `audit_summary` tool returns the same aggregates over MCP when
the `file` sink is configured.

## Response envelope

All tools return a stable envelope:

```json
{
  "data": { "...": "..." },
  "count": 1,
  "error": null,
  "meta": { "duration_ms": 12.3, "attempts": 1 }
}
```

On error, `error` is populated with a stable machine-readable `code`
(`validation_error`, `api_error`, `forbidden`, `rate_limited`,
`internal_error`), a human-friendly `message`, an optional upstream `status`,
and an optional actionable `hint`.

## Tuning

Retry and pagination limits are configurable via environment
variables: `BUILDIUM_MAX_RETRIES`,
`BUILDIUM_MAX_PAGE_LIMIT`, and `BUILDIUM_DEFAULT_PAGE_LIMIT`.
