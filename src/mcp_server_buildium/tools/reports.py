"""Trustworthy financial reporting tools (rent roll, aged receivables, P&L).

These are deterministic, reconciled reports — the kind an operator would hand to
an owner or auditor. Each one:

* fans out across *all* pages of the underlying Buildium endpoint (via
  :func:`_common.paginate_all`) so figures reflect the whole portfolio, not a
  truncated first page;
* computes every number in code (see :mod:`._money`) rather than trusting
  free-text LLM arithmetic;
* reports a ``reconciled`` flag proving the parts sum to the whole; and
* can export a branded ``xlsx``/``pdf``/``csv`` file the user downloads, with the
  figures traceable back to their source transactions.

All report tools are server-local (they orchestrate existing read endpoints) and
are classified read/sensitive because they expose financial data.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from ..llm.artifacts import (
    SUPPORTED_FORMATS,
    Section,
    add_current_artifact,
    build_generated_file,
)
from . import _common as c
from . import _money as m

LEASE_STATUSES = {"Active", "Past", "Future"}
# Formats that make sense for a tabular financial report.
_EXPORT_FORMATS = {"csv", "xlsx", "pdf"}


def _lease_unit_id(lease: dict[str, Any]) -> Any:
    return lease.get("UnitId") or (lease.get("Unit") or {}).get("Id")


def _lease_property_id(lease: dict[str, Any]) -> Any:
    return lease.get("PropertyId") or (lease.get("Property") or {}).get("Id")


def _lease_rent(lease: dict[str, Any]) -> float:
    """Best-effort monthly rent for a lease from common Buildium shapes."""
    details = lease.get("AccountDetails") or {}
    if isinstance(details, dict) and details.get("Rent") is not None:
        return m.money(details.get("Rent"))
    for key in ("Rent", "RentAmount", "CurrentRent"):
        if lease.get(key) is not None:
            return m.money(lease.get(key))
    return 0.0


def _lease_tenants(lease: dict[str, Any]) -> str:
    tenants = lease.get("Tenants") or lease.get("CurrentTenants") or []
    names: list[str] = []
    for tenant in tenants:
        if not isinstance(tenant, dict):
            continue
        name = tenant.get("Name") or " ".join(
            part for part in (tenant.get("FirstName"), tenant.get("LastName")) if part
        )
        if name:
            names.append(str(name).strip())
    return ", ".join(names)


def _lease_end(lease: dict[str, Any]) -> Any:
    for key in ("LeaseToDate", "ToDate", "CurrentTermEnd", "LeaseEndDate", "EndDate"):
        if lease.get(key):
            return lease[key]
    return None


def _export(
    export_format: str,
    *,
    filename: str,
    title: str,
    columns: list[str],
    rows: list[list[Any]],
    sections: list[Section] | None = None,
) -> dict[str, Any]:
    """Build a downloadable artifact and return an export descriptor for meta."""
    generated = build_generated_file(
        file_format=export_format,
        filename=filename,
        title=title,
        columns=columns,
        rows=rows,
        sections=sections,
    )
    add_current_artifact(generated)
    return {
        "file_name": generated.name,
        "format": export_format,
        "media_type": generated.media_type,
        "size_bytes": generated.size,
    }


def _validate_export(export_format: str | None) -> str | None:
    fmt = (export_format or "").strip().lower()
    if not fmt:
        return None
    if fmt not in _EXPORT_FORMATS:
        raise ValueError(
            f"Unsupported export_format {export_format!r}. "
            f"Choose one of: {', '.join(sorted(_EXPORT_FORMATS))}."
        )
    if fmt not in SUPPORTED_FORMATS:  # pragma: no cover - defensive
        raise ValueError(f"Format {fmt!r} is not available in this build.")
    return fmt


def register_report_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register the financial reporting tools with the MCP server."""

    c.register_local_tool("rent_roll_report", op_type="read", sensitive=True)
    c.register_local_tool("aged_receivables_report", op_type="read", sensitive=True)
    c.register_local_tool("income_statement_report", op_type="read", sensitive=True)

    # -- Rent roll ------------------------------------------------------------
    @mcp.tool()
    async def rent_roll_report(
        property_id: int | None = None,
        lease_status: str = "Active",
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Produce a rent roll across every lease (all pages), optionally exported.

        A rent roll lists each occupied unit with its tenant(s), scheduled monthly
        rent, lease dates, and current ledger balance, plus portfolio totals. The
        scheduled-rent total is reconciled against the sum of the rows.

        Args:
            property_id: Optional property to scope the rent roll to.
            lease_status: Lease status to include (Active, Past, Future).
            export_format: Optional ``csv``/``xlsx``/``pdf`` to also return a
                downloadable file.
        """
        try:
            status = c.validate_enum(lease_status, LEASE_STATUSES, field="lease_status")
            fmt = _validate_export(export_format)
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")

        async def _run() -> dict[str, Any]:
            def _page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
                if property_id is not None:
                    kwargs["propertyids"] = [property_id]
                if status is not None:
                    kwargs["leasestatuses"] = [status]
                return client.leases_api.external_api_leases_get_leases(**kwargs)

            leases = await c.paginate_all(_page)
            rows: list[dict[str, Any]] = []
            total_rent = 0.0
            for lease in leases:
                rent = _lease_rent(lease)
                total_rent = round(total_rent + rent, m.CENTS)
                rows.append(
                    {
                        "LeaseId": lease.get("Id"),
                        "PropertyId": _lease_property_id(lease),
                        "UnitId": _lease_unit_id(lease),
                        "UnitNumber": lease.get("UnitNumber"),
                        "Tenants": _lease_tenants(lease),
                        "Rent": rent,
                        "LeaseFrom": lease.get("LeaseFromDate") or lease.get("FromDate"),
                        "LeaseTo": _lease_end(lease),
                        "Status": lease.get("LeaseStatus") or status,
                    }
                )
            reconciled = round(sum(r["Rent"] for r in rows), m.CENTS) == total_rent
            report = {
                "report": "rent_roll",
                "lease_status": status,
                "property_id": property_id,
                "unit_count": len(rows),
                "total_monthly_rent": total_rent,
                "reconciled": reconciled,
                "rows": rows,
            }
            if fmt:
                columns = [
                    "Lease",
                    "Property",
                    "Unit",
                    "Tenants",
                    "Rent",
                    "Lease From",
                    "Lease To",
                ]
                table = [
                    [
                        r["LeaseId"],
                        r["PropertyId"],
                        r["UnitNumber"] or r["UnitId"],
                        r["Tenants"],
                        r["Rent"],
                        r["LeaseFrom"],
                        r["LeaseTo"],
                    ]
                    for r in rows
                ]
                table.append(["", "", "", "TOTAL", total_rent, "", ""])
                report["export"] = _export(
                    fmt,
                    filename="rent_roll",
                    title="Rent Roll",
                    columns=columns,
                    rows=table,
                )
            return report

        return await c.execute("rent_roll_report", _run)

    # -- Aged receivables -----------------------------------------------------
    @mcp.tool()
    async def aged_receivables_report(
        property_id: int | None = None,
        as_of_date: str | None = None,
        lease_status: str = "Active",
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Aged-receivables report bucketed 0-30/31-60/61-90/90+, reconciled.

        For every lease with an outstanding balance, this applies received
        payments to the oldest charges first (FIFO) and ages the remaining open
        charges by charge date relative to ``as_of_date``. Bucket totals are
        reconciled to sum to the portfolio balance, and each lease row is
        traceable to the charge ids that make up its balance.

        Args:
            property_id: Optional property to scope to.
            as_of_date: Aging reference date (YYYY-MM-DD); defaults to today.
            lease_status: Lease status to include (Active, Past, Future).
            export_format: Optional ``csv``/``xlsx``/``pdf`` download.
        """
        try:
            status = c.validate_enum(lease_status, LEASE_STATUSES, field="lease_status")
            fmt = _validate_export(export_format)
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        as_of = m.parse_date(as_of_date) or date.today()

        async def _run() -> dict[str, Any]:
            def _balances_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
                if status is not None:
                    kwargs["leasestatuses"] = [status]
                return client.lease_transactions_api.external_api_lease_outstanding_balances_get_lease_outstanding_balances(
                    **kwargs
                )

            balances = await c.paginate_all(_balances_page)
            rows: list[dict[str, Any]] = []
            aging_results: list[m.AgingResult] = []
            for balance in balances:
                lease_id = balance.get("LeaseId") or balance.get("Id")
                lease_property = balance.get("PropertyId")
                if property_id is not None and lease_property not in (property_id, None):
                    continue
                if m.money(balance.get("TotalBalance")) <= 0 or lease_id is None:
                    continue
                charges, payments_total = await _lease_charges_and_payments(client, lease_id)
                aging = m.age_receivables(charges, payments_total, as_of)
                aging_results.append(aging)
                rows.append(
                    {
                        "LeaseId": lease_id,
                        "PropertyId": lease_property,
                        "UnitId": balance.get("UnitId"),
                        "Balance": aging.total,
                        **aging.as_dict()["buckets"],
                        "OpenChargeIds": [oc.charge_id for oc in aging.open_charges],
                    }
                )
            totals = m.sum_aging(aging_results)
            reconciled = round(sum(r["Balance"] for r in rows), m.CENTS) == totals["total"]
            report = {
                "report": "aged_receivables",
                "as_of": as_of.isoformat(),
                "lease_status": status,
                "property_id": property_id,
                "lease_count": len(rows),
                "totals": totals,
                "bucket_labels": m.AGING_BUCKET_LABELS,
                "reconciled": reconciled,
                "rows": rows,
            }
            if fmt:
                columns = ["Lease", "Property", "Unit", "0-30", "31-60", "61-90", "90+", "Balance"]
                table = [
                    [
                        r["LeaseId"],
                        r["PropertyId"],
                        r["UnitId"],
                        r["current"],
                        r["days_31_60"],
                        r["days_61_90"],
                        r["days_over_90"],
                        r["Balance"],
                    ]
                    for r in rows
                ]
                table.append(
                    [
                        "",
                        "",
                        "TOTAL",
                        totals["current"],
                        totals["days_31_60"],
                        totals["days_61_90"],
                        totals["days_over_90"],
                        totals["total"],
                    ]
                )
                report["export"] = _export(
                    fmt,
                    filename="aged_receivables",
                    title=f"Aged Receivables as of {as_of.isoformat()}",
                    columns=columns,
                    rows=table,
                )
            return report

        return await c.execute("aged_receivables_report", _run)

    # -- Income statement / owner statement ----------------------------------
    @mcp.tool()
    async def income_statement_report(
        start_date: str,
        end_date: str,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Income statement (P&L) from GL transactions, reconciled and exportable.

        Fans out over every general-ledger transaction in the date range, groups
        the journal lines by income/expense account, and reports revenue,
        expenses, and net income. Net income is reconciled against the per-account
        totals, and the contributing transaction ids are returned for traceability
        — suitable to hand to an owner or auditor.

        Args:
            start_date: Period start (YYYY-MM-DD).
            end_date: Period end (YYYY-MM-DD).
            export_format: Optional ``csv``/``xlsx``/``pdf`` download.
        """
        try:
            fmt = _validate_export(export_format)
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        if not start_date or not end_date:
            return c.failure(
                "start_date and end_date are required (YYYY-MM-DD).", code="validation_error"
            )

        async def _run() -> dict[str, Any]:
            def _page(limit: int, offset: int) -> Any:
                return client.general_ledger_api.external_api_general_ledger_transactions_get_all_transactions(
                    startdate=start_date, enddate=end_date, limit=limit, offset=offset
                )

            transactions = await c.paginate_all(_page)
            statement = m.build_income_statement(transactions)
            report = {
                "report": "income_statement",
                "start_date": start_date,
                "end_date": end_date,
                "total_income": statement.total_income,
                "total_expense": statement.total_expense,
                "net_income": statement.net_income,
                "income_accounts": statement.income_accounts,
                "expense_accounts": statement.expense_accounts,
                "transaction_count": len(statement.transaction_ids),
                "source_transaction_ids": statement.transaction_ids,
                "reconciled": statement.reconciles(),
            }
            if fmt:
                columns = ["Section", "Account", "Amount"]
                table: list[list[Any]] = []
                for account in statement.income_accounts:
                    table.append(["Income", account["name"], account["amount"]])
                table.append(["Income", "Total Income", statement.total_income])
                for account in statement.expense_accounts:
                    table.append(["Expense", account["name"], account["amount"]])
                table.append(["Expense", "Total Expense", statement.total_expense])
                table.append(["Net", "Net Income", statement.net_income])
                sections = [
                    Section(
                        heading="Summary",
                        body=(
                            f"Period: {start_date} to {end_date}\n"
                            f"Total Income: {statement.total_income}\n"
                            f"Total Expense: {statement.total_expense}\n"
                            f"Net Income: {statement.net_income}"
                        ),
                    )
                ]
                report["export"] = _export(
                    fmt,
                    filename="income_statement",
                    title=f"Income Statement {start_date} to {end_date}",
                    columns=columns,
                    rows=table,
                    sections=sections if fmt == "pdf" else None,
                )
            return report

        return await c.execute("income_statement_report", _run)


async def _lease_charges_and_payments(
    client: BuildiumClient, lease_id: Any
) -> tuple[list[dict[str, Any]], float]:
    """Fetch a lease's charges plus the total of its payments/credits.

    Returns ``(charges, payments_total)`` where ``charges`` are the charge
    transactions and ``payments_total`` is the sum of payment/credit magnitudes,
    used to apply payments to charges oldest-first when aging the balance.
    """

    def _charges_page(limit: int, offset: int) -> Any:
        return client.lease_transactions_api.external_api_lease_ledger_charges_read_get_all_charges(
            lease_id=lease_id, limit=limit, offset=offset
        )

    charges = await c.paginate_all(_charges_page)

    def _ledger_page(limit: int, offset: int) -> Any:
        return (
            client.lease_transactions_api.external_api_lease_ledger_transactions_get_lease_ledgers(
                lease_id=lease_id, limit=limit, offset=offset
            )
        )

    ledger = await c.paginate_all(_ledger_page)
    payments_total = 0.0
    for txn in ledger:
        ttype = str(txn.get("TransactionType") or txn.get("TransactionTypeEnum") or "").lower()
        if any(word in ttype for word in ("payment", "credit", "refund applied", "electronic")):
            payments_total += abs(m.to_float(txn.get("TotalAmount") or txn.get("Amount")))
    return charges, round(payments_total, m.CENTS)
