"""Unit management tools for Buildium (rental and association)."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c


def register_unit_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register unit-related tools with the MCP server."""

    c.register_operation("list_rental_units", "ExternalApiRentalUnits_GetAllRentalUnits")
    c.register_operation("get_rental_unit", "ExternalApiRentalUnits_GetRentalUnitById")
    c.register_operation("create_rental_unit", "ExternalApiRentalUnits_CreateRentalUnit")
    c.register_operation("update_rental_unit", "ExternalApiRentalUnits_UpdateRentalUnit")
    c.register_operation(
        "list_association_units", "ExternalApiAssociationUnits_GetAllAssociationUnits"
    )
    c.register_operation(
        "create_association_unit", "ExternalApiAssociationUnits_CreateAssociationUnit"
    )
    c.register_operation(
        "update_association_unit", "ExternalApiAssociationUnits_UpdateAssociationUnit"
    )

    # Rental Units
    @mcp.tool()
    async def list_rental_units(
        property_id: int | None = None, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List rental units from Buildium, optionally filtered by property."""
        limit, offset = c.clamp_pagination(limit, offset)
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if property_id is not None:
            kwargs["propertyids"] = [property_id]
        return await c.execute(
            "list_rental_units",
            lambda: client.rental_units_api.external_api_rental_units_get_all_rental_units(
                **kwargs
            ),
        )

    @mcp.tool()
    async def get_rental_unit(unit_id: int) -> dict[str, Any]:
        """Get a specific rental unit by ID."""
        return await c.execute(
            "get_rental_unit",
            lambda: client.rental_units_api.external_api_rental_units_get_rental_unit_by_id(
                unit_id=unit_id
            ),
        )

    @mcp.tool()
    async def create_rental_unit(unit_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new rental unit."""
        return await c.create(
            "create_rental_unit",
            "rental_unit_post_message",
            "RentalUnitPostMessage",
            unit_data,
            lambda message: client.rental_units_api.external_api_rental_units_create_rental_unit(
                rental_unit_post_message=message
            ),
        )

    @mcp.tool()
    async def update_rental_unit(unit_id: int, unit_data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing rental unit, merging changes onto the current record.

        ``unit_data`` only needs the fields you want to change; the current unit
        is fetched first to supply required fields so partial edits succeed
        without a full schema.
        """

        async def _do_update() -> Any:
            api = client.rental_units_api
            current = await api.external_api_rental_units_get_rental_unit_by_id(unit_id=unit_id)
            merged = c.merge_update(current, unit_data)
            message = c.build_model("rental_unit_put_message", "RentalUnitPutMessage", merged)
            return await api.external_api_rental_units_update_rental_unit(
                unit_id=unit_id, rental_unit_put_message=message
            )

        return await c.execute("update_rental_unit", _do_update)

    # Association Units
    @mcp.tool()
    async def list_association_units(
        association_id: int | None = None, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List association units from Buildium, optionally filtered by association."""
        limit, offset = c.clamp_pagination(limit, offset)
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if association_id is not None:
            kwargs["associationids"] = [association_id]
        return await c.execute(
            "list_association_units",
            lambda: (
                client.association_units_api.external_api_association_units_get_all_association_units(
                    **kwargs
                )
            ),
        )

    @mcp.tool()
    async def create_association_unit(unit_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new association unit."""
        return await c.create(
            "create_association_unit",
            "association_unit_post_message",
            "AssociationUnitPostMessage",
            unit_data,
            lambda message: (
                client.association_units_api.external_api_association_units_create_association_unit(
                    association_unit_post_message=message
                )
            ),
        )

    @mcp.tool()
    async def update_association_unit(unit_id: int, unit_data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing association unit, merging changes onto the current record.

        ``unit_data`` only needs the fields you want to change; the current unit
        is fetched first to supply required fields so partial edits succeed
        without a full schema.
        """

        async def _do_update() -> Any:
            api = client.association_units_api
            current = await api.external_api_association_units_get_association_unit_by_id(
                unit_id=unit_id
            )
            merged = c.merge_update(current, unit_data)
            message = c.build_model(
                "association_unit_put_message", "AssociationUnitPutMessage", merged
            )
            return await api.external_api_association_units_update_association_unit(
                unit_id=unit_id, association_unit_put_message=message
            )

        return await c.execute("update_association_unit", _do_update)
