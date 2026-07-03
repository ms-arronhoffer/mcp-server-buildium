"""Main MCP server entry point for Buildium."""

from typing import Any

from fastmcp import FastMCP

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

# Optional header-auth passthrough: when a token is configured, MCP clients must
# present it via the Authorization header.
auth = None
if config.mcp_auth_token:
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    auth = StaticTokenVerifier(
        tokens={config.mcp_auth_token: {"client_id": "buildium-mcp", "scopes": []}}
    )
    logger.info("MCP header-auth passthrough enabled")

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
    data = {
        "status": "ok",
        "server": "buildium",
        "base_url": config.base_url,
        "credentials_configured": bool(config.client_id and config.client_secret),
        "auth_passthrough_enabled": bool(config.mcp_auth_token),
        "enabled_categories": sorted(enabled) if enabled is not None else "all",
    }
    return c.success(data)


def main() -> None:
    """Run the MCP server (stdio by default)."""
    logger.info("Starting Buildium MCP server")
    mcp.run()


if __name__ == "__main__":
    main()
