# Buildium MCP Server

**Experimental** Model Context Protocol (MCP) server for Buildium Property Management API, built with Python + FastMCP. Uses API key authentication for server-to-server communication.

## ⚠️ Status & Disclaimers

* **Experimental**: Not production-ready; no SLA; APIs and behavior may change.
* **No affiliation with Buildium**: This is a community integration. Buildium is a trademark of Buildium, LLC.
* **Security**: Do **not** commit secrets. Treat client IDs and secrets as sensitive; use a secrets manager. **Use at your own risk.**

## Features

* 🔐 **API Key Authentication** - Secure server-to-server authentication via headers
* 🏘️ **90 Tools Across 14 Categories** - Comprehensive property management coverage
* 📋 **Selective Tool Loading** - Enable only the categories you need
* 🛡️ **Roles & Guardrails** - Read-only mode, RBAC roles, allow/deny lists, rate limiting
* 🧾 **Audit Trail** - Structured, redacted audit events with pluggable sinks and reporting
* 🏢 **Multi-Property Types** - Rentals, associations, and units
* 🔌 **MCP Protocol** - Compatible with Claude Desktop, Cursor, and other MCP clients

## Requirements

* Python 3.11+
* `uv` package manager (or `pip`)
* Buildium API credentials (client ID and client secret)

## Installation

### Using `uv` (Recommended)

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install the package
uv pip install -e "git+https://github.com/luthersystems/mcp-server-buildium.git"
```

### Using `pip`

```bash
pip install git+https://github.com/luthersystems/mcp-server-buildium.git
```

## Configuration

Configure the server using environment variables:

```bash
# API Base URL (no /v1 suffix - SDK adds it automatically)
BUILDIUM_BASE_URL=https://api.buildium.com  # Production
# BUILDIUM_BASE_URL=https://apisandbox.buildium.com  # Sandbox

# API Key Credentials
BUILDIUM_CLIENT_ID=your-client-id
BUILDIUM_CLIENT_SECRET=your-client-secret

# Optional: Selective Tool Categories (comma-separated)
# If not specified, all categories are enabled
BUILDIUM_CATEGORIES=associations,leases,rentals
```

### Tool Categories

Control which tool categories are enabled using the `BUILDIUM_CATEGORIES` environment variable:

| Category | Tools | Description |
|----------|-------|-------------|
| `associations` | 6 | Homeowner association management |
| `leases` | 6 | Lease agreements and transactions |
| `rentals` | 5 | Rental properties and listings |
| `applicants` | 10 | Rental applicants and applications |
| `tenants` | 7 | Tenant management (rental & association) |
| `owners` | 8 | Property owner management |
| `units` | 7 | Individual unit management |
| `vendors` | 7 | Vendor and service provider management |
| `tasks` | 5 | Task and to-do management |
| `bills` | 7 | Bill and payment management |
| `files` | 8 | Document and file management |
| `bank_accounts` | 6 | Bank account and transaction management |
| `general_ledger` | 4 | General ledger accounts and transactions |
| `work_orders` | 4 | Work order management |

**Total: 90 category tools + built-in `health_check` and `audit_summary` tools (92 total).**

If `BUILDIUM_CATEGORIES` is not set, all 90 tools across all 14 categories are enabled.

### Security, Roles & Audit

The server includes an opt-in security layer. With no extra configuration it
behaves as before (all tools enabled, logging to stderr). Configure roles and
guardrails via environment variables:

| Env var | Description |
|---------|-------------|
| `BUILDIUM_ROLE` | `readonly`, `operator`, `admin` (default), or `custom` |
| `BUILDIUM_READONLY` | `true` disables all mutating tools |
| `BUILDIUM_BLOCK_SENSITIVE` | `true` disables financially sensitive tools (bills, bank, GL, payments, file URLs) |
| `BUILDIUM_ALLOW_TOOLS` | Comma-separated strict whitelist of tool names |
| `BUILDIUM_DENY_TOOLS` | Comma-separated blacklist (deny always wins) |
| `BUILDIUM_RATE_LIMIT_PER_MINUTE` | Cap invocations per 60s window (0 = off) |
| `BUILDIUM_AUDIT_SINK` | `log` (default), `file`, or `none` |
| `BUILDIUM_AUDIT_FILE` | Path for the `file` audit sink |

Forbidden tools are not registered, so they are never advertised to clients.
Every invocation (and every denial/rate-limit) is audited with redacted
arguments. See [`docs/security-and-audit.md`](docs/security-and-audit.md) for the
full guide, including the `scripts/generate_audit_report.py` reporting tool.

### Response Envelope

Every tool returns a stable envelope:

```json
{ "data": {}, "count": 1, "error": null, "meta": { "duration_ms": 12.3, "attempts": 1 } }
```

On failure, `error` carries a machine-readable `code` (`validation_error`,
`api_error`, `forbidden`, `rate_limited`, `internal_error`), a human `message`,
an optional upstream `status`, and an optional `hint`.

### Environment File

Create a `.env` file (copy from `.env.example`):

```bash
cp .env.example .env
# Edit .env with your credentials
```

### Transport & Remote Access (HTTP + Microsoft Entra ID)

By default the server speaks the **stdio** transport, which embeds it in a local
MCP client (Claude Desktop, Cursor). To let **remote clients** — such as the
[browser extension](extension/) — reach it over the network, run the
**Streamable HTTP** transport and protect it with **Microsoft Entra ID (Azure AD)**
JWT authentication.

```bash
# Serve Streamable HTTP instead of stdio
BUILDIUM_TRANSPORT=http
BUILDIUM_HOST=0.0.0.0
BUILDIUM_PORT=8000
BUILDIUM_MCP_PATH=/mcp

