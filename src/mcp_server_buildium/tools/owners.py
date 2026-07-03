"""Owner management tools for Buildium (rental and association)."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c


def register_owner_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register owner-related tools with the MCP server."""

    c.register_operation("list_rental_owners", "ExternalApiRentalOwners_GetRentalOwners")
    c.register_operation("get_rental_owner", "ExternalApiRentalOwners_GetRentalOwnerById")
    c.register_operation("create_rental_owner", "ExternalApiRentalOwners_CreateRentalOwner")
    c.register_operation("update_rental_owner", "ExternalApiRentalOwners_UpdateRentalOwner")
    c.register_operation(
        "list_association_owners", "ExternalApiAssociationOwners_GetAllAssociationOwners"
    )
    c.register_operation(
        "get_association_owner", "ExternalApiAssociationOwners_GetAssociationOwnerById"
    )
    c.register_operation(
        "create_association_owner", "ExternalApiAssociationOwners_CreateAssociationOwner"
    )
    c.register_operation(
        "update_association_owner", "ExternalApiAssociationOwners_UpdateAssociationOwner"
    )

    # Rental Owners
    @mcp.tool()
    async def list_rental_owners(
        property_id: int | None = None, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List rental owners from Buildium, optionally filtered by property."""
        limit, offset = c.clamp_pagination(limit, offset)
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if property_id is not None:
            kwargs["propertyids"] = [property_id]
        return await c.execute(
            "list_rental_owners",
            lambda: client.rental_owners_api.external_api_rental_owners_get_rental_owners(**kwargs),
        )

    @mcp.tool()
    async def get_rental_owner(owner_id: int) -> dict[str, Any]:
        """Get a specific rental owner by ID."""
        return await c.execute(
            "get_rental_owner",
            lambda: client.rental_owners_api.external_api_rental_owners_get_rental_owner_by_id(
                rental_owner_id=owner_id
            ),
        )

    @mcp.tool()
    async def create_rental_owner(owner_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new rental owner."""
        message = c.build_model("rental_owner_post_message", "RentalOwnerPostMessage", owner_data)
        return await c.execute(
            "create_rental_owner",
            lambda: client.rental_owners_api.external_api_rental_owners_create_rental_owner(
                rental_owner_post_message=message
            ),
        )

    @mcp.tool()
    async def update_rental_owner(owner_id: int, owner_data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing rental owner."""
        message = c.build_model("rental_owner_put_message", "RentalOwnerPutMessage", owner_data)
        return await c.execute(
            "update_rental_owner",
            lambda: client.rental_owners_api.external_api_rental_owners_update_rental_owner(
                rental_owner_id=owner_id, rental_owner_put_message=message
            ),
        )

    # Association Owners
    @mcp.tool()
    async def list_association_owners(
        association_id: int | None = None, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List association owners from Buildium, optionally filtered by association."""
        limit, offset = c.clamp_pagination(limit, offset)
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if association_id is not None:
            kwargs["associationids"] = [association_id]
        return await c.execute(
            "list_association_owners",
            lambda: (
                client.association_owners_api.external_api_association_owners_get_all_association_owners(
                    **kwargs
                )
            ),
        )

    @mcp.tool()
    async def get_association_owner(owner_id: int) -> dict[str, Any]:
        """Get a specific association owner by ID."""
        return await c.execute(
            "get_association_owner",
            lambda: (
                client.association_owners_api.external_api_association_owners_get_association_owner_by_id(
                    owner_id=owner_id
                )
            ),
        )

    @mcp.tool()
    async def create_association_owner(owner_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new association owner on an existing ownership account."""
        message = c.build_model(
            "association_owner_to_existing_ownership_account_post_message",
            "AssociationOwnerToExistingOwnershipAccountPostMessage",
            owner_data,
        )
        return await c.execute(
            "create_association_owner",
            lambda: (
                client.association_owners_api.external_api_association_owners_create_association_owner(
                    association_owner_to_existing_ownership_account_post_message=message
                )
            ),
        )

    @mcp.tool()
    async def update_association_owner(owner_id: int, owner_data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing association owner."""
        message = c.build_model(
            "association_owner_put_message", "AssociationOwnerPutMessage", owner_data
        )
        return await c.execute(
            "update_association_owner",
            lambda: (
                client.association_owners_api.external_api_association_owners_update_association_owner(
                    owner_id=owner_id, association_owner_put_message=message
                )
            ),
        )
