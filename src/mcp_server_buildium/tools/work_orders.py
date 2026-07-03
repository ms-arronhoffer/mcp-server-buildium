"""Work order tools for Buildium."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c

WORK_ORDER_STATUSES = {"New", "InProgress", "Completed", "Deferred", "Closed"}


def register_work_order_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register work-order tools with the MCP server."""

    c.register_operation("list_work_orders", "ExternalApiWorkOrders_GetAllWorkOrders")
    c.register_operation("get_work_order", "ExternalApiWorkOrders_GetWorkOrderById")
    c.register_operation("create_work_order", "ExternalApiWorkOrders_CreateWorkOrder")
    c.register_operation("update_work_order", "ExternalApiWorkOrders_UpdateWorkOrder")

    @mcp.tool()
    async def list_work_orders(
        status: str | None = None,
        assigned_to_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List work orders from Buildium.

        Args:
            status: Optional status (New, InProgress, Completed, Deferred, Closed).
            assigned_to_id: Optional staff user ID the work order is assigned to.
            limit: Maximum number of results (1-1000, default: 100).
            offset: Zero-based pagination offset (default: 0).
        """
        limit, offset = c.clamp_pagination(limit, offset)
        try:
            status = c.validate_enum(status, WORK_ORDER_STATUSES, field="status")
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            kwargs["statuses"] = [status]
        if assigned_to_id is not None:
            kwargs["assignedtoid"] = assigned_to_id
        return await c.execute(
            "list_work_orders",
            lambda: client.work_orders_api.external_api_work_orders_get_all_work_orders(**kwargs),
        )

    @mcp.tool()
    async def get_work_order(work_order_id: int) -> dict[str, Any]:
        """Get a specific work order by ID."""
        return await c.execute(
            "get_work_order",
            lambda: client.work_orders_api.external_api_work_orders_get_work_order_by_id(
                work_order_id=work_order_id
            ),
        )

    @mcp.tool()
    async def create_work_order(work_order_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new work order."""
        return await c.create(
            "create_work_order",
            "work_order_post_message",
            "WorkOrderPostMessage",
            work_order_data,
            lambda message: client.work_orders_api.external_api_work_orders_create_work_order(
                work_order_post_message=message
            ),
        )

    @mcp.tool()
    async def update_work_order(
        work_order_id: int, work_order_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an existing work order, merging changes onto the current record.

        ``work_order_data`` only needs the fields you want to change; the current
        work order is fetched first to supply required fields so partial edits
        succeed without a full schema.
        """

        async def _do_update() -> Any:
            api = client.work_orders_api
            current = await api.external_api_work_orders_get_work_order_by_id(
                work_order_id=work_order_id
            )
            merged = c.merge_update(current, work_order_data)
            message = c.build_model("work_order_put_message", "WorkOrderPutMessage", merged)
            return await api.external_api_work_orders_update_work_order(
                work_order_id=work_order_id, work_order_put_message=message
            )

        return await c.execute("update_work_order", _do_update)