# Require Entra ID access tokens (verified against Entra's JWKS)
BUILDIUM_ENTRA_TENANT_ID=<tenant-guid>
BUILDIUM_ENTRA_AUDIENCE=api://<api-app-client-id>
# Optional required scopes (comma-separated)
BUILDIUM_ENTRA_REQUIRED_SCOPES=MCP.Access

# Allow the browser extension's origin(s) for CORS
BUILDIUM_CORS_ALLOW_ORIGINS=chrome-extension://<extension-id>,moz-extension://<extension-id>
```

When `BUILDIUM_ENTRA_TENANT_ID` and `BUILDIUM_ENTRA_AUDIENCE` are set, every MCP
request must carry a valid Entra access token in the `Authorization` header
(using the `Bearer` scheme).
The server verifies the token's **signature** (against Entra's rotating JWKS),
**issuer**, **audience**, **expiry**, and any **required scopes**. The issuer and
JWKS URI are derived from the tenant ID unless overridden with
`BUILDIUM_ENTRA_ISSUER` / `BUILDIUM_ENTRA_JWKS_URI`.

**Auth precedence:** Entra ID → static bearer token (`BUILDIUM_MCP_AUTH_TOKEN`,
useful for local/dev) → none (stdio default).

> **Trust boundary:** the upstream Buildium API key
> (`BUILDIUM_CLIENT_ID` / `BUILDIUM_CLIENT_SECRET`) never leaves the server. Remote
> clients authenticate only with Entra tokens and never see Buildium credentials.

**Production:** terminate TLS at a reverse proxy in front of the HTTP service and
restrict `BUILDIUM_CORS_ALLOW_ORIGINS` to your extension's origin.

Run the HTTP transport with Docker Compose:

```bash
docker compose up --build mcp-server-http   # serves http://localhost:8000/mcp
```

### Server-side LLM assistant (`/chat`)

The HTTP transport also exposes a **server-side assistant** so provider API keys
stay on the server and are **never** shipped to the browser extension. The
extension is a thin client: it POSTs chat turns to `/chat` (authenticated with
the user's Entra token) and streams back tokens and tool events over SSE. The
tool-calling loop and the provider call both run in-process on the server.

Three providers are supported via a server-side adapter layer:

| Provider | Wire API | Key env var |
| --- | --- | --- |
| OpenAI | Chat Completions | `BUILDIUM_LLM_OPENAI_API_KEY` |
| Anthropic | Messages API | `BUILDIUM_LLM_ANTHROPIC_API_KEY` |
| Google Gemini | `generateContent` | `BUILDIUM_LLM_GEMINI_API_KEY` |

Select the active provider and default model, and (optionally) an allow-list of
models a client may request:

```bash
BUILDIUM_LLM_PROVIDER=openai            # openai | anthropic | gemini
BUILDIUM_LLM_MODEL=gpt-4o-mini          # required when a provider is set
BUILDIUM_LLM_ALLOWED_MODELS=gpt-4o-mini,gpt-4o   # optional; default must be a member
BUILDIUM_LLM_OPENAI_API_KEY=sk-...      # matches the selected provider
```

`/chat` is protected by the **same Entra JWT auth as `/mcp`**, so only signed-in
users reach the keyed provider calls (and every turn is audited). A companion
`GET /capabilities` endpoint returns only **non-secret** metadata — whether the
assistant is enabled, the provider name, and the selectable model names — so the
extension can populate a model dropdown. **No endpoint ever emits key material.**

> **Breaking change:** the previous "bring-your-own-key in the browser" flow is
> removed. Provider keys now live only in server config/secrets; the extension no
> longer stores an LLM API base, key, or free-text model.

### Dev auth bypass (local/mock testing)

To run the HTTP transport locally against the mock API **without** an Entra
tenant or token, set:

```bash
BUILDIUM_DEV_AUTH_BYPASS=true
```

This disables **all** authentication on `/mcp`, `/chat`, and `/capabilities`.
It is intended only for local development and mock testing — **never enable it on
a network-reachable or production deployment.** The `docker compose`
`mcp-server-http` service enables it by default so the extension can connect to
the seeded mock API out of the box.


## Usage

### Running the Server

```bash
# With uv
uv run mcp-server-buildium

