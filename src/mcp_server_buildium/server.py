"""Main MCP server entry point for Buildium."""

from typing import Any

from fastmcp import FastMCP

from . import audit as audit_mod
from .buildium_client import BuildiumClient
from .config import BuildiumConfig
from .logging_config import configure_logging, get_logger
from .security.policy import RateLimiter, ToolPolicy
from .security.registration import GuardedMCP
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

# Optional header-auth passthrough: when a token is configured, MCP clients must
# present it via the Authorization header.
auth = None
if config.mcp_auth_token:
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    auth = StaticTokenVerifier(
        tokens={config.mcp_auth_token: {"client_id": "buildium-mcp", "scopes": []}}
    )
    logger.info("MCP header-auth passthrough enabled")

# Create FastMCP server and wrap it so the security policy, rate limiter, and
# audit trail are applied centrally to every registered tool.
_base_mcp = FastMCP("buildium", auth=auth)
mcp = GuardedMCP(_base_mcp, policy, audit_recorder, rate_limiter)

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

    Returns a ``{data, count, error, meta}`` envelope describing the server
    version, configured Buildium base URL, whether credentials are present, the
    set of enabled tool categories, and the effective security policy. Does not
    leak secret values.
    """
    enabled = config.get_enabled_categories()
    data = {
        "status": "ok",
        "server": "buildium",
        "base_url": config.base_url,
        "credentials_configured": bool(config.client_id and config.client_secret),
        "auth_passthrough_enabled": bool(config.mcp_auth_token),
        "enabled_categories": sorted(enabled) if enabled is not None else "all",
        "policy": policy.describe(),
        "rate_limit_per_minute": rate_limiter.per_minute,
        "audit_sink": config.audit_sink,
    }
    return c.success(data)


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
    """Run the MCP server (stdio by default)."""
    logger.info("Starting Buildium MCP server")
    mcp.run()


if __name__ == "__main__":
    main()
