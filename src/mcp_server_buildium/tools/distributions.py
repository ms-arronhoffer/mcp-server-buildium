"""Owner distribution (owner draw / payout) automation.

The natural follow-on to *"close my books"*: once rent is posted and payments are
reconciled, the next question every owner asks is *"how much do I get paid?"* This
module answers it deterministically. Given a property (or a set of properties),
:func:`owner_distributions` computes, per property:

* **Collected** — cash received (payments/credits) against the property's active
  lease ledgers for the period.
* **Unpaid bills** — approved-but-unpaid vendor bills attributed to the property
  (optional; netted out before an owner is paid).
* **Reserve withheld** — a retained reserve, either a flat amount or a percent of
  what was collected, held back for future expenses.
* **Distributable** — collected minus unpaid bills minus reserve, floored at zero
  (you never distribute more cash than is on hand).

Every figure is computed in code (see :mod:`._money`), reconciled so the parts sum
to the whole, and traceable back to the contributing lease and bill ids. An
owner-distribution statement can be exported as ``csv``/``xlsx``/``pdf``. The tool
is read-only (it plans the distribution; it does not move money) but classified
sensitive because it exposes owner financials.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from ..llm.artifacts import Section, add_current_artifact, build_generated_file
from . import _common as c
from . import _money as m
from .close import _resolve_property_ids
from .reports import _lease_charges_and_payments

LEASE_STATUS_ACTIVE = "Active"
_EXPORT_FORMATS = {"csv", "xlsx", "pdf"}


def register_distribution_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register the owner-distribution automation tool with the MCP server."""

    c.register_local_tool("owner_distributions", op_type="read", sensitive=True)

    @mcp.tool()
    async def owner_distributions(
        property_id: int | None = None,
        property_ids: list[int] | None = None,
        reserve_amount: float | None = None,
        reserve_percent: float | None = None,
        include_unpaid_bills: bool = True,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Compute distributable owner funds per property, reconciled and exportable.

        The killer one-liner: *"How much can I pay each owner this month?"* For every
        property, this sums the cash collected against active leases, nets out
        approved unpaid bills, holds back a reserve, and reports what is safe to
        distribute — with each figure traceable to its source lease and bill ids.

        Args:
            property_id: A single property to compute. Use this or ``property_ids``.
            property_ids: Multiple properties to compute in one run.
            reserve_amount: Flat reserve to retain per property (mutually exclusive
                with ``reserve_percent``).
            reserve_percent: Reserve as a percent (0-100) of collected cash.
            include_unpaid_bills: Net approved unpaid bills out of the distribution.
            export_format: Optional ``csv``/``xlsx``/``pdf`` owner-distribution
                statement download.
        """
        targets = _resolve_property_ids(property_id, property_ids)
        if not targets:
            return c.failure(
                "Provide property_id or property_ids to scope the distribution.",
                code="validation_error",
            )
        if reserve_amount is not None and reserve_percent is not None:
            return c.failure(
                "Provide either reserve_amount or reserve_percent, not both.",
                code="validation_error",
            )
        if reserve_percent is not None and not (0 <= reserve_percent <= 100):
            return c.failure(
                "reserve_percent must be between 0 and 100.", code="validation_error"
            )
        fmt = (export_format or "").strip().lower() or None
        if fmt is not None and fmt not in _EXPORT_FORMATS:
            return c.failure(
                f"Unsupported export_format {export_format!r}. "
                f"Choose one of: {', '.join(sorted(_EXPORT_FORMATS))}.",
                code="validation_error",
            )

        async def _run() -> dict[str, Any]:
            bills_by_property = (
                await _unpaid_bills_by_property(client, targets)
                if include_unpaid_bills
                else {}
            )
            rows: list[dict[str, Any]] = []
            totals = {
                "collected": 0.0,
                "unpaid_bills": 0.0,
                "reserve_withheld": 0.0,
                "distributable": 0.0,
            }
            for prop in targets:
                collected = 0.0
                outstanding = 0.0
                lease_ids: list[Any] = []
                for lease in await _active_leases(client, prop):
                    lease_id = lease.get("Id")
                    charges, payments_total = await _lease_charges_and_payments(client, lease_id)
                    fifo = m.apply_fifo(charges, payments_total)
                    collected = round(collected + payments_total, m.CENTS)
                    outstanding = round(outstanding + fifo.total_open, m.CENTS)
                    if lease_id is not None:
                        lease_ids.append(lease_id)
                bill_amount, bill_ids = bills_by_property.get(prop, (0.0, []))
                distributable, reserve = m.distributable_amount(
                    collected,
                    unpaid_bills=bill_amount,
                    reserve_amount=reserve_amount,
                    reserve_percent=reserve_percent,
                )
                rows.append(
                    {
                        "property_id": prop,
                        "lease_count": len(lease_ids),
                        "collected": collected,
                        "outstanding": outstanding,
                        "unpaid_bills": bill_amount,
                        "reserve_withheld": reserve,
                        "distributable": distributable,
                        "lease_ids": lease_ids,
                        "unpaid_bill_ids": bill_ids,
                    }
                )
                totals["collected"] = round(totals["collected"] + collected, m.CENTS)
                totals["unpaid_bills"] = round(totals["unpaid_bills"] + bill_amount, m.CENTS)
                totals["reserve_withheld"] = round(totals["reserve_withheld"] + reserve, m.CENTS)
                totals["distributable"] = round(totals["distributable"] + distributable, m.CENTS)

            reconciled = (
                round(sum(r["distributable"] for r in rows), m.CENTS) == totals["distributable"]
            )
            report: dict[str, Any] = {
                "report": "owner_distributions",
                "properties": targets,
                "property_count": len(rows),
                "totals": totals,
                "reconciled": reconciled,
                "rows": rows,
            }
            if fmt:
                report["export"] = _distribution_export(fmt, rows, totals)
            return report

        return await c.execute("owner_distributions", _run)


async def _active_leases(client: BuildiumClient, property_id: int) -> list[dict[str, Any]]:
    def _page(limit: int, offset: int) -> Any:
        return client.leases_api.external_api_leases_get_leases(
            propertyids=[property_id],
            leasestatuses=[LEASE_STATUS_ACTIVE],
            limit=limit,
            offset=offset,
        )

    return await c.paginate_all(_page)


async def _unpaid_bills_by_property(
    client: BuildiumClient, targets: list[int]
) -> dict[Any, tuple[float, list[Any]]]:
    """Sum approved unpaid bill amounts per property from the bill line items.

    Buildium bill lines carry an ``AccountingEntity`` whose ``Id`` is the property
    (for ``Rental`` entities); line amounts are attributed to that property. Bills
    or lines whose property can't be resolved are simply skipped (never guessed).
    Only the requested ``targets`` are accumulated.
    """

    def _page(limit: int, offset: int) -> Any:
        return client.bills_api.external_api_bills_get_bills_async(
            paidstatus="Unpaid", limit=limit, offset=offset
        )

    bills = await c.paginate_all(_page)
    target_set = set(targets)
    by_property: dict[Any, tuple[float, list[Any]]] = {}
    for bill in bills:
        bill_id = bill.get("Id")
        for line in bill.get("Lines") or []:
            if not isinstance(line, dict):
                continue
            prop_id = _line_property_id(line)
            if prop_id is None or prop_id not in target_set:
                continue
            amount = m.money(line.get("Amount"))
            if amount == 0:
                continue
            total, ids = by_property.get(prop_id, (0.0, []))
            total = round(total + amount, m.CENTS)
            if bill_id is not None and bill_id not in ids:
                ids = [*ids, bill_id]
            by_property[prop_id] = (total, ids)
    return by_property


def _line_property_id(line: dict[str, Any]) -> Any:
    """Best-effort property id for a bill line from common Buildium shapes."""
    entity = line.get("AccountingEntity")
    if isinstance(entity, dict):
        entity_type = str(entity.get("AccountingEntityType") or "").lower()
        if entity.get("Id") is not None and entity_type in ("", "rental"):
            return entity.get("Id")
    for key in ("PropertyId", "RentalId"):
        if line.get(key) is not None:
            return line[key]
    return None


def _distribution_export(
    export_format: str, rows: list[dict[str, Any]], totals: dict[str, float]
) -> dict[str, Any]:
    """Build a downloadable owner-distribution statement artifact."""
    columns = [
        "Property",
        "Leases",
        "Collected",
        "Unpaid Bills",
        "Reserve",
        "Distributable",
    ]
    table: list[list[Any]] = [
        [
            r["property_id"],
            r["lease_count"],
            r["collected"],
            r["unpaid_bills"],
            r["reserve_withheld"],
            r["distributable"],
        ]
        for r in rows
    ]
    table.append(
        [
            "TOTAL",
            "",
            totals["collected"],
            totals["unpaid_bills"],
            totals["reserve_withheld"],
            totals["distributable"],
        ]
    )
    sections = [
        Section(
            heading=f"Property {r['property_id']}",
            body=(
                f"Leases: {r['lease_count']}\n"
                f"Collected: {r['collected']}\n"
                f"Unpaid bills: {r['unpaid_bills']}\n"
                f"Reserve withheld: {r['reserve_withheld']}\n"
                f"Distributable: {r['distributable']}"
            ),
        )
        for r in rows
    ]
    generated = build_generated_file(
        file_format=export_format,
        filename="owner_distributions",
        title="Owner Distributions",
        columns=columns,
        rows=table,
        sections=sections if export_format == "pdf" else None,
    )
    add_current_artifact(generated)
    return {
        "file_name": generated.name,
        "format": export_format,
        "media_type": generated.media_type,
        "size_bytes": generated.size,
    }
