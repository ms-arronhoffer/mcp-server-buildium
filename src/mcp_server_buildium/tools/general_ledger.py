"""General ledger tools for Buildium."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c

GL_ACCOUNT_TYPES = {"Asset", "Liability", "Equity", "Income", "Expense"}


def register_general_ledger_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register general-ledger tools with the MCP server."""

    c.register_operation("list_gl_accounts", "ExternalApiGeneralLedgerAccounts_GetAllGLAccounts")
    c.register_operation("get_gl_account", "ExternalApiGeneralLedgerAccounts_GetGlAccountById")
    c.register_operation(
        "list_gl_transactions", "ExternalApiGeneralLedgerTransactions_GetAllTransactions"
    )
    c.register_operation(
        "get_gl_transaction", "ExternalApiGeneralLedgerTransactions_GetTransactionById"
    )

    @mcp.tool()
    async def list_gl_accounts(
        account_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List general ledger accounts from Buildium.

        Args:
            account_type: Optional account type (Asset, Liability, Equity, Income, Expense).
            limit: Maximum number of results (1-1000, default: 100).
            offset: Zero-based pagination offset (default: 0).
        """
        limit, offset = c.clamp_pagination(limit, offset)
        try:
            account_type = c.validate_enum(account_type, GL_ACCOUNT_TYPES, field="account_type")
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if account_type is not None:
            kwargs["accounttypes"] = [account_type]
        return await c.execute(
            "list_gl_accounts",
            lambda: (
                client.general_ledger_api.external_api_general_ledger_accounts_get_all_gl_accounts(
                    **kwargs
                )
            ),
        )

    @mcp.tool()
    async def get_gl_account(gl_account_id: int) -> dict[str, Any]:
        """Get a specific general ledger account by ID."""
        return await c.execute(
            "get_gl_account",
            lambda: (
                client.general_ledger_api.external_api_general_ledger_accounts_get_gl_account_by_id(
                    gl_account_id=gl_account_id
                )
            ),
        )

    @mcp.tool()
    async def list_gl_transactions(
        start_date: str,
        end_date: str,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List general ledger transactions within a date range.

        Args:
            start_date: Start date (YYYY-MM-DD), required by Buildium.
            end_date: End date (YYYY-MM-DD), required by Buildium.
            limit: Maximum number of results (1-1000, default: 100).
            offset: Zero-based pagination offset (default: 0).
        """
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_gl_transactions",
            lambda: (
                client.general_ledger_api.external_api_general_ledger_transactions_get_all_transactions(
                    startdate=start_date, enddate=end_date, limit=limit, offset=offset
                )
            ),
        )

    @mcp.tool()
    async def get_gl_transaction(transaction_id: int) -> dict[str, Any]:
        """Get a specific general ledger transaction by ID."""
        return await c.execute(
            "get_gl_transaction",
            lambda: (
                client.general_ledger_api.external_api_general_ledger_transactions_get_transaction_by_id(
                    transaction_id=transaction_id
                )
            ),
        )
