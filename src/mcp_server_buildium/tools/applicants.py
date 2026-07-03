"""Applicant management tools for Buildium."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c


def register_applicant_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register applicant-related tools with the MCP server."""

    c.register_operation("list_applicants", "ExternalApiApplicants_GetApplicants")
    c.register_operation("get_applicant", "ExternalApiApplicants_GetApplicantById")
    c.register_operation("create_applicant", "ExternalApiApplicants_CreateApplicant")
    c.register_operation("update_applicant", "ExternalApiApplicants_UpdateApplicant")
    c.register_operation(
        "list_applicant_applications",
        "ExternalApiApplicantApplications_GetApplicationsForApplicant",
    )
    c.register_operation(
        "get_application", "ExternalApiApplicantApplications_GetApplicationForApplicantById"
    )
    c.register_operation("update_application", "ExternalApiApplicantApplications_UpdateApplication")
    c.register_operation("list_applicant_groups", "ExternalApiApplicantGroups_GetApplicantGroups")
    c.register_operation(
        "create_applicant_group", "ExternalApiApplicantGroups_CreateApplicantGroup"
    )
    c.register_operation(
        "update_applicant_group", "ExternalApiApplicantGroups_UpdateApplicantGroup"
    )

    @mcp.tool()
    async def list_applicants(
        email: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List rental applicants from Buildium.

        Args:
            email: Optional email address to filter by.
            limit: Maximum number of results (1-1000, default: 100).
            offset: Zero-based pagination offset (default: 0).
        """
        limit, offset = c.clamp_pagination(limit, offset)
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if email is not None:
            kwargs["email"] = email
        return await c.execute(
            "list_applicants",
            lambda: client.applicants_api.external_api_applicants_get_applicants(**kwargs),
        )

    @mcp.tool()
    async def get_applicant(applicant_id: int) -> dict[str, Any]:
        """Get a specific applicant by ID."""
        return await c.execute(
            "get_applicant",
            lambda: client.applicants_api.external_api_applicants_get_applicant_by_id(
                applicant_id=applicant_id
            ),
        )

    @mcp.tool()
    async def create_applicant(applicant_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new applicant."""
        message = c.build_model("applicant_post_message", "ApplicantPostMessage", applicant_data)
        return await c.execute(
            "create_applicant",
            lambda: client.applicants_api.external_api_applicants_create_applicant(
                applicant_post_message=message
            ),
        )

    @mcp.tool()
    async def update_applicant(applicant_id: int, applicant_data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing applicant."""
        message = c.build_model("applicant_put_message", "ApplicantPutMessage", applicant_data)
        return await c.execute(
            "update_applicant",
            lambda: client.applicants_api.external_api_applicants_update_applicant(
                applicant_id=applicant_id, applicant_put_message=message
            ),
        )

    @mcp.tool()
    async def list_applicant_applications(applicant_id: int) -> dict[str, Any]:
        """List applications for a specific applicant."""
        return await c.execute(
            "list_applicant_applications",
            lambda: (
                client.applicants_api.external_api_applicant_applications_get_applications_for_applicant(
                    applicant_id=applicant_id
                )
            ),
        )

    @mcp.tool()
    async def get_application(applicant_id: int, application_id: int) -> dict[str, Any]:
        """Get a specific application by ID."""
        return await c.execute(
            "get_application",
            lambda: (
                client.applicants_api.external_api_applicant_applications_get_application_for_applicant_by_id(
                    applicant_id=applicant_id, application_id=application_id
                )
            ),
        )

    @mcp.tool()
    async def update_application(
        applicant_id: int, application_id: int, application_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an application status."""
        message = c.build_model(
            "application_put_message", "ApplicationPutMessage", application_data
        )
        return await c.execute(
            "update_application",
            lambda: client.applicants_api.external_api_applicant_applications_update_application(
                applicant_id=applicant_id,
                application_id=application_id,
                application_put_message=message,
            ),
        )

    @mcp.tool()
    async def list_applicant_groups(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List applicant groups from Buildium."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_applicant_groups",
            lambda: client.applicants_api.external_api_applicant_groups_get_applicant_groups(
                limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def create_applicant_group(group_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new applicant group."""
        message = c.build_model(
            "applicant_group_post_message", "ApplicantGroupPostMessage", group_data
        )
        return await c.execute(
            "create_applicant_group",
            lambda: client.applicants_api.external_api_applicant_groups_create_applicant_group(
                applicant_group_post_message=message
            ),
        )

    @mcp.tool()
    async def update_applicant_group(group_id: int, group_data: dict[str, Any]) -> dict[str, Any]:
        """Update an applicant group."""
        message = c.build_model(
            "applicant_group_put_message", "ApplicantGroupPutMessage", group_data
        )
        return await c.execute(
            "update_applicant_group",
            lambda: client.applicants_api.external_api_applicant_groups_update_applicant_group(
                applicant_group_id=group_id, applicant_group_put_message=message
            ),
        )
