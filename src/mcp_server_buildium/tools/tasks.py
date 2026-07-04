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
    c.register_operation("list_task_history", "ExternalApiTaskHistory_GetTaskHistories")
    c.register_operation("get_task_history", "ExternalApiTaskHistory_GetTaskHistoryById")
    c.register_operation("update_task_history", "ExternalApiTaskHistory_UpdateTaskHistory")
    c.register_operation(
        "list_contact_requests", "ExternalApiContactRequestTasks_GetContactRequestTasks"
    )
    c.register_operation(
        "get_contact_request", "ExternalApiContactRequestTasks_GetContactRequestTaskById"
    )
    c.register_operation(
        "create_contact_request", "ExternalApiContactRequestTasks_CreateContactRequestTask"
    )
    c.register_operation(
        "update_contact_request", "ExternalApiContactRequestTasks_UpdateContactRequestTask"
    )
    c.register_operation("list_todo_requests", "ExternalApiToDoTasks_GetToDoTasks")
    c.register_operation("get_todo_request", "ExternalApiToDoTasks_GetToDoTaskById")
    c.register_operation("create_todo_request", "ExternalApiToDoTasks_CreateToDoTask")
    c.register_operation("update_todo_request", "ExternalApiToDoTasks_UpdateToDoTask")
    c.register_operation(
        "list_resident_requests", "ExternalApiResidentRequestTasks_GetResidentRequestTasks"
    )
    c.register_operation(
        "get_resident_request", "ExternalApiResidentRequestTasks_GetResidentRequestTask"
    )
    c.register_operation(
        "create_resident_request", "ExternalApiResidentRequestTasks_CreateResource"
    )
    c.register_operation(
        "update_resident_request", "ExternalApiResidentRequestTasks_UpdateResource"
    )
    c.register_operation(
        "list_rental_owner_requests",
        "ExternalApiRentalOwnerRequestTasks_GetAllRentalOwnerRequestTasks",
    )
    c.register_operation(
        "get_rental_owner_request",
        "ExternalApiRentalOwnerRequestTasks_GetRentalOwnerRequestTaskById",
    )
    c.register_operation(
        "create_rental_owner_request",
        "ExternalApiRentalOwnerRequestTasks_CreateRentalOwnerRequestTask",
    )
    c.register_operation(
        "update_rental_owner_request",
        "ExternalApiRentalOwnerRequestTasks_UpdateRentalOwnerRequestTask",
    )

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
        return await c.create(
            "create_task_category",
            "task_category_post_message",
            "TaskCategoryPostMessage",
            category_data,
            lambda message: client.tasks_api.external_api_task_categories_create_task_category(
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

    # -- Task history (status changes, notes, assignments) --------------------
    @mcp.tool()
    async def list_task_history(task_id: int, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List the history entries (status changes, notes) for a task."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_task_history",
            lambda: client.tasks_api.external_api_task_history_get_task_histories(
                task_id=task_id, limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def get_task_history(task_id: int, task_history_id: int) -> dict[str, Any]:
        """Get a specific task history entry by ID."""
        return await c.execute(
            "get_task_history",
            lambda: client.tasks_api.external_api_task_history_get_task_history_by_id(
                task_id=task_id, task_history_id=task_history_id
            ),
        )

    @mcp.tool()
    async def update_task_history(
        task_id: int, task_history_id: int, history_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a task history entry, merging changes onto the current record.

        Use this to advance a task's status, add notes, or reassign it.
        ``history_data`` only needs the fields you want to change.
        """

        async def _do_update() -> Any:
            api = client.tasks_api
            current = await api.external_api_task_history_get_task_history_by_id(
                task_id=task_id, task_history_id=task_history_id
            )
            merged = c.merge_update(current, history_data)
            message = c.build_model("task_history_put_message", "TaskHistoryPutMessage", merged)
            return await api.external_api_task_history_update_task_history(
                task_id=task_id, task_history_id=task_history_id, task_history_put_message=message
            )

        return await c.execute("update_task_history", _do_update)

    # -- Contact request tasks ------------------------------------------------
    @mcp.tool()
    async def list_contact_requests(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List contact request tasks (resident/prospect enquiries)."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_contact_requests",
            lambda: client.contact_requests_api.external_api_contact_request_tasks_get_contact_request_tasks(
                limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def get_contact_request(contact_request_task_id: int) -> dict[str, Any]:
        """Get a specific contact request task by ID."""
        return await c.execute(
            "get_contact_request",
            lambda: client.contact_requests_api.external_api_contact_request_tasks_get_contact_request_task_by_id(
                contact_request_task_id=contact_request_task_id
            ),
        )

    @mcp.tool()
    async def create_contact_request(task_data: dict[str, Any]) -> dict[str, Any]:
        """Create a contact request task."""
        return await c.create(
            "create_contact_request",
            "contact_request_task_post_message",
            "ContactRequestTaskPostMessage",
            task_data,
            lambda message: client.contact_requests_api.external_api_contact_request_tasks_create_contact_request_task(
                contact_request_task_post_message=message
            ),
        )

    @mcp.tool()
    async def update_contact_request(
        contact_request_task_id: int, task_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a contact request task, merging changes onto the current record."""

        async def _do_update() -> Any:
            api = client.contact_requests_api
            current = await api.external_api_contact_request_tasks_get_contact_request_task_by_id(
                contact_request_task_id=contact_request_task_id
            )
            merged = c.merge_update(current, task_data)
            message = c.build_model(
                "contact_request_task_put_message", "ContactRequestTaskPutMessage", merged
            )
            return await api.external_api_contact_request_tasks_update_contact_request_task(
                contact_request_task_id=contact_request_task_id,
                contact_request_task_put_message=message,
            )

        return await c.execute("update_contact_request", _do_update)

    # -- To-do tasks ----------------------------------------------------------
    @mcp.tool()
    async def list_todo_requests(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List to-do tasks."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_todo_requests",
            lambda: client.to_do_requests_api.external_api_to_do_tasks_get_to_do_tasks(
                limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def get_todo_request(to_do_task_id: int) -> dict[str, Any]:
        """Get a specific to-do task by ID."""
        return await c.execute(
            "get_todo_request",
            lambda: client.to_do_requests_api.external_api_to_do_tasks_get_to_do_task_by_id(
                to_do_task_id=to_do_task_id
            ),
        )

    @mcp.tool()
    async def create_todo_request(task_data: dict[str, Any]) -> dict[str, Any]:
        """Create a to-do task."""
        return await c.create(
            "create_todo_request",
            "to_do_task_post_message",
            "ToDoTaskPostMessage",
            task_data,
            lambda message: client.to_do_requests_api.external_api_to_do_tasks_create_to_do_task(
                to_do_task_post_message=message
            ),
        )

    @mcp.tool()
    async def update_todo_request(to_do_task_id: int, task_data: dict[str, Any]) -> dict[str, Any]:
        """Update a to-do task, merging changes onto the current record."""

        async def _do_update() -> Any:
            api = client.to_do_requests_api
            current = await api.external_api_to_do_tasks_get_to_do_task_by_id(
                to_do_task_id=to_do_task_id
            )
            merged = c.merge_update(current, task_data)
            message = c.build_model("to_do_task_put_message", "ToDoTaskPutMessage", merged)
            return await api.external_api_to_do_tasks_update_to_do_task(
                to_do_task_id=to_do_task_id, to_do_task_put_message=message
            )

        return await c.execute("update_todo_request", _do_update)

    # -- Resident request tasks ----------------------------------------------
    @mcp.tool()
    async def list_resident_requests(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List resident request tasks (maintenance/service requests from residents)."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_resident_requests",
            lambda: client.resident_requests_api.external_api_resident_request_tasks_get_resident_request_tasks(
                limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def get_resident_request(resident_request_task_id: int) -> dict[str, Any]:
        """Get a specific resident request task by ID."""
        return await c.execute(
            "get_resident_request",
            lambda: client.resident_requests_api.external_api_resident_request_tasks_get_resident_request_task(
                resident_request_task_id=resident_request_task_id
            ),
        )

    @mcp.tool()
    async def create_resident_request(task_data: dict[str, Any]) -> dict[str, Any]:
        """Create a resident request task."""
        return await c.create(
            "create_resident_request",
            "resident_request_task_post_message",
            "ResidentRequestTaskPostMessage",
            task_data,
            lambda message: client.resident_requests_api.external_api_resident_request_tasks_create_resource(
                resident_request_task_post_message=message
            ),
        )

    @mcp.tool()
    async def update_resident_request(
        resident_request_task_id: int, task_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a resident request task, merging changes onto the current record."""

        async def _do_update() -> Any:
            api = client.resident_requests_api
            current = await api.external_api_resident_request_tasks_get_resident_request_task(
                resident_request_task_id=resident_request_task_id
            )
            merged = c.merge_update(current, task_data)
            message = c.build_model(
                "resident_request_task_put_message", "ResidentRequestTaskPutMessage", merged
            )
            return await api.external_api_resident_request_tasks_update_resource(
                resident_request_task_id=resident_request_task_id,
                resident_request_task_put_message=message,
            )

        return await c.execute("update_resident_request", _do_update)

    # -- Rental owner request tasks ------------------------------------------
    @mcp.tool()
    async def list_rental_owner_requests() -> dict[str, Any]:
        """List rental owner request tasks."""
        return await c.execute(
            "list_rental_owner_requests",
            lambda: client.rental_owner_requests_api.external_api_rental_owner_request_tasks_get_all_rental_owner_request_tasks(),
        )

    @mcp.tool()
    async def get_rental_owner_request(rental_owner_request_task_id: int) -> dict[str, Any]:
        """Get a specific rental owner request task by ID."""
        return await c.execute(
            "get_rental_owner_request",
            lambda: client.rental_owner_requests_api.external_api_rental_owner_request_tasks_get_rental_owner_request_task_by_id(
                rental_owner_request_task_id=rental_owner_request_task_id
            ),
        )

    @mcp.tool()
    async def create_rental_owner_request(task_data: dict[str, Any]) -> dict[str, Any]:
        """Create a rental owner request task."""
        return await c.create(
            "create_rental_owner_request",
            "rental_owner_request_task_post_message",
            "RentalOwnerRequestTaskPostMessage",
            task_data,
            lambda message: client.rental_owner_requests_api.external_api_rental_owner_request_tasks_create_rental_owner_request_task(
                rental_owner_request_task_post_message=message
            ),
        )

    @mcp.tool()
    async def update_rental_owner_request(
        rental_owner_request_task_id: int, task_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a rental owner request task, merging changes onto the current record."""

        async def _do_update() -> Any:
            api = client.rental_owner_requests_api
            current = await api.external_api_rental_owner_request_tasks_get_rental_owner_request_task_by_id(
                rental_owner_request_task_id=rental_owner_request_task_id
            )
            merged = c.merge_update(current, task_data)
            message = c.build_model(
                "rental_owner_request_task_put_message", "RentalOwnerRequestTaskPutMessage", merged
            )
            return (
                await api.external_api_rental_owner_request_tasks_update_rental_owner_request_task(
                    rental_owner_request_task_id=rental_owner_request_task_id,
                    rental_owner_request_task_put_message=message,
                )
            )

        return await c.execute("update_rental_owner_request", _do_update)
