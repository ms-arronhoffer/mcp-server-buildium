"""Vendor management tools for Buildium."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c

VENDOR_STATUSES = {"Active", "Inactive"}


def register_vendor_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register vendor-related tools with the MCP server."""

    c.register_operation("list_vendors", "ExternalApiVendors_GetAllVendors")
    c.register_operation("get_vendor", "ExternalApiVendors_GetVendorById")
    c.register_operation("create_vendor", "ExternalApiVendors_CreateVendor")
    c.register_operation("update_vendor", "ExternalApiVendors_UpdateVendor")
    c.register_operation(
        "list_vendor_categories", "ExternalApiVendorCategories_GetAllVendorCategories"
    )
    c.register_operation(
        "create_vendor_category", "ExternalApiVendorCategories_CreateVendorCategory"
    )
    c.register_operation(
        "update_vendor_category", "ExternalApiVendorCategories_UpdateVendorCategory"
    )

    @mcp.tool()
    async def list_vendors(
        status: str | None = None, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List vendors from Buildium, optionally filtered by status (Active/Inactive)."""
        limit, offset = c.clamp_pagination(limit, offset)
        try:
            status = c.validate_enum(status, VENDOR_STATUSES, field="status")
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            kwargs["status"] = status
        return await c.execute(
            "list_vendors",
            lambda: client.vendors_api.external_api_vendors_get_all_vendors(**kwargs),
        )

    @mcp.tool()
    async def get_vendor(vendor_id: int) -> dict[str, Any]:
        """Get a specific vendor by ID."""
        return await c.execute(
            "get_vendor",
            lambda: client.vendors_api.external_api_vendors_get_vendor_by_id(vendor_id=vendor_id),
        )

    @mcp.tool()
    async def create_vendor(vendor_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new vendor."""
        message = c.build_model("vendor_post_message", "VendorPostMessage", vendor_data)
        return await c.execute(
            "create_vendor",
            lambda: client.vendors_api.external_api_vendors_create_vendor(
                vendor_post_message=message
            ),
        )

    @mcp.tool()
    async def update_vendor(vendor_id: int, vendor_data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing vendor."""
        message = c.build_model("vendor_put_message", "VendorPutMessage", vendor_data)
        return await c.execute(
            "update_vendor",
            lambda: client.vendors_api.external_api_vendors_update_vendor(
                vendor_id=vendor_id, vendor_put_message=message
            ),
        )

    @mcp.tool()
    async def list_vendor_categories(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List vendor categories from Buildium."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_vendor_categories",
            lambda: client.vendors_api.external_api_vendor_categories_get_all_vendor_categories(
                limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def create_vendor_category(category_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new vendor category."""
        message = c.build_model(
            "vendor_category_save_message", "VendorCategorySaveMessage", category_data
        )
        return await c.execute(
            "create_vendor_category",
            lambda: client.vendors_api.external_api_vendor_categories_create_vendor_category(
                vendor_category_save_message=message
            ),
        )

    @mcp.tool()
    async def update_vendor_category(
        category_id: int, category_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an existing vendor category."""
        message = c.build_model(
            "vendor_category_save_message", "VendorCategorySaveMessage", category_data
        )
        return await c.execute(
            "update_vendor_category",
            lambda: client.vendors_api.external_api_vendor_categories_update_vendor_category(
                vendor_category_id=category_id, vendor_category_save_message=message
            ),
        )
