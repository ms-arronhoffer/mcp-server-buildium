"""Ownership account (association) ledger tools for Buildium.

Mirrors the lease-ledger surface for HOA ownership accounts: charges, payments,
credits, refunds, the full ledger, and outstanding balances. Financial writes
are automatically flagged sensitive by the shared classifier (see
``_common.classify_sensitive``).
"""

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c


def register_ownership_account_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register ownership-account ledger tools with the MCP server."""

    c.register_operation(
        "get_ownership_account", "ExternalApiOwnershipAccounts_GetOwnershipAccountById"
    )
    c.register_operation(
        "list_ownership_account_transactions",
        "ExternalApiOwnershipAccountsLedger_GetOwnershipAccountLedger",
    )
    c.register_operation(
        "get_ownership_account_transaction",
        "ExternalApiOwnershipAccountsLedger_GetOwnershipAccountTransactionById",
    )
    c.register_operation(
        "list_ownership_account_charges",
        "ExternalApiOwnershipAccountLedgerCharges_GetAllOwnershipAccountCharges",
    )
    c.register_operation(
        "get_ownership_account_charge",
        "ExternalApiOwnershipAccountLedgerCharges_GetOwnershipAccountChargeById",
    )
    c.register_operation(
        "create_ownership_account_charge",
        "ExternalApiOwnershipAccountLedgerCharges_CreateCharge",
    )
    c.register_operation(
        "update_ownership_account_charge",
        "ExternalApiOwnershipAccountLedgerCharges_UpdateOwnershipAccountCharge",
    )
    c.register_operation(
        "create_ownership_account_payment",
        "ExternalApiOwnershipAccountLedgerPayments_CreateOwnershipAccountLedgerPayment",
    )
    c.register_operation(
        "create_ownership_account_credit",
        "ExternalApiOwnershipAccountLedgerCredits_CreateOwnershipAccountCredit",
    )
    c.register_operation(
        "create_ownership_account_refund",
        "ExternalApiOwnershipAccountRefund_CreateOwnershipAccountRefund",
    )
    c.register_operation(
        "list_ownership_account_outstanding_balances",
        "ExternalApiOwnershipAccountOutstandingBalances_GetOwnershipAccountOutstandingBalances",
    )

    @mcp.tool()
    async def get_ownership_account(ownership_account_id: int) -> dict[str, Any]:
        """Get a specific association ownership account by ID."""
        return await c.execute(
            "get_ownership_account",
            lambda: (
                client.ownership_accounts_api.external_api_ownership_accounts_get_ownership_account_by_id(
                    ownership_account_id=ownership_account_id
                )
            ),
        )

    @mcp.tool()
    async def list_ownership_account_transactions(
        ownership_account_id: int, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List ledger transactions for a specific ownership account."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_ownership_account_transactions",
            lambda: (
                client.ownership_account_transactions_api.external_api_ownership_accounts_ledger_get_ownership_account_ledger(
                    ownership_account_id=ownership_account_id, limit=limit, offset=offset
                )
            ),
        )

    @mcp.tool()
    async def get_ownership_account_transaction(
        ownership_account_id: int, transaction_id: int
    ) -> dict[str, Any]:
        """Get a specific ownership account ledger transaction by ID."""
        return await c.execute(
            "get_ownership_account_transaction",
            lambda: (
                client.ownership_account_transactions_api.external_api_ownership_accounts_ledger_get_ownership_account_transaction_by_id(
                    ownership_account_id=ownership_account_id, transaction_id=transaction_id
                )
            ),
        )

    # -- Charges --------------------------------------------------------------
    @mcp.tool()
    async def list_ownership_account_charges(
        ownership_account_id: int, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List ledger charges for a specific ownership account."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_ownership_account_charges",
            lambda: (
                client.ownership_account_transactions_api.external_api_ownership_account_ledger_charges_get_all_ownership_account_charges(
                    ownership_account_id=ownership_account_id, limit=limit, offset=offset
                )
            ),
        )

    @mcp.tool()
    async def get_ownership_account_charge(
        ownership_account_id: int, charge_id: int
    ) -> dict[str, Any]:
        """Get a specific ownership account ledger charge by ID."""
        return await c.execute(
            "get_ownership_account_charge",
            lambda: (
                client.ownership_account_transactions_api.external_api_ownership_account_ledger_charges_get_ownership_account_charge_by_id(
                    ownership_account_id=ownership_account_id, charge_id=charge_id
                )
            ),
        )

    @mcp.tool()
    async def create_ownership_account_charge(
        ownership_account_id: int, charge_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a ledger charge on an ownership account (e.g. a dues assessment)."""
        return await c.create(
            "create_ownership_account_charge",
            "ownership_account_ledger_charge_post_message",
            "OwnershipAccountLedgerChargePostMessage",
            charge_data,
            lambda message: (
                client.ownership_account_transactions_api.external_api_ownership_account_ledger_charges_create_charge(
                    ownership_account_id=ownership_account_id,
                    ownership_account_ledger_charge_post_message=message,
                )
            ),
        )

    @mcp.tool()
    async def update_ownership_account_charge(
        ownership_account_id: int, charge_id: int, charge_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an ownership account charge, merging changes onto the current record."""

        async def _do_update() -> Any:
            api = client.ownership_account_transactions_api
            current = await api.external_api_ownership_account_ledger_charges_get_ownership_account_charge_by_id(
                ownership_account_id=ownership_account_id, charge_id=charge_id
            )
            merged = c.merge_update(current, charge_data)
            message = c.build_model(
                "ownership_account_ledger_charge_put_message",
                "OwnershipAccountLedgerChargePutMessage",
                merged,
            )
            return await api.external_api_ownership_account_ledger_charges_update_ownership_account_charge(
                ownership_account_id=ownership_account_id,
                charge_id=charge_id,
                ownership_account_ledger_charge_put_message=message,
            )

        return await c.execute("update_ownership_account_charge", _do_update)

    # -- Payments, credits & refunds -----------------------------------------
    @mcp.tool()
    async def create_ownership_account_payment(
        ownership_account_id: int, payment_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Record a payment against an ownership account ledger."""
        return await c.create(
            "create_ownership_account_payment",
            "ownership_account_ledger_payment_post_message",
            "OwnershipAccountLedgerPaymentPostMessage",
            payment_data,
            lambda message: (
                client.ownership_account_transactions_api.external_api_ownership_account_ledger_payments_create_ownership_account_ledger_payment(
                    ownership_account_id=ownership_account_id,
                    ownership_account_ledger_payment_post_message=message,
                )
            ),
        )

    @mcp.tool()
    async def create_ownership_account_credit(
        ownership_account_id: int, credit_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Issue a credit on an ownership account ledger."""
        return await c.create(
            "create_ownership_account_credit",
            "ownership_account_credit_post_message",
            "OwnershipAccountCreditPostMessage",
            credit_data,
            lambda message: (
                client.ownership_account_transactions_api.external_api_ownership_account_ledger_credits_create_ownership_account_credit(
                    ownership_account_id=ownership_account_id,
                    ownership_account_credit_post_message=message,
                )
            ),
        )

    @mcp.tool()
    async def create_ownership_account_refund(
        ownership_account_id: int, refund_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Issue a refund on an ownership account ledger."""
        return await c.create(
            "create_ownership_account_refund",
            "ownership_account_refund_post_message",
            "OwnershipAccountRefundPostMessage",
            refund_data,
            lambda message: (
                client.ownership_account_transactions_api.external_api_ownership_account_refund_create_ownership_account_refund(
                    ownership_account_id=ownership_account_id,
                    ownership_account_refund_post_message=message,
                )
            ),
        )

    @mcp.tool()
    async def list_ownership_account_outstanding_balances(
        association_id: int | None = None, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List outstanding balances across ownership accounts.

        Args:
            association_id: Optional association ID to filter by.
            limit: Maximum number of results (1-1000, default: 100).
            offset: Zero-based pagination offset (default: 0).
        """
        limit, offset = c.clamp_pagination(limit, offset)
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if association_id is not None:
            kwargs["associationid"] = association_id
        return await c.execute(
            "list_ownership_account_outstanding_balances",
            lambda: (
                client.ownership_account_transactions_api.external_api_ownership_account_outstanding_balances_get_ownership_account_outstanding_balances(
                    **kwargs
                )
            ),
        )
