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

## Per-user scoping with Entra App Roles

The role and guardrails above are **process-wide** — they form the server-wide
*ceiling* (the maximum grant). When the server authenticates callers with
Microsoft Entra (see `BUILDIUM_ENTRA_*` in the README), you can additionally
narrow the available tools **per authenticated user** based on the user's Entra
**App Role**.

### App-registration setup

1. On the MCP API app registration, define **App Roles** (Entra ID → *App
   registrations* → your API → *App roles*), for example `Buildium.ReadOnly`,
   `Buildium.Operator`, `Buildium.Admin`.
2. Assign users or security groups to those app roles under *Enterprise
   applications* → your API → *Users and groups*.
3. Entra then emits a `roles` claim in the access token containing the assigned
   app-role values. (App Roles are preferred over group claims because they are
   not subject to the Entra "groups overage" truncation.)

### Mapping config

Set `BUILDIUM_ENTRA_ROLE_POLICY_MAP` to a JSON object mapping each app-role
value to one of the coarse roles `readonly`, `operator`, or `admin`:

```
BUILDIUM_ENTRA_ROLE_POLICY_MAP='{"Buildium.Admin":"admin","Buildium.Operator":"operator","Buildium.ReadOnly":"readonly"}'
```

Group object IDs from the token `groups` claim are also matched against the map
keys, so existing security groups can be used as a fallback.

### Semantics

- The caller's effective policy is the **intersection** of the server-wide
  policy and the policy implied by their mapped coarse role (server ceiling ∩
  user grant). The server-wide guardrails can never be widened by a user's role.
- If a caller matches **more than one** mapped role, the **most permissive**
  (`admin` > `operator` > `readonly`) is used.
- A caller who matches **no** mapped role/group is **denied all tools**
  (deny-all fallback).
- Scoping is only active when the map is configured **and** Entra auth is
  enabled. Static-token / dev-bypass / stdio modes are unaffected.

### Enforcement surfaces

Per-user scoping is enforced at both surfaces, for visibility and hard denial:

| Surface | Visibility | Call-time denial |
| --- | --- | --- |
| MCP `tools/list` / `tools/call` | Filtered by middleware | Rejected with a tool error |
| `/chat` (server-side LLM) | Offered tool specs filtered | Tool runner refuses forbidden tools |

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
