"""File management tools for Buildium."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c


def register_file_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register file-related tools with the MCP server."""

    c.register_operation("list_files", "ExternalApiFiles_GetFiles")
    c.register_operation("get_file", "ExternalApiFiles_GetFileById")
    c.register_operation("update_file", "ExternalApiFiles_UpdateFile")
    c.register_operation(
        "create_file_upload_request", "ExternalApiFilesUploads_CreateUploadRequestAsync"
    )
    c.register_operation(
        "create_file_download_request", "ExternalApiFileDownload_GetFileDownloadUrlAsync"
    )
    c.register_operation("list_file_categories", "ExternalApiFileCategories_GetFileCategories")
    c.register_operation("create_file_category", "ExternalApiFileCategories_CreateFileCategory")
    c.register_operation("update_file_category", "ExternalApiFileCategories_UpdateFileCategory")

    @mcp.tool()
    async def list_files(
        entity_type: str | None = None,
        entity_id: int | None = None,
        category_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List files from Buildium, optionally filtered by entity/category."""
        limit, offset = c.clamp_pagination(limit, offset)
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if entity_type is not None:
            kwargs["entitytype"] = entity_type
        if entity_id is not None:
            kwargs["entityid"] = entity_id
        if category_id is not None:
            kwargs["categoryid"] = category_id
        return await c.execute(
            "list_files",
            lambda: client.files_api.external_api_files_get_files(**kwargs),
        )

    @mcp.tool()
    async def get_file(file_id: int) -> dict[str, Any]:
        """Get a specific file by ID."""
        return await c.execute(
            "get_file",
            lambda: client.files_api.external_api_files_get_file_by_id(file_id=file_id),
        )

    @mcp.tool()
    async def update_file(file_id: int, file_data: dict[str, Any]) -> dict[str, Any]:
        """Update file metadata, merging changes onto the current record.

        ``file_data`` only needs the fields you want to change; the current file
        is fetched first to supply required fields so partial edits succeed
        without a full schema.
        """

        async def _do_update() -> Any:
            api = client.files_api
            current = await api.external_api_files_get_file_by_id(file_id=file_id)
            merged = c.merge_update(current, file_data)
            message = c.build_model("file_put_message", "FilePutMessage", merged)
            return await api.external_api_files_update_file(
                file_id=file_id, file_put_message=message
            )

        return await c.execute("update_file", _do_update)

    @mcp.tool()
    async def create_file_upload_request(upload_request: dict[str, Any]) -> dict[str, Any]:
        """Create a file upload request to obtain an upload URL."""
        message = c.build_model("file_upload_post_message", "FileUploadPostMessage", upload_request)
        return await c.execute(
            "create_file_upload_request",
            lambda: client.files_api.external_api_files_uploads_create_upload_file_request_async(
                file_upload_post_message=message
            ),
        )

    @mcp.tool()
    async def create_file_download_request(file_id: int) -> dict[str, Any]:
        """Create a file download request to obtain a download URL."""
        return await c.execute(
            "create_file_download_request",
            lambda: client.files_api.external_api_file_download_get_file_download_url_async(
                file_id=file_id
            ),
        )

    @mcp.tool()
    async def list_file_categories(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List file categories from Buildium."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_file_categories",
            lambda: client.files_api.external_api_file_categories_get_file_categories(
                limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def create_file_category(category_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new file category."""
        message = c.build_model(
            "file_category_post_message", "FileCategoryPostMessage", category_data
        )
        return await c.execute(
            "create_file_category",
            lambda: client.files_api.external_api_file_categories_create_file_category(
                file_category_post_message=message
            ),
        )

    @mcp.tool()
    async def update_file_category(
        category_id: int, category_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an existing file category, merging changes onto the current record.

        ``category_data`` only needs the fields you want to change; the current
        category is fetched first to supply required fields so partial edits
        succeed without a full schema.
        """

        async def _do_update() -> Any:
            api = client.files_api
            current = await api.external_api_file_categories_get_file_category_by_id(
                file_category_id=category_id
            )
            merged = c.merge_update(current, category_data)
            message = c.build_model("file_category_put_message", "FileCategoryPutMessage", merged)
            return await api.external_api_file_categories_update_file_category(
                file_category_id=category_id, file_category_put_message=message
            )

        return await c.execute("update_file_category", _do_update)