# Or with Python
python -m mcp_server_buildium.server
```

### Using with Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "buildium": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/luthersystems/mcp-server-buildium",
        "mcp-server-buildium"
      ],
      "env": {
        "BUILDIUM_BASE_URL": "https://apisandbox.buildium.com/",
        "BUILDIUM_CLIENT_ID": "your-client-id",
        "BUILDIUM_CLIENT_SECRET": "your-client-secret"
      }
    }
  }
}
```

## Available Tools (90 category tools)

> In addition to the 90 category tools below, the server exposes two built-in
> tools: `health_check` and `audit_summary` (admin-only), for a total of 92.

### Associations (6 tools)
* `list_associations` - List all associations
* `get_association` - Get association details by ID
* `create_association` - Create a new association
* `update_association` - Update an existing association
* `list_association_board_members` - List board members for an association
* `list_association_ownership_accounts` - List ownership accounts for an association

### Leases (6 tools)
* `list_leases` - List leases with optional filters
* `get_lease` - Get lease details by ID
* `create_lease` - Create a new lease
* `update_lease` - Update an existing lease
* `list_lease_transactions` - List transactions for a lease
* `get_lease_transaction` - Get a lease ledger transaction by ID

### Rentals (5 tools)
* `list_rentals` - List rental properties
* `get_rental` - Get rental property details by ID
* `create_rental` - Create a new rental property
* `update_rental` - Update an existing rental property
* `list_unit_listings` - List unit listings for rentals

### Applicants (10 tools)
* `list_applicants` - List rental applicants
* `get_applicant` - Get applicant details by ID
* `create_applicant` - Create a new applicant
* `update_applicant` - Update an existing applicant
* `list_applicant_applications` - List applications for an applicant
* `get_application` - Get application details by ID
* `update_application` - Update an application
* `list_applicant_groups` - List applicant groups
* `create_applicant_group` - Create a new applicant group
* `update_applicant_group` - Update an applicant group

### Tenants (7 tools)
* `list_rental_tenants` - List rental tenants
* `get_rental_tenant` - Get rental tenant details by ID
* `create_rental_tenant` - Create a new rental tenant
* `update_rental_tenant` - Update a rental tenant
* `list_association_tenants` - List association tenants
* `create_association_tenant` - Create a new association tenant
* `update_association_tenant` - Update an association tenant

