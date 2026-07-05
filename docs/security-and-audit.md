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
under the `policy` key. `health_check` is always registered and does not
require a Buildium API call, so you can inspect the active configuration at any
time — including which roles, guardrails, allow/deny lists, and rate limits are
in force — without needing real credentials.

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

## Admin management routes (`/manage/*`)

An optional, **admin-only** capability lets an administrator manage users and
distribute the browser extension. It is disabled by default and turned on with
`BUILDIUM_MANAGEMENT_ENABLED=true`.

The routes are HTTP custom routes served next to `/mcp` and `/chat`, gated by the
**same** Entra JWT auth. A caller must additionally resolve to the coarse
`admin` role — the *same* admin notion that governs admin-only tools like
`audit_summary` (see [Per-user scoping](#per-user-scoping-with-entra-app-roles)).
Non-admin callers receive `403`; unauthenticated callers receive `401`.

| Route | Method | Purpose |
| --- | --- | --- |
| `/manage/capabilities` | GET | Report whether management is enabled and whether the caller is an admin (lets the extension show/hide its admin panel). |
| `/manage/users` | GET | List users assigned to the API app and their coarse roles. |
| `/manage/users` | POST | Invite an Entra **B2B guest** (`{email, role}`) and assign the role. |
| `/manage/users/{id}/role` | PATCH | Change a user's role (`{role}`). |
| `/manage/extension?browser=chrome\|firefox` | GET | Download the prebuilt, preconfigured extension archive. |

Management actions are recorded in the audit trail (`manage_invite_user`,
`manage_edit_role`, `manage_list_users`, `manage_download_extension`).

### Microsoft Graph setup

User invitations and role assignments are performed server-side via Microsoft
Graph using the **client-credentials** (app-only) grant. Register a dedicated
Entra app and grant it the **admin-consented application permissions**
`User.Invite.All`, `AppRoleAssignment.ReadWrite.All`, and `Application.Read.All`.
Its credentials stay server-side and are never returned by any endpoint:

- `BUILDIUM_GRAPH_CLIENT_ID`, `BUILDIUM_GRAPH_CLIENT_SECRET`
- `BUILDIUM_GRAPH_TENANT_ID` (defaults to `BUILDIUM_ENTRA_TENANT_ID`)
- `BUILDIUM_ENTRA_API_SERVICE_PRINCIPAL_ID` — object ID of the API app's
  service principal (enterprise application); app-role assignments are created
  on it.
- `BUILDIUM_ENTRA_APP_ROLE_ID_MAP` — JSON mapping `admin`/`operator`/`readonly`
  to the API app's Entra **App Role IDs**, e.g.
  `{"admin":"<guid>","operator":"<guid>","readonly":"<guid>"}`. Keep these
  consistent with `BUILDIUM_ENTRA_ROLE_POLICY_MAP` so the same role vocabulary
  flows end to end.

### Preconfigured extension download

`GET /manage/extension` serves a **prebuilt** archive whose configuration is
already baked in — no packaging happens in the request path. Produce the archive
at release time by running the extension build with your deployment's public
defaults (see `extension/README.md`), then zip `dist/chrome` to a `.zip` and
`dist/firefox` to a `.xpi` and point the server at them:

- `BUILDIUM_MANAGEMENT_EXTENSION_CHROME_PATH`
- `BUILDIUM_MANAGEMENT_EXTENSION_FIREFOX_PATH`

If the requested browser's archive is not configured, the route returns `503`.

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

With the `file` sink you can summarize activity two ways:

**Command-line report** (offline, from the log file):

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

**`audit_summary` tool** (live, over MCP, admin-only): the built-in
`audit_summary` tool returns the same aggregates directly in an MCP tool call
when the `file` sink is configured. No file access is required — any admin
client connected to the server can query it on demand.

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

Retry, pagination, and auto-pagination limits are configurable via environment
variables:

| Env var | Default | Effect |
|---------|---------|--------|
| `BUILDIUM_MAX_RETRIES` | `3` | Maximum retry attempts per transient failure |
| `BUILDIUM_BASE_BACKOFF_SECONDS` | `0.5` | Base delay for the first retry |
| `BUILDIUM_MAX_BACKOFF_SECONDS` | `8.0` | Maximum per-retry delay |
| `BUILDIUM_MAX_PAGE_LIMIT` | `1000` | Maximum page size for list endpoints |
| `BUILDIUM_DEFAULT_PAGE_LIMIT` | `100` | Default page size when not specified |
| `BUILDIUM_MAX_FETCH_ALL_RECORDS` | `5000` | Record ceiling for auto-paginating tools (reports, alerts) |
