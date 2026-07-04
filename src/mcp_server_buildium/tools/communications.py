"""Communications tools for Buildium (announcements, phone logs, mailing templates).

Enables resident/owner outreach: create and manage announcements, log and manage
phone calls, and read mailing templates.

Note: the email feature (list/get/create emails and email recipients) is
currently stashed and intentionally not registered. The Buildium email
operations remain available in the SDK and can be restored from git history.
"""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c


def register_communication_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register communications tools with the MCP server."""

    c.register_operation("list_announcements", "ExternalApiAnnouncements_GetAllAnnouncements")
    c.register_operation("get_announcement", "ExternalApiAnnouncements_GetAnnouncementById")
    c.register_operation("create_announcement", "ExternalApiAnnouncements_CreateAnnouncement")
    c.register_operation(
        "expire_announcement", "ExternalApiAnnouncementsExpiration_ExpireAnnouncement"
    )
    c.register_operation("list_phone_logs", "ExternalApiPhoneLogs_GetPhoneLogs")
    c.register_operation("get_phone_log", "ExternalApiPhoneLogs_GetPhoneLogById")
    c.register_operation("create_phone_log", "ExternalApiPhoneLogs_CreatePhoneLog")
    c.register_operation("update_phone_log", "ExternalApiPhoneLogs_UpdatePhoneLog")
    c.register_operation(
        "list_mailing_templates", "ExternalApiMailingTemplates_GetMailingTemplates"
    )
    c.register_operation(
        "get_mailing_template", "ExternalApiMailingTemplates_GetMailingTemplatesById"
    )

    # -- Announcements --------------------------------------------------------
    @mcp.tool()
    async def list_announcements(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List announcements sent to residents/owners."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_announcements",
            lambda: client.communications_api.external_api_announcements_get_all_announcements(
                limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def get_announcement(announcement_id: int) -> dict[str, Any]:
        """Get a specific announcement by ID."""
        return await c.execute(
            "get_announcement",
            lambda: client.communications_api.external_api_announcements_get_announcement_by_id(
                announcement_id=announcement_id
            ),
        )

    @mcp.tool()
    async def create_announcement(announcement_data: dict[str, Any]) -> dict[str, Any]:
        """Create (send) a new announcement."""
        return await c.create(
            "create_announcement",
            "announcement_post_message",
            "AnnouncementPostMessage",
            announcement_data,
            lambda message: client.communications_api.external_api_announcements_create_announcement(
                announcement_post_message=message
            ),
        )

    @mcp.tool()
    async def expire_announcement(announcement_id: int) -> dict[str, Any]:
        """Expire (retract) an active announcement."""
        return await c.execute(
            "expire_announcement",
            lambda: client.communications_api.external_api_announcements_expiration_expire_announcement(
                announcement_id=announcement_id
            ),
        )

    # -- Emails ---------------------------------------------------------------
    # The email feature is stashed: list_emails / get_email / create_email /
    # list_email_recipients are intentionally not registered. Restore from git
    # history to re-enable resident/owner email outreach.

    # -- Phone logs -----------------------------------------------------------
    @mcp.tool()
    async def list_phone_logs(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List phone call logs."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_phone_logs",
            lambda: client.communications_api.external_api_phone_logs_get_phone_logs(
                limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def get_phone_log(phone_log_id: int) -> dict[str, Any]:
        """Get a specific phone log by ID."""
        return await c.execute(
            "get_phone_log",
            lambda: client.communications_api.external_api_phone_logs_get_phone_log_by_id(
                phone_log_id=phone_log_id
            ),
        )

    @mcp.tool()
    async def create_phone_log(phone_log_data: dict[str, Any]) -> dict[str, Any]:
        """Create a phone call log entry."""
        return await c.create(
            "create_phone_log",
            "phone_log_post_message",
            "PhoneLogPostMessage",
            phone_log_data,
            lambda message: client.communications_api.external_api_phone_logs_create_phone_log(
                phone_log_post_message=message
            ),
        )

    @mcp.tool()
    async def update_phone_log(phone_log_id: int, phone_log_data: dict[str, Any]) -> dict[str, Any]:
        """Update a phone log, merging changes onto the current record."""

        async def _do_update() -> Any:
            api = client.communications_api
            current = await api.external_api_phone_logs_get_phone_log_by_id(
                phone_log_id=phone_log_id
            )
            merged = c.merge_update(current, phone_log_data)
            message = c.build_model("phone_log_put_message", "PhoneLogPutMessage", merged)
            return await api.external_api_phone_logs_update_phone_log(
                phone_log_id=phone_log_id, phone_log_put_message=message
            )

        return await c.execute("update_phone_log", _do_update)

    # -- Mailing templates ----------------------------------------------------
    @mcp.tool()
    async def list_mailing_templates(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List available mailing templates."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_mailing_templates",
            lambda: client.communications_api.external_api_mailing_templates_get_mailing_templates(
                limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def get_mailing_template(template_id: int) -> dict[str, Any]:
        """Get a specific mailing template by ID."""
        return await c.execute(
            "get_mailing_template",
            lambda: client.communications_api.external_api_mailing_templates_get_mailing_templates_by_id(
                template_id=template_id
            ),
        )
