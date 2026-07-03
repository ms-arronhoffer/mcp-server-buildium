"""Vendor management tools for Buildium."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c

VENDOR_STATUSES = {"Active", "Inactive"}

# Buildium GET returns the vendor's category as a lookup object (``{"Id", "Name"}``)
# and phone numbers as a list, but the create/update messages want ``CategoryId``
# and a keyed ``PhoneNumbers`` object. These map the GET shape onto the POST/PUT
# shape so a caller can reuse the record it just read (see _common.reshape_input).
_VENDOR_LOOKUP_IDS = {"Category": "CategoryId"}


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
        """Create a new vendor.

        ``vendor_data`` may reuse the shape a GET returns: a ``Category`` lookup
        object (``{"Id": 1}``) is accepted in place of ``CategoryId``, and phone
        numbers may be supplied either as the keyed object form
        (``{"phone_numbers": {"mobile": "..."}}``) or the GET-style list.
        """
        vendor_data = c.reshape_input(
            vendor_data, reshape_phones=True, lookup_ids=_VENDOR_LOOKUP_IDS
        )
        return await c.create(
            "create_vendor",
            "vendor_post_message",
            "VendorPostMessage",
            vendor_data,
            lambda message: client.vendors_api.external_api_vendors_create_vendor(
                vendor_post_message=message
            ),
        )

    @mcp.tool()
    async def update_vendor(vendor_id: int, vendor_data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing vendor, merging changes onto the current record.

        ``vendor_data`` only needs the fields you want to change; the current
        vendor is fetched first to supply required fields so partial edits
        succeed without a full schema. Keys may use JSON aliases or field names,
        and phone numbers use the keyed object form, e.g.
        ``{"phone_numbers": {"mobile": "555-555-5555"}}``. The category may be
        given as ``{"category_id": 1}`` or a ``{"Category": {"Id": 1}}`` lookup
        object; if omitted, the vendor's existing category is preserved.
        """

        async def _do_update() -> Any:
            api = client.vendors_api
            current = await api.external_api_vendors_get_vendor_by_id(vendor_id=vendor_id)
            merged = c.merge_update(
                current, vendor_data, reshape_phones=True, lookup_ids=_VENDOR_LOOKUP_IDS
            )
            message = c.build_model("vendor_put_message", "VendorPutMessage", merged)
            return await api.external_api_vendors_update_vendor(
                vendor_id=vendor_id, vendor_put_message=message
            )

        return await c.execute("update_vendor", _do_update)

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
        return await c.create(
            "create_vendor_category",
            "vendor_category_save_message",
            "VendorCategorySaveMessage",
            category_data,
            lambda message: client.vendors_api.external_api_vendor_categories_create_vendor_category(
                vendor_category_save_message=message
            ),
        )

    @mcp.tool()
    async def update_vendor_category(
        category_id: int, category_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an existing vendor category, merging changes onto the current record.

        ``category_data`` only needs the fields you want to change; the current
        category is fetched first to supply required fields so partial edits
        succeed without a full schema.
        """

        async def _do_update() -> Any:
            api = client.vendors_api
            current = await api.external_api_vendor_categories_get_vendor_category_by_id(
                vendor_category_id=category_id
            )
            merged = c.merge_update(current, category_data)
            message = c.build_model(
                "vendor_category_save_message", "VendorCategorySaveMessage", merged
            )
            return await api.external_api_vendor_categories_update_vendor_category(
                vendor_category_id=category_id, vendor_category_save_message=message
            )

        return await c.execute("update_vendor_category", _do_update)
