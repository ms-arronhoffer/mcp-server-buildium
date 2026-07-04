"""Budget management tools for Buildium.

Supports financial planning workflows and pairs with the general-ledger tools.
Budget writes are automatically flagged sensitive by the shared classifier.
"""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c


def register_budget_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register budget-related tools with the MCP server."""

    c.register_operation("list_budgets", "ExternalApiBudgets_GetBudgets")
    c.register_operation("get_budget", "ExternalApiBudgets_GetBudgetById")
    c.register_operation("create_budget", "ExternalApiBudgets_CreateBudgetAsync")
    c.register_operation("update_budget", "ExternalApiBudgets_UpdateBudget")

    @mcp.tool()
    async def list_budgets(
        fiscal_year: int | None = None, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List budgets from Buildium.

        Args:
            fiscal_year: Optional fiscal year to filter by.
            limit: Maximum number of results (1-1000, default: 100).
            offset: Zero-based pagination offset (default: 0).
        """
        limit, offset = c.clamp_pagination(limit, offset)
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if fiscal_year is not None:
            kwargs["fiscalyear"] = fiscal_year
        return await c.execute(
            "list_budgets",
            lambda: client.budgets_api.external_api_budgets_get_budgets(**kwargs),
        )

    @mcp.tool()
    async def get_budget(budget_id: int) -> dict[str, Any]:
        """Get a specific budget by ID."""
        return await c.execute(
            "get_budget",
            lambda: client.budgets_api.external_api_budgets_get_budget_by_id(budget_id=budget_id),
        )

    @mcp.tool()
    async def create_budget(budget_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new budget."""
        return await c.create(
            "create_budget",
            "budget_post_message",
            "BudgetPostMessage",
            budget_data,
            lambda message: client.budgets_api.external_api_budgets_create_budget_async(
                budget_post_message=message
            ),
        )

    @mcp.tool()
    async def update_budget(budget_id: int, budget_data: dict[str, Any]) -> dict[str, Any]:
        """Update a budget, merging changes onto the current record.

        ``budget_data`` only needs the fields you want to change; the current
        budget is fetched first to supply required fields so partial edits
        succeed without a full schema.
        """

        async def _do_update() -> Any:
            api = client.budgets_api
            current = await api.external_api_budgets_get_budget_by_id(budget_id=budget_id)
            merged = c.merge_update(current, budget_data)
            message = c.build_model("budget_put_message", "BudgetPutMessage", merged)
            return await api.external_api_budgets_update_budget(
                budget_id=budget_id, budget_put_message=message
            )

        return await c.execute("update_budget", _do_update)
