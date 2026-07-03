"""Rental property management tools for Buildium."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c

try:
    from mcp_server_buildium.buildium_sdk.models.rental_property_put_message import (
        RentalPropertyPutMessage,
    )
except ImportError:  # pragma: no cover

    class RentalPropertyPutMessage:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)


def register_rental_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register rental-related tools with the MCP server."""

    c.register_operation("list_rentals", "ExternalApiRentals_GetAllRentals")
    c.register_operation("get_rental", "ExternalApiRentals_GetRentalById")
    c.register_operation("create_rental", "ExternalApiRentals_CreateRentalProperty")
    c.register_operation("update_rental", "ExternalApiRentals_UpdateRentalProperty")
    c.register_operation("list_unit_listings", "ExternalApiListings_GetListingsAsync")

    @mcp.tool()
    async def list_rentals(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List rental properties from Buildium."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_rentals",
            lambda: client.rentals_api.external_api_rentals_get_all_rentals(
                limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def get_rental(property_id: int) -> dict[str, Any]:
        """Get a specific rental property by ID."""
        return await c.execute(
            "get_rental",
            lambda: client.rentals_api.external_api_rentals_get_rental_by_id(
                property_id=property_id
            ),
        )

    @mcp.tool()
    async def create_rental(rental_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new rental property."""
        return await c.create(
            "create_rental",
            "rental_property_post_message",
            "RentalPropertyPostMessage",
            rental_data,
            lambda message: client.rentals_api.external_api_rentals_create_rental_property(
                rental_property_post_message=message
            ),
        )

    @mcp.tool()
    async def update_rental(property_id: int, rental_data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing rental property, merging changes onto the current record.

        ``rental_data`` only needs the fields you want to change; the current
        property is fetched first to supply required fields so partial edits
        succeed without a full schema.
        """

        async def _do_update() -> Any:
            current = await client.rentals_api.external_api_rentals_get_rental_by_id(
                property_id=property_id
            )
            merged = c.merge_update(current, rental_data)
            message = RentalPropertyPutMessage(**merged)
            return await client.rentals_api.external_api_rentals_update_rental_property(
                property_id=property_id, rental_property_put_message=message
            )

        return await c.execute("update_rental", _do_update)

    @mcp.tool()
    async def list_unit_listings(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List unit listings from Buildium."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_unit_listings",
            lambda: client.listings_api.external_api_listings_get_listings_async(
                limit=limit, offset=offset
            ),
        )
