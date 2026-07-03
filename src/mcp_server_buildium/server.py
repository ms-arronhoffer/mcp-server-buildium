"""Main MCP server entry point for Buildium."""

from typing import Any

from fastmcp import FastMCP

from .auth import build_auth
from .buildium_client import BuildiumClient
from .config import BuildiumConfig
from .logging_config import configure_logging, get_logger
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

# Optional authentication. Precedence: Entra ID JWT → static bearer token → none.
# Remote clients (e.g. the browser extension) authenticate with Entra; the upstream
# Buildium API key never leaves the server.
auth = build_auth(config)
if auth is not None:
    logger.info("MCP authentication enabled (%s)", type(auth).__name__)

# Create FastMCP server
mcp = FastMCP("buildium", auth=auth)

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


@mcp.tool()
async def health_check() -> dict[str, Any]:
    """Report server health and configuration status.

    Returns a ``{data, count, error}`` envelope describing the server version,
    configured Buildium base URL, whether credentials are present, and the set
    of enabled tool categories. Does not leak secret values.
    """
    enabled = config.get_enabled_categories()
    if config.entra_enabled():
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
