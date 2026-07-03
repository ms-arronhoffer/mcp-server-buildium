"""Lease management tools for Buildium."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c

try:
    from mcp_server_buildium.buildium_sdk.models.lease_post_message import LeasePostMessage
    from mcp_server_buildium.buildium_sdk.models.lease_put_message import LeasePutMessage
except ImportError:  # pragma: no cover

    class LeasePostMessage:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class LeasePutMessage:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)


LEASE_STATUSES = {"Active", "Past", "Future"}


def register_lease_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register lease-related tools with the MCP server."""

    c.register_operation("list_leases", "ExternalApiLeases_GetLeases")
    c.register_operation("get_lease", "ExternalApiLeases_GetLeaseById")
    c.register_operation("create_lease", "ExternalApiLeases_CreateLease")
    c.register_operation("update_lease", "ExternalApiLeases_UpdateLease")
    c.register_operation(
        "list_lease_transactions", "ExternalApiLeaseLedgerTransactions_GetLeaseLedgers"
    )
    c.register_operation(
        "get_lease_transaction",
        "ExternalApiLeaseLedgerTransactions_GetLeaseLedgerTransactionById",
    )

    @mcp.tool()
    async def list_leases(
        property_id: int | None = None,
        lease_status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List leases from Buildium.

        Args:
            property_id: Optional property ID to filter by.
            lease_status: Optional status filter (one of: Active, Past, Future).
            limit: Maximum number of results (1-1000, default: 100).
            offset: Zero-based pagination offset (default: 0).
        """
        limit, offset = c.clamp_pagination(limit, offset)
        try:
            lease_status = c.validate_enum(lease_status, LEASE_STATUSES, field="lease_status")
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")

        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if property_id is not None:
            kwargs["propertyids"] = [property_id]
        if lease_status is not None:
            kwargs["leasestatuses"] = [lease_status]

        return await c.execute(
            "list_leases",
            lambda: client.leases_api.external_api_leases_get_leases(**kwargs),
        )

    @mcp.tool()
    async def get_lease(lease_id: int) -> dict[str, Any]:
        """Get a specific lease by ID."""
        return await c.execute(
            "get_lease",
            lambda: client.leases_api.external_api_leases_get_lease_by_id(lease_id=lease_id),
        )

    @mcp.tool()
    async def create_lease(lease_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new lease."""
        message = LeasePostMessage(**lease_data)
        return await c.execute(
            "create_lease",
            lambda: client.leases_api.external_api_leases_create_lease(lease_post_message=message),
        )

    @mcp.tool()
    async def update_lease(lease_id: int, lease_data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing lease."""
        message = LeasePutMessage(**lease_data)
        return await c.execute(
            "update_lease",
            lambda: client.leases_api.external_api_leases_update_lease(
                lease_id=lease_id, lease_put_message=message
            ),
        )

    @mcp.tool()
    async def list_lease_transactions(
        lease_id: int, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List ledger transactions for a specific lease."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_lease_transactions",
            lambda: (
                client.lease_transactions_api.external_api_lease_ledger_transactions_get_lease_ledgers(
                    lease_id=lease_id, limit=limit, offset=offset
                )
            ),
        )

    @mcp.tool()
    async def get_lease_transaction(lease_id: int, transaction_id: int) -> dict[str, Any]:
        """Get a specific lease ledger transaction by ID."""
        return await c.execute(
            "get_lease_transaction",
            lambda: (
                client.lease_transactions_api.external_api_lease_ledger_transactions_get_lease_ledger_transaction_by_id(
                    lease_id=lease_id, transaction_id=transaction_id
                )
            ),
        )
