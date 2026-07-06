"""Lease management tools for Buildium."""

import inspect
from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from . import _common as c

try:
    from mcp_server_buildium.buildium_sdk.models.lease_put_message import LeasePutMessage
except ImportError:  # pragma: no cover

    class LeasePutMessage:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)


LEASE_STATUSES = {"Active", "Past", "Future"}


async def _resolve_property_name(
    client: BuildiumClient, property_id: Any, cache: dict[Any, str | None]
) -> str | None:
    """Resolve a rental property's display name from its id (best-effort, cached).

    A lease read model only carries ``PropertyId``/``UnitId``, so on its own the
    assistant can only render a bare "property 4". Looking the property up lets it
    show the human-friendly name (e.g. "Riverside Commons"). Failures are swallowed
    so enrichment never turns a successful lease read into an error.
    """
    if property_id is None:
        return None
    if property_id in cache:
        return cache[property_id]
    name: str | None = None
    try:
        prop = await client.rentals_api.external_api_rentals_get_rental_by_id(
            property_id=property_id
        )
        if isinstance(prop, list):
            prop = prop[0] if prop else None
        if hasattr(prop, "to_dict"):
            prop = prop.to_dict()
        if isinstance(prop, dict):
            resolved = prop.get("Name")
            name = resolved if isinstance(resolved, str) and resolved.strip() else None
    except Exception:  # noqa: BLE001 - enrichment is best-effort, never fatal
        name = None
    cache[property_id] = name
    return name


async def _attach_property_name(
    client: BuildiumClient, lease: Any, cache: dict[Any, str | None]
) -> Any:
    """Attach a ``PropertyName`` to a serialized lease dict when it can be resolved."""
    if not isinstance(lease, dict) or lease.get("PropertyName"):
        return lease
    name = await _resolve_property_name(client, lease.get("PropertyId"), cache)
    if name:
        lease["PropertyName"] = name
    return lease


async def enrich_leases_with_property_name(client: BuildiumClient, result: Any) -> Any:
    """Await/serialize a lease read result and enrich each lease with ``PropertyName``.

    Accepts an awaitable (the SDK call), a single lease model/dict, or a list of
    them, resolves each distinct ``PropertyId`` once (via an in-call cache), and
    returns plain JSON-ready data.
    """
    if inspect.isawaitable(result):
        result = await result
    data = c._serialize(result)
    cache: dict[Any, str | None] = {}
    if isinstance(data, list):
        for item in data:
            await _attach_property_name(client, item, cache)
    else:
        await _attach_property_name(client, data, cache)
    return data