### Owners (8 tools)
* `list_rental_owners` - List rental property owners
* `get_rental_owner` - Get rental owner details by ID
* `create_rental_owner` - Create a new rental owner
* `update_rental_owner` - Update a rental owner
* `list_association_owners` - List association owners
* `get_association_owner` - Get association owner details by ID
* `create_association_owner` - Create a new association owner
* `update_association_owner` - Update an association owner

### Units (7 tools)
* `list_rental_units` - List rental units
* `get_rental_unit` - Get rental unit details by ID
* `create_rental_unit` - Create a new rental unit
* `update_rental_unit` - Update a rental unit
* `list_association_units` - List association units
* `create_association_unit` - Create a new association unit
* `update_association_unit` - Update an association unit

### Vendors (7 tools)
* `list_vendors` - List vendors
* `get_vendor` - Get vendor details by ID
* `create_vendor` - Create a new vendor
* `update_vendor` - Update an existing vendor
* `list_vendor_categories` - List vendor categories
* `create_vendor_category` - Create a new vendor category
* `update_vendor_category` - Update a vendor category

### Tasks (5 tools)
* `list_tasks` - List tasks
* `get_task` - Get task details by ID
* `list_task_categories` - List task categories
* `create_task_category` - Create a new task category
* `update_task_category` - Update a task category

### Bills (7 tools)
* `list_bills` - List bills
* `get_bill` - Get bill details by ID
* `create_bill` - Create a new bill
* `update_bill` - Update an existing bill
* `list_bill_payments` - List payments for bills
* `get_bill_payment` - Get bill payment details by ID
* `create_bill_payment` - Create a new bill payment

### Files (8 tools)
* `list_files` - List files
* `get_file` - Get file details by ID
* `update_file` - Update file metadata
* `create_file_upload_request` - Create a file upload request
* `create_file_download_request` - Create a file download request
* `list_file_categories` - List file categories
* `create_file_category` - Create a new file category
* `update_file_category` - Update a file category

### Bank Accounts (6 tools)
* `list_bank_accounts` - List bank accounts
* `get_bank_account` - Get bank account details by ID
* `create_bank_account` - Create a new bank account
* `update_bank_account` - Update a bank account
* `list_bank_account_transactions` - List transactions for a bank account
* `get_bank_account_transaction` - Get bank account transaction details by ID

### General Ledger (4 tools)
* `list_gl_accounts` - List general ledger accounts
* `get_gl_account` - Get general ledger account details by ID
* `list_gl_transactions` - List general ledger transactions
* `get_gl_transaction` - Get general ledger transaction details by ID

### Work Orders (4 tools)
* `list_work_orders` - List work orders
* `get_work_order` - Get work order details by ID
* `create_work_order` - Create a new work order
* `update_work_order` - Update an existing work order

## Tool Request/Response Examples

This section provides detailed schemas and examples for key MCP tools.

### Example: List Leases

Query leases with optional filters (property, unit, status).

**Parameters:**
- `property_id` (int, optional): Filter by property ID
- `unit_id` (int, optional): Filter by unit ID
- `lease_status` (str, optional): Filter by status (e.g., "Active", "Future", "Past", "Expired")
- `limit` (int, optional): Maximum results (default: 100)
- `offset` (int, optional): Pagination offset (default: 0)

**Example Request (no filters):**
```json
{
  "name": "list_leases",
  "arguments": {}
}
```

**Example Request (with filters):**
```json
{
  "name": "list_leases",
  "arguments": {
    "property_id": 123,
    "lease_status": "Active",
    "limit": 50
  }
}
```

**Example Response:**
```json
{
  "leases": [
    {
      "id": 12345,
      "propertyId": 123,
      "unitId": 456,
      "leaseType": "Fixed",
      "leaseFromDate": "2024-01-01",
      "leaseToDate": "2024-12-31",
      "status": "Active",
      "tenants": [
        {
          "id": 789,
          "firstName": "John",
          "lastName": "Doe"
        }
      ]
    }
  ],
  "count": 1
}
```

