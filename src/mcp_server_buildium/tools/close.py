"""Month-end "close my books" money-movement automation.

This collapses the month-end grind into one instruction. Given a property (or a
set of properties), :func:`run_month_end_close` will, across every active lease:

1. **Post recurring rent** — a rent charge for the period.
2. **Apply received payments** — reconcile payments against charges oldest-first
   (FIFO) and report the remaining open balance.
3. **Assess late fees** — for balances still past due beyond the grace period.
4. **Summarise owner distributions** — per-property collected/outstanding totals
   and (optionally) an exported owner statement.

Safety first: the tool is **dry-run by default**. A dry run performs no writes and
returns the exact plan (amounts, counts, per-lease actions) for review. Only when
``dry_run=False`` — and the required GL accounts are supplied — does it post
charges. It is classified a sensitive write.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from ..llm.artifacts import Section, add_current_artifact, build_generated_file
from . import _common as c
from . import _money as m
from .reports import _lease_charges_and_payments, _lease_property_id, _lease_rent

LEASE_STATUS_ACTIVE = "Active"


def register_close_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register the month-end close automation tool with the MCP server."""

    c.register_local_tool("run_month_end_close", op_type="write", sensitive=True)

    @mcp.tool()
    async def run_month_end_close(
        property_id: int | None = None,
        property_ids: list[int] | None = None,
        as_of_date: str | None = None,
        post_rent: bool = True,
        apply_payments: bool = True,
        assess_late_fees: bool = True,
        grace_period_days: int = 5,
        late_fee_amount: float | None = None,
        late_fee_percent: float | None = None,
        rent_gl_account_id: int | None = None,
        late_fee_gl_account_id: int | None = None,
        dry_run: bool = True,
        generate_statement: bool = False,
        export_format: str = "pdf",
    ) -> dict[str, Any]:
        """Run a month-end close for a property (post rent, apply payments, late fees).

        The killer one-liner: *"Run the July close for the Elm St portfolio."* This
        fans out over every active lease and builds a deterministic plan of the
        rent to post, how received payments net against open charges, and which
        leases warrant a late fee — then either returns that plan (``dry_run``) or
        executes it.

        Args:
            property_id: A single property to close. Use this or ``property_ids``.
            property_ids: Multiple properties to close in one run.
            as_of_date: Effective date for posted charges (YYYY-MM-DD; today if omitted).
            post_rent: Post each active lease's scheduled rent charge.
            apply_payments: Reconcile received payments against charges (oldest-first).
            assess_late_fees: Assess a late fee on leases past due beyond the grace period.
            grace_period_days: Days past the charge date before a balance is "late".
            late_fee_amount: Flat late fee to assess (mutually exclusive with percent).
            late_fee_percent: Late fee as a percent (0-100) of the overdue balance.
            rent_gl_account_id: GL income account to post rent to (required to execute posting).
            late_fee_gl_account_id: GL income account for late fees (required to execute posting).
            dry_run: When True (default), no writes are made — the plan is returned.
            generate_statement: Also produce a per-property owner statement file.
            export_format: Statement format (``pdf``/``xlsx``/``csv``).
        """
        targets = _resolve_property_ids(property_id, property_ids)
        if not targets:
            return c.failure(
                "Provide property_id or property_ids to scope the close.",
                code="validation_error",
            )
        if late_fee_amount is not None and late_fee_percent is not None:
            return c.failure(
                "Provide either late_fee_amount or late_fee_percent, not both.",
                code="validation_error",
            )
        if late_fee_percent is not None and not (0 <= late_fee_percent <= 100):
            return c.failure("late_fee_percent must be between 0 and 100.", code="validation_error")
        if not dry_run and post_rent and rent_gl_account_id is None:
            return c.failure(
                "rent_gl_account_id is required to post rent when dry_run=False.",
                code="validation_error",
                hint="Pass rent_gl_account_id (see list_gl_accounts) or set post_rent=False.",
            )
        if (
            not dry_run
            and assess_late_fees
            and _late_fee_configured(late_fee_amount, late_fee_percent)
            and late_fee_gl_account_id is None
        ):
            return c.failure(
                "late_fee_gl_account_id is required to post late fees when dry_run=False.",
                code="validation_error",
            )
        as_of = m.parse_date(as_of_date) or date.today()

        async def _run() -> dict[str, Any]:
            lease_actions: list[dict[str, Any]] = []
            property_summaries: dict[Any, dict[str, Any]] = {}
            totals = {
                "rent_posted": 0.0,
                "payments_applied": 0.0,
                "late_fees_assessed": 0.0,
                "open_balance": 0.0,
            }
            for prop in targets:
                leases = await _active_leases(client, prop)
                for lease in leases:
                    action = await _close_one_lease(
                        client,
                        lease,
                        as_of=as_of,
                        post_rent=post_rent,
                        apply_payments=apply_payments,
                        assess_late_fees=assess_late_fees,
                        grace_period_days=grace_period_days,
                        late_fee_amount=late_fee_amount,
                        late_fee_percent=late_fee_percent,
                        rent_gl_account_id=rent_gl_account_id,
                        late_fee_gl_account_id=late_fee_gl_account_id,
                        dry_run=dry_run,
                    )
                    lease_actions.append(action)
                    totals["rent_posted"] = round(
                        totals["rent_posted"] + action["rent_charge"], m.CENTS
                    )
                    totals["payments_applied"] = round(
                        totals["payments_applied"] + action["payments_applied"], m.CENTS
                    )
                    totals["late_fees_assessed"] = round(
                        totals["late_fees_assessed"] + action["late_fee"], m.CENTS
                    )
                    totals["open_balance"] = round(
                        totals["open_balance"] + action["open_balance"], m.CENTS
                    )
                    prop_id = action["property_id"]
                    summary = property_summaries.setdefault(
                        prop_id,
                        {
                            "property_id": prop_id,
                            "lease_count": 0,
                            "rent_posted": 0.0,
                            "payments_applied": 0.0,
                            "late_fees_assessed": 0.0,
                            "open_balance": 0.0,
                        },
                    )
                    summary["lease_count"] += 1
                    summary["rent_posted"] = round(
                        summary["rent_posted"] + action["rent_charge"], m.CENTS
                    )
                    summary["payments_applied"] = round(
                        summary["payments_applied"] + action["payments_applied"], m.CENTS
                    )
                    summary["late_fees_assessed"] = round(
                        summary["late_fees_assessed"] + action["late_fee"], m.CENTS
                    )
                    summary["open_balance"] = round(
                        summary["open_balance"] + action["open_balance"], m.CENTS
                    )

            result: dict[str, Any] = {
                "period": as_of.isoformat(),
                "dry_run": dry_run,
                "properties": targets,
                "lease_count": len(lease_actions),
                "totals": totals,
                "owner_statements": list(property_summaries.values()),
                "lease_actions": lease_actions,
            }
            if generate_statement and property_summaries:
                result["export"] = _statement_export(
                    export_format, as_of, list(property_summaries.values())
                )
            return result

        return await c.execute("run_month_end_close", _run)


