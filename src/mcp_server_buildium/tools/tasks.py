"""Task management tools for Buildium."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c

TASK_STATUSES = {"New", "InProgress", "Completed", "Deferred", "Closed"}


def register_task_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register task-related tools with the MCP server."""

    c.register_operation("list_tasks", "ExternalApiTasks_GetAllTasks")
    c.register_operation("get_task", "ExternalApiTasks_GetTaskById")
    c.register_operation("list_task_categories", "ExternalApiTaskCategories_GetAllTaskCategories")
    c.register_operation("create_task_category", "ExternalApiTaskCategories_CreateTaskCategory")
    c.register_operation("update_task_category", "ExternalApiTaskCategories_UpdateTaskCategory")

    @mcp.tool()
    async def list_tasks(
        task_status: str | None = None,
        assigned_to_user_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List tasks from Buildium.

        Args:
            task_status: Optional status filter (New, InProgress, Completed, Deferred, Closed).
            assigned_to_user_id: Optional filter by assigned user ID.
            limit: Maximum number of results (1-1000, default: 100).
            offset: Zero-based pagination offset (default: 0).
        """
        limit, offset = c.clamp_pagination(limit, offset)
        try:
            task_status = c.validate_enum(task_status, TASK_STATUSES, field="task_status")
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if task_status is not None:
            kwargs["statuses"] = [task_status]
        if assigned_to_user_id is not None:
            kwargs["assignedtoid"] = assigned_to_user_id
        return await c.execute(
            "list_tasks",
            lambda: client.tasks_api.external_api_tasks_get_all_tasks(**kwargs),
        )

    @mcp.tool()
    async def get_task(task_id: int) -> dict[str, Any]:
        """Get a specific task by ID."""
        return await c.execute(
            "get_task",
            lambda: client.tasks_api.external_api_tasks_get_task_by_id(task_id=task_id),
        )

    @mcp.tool()
    async def list_task_categories(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List task categories from Buildium."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_task_categories",
            lambda: client.tasks_api.external_api_task_categories_get_all_task_categories(
                limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def create_task_category(category_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new task category."""
        message = c.build_model(
            "task_category_post_message", "TaskCategoryPostMessage", category_data
        )
        return await c.execute(
            "create_task_category",
            lambda: client.tasks_api.external_api_task_categories_create_task_category(
                task_category_post_message=message
            ),
        )

    @mcp.tool()
    async def update_task_category(
        category_id: int, category_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a task category, merging changes onto the current record.

        ``category_data`` only needs the fields you want to change; the current
        category is fetched first to supply required fields so partial edits
        succeed without a full schema.
        """

        async def _do_update() -> Any:
            api = client.tasks_api
            current = await api.external_api_task_categories_get_task_category_by_id(
                task_category_id=category_id
            )
            merged = c.merge_update(current, category_data)
            message = c.build_model("task_category_put_message", "TaskCategoryPutMessage", merged)
            return await api.external_api_task_categories_update_task_category(
                task_category_id=category_id, task_category_put_message=message
            )

        return await c.execute("update_task_category", _do_update)
