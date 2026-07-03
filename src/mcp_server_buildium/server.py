"""Main MCP server entry point for Buildium."""

from typing import Any

from fastmcp import FastMCP

from . import audit as audit_mod
from .auth import build_auth
from .buildium_client import BuildiumClient
from .chat_endpoint import register_chat_routes
from .config import BuildiumConfig
from .logging_config import configure_logging, get_logger
from .security.policy import RateLimiter, ToolPolicy
from .security.registration import GuardedMCP
from .security.scoping import EntraScopingMiddleware
from .tools import _common as c
from .tools.applicants import register_applicant_tools
from .tools.associations import register_association_tools
from .tools.bank_accounts import register_bank_account_tools
from .tools.bills import register_bill_tools
from .tools.files import register_file_tools
from .tools.general_ledger import register_general_ledger_tools
from .tools.leases import register_lease_tools
from .tools.owners import register_owner_tools
from .tools.rentals import register_rental_tools
from .tools.tasks import register_task_tools
from .tools.tenants import register_tenant_tools
from .tools.units import register_unit_tools
from .tools.vendors import register_vendor_tools
from .tools.work_orders import register_work_order_tools

# Configure structured logging (to stderr) before anything else.
configure_logging()
logger = get_logger("mcp_server_buildium.server")

# Initialize Buildium client and config (fails fast on invalid configuration).
config = BuildiumConfig.from_env()
buildium_client = BuildiumClient(config=config)

# Resolve the security policy, audit recorder, and rate limiter from config.
policy = ToolPolicy.from_config(config)
audit_recorder = audit_mod.AuditRecorder.from_config(config)
rate_limiter = RateLimiter(config.rate_limit_per_minute)
logger.info(
    "Security policy resolved role=%s readonly=%s block_sensitive=%s",
    policy.role,
    policy.readonly,
    policy.block_sensitive,
)

# Optional authentication. Precedence: Entra ID JWT → static bearer token → none.
# Remote clients (e.g. the browser extension) authenticate with Entra; the upstream
# Buildium API key never leaves the server.
auth = build_auth(config)
if auth is not None:
    logger.info("MCP authentication enabled (%s)", type(auth).__name__)

# Create FastMCP server and wrap it so the security policy, rate limiter, and
# audit trail are applied centrally to every registered tool.
_base_mcp = FastMCP("buildium", auth=auth)
mcp = GuardedMCP(_base_mcp, policy, audit_recorder, rate_limiter)

# Per-identity tool scoping: when BUILDIUM_ENTRA_ROLE_POLICY_MAP is configured
# (with Entra auth), narrow each request's tools to the caller's App Role,
# intersected with the process-wide policy above. No-op otherwise.
_scoping = EntraScopingMiddleware(config, policy)
if _scoping.active:
    _base_mcp.add_middleware(_scoping)
    logger.info("Entra App Role tool scoping enabled")

# Map category name -> registration function so registration is data-driven.
_CATEGORY_REGISTRARS = {
    "associations": register_association_tools,
    "leases": register_lease_tools,
    "rentals": register_rental_tools,
    "applicants": register_applicant_tools,
    "tenants": register_tenant_tools,
    "owners": register_owner_tools,
    "units": register_unit_tools,
    "vendors": register_vendor_tools,
    "tasks": register_task_tools,
    "bills": register_bill_tools,
    "files": register_file_tools,
    "bank_accounts": register_bank_account_tools,
    "general_ledger": register_general_ledger_tools,
    "work_orders": register_work_order_tools,
}

for _category, _register in _CATEGORY_REGISTRARS.items():
    if config.is_category_enabled(_category):
        _register(mcp, buildium_client)

# Register the server-side assistant HTTP routes (/chat, /capabilities). These are
# only reachable over the HTTP transport; they run the LLM loop server-side so
# provider API keys never reach the browser.
register_chat_routes(mcp, config, auth, policy)
if config.llm_enabled():
    logger.info("Server-side assistant enabled (provider=%s)", config.get_llm_provider())


@mcp.tool()
async def health_check() -> dict[str, Any]:
    """Report server health and configuration status.

    Returns a ``{data, count, error, meta}`` envelope describing the server
    version, configured Buildium base URL, whether credentials are present, the
    set of enabled tool categories, and the effective security policy. Does not
    leak secret values.
    """
    enabled = config.get_enabled_categories()
    if config.dev_auth_bypass:
        auth_mode = "dev_bypass"
    elif config.entra_enabled():
        auth_mode = "entra"
    elif config.mcp_auth_token:
        auth_mode = "static_token"
    else:
        auth_mode = "none"
    data = {
        "status": "ok",
        "server": "buildium",
        "base_url": config.base_url,
        "credentials_configured": bool(config.client_id and config.client_secret),
        "transport": config.transport,
        "auth_mode": auth_mode,
        "enabled_categories": sorted(enabled) if enabled is not None else "all",
        "policy": policy.describe(),
        "rate_limit_per_minute": rate_limiter.per_minute,
        "audit_sink": config.audit_sink,
        "assistant_enabled": config.llm_enabled(),
        "assistant_provider": config.get_llm_provider(),
    }
    return c.success(data)


def _build_cors_middleware() -> list:
    """Build CORS middleware for the HTTP transport from configuration.

    Returns an empty list when no origins are configured so the server does not
    emit CORS headers by default.
    """
    origins = config.get_cors_origins()
    if not origins:
        return []

    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware

    return [
        Middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Mcp-Session-Id"],
            expose_headers=["Mcp-Session-Id"],
            allow_credentials="*" not in origins,
        )
    ]


@mcp.tool()
async def audit_summary(limit: int = 500) -> dict[str, Any]:
    """Summarize recent audit activity (admin only).

    Reads the configured file audit sink and returns aggregate counts by tool,
    outcome, and operation type, plus a list of recent denied/rate-limited
    attempts. Only available with the ``admin`` role and when
    ``BUILDIUM_AUDIT_SINK=file`` is configured.

    Args:
        limit: Maximum number of most-recent audit records to scan (1-10000).
    """
    if config.audit_sink != "file" or not config.audit_file:
        return c.failure(
            "Audit summary requires BUILDIUM_AUDIT_SINK=file with BUILDIUM_AUDIT_FILE set.",
            code="validation_error",
            hint="Set BUILDIUM_AUDIT_SINK=file and BUILDIUM_AUDIT_FILE=/path/to/audit.log.",
        )
    summary = audit_mod.summarize_file(config.audit_file, limit=max(1, min(10000, int(limit))))
    return c.success(summary)


def main() -> None:
    """Run the MCP server.

    Defaults to the ``stdio`` transport (embedded in a local MCP client). When
    ``BUILDIUM_TRANSPORT=http`` the server serves the Streamable HTTP transport so
    remote clients — such as the browser extension — can connect over the network.
    """
    if config.transport.lower() == "http":
        logger.info(
            "Starting Buildium MCP server (http) on %s:%s%s",
            config.host,
            config.port,
            config.mcp_path,
        )
        mcp.run(
            transport="http",
            host=config.host,
            port=config.port,
            path=config.mcp_path,
            middleware=_build_cors_middleware(),
        )
    else:
        logger.info("Starting Buildium MCP server (stdio)")
        mcp.run()


if __name__ == "__main__":
    main()