def _resolve_property_ids(property_id: int | None, property_ids: list[int] | None) -> list[int]:
    ids: list[int] = []
    if property_id is not None:
        ids.append(int(property_id))
    for pid in property_ids or []:
        if pid is not None and int(pid) not in ids:
            ids.append(int(pid))
    return ids


def _late_fee_configured(amount: float | None, percent: float | None) -> bool:
    return bool((amount and amount > 0) or (percent and percent > 0))


async def _active_leases(client: BuildiumClient, property_id: int) -> list[dict[str, Any]]:
    def _page(limit: int, offset: int) -> Any:
        return client.leases_api.external_api_leases_get_leases(
            propertyids=[property_id],
            leasestatuses=[LEASE_STATUS_ACTIVE],
            limit=limit,
            offset=offset,
        )

    return await c.paginate_all(_page)


async def _close_one_lease(
    client: BuildiumClient,
    lease: dict[str, Any],
    *,
    as_of: date,
    post_rent: bool,
    apply_payments: bool,
    assess_late_fees: bool,
    grace_period_days: int,
    late_fee_amount: float | None,
    late_fee_percent: float | None,
    rent_gl_account_id: int | None,
    late_fee_gl_account_id: int | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Plan (and optionally execute) the close for a single lease."""
    lease_id = lease.get("Id")
    rent = _lease_rent(lease) if post_rent else 0.0

    # Reconcile existing charges against received payments (oldest-first).
    charges, payments_total = await _lease_charges_and_payments(client, lease_id)
    fifo = m.apply_fifo(charges, payments_total)
    applied = round(payments_total - fifo.unapplied_credit, m.CENTS) if apply_payments else 0.0

    # Past-due balance = open charges older than the grace period.
    overdue = 0.0
    for open_charge in fifo.open_charges:
        age = m.days_between(open_charge.charge_date, as_of)
        if age is not None and age > grace_period_days:
            overdue = round(overdue + open_charge.remaining, m.CENTS)

    late_fee = 0.0
    if assess_late_fees and overdue > 0 and _late_fee_configured(late_fee_amount, late_fee_percent):
        if late_fee_amount is not None:
            late_fee = m.money(late_fee_amount)
        else:
            late_fee = m.money(overdue * (late_fee_percent or 0) / 100.0)

    open_balance = round(fifo.total_open + rent + late_fee, m.CENTS)

    posted: dict[str, Any] = {"rent": None, "late_fee": None}
    if not dry_run:
        if post_rent and rent > 0 and rent_gl_account_id is not None:
            posted["rent"] = await _post_charge(
                client, lease_id, rent, rent_gl_account_id, as_of, memo="Rent"
            )
        if late_fee > 0 and late_fee_gl_account_id is not None:
            posted["late_fee"] = await _post_charge(
                client, lease_id, late_fee, late_fee_gl_account_id, as_of, memo="Late fee"
            )

    return {
        "lease_id": lease_id,
        "property_id": _lease_property_id(lease),
        "rent_charge": rent,
        "payments_applied": applied,
        "unapplied_credit": fifo.unapplied_credit if apply_payments else 0.0,
        "overdue_balance": overdue,
        "late_fee": late_fee,
        "open_balance": open_balance,
        "open_charge_ids": [oc.charge_id for oc in fifo.open_charges],
        "posted": posted if not dry_run else None,
    }


async def _post_charge(
    client: BuildiumClient,
    lease_id: Any,
    amount: float,
    gl_account_id: int,
    as_of: date,
    *,
    memo: str,
) -> dict[str, Any]:
    """Post a single lease charge and return a compact result envelope."""
    charge_data = {
        "Date": as_of.isoformat(),
        "Memo": f"{memo} - {as_of.isoformat()}",
        "Lines": [{"Amount": amount, "GLAccountId": gl_account_id}],
    }
    return await c.create(
        "run_month_end_close",
        "lease_charge_post_message",
        "LeaseChargePostMessage",
        charge_data,
        lambda message: (
            client.lease_transactions_api.external_api_lease_ledger_charges_write_create_charge(
                lease_id=lease_id, lease_charge_post_message=message
            )
        ),
    )


def _statement_export(
    export_format: str, as_of: date, summaries: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build a per-owner statement download from the property summaries."""
    fmt = (export_format or "pdf").strip().lower()
    columns = ["Property", "Leases", "Rent Posted", "Payments Applied", "Late Fees", "Open Balance"]
    rows = [
        [
            s["property_id"],
            s["lease_count"],
            s["rent_posted"],
            s["payments_applied"],
            s["late_fees_assessed"],
            s["open_balance"],
        ]
        for s in summaries
    ]
    sections = [
        Section(
            heading=f"Property {s['property_id']}",
            body=(
                f"Leases: {s['lease_count']}\n"
                f"Rent posted: {s['rent_posted']}\n"
                f"Payments applied: {s['payments_applied']}\n"
                f"Late fees assessed: {s['late_fees_assessed']}\n"
                f"Open balance: {s['open_balance']}"
            ),
        )
        for s in summaries
    ]
    generated = build_generated_file(
        file_format=fmt,
        filename=f"owner_statement_{as_of.isoformat()}",
        title=f"Owner Statement - {as_of.isoformat()}",
        columns=columns,
        rows=rows,
        sections=sections if fmt in ("pdf", "docx") else None,
    )
    add_current_artifact(generated)
    return {
        "file_name": generated.name,
        "format": fmt,
        "media_type": generated.media_type,
        "size_bytes": generated.size,
    }
