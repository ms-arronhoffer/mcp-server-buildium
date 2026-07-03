"""Bill management tools for Buildium."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c

BILL_PAID_STATUSES = {"Paid", "Unpaid", "UncollectedMarkups"}


def register_bill_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register bill-related tools with the MCP server."""

    c.register_operation("list_bills", "ExternalApiBills_GetBillsAsync")
    c.register_operation("get_bill", "ExternalApiBills_GetBillById")
    c.register_operation("create_bill", "ExternalApiBills_CreateBill")
    c.register_operation("update_bill", "ExternalApiBills_UpdateBill")
    c.register_operation("list_bill_payments", "ExternalApiBillPaymentsRead_GetBillPayments")
    c.register_operation("get_bill_payment", "ExternalApiBillPaymentsRead_GetBillPaymentById")
    c.register_operation("create_bill_payment", "ExternalApiBillPaymentsWrite_CreateBillPayment")

    @mcp.tool()
    async def list_bills(
        vendor_id: int | None = None,
        paid_status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List bills from Buildium.

        Args:
            vendor_id: Optional vendor ID to filter by.
            paid_status: Optional paid status (Paid, Unpaid, UncollectedMarkups).
            limit: Maximum number of results (1-1000, default: 100).
            offset: Zero-based pagination offset (default: 0).
        """
        limit, offset = c.clamp_pagination(limit, offset)
        try:
            paid_status = c.validate_enum(paid_status, BILL_PAID_STATUSES, field="paid_status")
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if vendor_id is not None:
            kwargs["vendorid"] = vendor_id
        if paid_status is not None:
            kwargs["paidstatus"] = paid_status
        return await c.execute(
            "list_bills",
            lambda: client.bills_api.external_api_bills_get_bills_async(**kwargs),
        )

    @mcp.tool()
    async def get_bill(bill_id: int) -> dict[str, Any]:
        """Get a specific bill by ID."""
        return await c.execute(
            "get_bill",
            lambda: client.bills_api.external_api_bills_get_bill_by_id(bill_id=bill_id),
        )

    @mcp.tool()
    async def create_bill(bill_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new bill."""
        message = c.build_model("bill_post_message", "BillPostMessage", bill_data)
        return await c.execute(
            "create_bill",
            lambda: client.bills_api.external_api_bills_create_bill(bill_post_message=message),
        )

    @mcp.tool()
    async def update_bill(bill_id: int, bill_data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing bill."""
        message = c.build_model("bill_put_message", "BillPutMessage", bill_data)
        return await c.execute(
            "update_bill",
            lambda: client.bills_api.external_api_bills_update_bill(
                bill_id=bill_id, bill_put_message=message
            ),
        )

    @mcp.tool()
    async def list_bill_payments(bill_id: int, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List payments for a specific bill."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_bill_payments",
            lambda: client.bills_api.external_api_bill_payments_read_get_bill_payments(
                bill_id=bill_id, limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def get_bill_payment(bill_id: int, payment_id: int) -> dict[str, Any]:
        """Get a specific bill payment by ID."""
        return await c.execute(
            "get_bill_payment",
            lambda: client.bills_api.external_api_bill_payments_read_get_bill_payment_by_id(
                bill_id=bill_id, payment_id=payment_id
            ),
        )

    @mcp.tool()
    async def create_bill_payment(bill_id: int, payment_data: dict[str, Any]) -> dict[str, Any]:
        """Create a payment for a bill."""
        message = c.build_model("bill_payment_post_message", "BillPaymentPostMessage", payment_data)
        return await c.execute(
            "create_bill_payment",
            lambda: client.bills_api.external_api_bill_payments_write_create_bill_payment(
                bill_id=bill_id, bill_payment_post_message=message
            ),
        )
