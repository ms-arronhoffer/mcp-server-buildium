"""Association management tools for Buildium."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c

try:
    from mcp_server_buildium.buildium_sdk.models.association_post_message import (
        AssociationPostMessage,
    )
    from mcp_server_buildium.buildium_sdk.models.association_put_message import (
        AssociationPutMessage,
    )
except ImportError:  # pragma: no cover

    class AssociationPostMessage:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class AssociationPutMessage:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)


def register_association_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register association-related tools with the MCP server."""

    c.register_operation("list_associations", "ExternalApiAssociations_GetAssociations")
    c.register_operation("get_association", "ExternalApiAssociations_GetAssociationById")
    c.register_operation("create_association", "ExternalApiAssociations_CreateAssociation")
    c.register_operation("update_association", "ExternalApiAssociations_UpdateAssociation")
    c.register_operation(
        "list_association_board_members",
        "ExternalApiAssociationBoardMembers_GetAllAssociationBoardMembers",
    )
    c.register_operation(
        "list_association_ownership_accounts",
        "ExternalApiOwnershipAccounts_GetAllOwnershipAccounts",
    )

    @mcp.tool()
    async def list_associations(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List associations from Buildium.

        Args:
            limit: Maximum number of results (1-1000, default: 100).
            offset: Zero-based pagination offset (default: 0).

        Returns:
            Envelope ``{data, count, error}`` with the associations list.
        """
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_associations",
            lambda: client.associations_api.external_api_associations_get_associations(
                limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def get_association(association_id: int) -> dict[str, Any]:
        """Get a specific association by ID."""
        return await c.execute(
            "get_association",
            lambda: client.associations_api.external_api_associations_get_association_by_id(
                association_id=association_id
            ),
        )

    @mcp.tool()
    async def create_association(association_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new association."""
        message = AssociationPostMessage(**association_data)
        return await c.execute(
            "create_association",
            lambda: client.associations_api.external_api_associations_create_association(
                association_post_message=message
            ),
        )

    @mcp.tool()
    async def update_association(
        association_id: int, association_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an existing association."""
        message = AssociationPutMessage(**association_data)
        return await c.execute(
            "update_association",
            lambda: client.associations_api.external_api_associations_update_association(
                association_id=association_id, association_put_message=message
            ),
        )

    @mcp.tool()
    async def list_association_board_members(
        association_id: int, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List board members for a specific association."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_association_board_members",
            lambda: (
                client.board_members_api.external_api_association_board_members_get_all_association_board_members(
                    association_id=association_id, limit=limit, offset=offset
                )
            ),
        )

    @mcp.tool()
    async def list_association_ownership_accounts(
        association_id: int, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List ownership accounts for a specific association."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_association_ownership_accounts",
            lambda: (
                client.ownership_accounts_api.external_api_ownership_accounts_get_all_ownership_accounts(
                    associationids=[association_id], limit=limit, offset=offset
                )
            ),
        )