### Example: Get Lease

Retrieve details of a specific lease by ID.

**Parameters:**
- `lease_id` (int, required): The lease ID

**Example Request:**
```json
{
  "name": "get_lease",
  "arguments": {
    "lease_id": 12345
  }
}
```

**Example Response:**
```json
{
  "id": 12345,
  "propertyId": 123,
  "unitId": 456,
  "leaseType": "Fixed",
  "leaseFromDate": "2024-01-01",
  "leaseToDate": "2024-12-31",
  "status": "Active",
  "rentCycle": "Monthly",
  "rentAmount": 2000.00,
  "securityDepositAmount": 2000.00,
  "tenants": [
    {
      "id": 789,
      "firstName": "John",
      "lastName": "Doe",
      "email": "john.doe@example.com",
      "phoneNumbers": {
        "home": "555-0100"
      }
    }
  ],
  "createdDateTime": "2024-01-01T10:00:00Z",
  "lastModifiedDateTime": "2024-01-01T10:00:00Z"
}
```

### Example: Create Lease

Create a new lease agreement.

**Required Parameters:**
- `lease_type` (str): Lease type - `"AtWill"` (month-to-month), `"Fixed"` (specific dates), or `"FixedWithRollover"`
- `unit_id` (int): Unit ID for the lease
- `lease_from_date` (str): Start date (format: `YYYY-MM-DD`)
- `send_welcome_email` (bool): Whether to send welcome email to tenants

**Optional Parameters:**
- `lease_to_date` (str): End date (required for Fixed/FixedWithRollover leases)
- `tenant_ids` (list[int]): Existing tenant IDs to add (max 5)
- `tenants` (list[object]): New tenants to create (max 5)
- `applicant_ids` (list[int]): Approved applicant IDs to convert to tenants (max 5)
- `rent` (object): Rent configuration with cycle and charges
- `security_deposit` (object): Security deposit details
- `prorated_first_month_rent` (float): Prorated first month rent
- `prorated_last_month_rent` (float): Prorated last month rent
- `cosigners` (list[object]): Cosigner details

**Example Request (Minimal - Using Existing Tenant):**
```json
{
  "name": "create_lease",
  "arguments": {
    "lease_data": {
      "lease_type": "Fixed",
      "unit_id": 456,
      "lease_from_date": "2025-01-01",
      "lease_to_date": "2025-12-31",
      "send_welcome_email": true,
      "tenant_ids": [789]
    }
  }
}
```

**Example Request (Complete with New Tenant and Rent):**
```json
{
  "name": "create_lease",
  "arguments": {
    "lease_data": {
      "lease_type": "Fixed",
      "unit_id": 456,
      "lease_from_date": "2025-01-01",
      "lease_to_date": "2025-12-31",
      "send_welcome_email": true,
      "tenants": [
        {
          "first_name": "Jane",
          "last_name": "Smith",
          "email": "jane.smith@example.com",
          "phone_numbers": {
            "home": "555-0200"
          },
          "address": {
            "address_line1": "123 Main St",
            "city": "Springfield",
            "state": "IL",
            "postal_code": "62701",
            "country": "US"
          }
        }
      ],
      "rent": {
        "cycle": "Monthly",
        "charges": [
          {
            "gl_account_id": 1001,
            "amount": 2000.00
          }
        ]
      },
      "security_deposit": {
        "due_date": "2025-01-01",
        "amount": 2000.00
      }
    }
  }
}
```

**Example Response:**
```json
{
  "id": 12346,
  "propertyId": 123,
  "unitId": 456,
  "leaseType": "Fixed",
  "leaseFromDate": "2025-01-01",
  "leaseToDate": "2025-12-31",
  "status": "Active",
  "rentCycle": "Monthly",
  "rentAmount": 2000.00,
  "securityDepositAmount": 2000.00,
  "tenants": [
    {
      "id": 790,
      "firstName": "Jane",
      "lastName": "Smith",
      "email": "jane.smith@example.com"
    }
  ],
  "createdDateTime": "2024-11-07T00:00:00Z"
}
```