def register_lease_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register lease-related tools with the MCP server."""

    c.register_operation("list_leases", "ExternalApiLeases_GetLeases")
    c.register_operation("get_lease", "ExternalApiLeases_GetLeaseById")
    c.register_operation("create_lease", "ExternalApiLeases_CreateLease")
    c.register_operation("update_lease", "ExternalApiLeases_UpdateLease")
    c.register_operation(
        "list_lease_transactions", "ExternalApiLeaseLedgerTransactions_GetLeaseLedgers"
    )
    c.register_operation(
        "get_lease_transaction",
        "ExternalApiLeaseLedgerTransactions_GetLeaseLedgerTransactionById",
    )
    c.register_operation("list_lease_charges", "ExternalApiLeaseLedgerChargesRead_GetAllCharges")
    c.register_operation("get_lease_charge", "ExternalApiLeaseLedgerChargesRead_GetChargeById")
    c.register_operation("create_lease_charge", "ExternalApiLeaseLedgerChargesWrite_CreateCharge")
    c.register_operation(
        "update_lease_charge", "ExternalApiLeaseLedgerChargesWrite_UpdateLeaseCharge"
    )
    c.register_operation(
        "create_lease_payment", "ExternalApiLeaseLedgerPaymentsWrite_CreatePayment"
    )
    c.register_operation(
        "update_lease_payment", "ExternalApiLeaseLedgerPaymentsWrite_UpdateLeaseLedgerPayment"
    )
    c.register_operation(
        "create_lease_credit", "ExternalApiLeaseLedgerCreditsWrite_CreateLeaseCredit"
    )
    c.register_operation(
        "create_lease_refund", "ExternalApiLeaseLedgerRefunds_CreateLeaseLedgerRefund"
    )
    c.register_operation(
        "get_lease_refund", "ExternalApiLeaseLedgerRefunds_GetLeaseLedgerRefundById"
    )
    c.register_operation(
        "list_lease_recurring_transactions",
        "ExternalApiLeaseRecurringTransactions_GetLeaseRecurringTransactions",
    )
    c.register_operation(
        "list_lease_outstanding_balances",
        "ExternalApiLeaseOutstandingBalances_GetLeaseOutstandingBalances",
    )

    @mcp.tool()
    async def list_leases(
        property_id: int | None = None,
        lease_status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List leases from Buildium.

        Args:
            property_id: Optional property ID to filter by.
            lease_status: Optional status filter (one of: Active, Past, Future).
            limit: Maximum number of results (1-1000, default: 100).
            offset: Zero-based pagination offset (default: 0).
        """
        limit, offset = c.clamp_pagination(limit, offset)
        try:
            lease_status = c.validate_enum(lease_status, LEASE_STATUSES, field="lease_status")
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")

        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if property_id is not None:
            kwargs["propertyids"] = [property_id]
        if lease_status is not None:
            kwargs["leasestatuses"] = [lease_status]

        return await c.execute(
            "list_leases",
            lambda: enrich_leases_with_property_name(
                client,
                client.leases_api.external_api_leases_get_leases(**kwargs),
            ),
        )

    @mcp.tool()
    async def get_lease(lease_id: int) -> dict[str, Any]:
        """Get a specific lease by ID."""
        return await c.execute(
            "get_lease",
            lambda: enrich_leases_with_property_name(
                client,
                client.leases_api.external_api_leases_get_lease_by_id(lease_id=lease_id),
            ),
        )

    @mcp.tool()
    async def create_lease(lease_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new lease."""
        return await c.create(
            "create_lease",
            "lease_post_message",
            "LeasePostMessage",
            lease_data,
            lambda message: client.leases_api.external_api_leases_create_lease(
                lease_post_message=message
            ),
        )

    @mcp.tool()
    async def update_lease(lease_id: int, lease_data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing lease, merging changes onto the current record.

        ``lease_data`` only needs the fields you want to change; the current
        lease is fetched first to supply required fields so partial edits succeed
        without a full schema.
        """

        async def _do_update() -> Any:
            current = await client.leases_api.external_api_leases_get_lease_by_id(lease_id=lease_id)
            merged = c.merge_update(current, lease_data)
            message = LeasePutMessage(**merged)
            return await client.leases_api.external_api_leases_update_lease(
                lease_id=lease_id, lease_put_message=message
            )

        return await c.execute("update_lease", _do_update)

    @mcp.tool()
    async def list_lease_transactions(
        lease_id: int, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List ledger transactions for a specific lease."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_lease_transactions",
            lambda: (
                client.lease_transactions_api.external_api_lease_ledger_transactions_get_lease_ledgers(
                    lease_id=lease_id, limit=limit, offset=offset
                )
            ),
        )

    @mcp.tool()
    async def get_lease_transaction(lease_id: int, transaction_id: int) -> dict[str, Any]:
        """Get a specific lease ledger transaction by ID."""
        return await c.execute(
            "get_lease_transaction",
            lambda: (
                client.lease_transactions_api.external_api_lease_ledger_transactions_get_lease_ledger_transaction_by_id(
                    lease_id=lease_id, transaction_id=transaction_id
                )
            ),
        )

    # -- Charges --------------------------------------------------------------
    @mcp.tool()
    async def list_lease_charges(
        lease_id: int, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List ledger charges for a specific lease."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_lease_charges",
            lambda: (
                client.lease_transactions_api.external_api_lease_ledger_charges_read_get_all_charges(
                    lease_id=lease_id, limit=limit, offset=offset
                )
            ),
        )

    @mcp.tool()
    async def get_lease_charge(lease_id: int, charge_id: int) -> dict[str, Any]:
        """Get a specific lease ledger charge by ID."""
        return await c.execute(
            "get_lease_charge",
            lambda: (
                client.lease_transactions_api.external_api_lease_ledger_charges_read_get_charge_by_id(
                    lease_id=lease_id, charge_id=charge_id
                )
            ),
        )

    @mcp.tool()
    async def create_lease_charge(lease_id: int, charge_data: dict[str, Any]) -> dict[str, Any]:
        """Create a ledger charge on a lease (e.g. rent, a fee)."""
        return await c.create(
            "create_lease_charge",
            "lease_charge_post_message",
            "LeaseChargePostMessage",
            charge_data,
            lambda message: (
                client.lease_transactions_api.external_api_lease_ledger_charges_write_create_charge(
                    lease_id=lease_id, lease_charge_post_message=message
                )
            ),
        )

    @mcp.tool()
    async def update_lease_charge(
        lease_id: int, charge_id: int, charge_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a lease ledger charge, merging changes onto the current record.

        ``charge_data`` only needs the fields you want to change; the current
        charge is fetched first to supply required fields so partial edits
        succeed without a full schema.
        """

        async def _do_update() -> Any:
            api = client.lease_transactions_api
            current = await api.external_api_lease_ledger_charges_read_get_charge_by_id(
                lease_id=lease_id, charge_id=charge_id
            )
            merged = c.merge_update(current, charge_data)
            message = c.build_model("lease_charge_put_message", "LeaseChargePutMessage", merged)
            return await api.external_api_lease_ledger_charges_write_update_lease_charge(
                lease_id=lease_id, charge_id=charge_id, lease_charge_put_message=message
            )

        return await c.execute("update_lease_charge", _do_update)

    # -- Payments -------------------------------------------------------------
    @mcp.tool()
    async def create_lease_payment(lease_id: int, payment_data: dict[str, Any]) -> dict[str, Any]:
        """Record a payment against a lease ledger."""
        return await c.create(
            "create_lease_payment",
            "lease_ledger_payment_post_message",
            "LeaseLedgerPaymentPostMessage",
            payment_data,
            lambda message: (
                client.lease_transactions_api.external_api_lease_ledger_payments_write_create_payment(
                    lease_id=lease_id, lease_ledger_payment_post_message=message
                )
            ),
        )

    @mcp.tool()
    async def update_lease_payment(
        lease_id: int, payment_id: int, payment_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a lease ledger payment, merging changes onto the current record."""

        async def _do_update() -> Any:
            api = client.lease_transactions_api
            current = (
                await api.external_api_lease_ledger_transactions_get_lease_ledger_transaction_by_id(
                    lease_id=lease_id, transaction_id=payment_id
                )
            )
            merged = c.merge_update(current, payment_data)
            message = c.build_model(
                "lease_ledger_payment_put_message", "LeaseLedgerPaymentPutMessage", merged
            )
            return await api.external_api_lease_ledger_payments_write_update_lease_ledger_payment(
                lease_id=lease_id, payment_id=payment_id, lease_ledger_payment_put_message=message
            )

        return await c.execute("update_lease_payment", _do_update)

    # -- Credits & refunds ----------------------------------------------------
    @mcp.tool()
    async def create_lease_credit(lease_id: int, credit_data: dict[str, Any]) -> dict[str, Any]:
        """Issue a credit on a lease ledger."""
        return await c.create(
            "create_lease_credit",
            "lease_ledger_credit_post_message",
            "LeaseLedgerCreditPostMessage",
            credit_data,
            lambda message: (
                client.lease_transactions_api.external_api_lease_ledger_credits_write_create_lease_credit(
                    lease_id=lease_id, lease_ledger_credit_post_message=message
                )
            ),
        )

    @mcp.tool()
    async def create_lease_refund(lease_id: int, refund_data: dict[str, Any]) -> dict[str, Any]:
        """Issue a refund on a lease ledger."""
        return await c.create(
            "create_lease_refund",
            "lease_ledger_refund_post_message",
            "LeaseLedgerRefundPostMessage",
            refund_data,
            lambda message: (
                client.lease_transactions_api.external_api_lease_ledger_refunds_create_lease_ledger_refund(
                    lease_id=lease_id, lease_ledger_refund_post_message=message
                )
            ),
        )

    @mcp.tool()
    async def get_lease_refund(lease_id: int, refund_id: int) -> dict[str, Any]:
        """Get a specific lease ledger refund by ID."""
        return await c.execute(
            "get_lease_refund",
            lambda: (
                client.lease_transactions_api.external_api_lease_ledger_refunds_get_lease_ledger_refund_by_id(
                    lease_id=lease_id, refund_id=refund_id
                )
            ),
        )

    # -- Recurring & balances -------------------------------------------------
    @mcp.tool()
    async def list_lease_recurring_transactions(
        lease_id: int, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List the recurring transactions (charges, credits, payments) for a lease."""
        limit, offset = c.clamp_pagination(limit, offset)
        return await c.execute(
            "list_lease_recurring_transactions",
            lambda: (
                client.lease_transactions_api.external_api_lease_recurring_transactions_get_lease_recurring_transactions(
                    lease_id=lease_id, limit=limit, offset=offset
                )
            ),
        )

    @mcp.tool()
    async def list_lease_outstanding_balances(
        lease_status: str | None = None, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List outstanding balances across leases.

        Args:
            lease_status: Optional status filter (one of: Active, Past, Future).
            limit: Maximum number of results (1-1000, default: 100).
            offset: Zero-based pagination offset (default: 0).
        """
        limit, offset = c.clamp_pagination(limit, offset)
        try:
            lease_status = c.validate_enum(lease_status, LEASE_STATUSES, field="lease_status")
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
        if lease_status is not None:
            kwargs["leasestatuses"] = [lease_status]
        return await c.execute(
            "list_lease_outstanding_balances",
            lambda: (
                client.lease_transactions_api.external_api_lease_outstanding_balances_get_lease_outstanding_balances(
                    **kwargs
                )
            ),
        )

    c.register_local_tool("lease_receivables_summary", op_type="read", sensitive=True)

    @mcp.tool()
    async def lease_receivables_summary(
        lease_status: str = "Active", top_n: int = 10
    ) -> dict[str, Any]:
        """Summarize outstanding lease receivables into an LLM-friendly report.

        Fans out over *all* pages of the lease outstanding-balances endpoint
        (bounded) and returns aggregate totals plus the largest balances, so the
        assistant answers "who owes money / how much is outstanding" from a
        complete view instead of a single truncated page.

        Args:
            lease_status: Lease status to include (one of: Active, Past, Future).
            top_n: Number of largest balances to include in the breakdown.
        """
        try:
            status = c.validate_enum(lease_status, LEASE_STATUSES, field="lease_status")
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")

        async def _run() -> dict[str, Any]:
            def _page(limit: int, offset: int) -> Any:
                return client.lease_transactions_api.external_api_lease_outstanding_balances_get_lease_outstanding_balances(
                    leasestatuses=[status] if status else None, limit=limit, offset=offset
                )

            rows = await c.paginate_all(_page)
            total = 0.0
            for row in rows:
                try:
                    total += float(row.get("TotalBalance") or 0)
                except (TypeError, ValueError):
                    continue
            ranked = sorted(
                rows,
                key=lambda r: float(r.get("TotalBalance") or 0),
                reverse=True,
            )
            top = [
                {
                    "LeaseId": r.get("LeaseId"),
                    "PropertyId": r.get("PropertyId"),
                    "UnitId": r.get("UnitId"),
                    "TotalBalance": r.get("TotalBalance"),
                }
                for r in ranked[: max(0, int(top_n))]
            ]
            return {
                "lease_status": status,
                "lease_count": len(rows),
                "total_outstanding": round(total, 2),
                "top_balances": top,
            }

        return await c.execute("lease_receivables_summary", _run)
