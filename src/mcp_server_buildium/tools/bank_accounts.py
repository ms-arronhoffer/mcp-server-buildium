"""Bank account management tools for Buildium."""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c


def register_bank_account_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register bank account-related tools with the MCP server."""

    c.register_operation("list_bank_accounts", "ExternalApiBankAccounts_GetAllBankAccounts")
    c.register_operation("get_bank_account", "ExternalApiBankAccounts_GetBankAccount")
    c.register_operation("create_bank_account", "ExternalApiBankAccounts_CreateBankAccount")
    c.register_operation("update_bank_account", "ExternalApiBankAccounts_UpdateBankAccount")
    c.register_operation(
        "list_bank_account_transactions",
        "ExternalApiBankAccountTransactions_GetBankAccountTransactions",
    )
    c.register_operation(
        "get_bank_account_transaction",
        "ExternalApiBankAccountTransactions_GetBankAccountTransactionById",
    )

    @mcp.tool()
    async def list_bank_accounts(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List bank accounts from Buildium."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_bank_accounts",
            lambda: client.bank_accounts_api.external_api_bank_accounts_get_all_bank_accounts(
                limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    async def get_bank_account(bank_account_id: int) -> dict[str, Any]:
        """Get a specific bank account by ID."""
        return await c.execute(
            "get_bank_account",
            lambda: client.bank_accounts_api.external_api_bank_accounts_get_bank_account(
                bank_account_id=bank_account_id
            ),
        )

    @mcp.tool()
    async def create_bank_account(bank_account_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new bank account."""
        message = c.build_model(
            "bank_account_post_message", "BankAccountPostMessage", bank_account_data
        )
        return await c.execute(
            "create_bank_account",
            lambda: client.bank_accounts_api.external_api_bank_accounts_create_bank_account(
                bank_account_post_message=message
            ),
        )

    @mcp.tool()
    async def update_bank_account(
        bank_account_id: int, bank_account_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an existing bank account, merging changes onto the current record.

        ``bank_account_data`` only needs the fields you want to change; the
        current bank account is fetched first to supply required fields so
        partial edits succeed without a full schema.
        """

        async def _do_update() -> Any:
            api = client.bank_accounts_api
            current = await api.external_api_bank_accounts_get_bank_account(
                bank_account_id=bank_account_id
            )
            merged = c.merge_update(current, bank_account_data)
            message = c.build_model("bank_account_put_message", "BankAccountPutMessage", merged)
            return await api.external_api_bank_accounts_update_bank_account(
                bank_account_id=bank_account_id, bank_account_put_message=message
            )

        return await c.execute("update_bank_account", _do_update)

    @mcp.tool()
    async def list_bank_account_transactions(
        bank_account_id: int, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List transactions for a specific bank account."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_bank_account_transactions",
            lambda: (
                client.bank_accounts_api.external_api_bank_account_transactions_get_bank_account_transactions(
                    bank_account_id=bank_account_id, limit=limit, offset=offset
                )
            ),
        )

    @mcp.tool()
    async def get_bank_account_transaction(
        bank_account_id: int, transaction_id: int
    ) -> dict[str, Any]:
        """Get a specific bank account transaction by ID."""
        return await c.execute(
            "get_bank_account_transaction",
            lambda: (
                client.bank_accounts_api.external_api_bank_account_transactions_get_bank_account_transaction_by_id(
                    bank_account_id=bank_account_id, transaction_id=transaction_id
                )
            ),
        )