**Field Descriptions:**

**Lease Types:**
- `AtWill`: Month-to-month lease with no end date. Automatic transactions continue until manually ended.
- `Fixed`: Lease with specific start/end dates. Moves to expired on end date, stops automatic transactions.
- `FixedWithRollover`: Lease that converts to AtWill status at end date, continuing automatic transactions.

**Rent Configuration:**
- `cycle`: Rent frequency - `"Monthly"`, `"Weekly"`, `"Every2Weeks"`, `"Quarterly"`, `"Yearly"`, `"Every2Months"`, `"Daily"`, `"Every6Months"`, `"OneTime"`
- `charges`: Array of rent charges with GL account ID and amount

**Tenant Creation:**
- Can provide up to 5 tenants via `tenant_ids` (existing), `tenants` (new), or `applicant_ids` (approved applicants)
- Required tenant fields: `first_name`, `last_name`, `address`
- Optional: `email`, `phone_numbers`, `date_of_birth`, `emergency_contact`, etc.

## Example Usage

### Example Prompts

> "List all rental properties in Buildium"

> "Show me lease #12345"

> "Create a new lease for property 100, unit 205"

> "List all associations"

## Development

### Setting Up Development Environment

```bash
# Clone the repository
git clone https://github.com/luthersystems/mcp-server-buildium.git
cd mcp-server-buildium

# Install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv pip install -e ".[dev]"
```

### Running Tests

```bash
# Run unit tests (no credentials needed)
uv run pytest tests/ --ignore=tests/test_integration.py

# Run with coverage
uv run pytest --cov=mcp_server_buildium --cov-report=html

# Run linter
uv run ruff check .

# Format code
uv run ruff format .
```

### Local End-to-End Testing with the Mock API

A spec-accurate mock of the Buildium API (FastAPI + SQLite, in `mockapi/`) lets
you exercise every tool against realistic, seeded data with no live credentials.

```bash
# Install the mock API extra
uv pip install -e ".[dev,mockapi]"

# Seed the database and serve the mock on http://127.0.0.1:8080
python -m mockapi

# In another shell, point the server at the mock and run the e2e suite
uv run pytest tests/test_e2e_mock.py
```

Or run the whole stack in containers:

```bash
# Build and start the seeded mock API
docker compose up --build mockapi

# Run the MCP server against the mock (STDIO transport)
docker compose run --rm mcp-server
```

Two images are produced: `mcp-server-buildium` (`Dockerfile`) and
`mcp-server-buildium-mockapi` (`Dockerfile.mockapi`, seeds on startup).

### Validating Tools Against the OpenAPI Spec

Every tool is mapped to a real Buildium operation in `openapi.json`. Regenerate
the coverage report and run the validation tests with:

```bash
uv run python scripts/generate_tool_coverage.py   # writes docs/tool-coverage.md
uv run pytest tests/test_tool_spec_coverage.py
```

See [`docs/tool-coverage.md`](docs/tool-coverage.md) for the current tool → endpoint mapping.

### Integration Tests (Optional)

Integration tests validate real Buildium API authentication. They are **skipped by default** and only run when you provide real credentials.

**To enable integration tests:**

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Fill in your Buildium credentials in `.env`:
   ```bash
   BUILDIUM_BASE_URL=https://apisandbox.buildium.com/
   BUILDIUM_CLIENT_ID=your-client-id
   BUILDIUM_CLIENT_SECRET=your-client-secret
   ```

3. Run integration tests:
   ```bash
   uv run pytest tests/test_integration.py -v
   ```

**Test Credentials (Sandbox):**
- Client ID: `54f6ac5b-5629-4934-a930-d2c8174fcf4a`
- Client Secret: `tHXJx7mFoCEXtqCvBL3oV1Fv6hHb5WVokKHIvT1cUIA=`
- Base URL: `https://apisandbox.buildium.com/`

### CI/CD Setup (GitHub Actions)

To run integration tests in GitHub Actions:

1. **Add GitHub repository secrets:**
   * Go to your repo → **Settings** → **Secrets and variables** → **Actions**
   * Add the following secrets:
     * `BUILDIUM_BASE_URL`: `https://apisandbox.buildium.com/`
     * `BUILDIUM_CLIENT_ID`: Your client ID
     * `BUILDIUM_CLIENT_SECRET`: Your client secret

2. **The CI workflow will automatically run integration tests** when these secrets are present

**Note**: The integration tests will be skipped in PRs from forks (for security), but will run on pushes to main/develop branches.

**What the integration tests verify:**

* ✅ API key header authentication works with your credentials
* ✅ API calls work (tests `list_associations`, `list_rentals`, `list_leases`)

**Note**: Integration tests require a Buildium developer account (sandbox environment recommended).

## Generating SDK from OpenAPI Spec

If you want to generate a Python SDK from the Buildium OpenAPI spec:

```bash
# Generate SDK (requires Java for OpenAPI Generator)
make generate-sdk

# The generated SDK will be in buildium_sdk/ directory
```

See `Makefile` for more details.

## Project Structure

```
mcp-server-buildium/
├── src/mcp_server_buildium/
│   ├── __init__.py
│   ├── server.py           # Main FastMCP server + guarded registration
│   ├── config.py           # Configuration management (incl. roles & audit)
│   ├── buildium_client.py  # API key auth & API client
│   ├── logging_config.py   # Structured, secret-scrubbing logging
│   ├── audit.py            # Pluggable audit sinks + reporting helpers
│   ├── security/
│   │   ├── policy.py       # Roles, guardrails, rate limiter
│   │   └── registration.py # GuardedMCP: enforcement + audit wrapper
│   └── tools/
│       ├── _common.py      # Envelope, retries, classification, execute()
│       ├── associations.py # Association tools
│       ├── leases.py       # Lease tools
│       └── ...             # One module per category
├── scripts/
│   ├── generate_tool_coverage.py
│   └── generate_audit_report.py
├── tests/
├── docs/
│   ├── tool-coverage.md
│   └── security-and-audit.md
├── pyproject.toml
└── README.md
```

## Troubleshooting

### "401 Unauthorized"

* Verify `BUILDIUM_CLIENT_ID` and `BUILDIUM_CLIENT_SECRET` are correct
* Check base URL is correct (sandbox vs production) and does **not** include `/v1`
* Confirm the API key has the required permissions in Buildium

### "Connection timeout"

* Check internet connectivity
* Verify firewall settings
* Try sandbox environment first

## API Endpoints

* **Production**: `https://api.buildium.com/`
* **Sandbox**: `https://apisandbox.buildium.com/`

## Security Best Practices

1. **Never commit credentials** - Use `.gitignore` and environment variables
2. **Use secrets management** - Store credentials in secure vaults (AWS Secrets Manager, etc.)
3. **Rotate credentials regularly** - Generate new client secrets periodically
4. **Limit token lifetime** - Use shorter expiration for sensitive operations
5. **Monitor API usage** - Check Buildium dashboard for unusual activity
6. **Use sandbox environment** - Test with sandbox accounts before production

## Architecture

* **Language**: Python 3.11+
* **Framework**: FastMCP
* **Auth**: API key headers (`x-buildium-client-id` / `x-buildium-client-secret`)
* **Transport**: stdio (MCP protocol)
* **HTTP Client**: httpx
* **Testing**: pytest with mocks

## References

* [Buildium Developer Documentation](https://developer.buildium.com/)
* [Model Context Protocol](https://modelcontextprotocol.io)
* [FastMCP Framework](https://github.com/jlowin/fastmcp)

## Browser Extension (Sidebar Chat)

A Manifest V3 Chrome/Firefox extension that provides a full-height side panel chat
UI (Gemini-style), authenticates users with Microsoft Entra ID, and lets an LLM
drive Buildium tool calls through this server's HTTP transport, lives in
[`extension/`](extension/). See [`extension/README.md`](extension/README.md) for
build, configuration, and Entra app-registration instructions.

## License

MIT

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## Support

This is an experimental community project. For Buildium API issues, consult the [Buildium Developer Center](https://developer.buildium.com/).

