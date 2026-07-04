"""Deep-analysis and opportunity-surfacing tools.

Eight high-value analytical tools that cross-join data from multiple Buildium
endpoints to surface insights a plain list/get query cannot:

* :func:`budget_variance_report`   — budget vs. actual P&L deviation by account.
* :func:`vacancy_analysis`         — vacant units, days empty, estimated revenue loss.
* :func:`rent_trend_report`        — under-market leases expiring soon; re-pricing uplift.
* :func:`vendor_spend_report`      — vendor spend concentration, trends, top spenders.
* :func:`cash_flow_projection`     — 30/60/90-day rolling inflow/outflow projection.
* :func:`maintenance_roi_report`   — maintenance cost vs. rent per unit; "money pit" ranking.
* :func:`owner_distribution_report`— per-owner net distributable with exportable statement.
* :func:`delinquency_trend`        — multi-date aging + collection-risk scoring.

All tools are server-local (no spec operation), classified read/sensitive, and
follow the same ``{data, count, error, meta}`` envelope as every other tool.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from ..llm.artifacts import SUPPORTED_FORMATS, Section, add_current_artifact, build_generated_file
from . import _common as c
from . import _money as m

# Average days per month used for daily-rate calculations (365.25 / 12).
_DAYS_PER_MONTH: float = 30.44

# All work-order statuses in Buildium (used to fan-out paginated queries).
_WORK_ORDER_STATUSES: tuple[str, ...] = (
    "New",
    "InProgress",
    "Completed",
    "Deferred",
    "Closed",
)

_EXPORT_FORMATS = {"csv", "xlsx", "pdf"}


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


def _make_artifact(
    fmt: str,
    *,
    filename: str,
    title: str,
    columns: list[str],
    rows: list[list[Any]],
    sections: list[Section] | None = None,
) -> dict[str, Any]:
    generated = build_generated_file(
        file_format=fmt,
        filename=filename,
        title=title,
        columns=columns,
        rows=rows,
        sections=sections,
    )
    add_current_artifact(generated)
    return {
        "file_name": generated.name,
        "format": fmt,
        "media_type": generated.media_type,
        "size_bytes": generated.size,
    }


# ---------------------------------------------------------------------------
# Shared field helpers
# ---------------------------------------------------------------------------

def _bill_date(bill: dict[str, Any]) -> Any:
    for key in ("Date", "BillDate", "TransactionDate", "EntryDate"):
        if bill.get(key):
            return bill[key]
    return None


def _bill_amount(bill: dict[str, Any]) -> float:
    for key in ("Amount", "TotalAmount", "Total"):
        if bill.get(key) is not None:
            return m.to_float(bill[key])
    return 0.0


def _bill_vendor_id(bill: dict[str, Any]) -> Any:
    vendor = bill.get("Vendor") or {}
    if isinstance(vendor, dict) and vendor.get("Id") is not None:
        return vendor["Id"]
    return bill.get("VendorId")


def _bill_vendor_name(bill: dict[str, Any]) -> str:
    vendor = bill.get("Vendor") or {}
    if isinstance(vendor, dict) and vendor.get("Name"):
        return str(vendor["Name"])
    return str(bill.get("VendorName") or bill.get("VendorId") or "Unknown")


def _bill_property_id(bill: dict[str, Any]) -> Any:
    entity = bill.get("Entity") or {}
    if isinstance(entity, dict):
        etype = str(entity.get("EntityType") or "").lower()
        if etype in ("rental", "association", "property"):
            return entity.get("Id")
    return bill.get("PropertyId")


def _bill_due_date(bill: dict[str, Any]) -> Any:
    for key in ("DueDate", "PayByDate", "Date"):
        if bill.get(key):
            return bill[key]
    return None


def _work_order_amount(wo: dict[str, Any]) -> float:
    for key in ("Amount", "TotalAmount", "Cost", "TotalCost"):
        if wo.get(key) is not None:
            return m.to_float(wo[key])
    return 0.0


def _work_order_property_id(wo: dict[str, Any]) -> Any:
    prop = wo.get("Property") or {}
    if isinstance(prop, dict) and prop.get("Id") is not None:
        return prop["Id"]
    return wo.get("PropertyId")


def _work_order_unit_id(wo: dict[str, Any]) -> Any:
    unit = wo.get("Unit") or {}
    if isinstance(unit, dict) and unit.get("Id") is not None:
        return unit["Id"]
    return wo.get("UnitId")


def _work_order_date(wo: dict[str, Any]) -> Any:
    for key in ("CreatedDateTime", "DateCreated", "CreatedDate", "EnteredDate"):
        if wo.get(key):
            return wo[key]
    return None


def _owner_name(owner: dict[str, Any]) -> str:
    name = owner.get("Name") or " ".join(
        part for part in (owner.get("FirstName"), owner.get("LastName")) if part
    )
    return str(name).strip() if name else "Unknown"


def _bank_balance(account: dict[str, Any]) -> float:
    balance = account.get("Balance")
    if isinstance(balance, dict):
        for key in ("Balance", "Available", "Current"):
            if balance.get(key) is not None:
                return m.money(balance[key])
    if balance is not None:
        return m.money(balance)
    return m.money(account.get("CurrentBalance") or account.get("AvailableBalance"))


def _budget_annual_amount(line: dict[str, Any]) -> float:
    """Sum all monthly amounts for a budget line."""
    monthly = line.get("MonthlyAmounts") or line.get("Amounts")
    if monthly and isinstance(monthly, dict):
        return round(sum(m.to_float(v) for v in monthly.values()), m.CENTS)
    if monthly and isinstance(monthly, list):
        return round(sum(m.to_float(v) for v in monthly), m.CENTS)
    for key in ("AnnualAmount", "Amount", "Total"):
        if line.get(key) is not None:
            return m.money(line[key])
    return 0.0


def _budget_gl_account_id(line: dict[str, Any]) -> Any:
    acct = line.get("GLAccount") or line.get("GlAccount") or {}
    if isinstance(acct, dict):
        return acct.get("Id") or line.get("GLAccountId") or line.get("GlAccountId")
    return line.get("GLAccountId") or line.get("GlAccountId")


def _budget_gl_account_name(line: dict[str, Any]) -> str:
    acct = line.get("GLAccount") or line.get("GlAccount") or {}
    if isinstance(acct, dict) and acct.get("Name"):
        return str(acct["Name"])
    return str(line.get("AccountName") or line.get("GLAccountId") or "")


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_analytics_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register the deep-analysis and opportunity-surfacing tools."""

    for name in (
        "budget_variance_report",
        "vacancy_analysis",
        "rent_trend_report",
        "vendor_spend_report",
        "cash_flow_projection",
        "maintenance_roi_report",
        "owner_distribution_report",
        "delinquency_trend",
    ):
        c.register_local_tool(name, op_type="read", sensitive=True)

    # -----------------------------------------------------------------------
    # 1. Budget vs. Actual Variance Report
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def budget_variance_report(
        fiscal_year: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        variance_threshold_pct: float = 10.0,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Compare budgeted amounts to GL actuals and flag accounts over threshold.

        Cross-references every budget line (by GL account) against the income
        statement built from GL transactions for the same period. Returns
        per-account variance (amount and percent), a portfolio summary, and
        accounts that exceed ``variance_threshold_pct``.

        Args:
            fiscal_year: Budget fiscal year to analyse (defaults to current year).
            start_date: GL period start (YYYY-MM-DD); defaults to Jan 1 of fiscal_year.
            end_date: GL period end (YYYY-MM-DD); defaults to today or Dec 31.
            variance_threshold_pct: Flag accounts where |actual − budget| / budget
                exceeds this percentage (default 10 %).
            export_format: Optional ``csv``/``xlsx``/``pdf`` download.
        """
        try:
            fmt = _validate_export(export_format)
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")

        year = fiscal_year or date.today().year
        gl_start = start_date or f"{year}-01-01"
        gl_end = end_date or f"{year}-12-31"
        threshold = max(0.0, float(variance_threshold_pct))

        async def _run() -> dict[str, Any]:
            # Fetch all budgets for the year.
            def _budgets_page(limit: int, offset: int) -> Any:
                return client.budgets_api.external_api_budgets_get_budgets(
                    fiscalyear=year, limit=limit, offset=offset
                )

            budgets = await c.paginate_all(_budgets_page)

            # Build budget map: gl_account_id -> {name, budget_amount}
            budget_by_acct: dict[Any, dict[str, Any]] = {}
            for budget in budgets:
                lines = budget.get("BudgetLines") or budget.get("Lines") or []
                for line in lines:
                    if not isinstance(line, dict):
                        continue
                    acct_id = _budget_gl_account_id(line)
                    if acct_id is None:
                        continue
                    entry = budget_by_acct.setdefault(
                        acct_id,
                        {"account_id": acct_id, "account_name": _budget_gl_account_name(line), "budget": 0.0},
                    )
                    entry["budget"] = round(entry["budget"] + _budget_annual_amount(line), m.CENTS)

            # Fetch GL actuals for the period.
            def _gl_page(limit: int, offset: int) -> Any:
                return client.general_ledger_api.external_api_general_ledger_transactions_get_all_transactions(
                    startdate=gl_start, enddate=gl_end, limit=limit, offset=offset
                )

            transactions = await c.paginate_all(_gl_page)
            statement = m.build_income_statement(transactions)

            # Merge actuals into the budget map.
            actual_by_acct: dict[Any, float] = {}
            for acct in statement.income_accounts + statement.expense_accounts:
                actual_by_acct[acct["account_id"]] = acct["amount"]

            rows: list[dict[str, Any]] = []
            flagged: list[dict[str, Any]] = []
            all_acct_ids = set(budget_by_acct) | set(actual_by_acct)
            for acct_id in all_acct_ids:
                budgeted = (budget_by_acct.get(acct_id) or {}).get("budget", 0.0)
                actual = actual_by_acct.get(acct_id, 0.0)
                variance = round(actual - budgeted, m.CENTS)
                pct = round(abs(variance) / budgeted * 100, 2) if budgeted else None
                name = (budget_by_acct.get(acct_id) or {}).get("account_name") or str(acct_id)
                over_threshold = pct is not None and pct > threshold
                row = {
                    "account_id": acct_id,
                    "account_name": name,
                    "budget": budgeted,
                    "actual": actual,
                    "variance": variance,
                    "variance_pct": pct,
                    "over_threshold": over_threshold,
                }
                rows.append(row)
                if over_threshold:
                    flagged.append(row)

            rows.sort(key=lambda r: abs(r["variance"]), reverse=True)
            flagged.sort(key=lambda r: abs(r["variance"]), reverse=True)

            total_budget = round(sum(r["budget"] for r in rows), m.CENTS)
            total_actual = round(sum(r["actual"] for r in rows), m.CENTS)
            total_variance = round(total_actual - total_budget, m.CENTS)

            report: dict[str, Any] = {
                "report": "budget_variance",
                "fiscal_year": year,
                "gl_start": gl_start,
                "gl_end": gl_end,
                "variance_threshold_pct": threshold,
                "account_count": len(rows),
                "flagged_count": len(flagged),
                "total_budget": total_budget,
                "total_actual": total_actual,
                "total_variance": total_variance,
                "rows": rows,
                "flagged": flagged,
            }
            if fmt:
                columns = ["Account", "Budget", "Actual", "Variance", "Variance %", "Flagged"]
                table = [
                    [r["account_name"], r["budget"], r["actual"], r["variance"], r["variance_pct"], r["over_threshold"]]
                    for r in rows
                ]
                table.append(["TOTAL", total_budget, total_actual, total_variance, "", ""])
                report["export"] = _make_artifact(
                    fmt,
                    filename=f"budget_variance_{year}",
                    title=f"Budget vs. Actual Variance {year}",
                    columns=columns,
                    rows=table,
                )
            return report

        return await c.execute("budget_variance_report", _run)

    # -----------------------------------------------------------------------
    # 2. Vacancy Analysis
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def vacancy_analysis(
        property_id: int | None = None,
        as_of_date: str | None = None,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Find vacant units and estimate the revenue gap from lost rent.

        Compares every rental unit against active leases to identify units that
        are currently vacant. For each vacant unit, the tool reports days since
        the last lease ended, the last known rent (used as the revenue-loss rate),
        and the next scheduled occupancy from any future lease on that unit.

        Args:
            property_id: Optional property to scope the analysis to.
            as_of_date: Reference date (YYYY-MM-DD); defaults to today.
            export_format: Optional ``csv``/``xlsx``/``pdf`` download.
        """
        try:
            fmt = _validate_export(export_format)
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        as_of = m.parse_date(as_of_date) or date.today()

        async def _run() -> dict[str, Any]:
            # Fetch all rental units.
            def _units_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
                if property_id is not None:
                    kwargs["propertyids"] = [property_id]
                return client.rental_units_api.external_api_rental_units_get_all_rental_units(**kwargs)

            units = await c.paginate_all(_units_page)

            # Fetch active leases -> set of occupied unit ids.
            def _active_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"leasestatuses": ["Active"], "limit": limit, "offset": offset}
                if property_id is not None:
                    kwargs["propertyids"] = [property_id]
                return client.leases_api.external_api_leases_get_leases(**kwargs)

            active_leases = await c.paginate_all(_active_page)
            occupied_units: set[Any] = set()
            for lease in active_leases:
                uid = lease.get("UnitId") or (lease.get("Unit") or {}).get("Id")
                if uid is not None:
                    occupied_units.add(uid)

            # Fetch past leases -> last rent per unit.
            def _past_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"leasestatuses": ["Past"], "limit": limit, "offset": offset}
                if property_id is not None:
                    kwargs["propertyids"] = [property_id]
                return client.leases_api.external_api_leases_get_leases(**kwargs)

            past_leases = await c.paginate_all(_past_page)
            last_rent: dict[Any, float] = {}
            last_end: dict[Any, date | None] = {}
            for lease in past_leases:
                uid = lease.get("UnitId") or (lease.get("Unit") or {}).get("Id")
                if uid is None:
                    continue
                rent = _lease_rent(lease)
                end = m.parse_date(_lease_end_value(lease))
                if uid not in last_end or (
                    end is not None and (last_end[uid] is None or end > last_end[uid])
                ):
                    last_end[uid] = end
                    last_rent[uid] = rent

            # Fetch future leases -> next occupancy date per unit.
            def _future_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"leasestatuses": ["Future"], "limit": limit, "offset": offset}
                if property_id is not None:
                    kwargs["propertyids"] = [property_id]
                return client.leases_api.external_api_leases_get_leases(**kwargs)

            future_leases = await c.paginate_all(_future_page)
            next_occupancy: dict[Any, date | None] = {}
            for lease in future_leases:
                uid = lease.get("UnitId") or (lease.get("Unit") or {}).get("Id")
                if uid is None:
                    continue
                start = m.parse_date(lease.get("LeaseFromDate") or lease.get("FromDate"))
                if uid not in next_occupancy or (
                    start is not None and (next_occupancy[uid] is None or start < next_occupancy[uid])
                ):
                    next_occupancy[uid] = start

            # Build vacancy rows.
            vacant_rows: list[dict[str, Any]] = []
            for unit in units:
                uid = unit.get("Id")
                if uid in occupied_units:
                    continue
                prop_id = unit.get("PropertyId") or (unit.get("Property") or {}).get("Id")
                unit_num = unit.get("UnitNumber") or unit.get("Number") or str(uid)
                end = last_end.get(uid)
                days_vacant = m.days_between(end, as_of) if end else None
                monthly_rent = last_rent.get(uid, 0.0)
                annualized_loss = round(monthly_rent * 12, m.CENTS)
                daily_loss = round(monthly_rent / _DAYS_PER_MONTH, m.CENTS) if monthly_rent else 0.0
                estimated_loss = (
                    round(daily_loss * days_vacant, m.CENTS) if days_vacant is not None else None
                )
                next_occ = next_occupancy.get(uid)
                vacant_rows.append(
                    {
                        "unit_id": uid,
                        "property_id": prop_id,
                        "unit_number": unit_num,
                        "last_lease_end": end.isoformat() if end else None,
                        "days_vacant": days_vacant,
                        "last_monthly_rent": monthly_rent,
                        "estimated_revenue_loss": estimated_loss,
                        "annualized_revenue_loss": annualized_loss,
                        "next_occupancy": next_occ.isoformat() if next_occ else None,
                    }
                )

            vacant_rows.sort(key=lambda r: r["days_vacant"] or 0, reverse=True)

            total_units = len(units)
            vacant_count = len(vacant_rows)
            occupancy_rate = round((total_units - vacant_count) / total_units * 100, 2) if total_units else 0.0
            total_annualized_loss = round(sum(r["annualized_revenue_loss"] for r in vacant_rows), m.CENTS)

            report: dict[str, Any] = {
                "report": "vacancy_analysis",
                "as_of": as_of.isoformat(),
                "property_id": property_id,
                "total_units": total_units,
                "vacant_count": vacant_count,
                "occupancy_rate_pct": occupancy_rate,
                "total_annualized_revenue_loss": total_annualized_loss,
                "rows": vacant_rows,
            }
            if fmt:
                columns = [
                    "Unit",
                    "Property",
                    "Last Lease End",
                    "Days Vacant",
                    "Monthly Rent",
                    "Estimated Loss",
                    "Next Occupancy",
                ]
                table = [
                    [
                        r["unit_number"],
                        r["property_id"],
                        r["last_lease_end"],
                        r["days_vacant"],
                        r["last_monthly_rent"],
                        r["estimated_revenue_loss"],
                        r["next_occupancy"],
                    ]
                    for r in vacant_rows
                ]
                report["export"] = _make_artifact(
                    fmt,
                    filename=f"vacancy_analysis_{as_of.isoformat()}",
                    title=f"Vacancy Analysis — {as_of.isoformat()}",
                    columns=columns,
                    rows=table,
                )
            return report

        return await c.execute("vacancy_analysis", _run)

    # -----------------------------------------------------------------------
    # 3. Rent Trend & Renewal Opportunity Detector
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def rent_trend_report(
        expiry_window_days: int = 90,
        under_market_threshold_pct: float = 5.0,
        property_id: int | None = None,
        as_of_date: str | None = None,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Find leases expiring soon that are below the property rent average.

        For every active lease expiring within ``expiry_window_days``, the tool
        computes the property-average rent from all active leases and flags any
        lease where the scheduled rent falls more than ``under_market_threshold_pct``
        below that average — a re-pricing opportunity. The result is ranked by
        estimated annual uplift.

        Args:
            expiry_window_days: Consider active leases expiring within this many days.
            under_market_threshold_pct: Flag leases more than this % below the property
                average (default 5 %).
            property_id: Optional property to scope to.
            as_of_date: Reference date (YYYY-MM-DD); defaults to today.
            export_format: Optional ``csv``/``xlsx``/``pdf`` download.
        """
        try:
            fmt = _validate_export(export_format)
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        as_of = m.parse_date(as_of_date) or date.today()
        threshold = max(0.0, float(under_market_threshold_pct))

        async def _run() -> dict[str, Any]:
            def _active_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"leasestatuses": ["Active"], "limit": limit, "offset": offset}
                if property_id is not None:
                    kwargs["propertyids"] = [property_id]
                return client.leases_api.external_api_leases_get_leases(**kwargs)

            active_leases = await c.paginate_all(_active_page)

            # Group rents by property for computing property averages.
            prop_rents: dict[Any, list[float]] = {}
            for lease in active_leases:
                pid = lease.get("PropertyId") or (lease.get("Property") or {}).get("Id")
                rent = _lease_rent(lease)
                if pid is not None:
                    prop_rents.setdefault(pid, []).append(rent)

            prop_avg: dict[Any, float] = {
                pid: round(sum(rents) / len(rents), m.CENTS)
                for pid, rents in prop_rents.items()
                if rents
            }

            opportunities: list[dict[str, Any]] = []
            for lease in active_leases:
                end = m.parse_date(_lease_end_value(lease))
                if end is None:
                    continue
                days_left = m.days_between(as_of, end)
                if days_left is None or days_left < 0 or days_left > expiry_window_days:
                    continue
                pid = lease.get("PropertyId") or (lease.get("Property") or {}).get("Id")
                avg = prop_avg.get(pid)
                if avg is None or avg <= 0:
                    continue
                rent = _lease_rent(lease)
                gap_pct = round((avg - rent) / avg * 100, 2)
                if gap_pct < threshold:
                    continue
                monthly_uplift = round(avg - rent, m.CENTS)
                annual_uplift = round(monthly_uplift * 12, m.CENTS)
                opportunities.append(
                    {
                        "lease_id": lease.get("Id"),
                        "property_id": pid,
                        "unit_id": lease.get("UnitId") or (lease.get("Unit") or {}).get("Id"),
                        "lease_end": end.isoformat(),
                        "days_until_expiry": days_left,
                        "current_rent": rent,
                        "property_avg_rent": avg,
                        "below_market_pct": gap_pct,
                        "monthly_uplift": monthly_uplift,
                        "annual_uplift": annual_uplift,
                    }
                )

            opportunities.sort(key=lambda r: r["annual_uplift"], reverse=True)

            total_annual_uplift = round(sum(r["annual_uplift"] for r in opportunities), m.CENTS)
            report: dict[str, Any] = {
                "report": "rent_trend",
                "as_of": as_of.isoformat(),
                "expiry_window_days": expiry_window_days,
                "under_market_threshold_pct": threshold,
                "property_id": property_id,
                "opportunity_count": len(opportunities),
                "total_potential_annual_uplift": total_annual_uplift,
                "property_averages": [
                    {"property_id": pid, "avg_rent": avg} for pid, avg in sorted(prop_avg.items())
                ],
                "opportunities": opportunities,
            }
            if fmt:
                columns = [
                    "Lease",
                    "Property",
                    "Unit",
                    "Lease End",
                    "Days Left",
                    "Current Rent",
                    "Property Avg",
                    "Below Market %",
                    "Annual Uplift",
                ]
                table = [
                    [
                        r["lease_id"],
                        r["property_id"],
                        r["unit_id"],
                        r["lease_end"],
                        r["days_until_expiry"],
                        r["current_rent"],
                        r["property_avg_rent"],
                        r["below_market_pct"],
                        r["annual_uplift"],
                    ]
                    for r in opportunities
                ]
                report["export"] = _make_artifact(
                    fmt,
                    filename=f"rent_trend_{as_of.isoformat()}",
                    title=f"Rent Renewal Opportunities — {as_of.isoformat()}",
                    columns=columns,
                    rows=table,
                )
            return report

        return await c.execute("rent_trend_report", _run)

    # -----------------------------------------------------------------------
    # 4. Vendor Spend Analysis
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def vendor_spend_report(
        start_date: str | None = None,
        end_date: str | None = None,
        property_id: int | None = None,
        concentration_threshold_pct: float = 25.0,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Analyse vendor spend concentration and surface top spenders.

        Pulls all bills, groups them by vendor, and reports total spend, invoice
        count, and average invoice size per vendor. Vendors whose share of total
        spend exceeds ``concentration_threshold_pct`` are flagged as concentration
        risks. Results are ranked largest-spend first.

        Args:
            start_date: Include bills with a date on or after this date (YYYY-MM-DD).
            end_date: Include bills with a date on or before this date (YYYY-MM-DD).
            property_id: Optional property to scope to.
            concentration_threshold_pct: Flag vendors whose spend is this % or more
                of the total (default 25 %).
            export_format: Optional ``csv``/``xlsx``/``pdf`` download.
        """
        try:
            fmt = _validate_export(export_format)
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        from_date = m.parse_date(start_date)
        to_date = m.parse_date(end_date)
        concentration = max(0.0, float(concentration_threshold_pct))

        async def _run() -> dict[str, Any]:
            def _bills_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
                if property_id is not None:
                    kwargs["entityid"] = property_id
                    kwargs["entitytype"] = "Rental"
                return client.bills_api.external_api_bills_get_bills_async(**kwargs)

            all_bills = await c.paginate_all(_bills_page)

            # Filter by date range in Python (bill date, not paid date).
            filtered = []
            for bill in all_bills:
                bd = m.parse_date(_bill_date(bill))
                if from_date is not None and bd is not None and bd < from_date:
                    continue
                if to_date is not None and bd is not None and bd > to_date:
                    continue
                filtered.append(bill)

            # Aggregate by vendor.
            vendor_data: dict[Any, dict[str, Any]] = {}
            for bill in filtered:
                vid = _bill_vendor_id(bill)
                name = _bill_vendor_name(bill)
                amt = _bill_amount(bill)
                entry = vendor_data.setdefault(
                    vid,
                    {"vendor_id": vid, "vendor_name": name, "total_spend": 0.0, "invoice_count": 0},
                )
                entry["total_spend"] = round(entry["total_spend"] + amt, m.CENTS)
                entry["invoice_count"] += 1

            total_spend = round(sum(v["total_spend"] for v in vendor_data.values()), m.CENTS)
            rows: list[dict[str, Any]] = []
            for entry in vendor_data.values():
                spend = entry["total_spend"]
                count = entry["invoice_count"]
                share = round(spend / total_spend * 100, 2) if total_spend else 0.0
                avg_invoice = round(spend / count, m.CENTS) if count else 0.0
                rows.append(
                    {
                        **entry,
                        "avg_invoice_size": avg_invoice,
                        "spend_share_pct": share,
                        "concentration_flag": share >= concentration,
                    }
                )

            rows.sort(key=lambda r: r["total_spend"], reverse=True)
            flagged = [r for r in rows if r["concentration_flag"]]

            report: dict[str, Any] = {
                "report": "vendor_spend",
                "start_date": start_date,
                "end_date": end_date,
                "property_id": property_id,
                "concentration_threshold_pct": concentration,
                "bill_count": len(filtered),
                "vendor_count": len(rows),
                "total_spend": total_spend,
                "flagged_vendors": flagged,
                "rows": rows,
            }
            if fmt:
                columns = [
                    "Vendor",
                    "Total Spend",
                    "Invoices",
                    "Avg Invoice",
                    "Spend Share %",
                    "Concentration Flag",
                ]
                table = [
                    [
                        r["vendor_name"],
                        r["total_spend"],
                        r["invoice_count"],
                        r["avg_invoice_size"],
                        r["spend_share_pct"],
                        r["concentration_flag"],
                    ]
                    for r in rows
                ]
                report["export"] = _make_artifact(
                    fmt,
                    filename="vendor_spend",
                    title="Vendor Spend Analysis",
                    columns=columns,
                    rows=table,
                )
            return report

        return await c.execute("vendor_spend_report", _run)

    # -----------------------------------------------------------------------
    # 5. Cash Flow Projection
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def cash_flow_projection(
        property_id: int | None = None,
        as_of_date: str | None = None,
        min_bank_reserve: float | None = None,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Project 30/60/90-day cash flow from scheduled rents and outstanding bills.

        Combines the monthly rent from every active lease (inflows) with the
        outstanding (unpaid) bills due in each horizon (outflows) plus the current
        bank balances to project ending cash at 30, 60, and 90 days. Properties
        where the projected balance falls below ``min_bank_reserve`` are flagged.

        Args:
            property_id: Optional property to scope inflows/outflows to.
            as_of_date: Projection start date (YYYY-MM-DD); defaults to today.
            min_bank_reserve: Flag projected balances below this amount. ``None`` skips.
            export_format: Optional ``csv``/``xlsx``/``pdf`` download.
        """
        try:
            fmt = _validate_export(export_format)
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        as_of = m.parse_date(as_of_date) or date.today()
        reserve = m.money(min_bank_reserve) if min_bank_reserve is not None else None

        async def _run() -> dict[str, Any]:
            # Active lease inflows (monthly rent).
            def _active_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"leasestatuses": ["Active"], "limit": limit, "offset": offset}
                if property_id is not None:
                    kwargs["propertyids"] = [property_id]
                return client.leases_api.external_api_leases_get_leases(**kwargs)

            active_leases = await c.paginate_all(_active_page)
            monthly_inflow = round(sum(_lease_rent(lease) for lease in active_leases), m.CENTS)

            # Outstanding bill outflows due in each horizon.
            def _bills_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"paidstatus": "Unpaid", "limit": limit, "offset": offset}
                if property_id is not None:
                    kwargs["entityid"] = property_id
                    kwargs["entitytype"] = "Rental"
                return client.bills_api.external_api_bills_get_bills_async(**kwargs)

            unpaid_bills = await c.paginate_all(_bills_page)

            # Bucket bills by due date into horizons.
            outflow: dict[str, float] = {"30": 0.0, "60": 0.0, "90": 0.0, "beyond": 0.0}
            horizon_30 = as_of + timedelta(days=30)
            horizon_60 = as_of + timedelta(days=60)
            horizon_90 = as_of + timedelta(days=90)
            for bill in unpaid_bills:
                due = m.parse_date(_bill_due_date(bill))
                amt = _bill_amount(bill)
                if due is None or due <= horizon_30:
                    outflow["30"] = round(outflow["30"] + amt, m.CENTS)
                elif due <= horizon_60:
                    outflow["60"] = round(outflow["60"] + amt, m.CENTS)
                elif due <= horizon_90:
                    outflow["90"] = round(outflow["90"] + amt, m.CENTS)
                else:
                    outflow["beyond"] = round(outflow["beyond"] + amt, m.CENTS)

            # Bank balances.
            def _bank_page(limit: int, offset: int) -> Any:
                return client.bank_accounts_api.external_api_bank_accounts_get_all_bank_accounts(
                    limit=limit, offset=offset
                )

            accounts = await c.paginate_all(_bank_page)
            current_balance = round(
                sum(_bank_balance(a) for a in accounts if a.get("IsActive") is not False),
                m.CENTS,
            )

            # Rolling projected balances. Each horizon adds one more month of
            # scheduled inflow and subtracts that horizon's incremental bill
            # outflow bucket, so the per-month ``inflow_30`` is applied cumulatively.
            inflow_30 = monthly_inflow  # ~1 month

            proj_30 = round(current_balance + inflow_30 - outflow["30"], m.CENTS)
            proj_60 = round(proj_30 + inflow_30 - outflow["60"], m.CENTS)
            proj_90 = round(proj_60 + inflow_30 - outflow["90"], m.CENTS)

            def _flag(bal: float) -> bool:
                return reserve is not None and bal < reserve

            projections = [
                {
                    "horizon": "30_days",
                    "end_date": (as_of + timedelta(days=30)).isoformat(),
                    "projected_inflow": inflow_30,
                    "projected_outflow": outflow["30"],
                    "projected_balance": proj_30,
                    "below_reserve": _flag(proj_30),
                },
                {
                    "horizon": "60_days",
                    "end_date": (as_of + timedelta(days=60)).isoformat(),
                    "projected_inflow": inflow_30,
                    "projected_outflow": outflow["60"],
                    "projected_balance": proj_60,
                    "below_reserve": _flag(proj_60),
                },
                {
                    "horizon": "90_days",
                    "end_date": (as_of + timedelta(days=90)).isoformat(),
                    "projected_inflow": inflow_30,
                    "projected_outflow": outflow["90"],
                    "projected_balance": proj_90,
                    "below_reserve": _flag(proj_90),
                },
            ]

            report: dict[str, Any] = {
                "report": "cash_flow_projection",
                "as_of": as_of.isoformat(),
                "property_id": property_id,
                "current_bank_balance": current_balance,
                "monthly_scheduled_inflow": monthly_inflow,
                "active_lease_count": len(active_leases),
                "unpaid_bill_count": len(unpaid_bills),
                "min_bank_reserve": reserve,
                "projections": projections,
            }
            if fmt:
                columns = ["Horizon", "End Date", "Inflow", "Outflow", "Projected Balance", "Below Reserve"]
                table = [
                    [p["horizon"], p["end_date"], p["projected_inflow"], p["projected_outflow"], p["projected_balance"], p["below_reserve"]]
                    for p in projections
                ]
                report["export"] = _make_artifact(
                    fmt,
                    filename=f"cash_flow_{as_of.isoformat()}",
                    title=f"Cash Flow Projection — {as_of.isoformat()}",
                    columns=columns,
                    rows=table,
                )
            return report

        return await c.execute("cash_flow_projection", _run)

    # -----------------------------------------------------------------------
    # 6. Maintenance Cost per Unit / ROI Analysis
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def maintenance_roi_report(
        property_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        cost_to_rent_threshold_pct: float = 30.0,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Rank units by maintenance cost vs. rent collected — surface the money pits.

        Aggregates work-order costs and bills tagged to each rental property for the
        given period, then computes maintenance spend as a percentage of annualised
        rent. Units exceeding ``cost_to_rent_threshold_pct`` are flagged as
        candidates for capital reinvestment or disposition.

        Args:
            property_id: Optional property to scope to.
            start_date: Period start (YYYY-MM-DD); defaults to 12 months ago.
            end_date: Period end (YYYY-MM-DD); defaults to today.
            cost_to_rent_threshold_pct: Flag properties where maintenance cost is
                this % or more of annual rent (default 30 %).
            export_format: Optional ``csv``/``xlsx``/``pdf`` download.
        """
        try:
            fmt = _validate_export(export_format)
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        as_of = date.today()
        to_date = m.parse_date(end_date) or as_of
        from_date = m.parse_date(start_date) or (to_date - timedelta(days=365))
        threshold = max(0.0, float(cost_to_rent_threshold_pct))

        async def _run() -> dict[str, Any]:
            # Active lease rents by property for annual rent estimation.
            def _leases_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"leasestatuses": ["Active"], "limit": limit, "offset": offset}
                if property_id is not None:
                    kwargs["propertyids"] = [property_id]
                return client.leases_api.external_api_leases_get_leases(**kwargs)

            leases = await c.paginate_all(_leases_page)
            annual_rent_by_prop: dict[Any, float] = {}
            for lease in leases:
                pid = lease.get("PropertyId") or (lease.get("Property") or {}).get("Id")
                if pid is None:
                    continue
                annual_rent_by_prop[pid] = round(
                    annual_rent_by_prop.get(pid, 0.0) + _lease_rent(lease) * 12, m.CENTS
                )

            # Work order costs by property.
            wo_cost_by_prop: dict[Any, float] = {}
            seen_wo: set[Any] = set()
            for status in _WORK_ORDER_STATUSES:

                def _wo_page(limit: int, offset: int, _s: str = status) -> Any:
                    kwargs: dict[str, Any] = {"statuses": [_s], "limit": limit, "offset": offset}
                    return client.work_orders_api.external_api_work_orders_get_all_work_orders(**kwargs)

                wos = await c.paginate_all(_wo_page)
                for wo in wos:
                    wo_id = wo.get("Id")
                    if wo_id in seen_wo:
                        continue
                    seen_wo.add(wo_id)
                    # Filter by date.
                    created = m.parse_date(_work_order_date(wo))
                    if created is not None:
                        if created < from_date or created > to_date:
                            continue
                    pid = _work_order_property_id(wo)
                    if property_id is not None and pid != property_id:
                        continue
                    if pid is None:
                        continue
                    cost = _work_order_amount(wo)
                    wo_cost_by_prop[pid] = round(wo_cost_by_prop.get(pid, 0.0) + cost, m.CENTS)

            # Bill costs by property.
            def _bills_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
                if property_id is not None:
                    kwargs["entityid"] = property_id
                    kwargs["entitytype"] = "Rental"
                return client.bills_api.external_api_bills_get_bills_async(**kwargs)

            bills = await c.paginate_all(_bills_page)
            bill_cost_by_prop: dict[Any, float] = {}
            for bill in bills:
                bd = m.parse_date(_bill_date(bill))
                if bd is not None and (bd < from_date or bd > to_date):
                    continue
                pid = _bill_property_id(bill)
                if property_id is not None and pid != property_id:
                    continue
                if pid is None:
                    continue
                amt = _bill_amount(bill)
                bill_cost_by_prop[pid] = round(bill_cost_by_prop.get(pid, 0.0) + amt, m.CENTS)

            # Build rows for all properties that appear in any dataset.
            all_props = set(annual_rent_by_prop) | set(wo_cost_by_prop) | set(bill_cost_by_prop)
            rows: list[dict[str, Any]] = []
            for pid in all_props:
                annual_rent = annual_rent_by_prop.get(pid, 0.0)
                wo_cost = wo_cost_by_prop.get(pid, 0.0)
                bill_cost = bill_cost_by_prop.get(pid, 0.0)
                total_maint = round(wo_cost + bill_cost, m.CENTS)
                ratio_pct = round(total_maint / annual_rent * 100, 2) if annual_rent else None
                flagged = ratio_pct is not None and ratio_pct >= threshold
                rows.append(
                    {
                        "property_id": pid,
                        "annual_rent": annual_rent,
                        "work_order_cost": wo_cost,
                        "bill_cost": bill_cost,
                        "total_maintenance_cost": total_maint,
                        "cost_to_rent_pct": ratio_pct,
                        "money_pit_flag": flagged,
                    }
                )

            rows.sort(key=lambda r: r["cost_to_rent_pct"] or 0.0, reverse=True)
            flagged_rows = [r for r in rows if r["money_pit_flag"]]

            report: dict[str, Any] = {
                "report": "maintenance_roi",
                "start_date": from_date.isoformat(),
                "end_date": to_date.isoformat(),
                "property_id": property_id,
                "cost_to_rent_threshold_pct": threshold,
                "property_count": len(rows),
                "flagged_count": len(flagged_rows),
                "rows": rows,
                "flagged": flagged_rows,
            }
            if fmt:
                columns = [
                    "Property",
                    "Annual Rent",
                    "Work Order Cost",
                    "Bill Cost",
                    "Total Maintenance",
                    "Cost/Rent %",
                    "Money Pit",
                ]
                table = [
                    [
                        r["property_id"],
                        r["annual_rent"],
                        r["work_order_cost"],
                        r["bill_cost"],
                        r["total_maintenance_cost"],
                        r["cost_to_rent_pct"],
                        r["money_pit_flag"],
                    ]
                    for r in rows
                ]
                report["export"] = _make_artifact(
                    fmt,
                    filename="maintenance_roi",
                    title="Maintenance ROI Report",
                    columns=columns,
                    rows=table,
                )
            return report

        return await c.execute("maintenance_roi_report", _run)

    # -----------------------------------------------------------------------
    # 7. Owner Distribution Summary & Statement
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def owner_distribution_report(
        property_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        uncollected_threshold_days: int = 30,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Per-owner net distributable amount with exportable owner statement.

        For each rental property, computes rent collected (sum of lease payments
        in the period) minus expenses (bills) to derive the net distributable
        amount. Owners where the distributable balance has remained uncollected
        beyond ``uncollected_threshold_days`` are flagged.

        Args:
            property_id: Optional property to scope to.
            start_date: Period start (YYYY-MM-DD); defaults to first of current month.
            end_date: Period end (YYYY-MM-DD); defaults to today.
            uncollected_threshold_days: Flag owners whose distributable balance is
                older than this many days (based on period end date vs. today).
            export_format: Optional ``csv``/``xlsx``/``pdf`` download.
        """
        try:
            fmt = _validate_export(export_format)
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        today = date.today()
        to_date = m.parse_date(end_date) or today
        from_date = m.parse_date(start_date) or to_date.replace(day=1)
        days_since_period_end = m.days_between(to_date, today) or 0
        flag_uncollected = days_since_period_end > uncollected_threshold_days

        async def _run() -> dict[str, Any]:
            # Rental owners.
            def _owners_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
                if property_id is not None:
                    kwargs["propertyids"] = [property_id]
                return client.rental_owners_api.external_api_rental_owners_get_rental_owners(**kwargs)

            owners = await c.paginate_all(_owners_page)
            owner_by_id: dict[Any, dict[str, Any]] = {o.get("Id"): o for o in owners if o.get("Id")}

            # Active leases to identify which properties belong to which owner.
            def _leases_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"leasestatuses": ["Active"], "limit": limit, "offset": offset}
                if property_id is not None:
                    kwargs["propertyids"] = [property_id]
                return client.leases_api.external_api_leases_get_leases(**kwargs)

            leases = await c.paginate_all(_leases_page)

            # Revenue from lease ledger: sum payment transactions in the period.
            income_by_prop: dict[Any, float] = {}
            for lease in leases:
                lease_id = lease.get("Id")
                pid = lease.get("PropertyId") or (lease.get("Property") or {}).get("Id")
                if pid is None:
                    continue

                def _ledger_page(limit: int, offset: int, _lid: Any = lease_id) -> Any:
                    return client.lease_transactions_api.external_api_lease_ledger_transactions_get_lease_ledgers(
                        lease_id=_lid, limit=limit, offset=offset
                    )

                ledger = await c.paginate_all(_ledger_page)
                for txn in ledger:
                    txn_date = m.parse_date(txn.get("Date") or txn.get("TransactionDate"))
                    if txn_date is not None and (txn_date < from_date or txn_date > to_date):
                        continue
                    ttype = str(txn.get("TransactionType") or txn.get("TransactionTypeEnum") or "").lower()
                    if any(w in ttype for w in ("payment", "electronic")):
                        amt = abs(m.to_float(txn.get("TotalAmount") or txn.get("Amount")))
                        income_by_prop[pid] = round(income_by_prop.get(pid, 0.0) + amt, m.CENTS)

            # Expenses from bills.
            def _bills_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
                if property_id is not None:
                    kwargs["entityid"] = property_id
                    kwargs["entitytype"] = "Rental"
                return client.bills_api.external_api_bills_get_bills_async(**kwargs)

            bills = await c.paginate_all(_bills_page)
            expense_by_prop: dict[Any, float] = {}
            for bill in bills:
                bd = m.parse_date(_bill_date(bill))
                if bd is not None and (bd < from_date or bd > to_date):
                    continue
                pid = _bill_property_id(bill)
                if pid is None:
                    continue
                amt = _bill_amount(bill)
                expense_by_prop[pid] = round(expense_by_prop.get(pid, 0.0) + amt, m.CENTS)

            # Outstanding balances (receivables remaining).
            def _bal_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"leasestatuses": ["Active"], "limit": limit, "offset": offset}
                return client.lease_transactions_api.external_api_lease_outstanding_balances_get_lease_outstanding_balances(
                    **kwargs
                )

            balances = await c.paginate_all(_bal_page)
            receivables_by_prop: dict[Any, float] = {}
            for bal in balances:
                pid = bal.get("PropertyId")
                if pid is None:
                    continue
                receivables_by_prop[pid] = round(
                    receivables_by_prop.get(pid, 0.0) + m.money(bal.get("TotalBalance")), m.CENTS
                )

            # Merge into per-owner rows.
            all_props = set(income_by_prop) | set(expense_by_prop) | set(receivables_by_prop)
            prop_rows: list[dict[str, Any]] = []
            for pid in all_props:
                income = income_by_prop.get(pid, 0.0)
                expenses = expense_by_prop.get(pid, 0.0)
                net = round(income - expenses, m.CENTS)
                receivables = receivables_by_prop.get(pid, 0.0)
                prop_rows.append(
                    {
                        "property_id": pid,
                        "rent_collected": income,
                        "expenses_paid": expenses,
                        "net_distributable": net,
                        "outstanding_receivables": receivables,
                        "uncollected_flag": flag_uncollected and net > 0,
                    }
                )

            prop_rows.sort(key=lambda r: r["net_distributable"], reverse=True)

            total_income = round(sum(r["rent_collected"] for r in prop_rows), m.CENTS)
            total_expenses = round(sum(r["expenses_paid"] for r in prop_rows), m.CENTS)
            total_net = round(sum(r["net_distributable"] for r in prop_rows), m.CENTS)
            total_receivables = round(sum(r["outstanding_receivables"] for r in prop_rows), m.CENTS)

            report: dict[str, Any] = {
                "report": "owner_distribution",
                "start_date": from_date.isoformat(),
                "end_date": to_date.isoformat(),
                "property_id": property_id,
                "uncollected_threshold_days": uncollected_threshold_days,
                "property_count": len(prop_rows),
                "owner_count": len(owner_by_id),
                "totals": {
                    "rent_collected": total_income,
                    "expenses_paid": total_expenses,
                    "net_distributable": total_net,
                    "outstanding_receivables": total_receivables,
                },
                "rows": prop_rows,
            }
            if fmt:
                columns = [
                    "Property",
                    "Rent Collected",
                    "Expenses",
                    "Net Distributable",
                    "Receivables",
                    "Uncollected Flag",
                ]
                table = [
                    [
                        r["property_id"],
                        r["rent_collected"],
                        r["expenses_paid"],
                        r["net_distributable"],
                        r["outstanding_receivables"],
                        r["uncollected_flag"],
                    ]
                    for r in prop_rows
                ]
                table.append(["TOTAL", total_income, total_expenses, total_net, total_receivables, ""])
                sections = [
                    Section(
                        heading=f"Period: {from_date.isoformat()} to {to_date.isoformat()}",
                        body=(
                            f"Total Rent Collected: {total_income}\n"
                            f"Total Expenses: {total_expenses}\n"
                            f"Net Distributable: {total_net}\n"
                            f"Outstanding Receivables: {total_receivables}"
                        ),
                    )
                ]
                report["export"] = _make_artifact(
                    fmt,
                    filename=f"owner_distribution_{from_date.isoformat()}",
                    title=f"Owner Distribution Statement {from_date.isoformat()} to {to_date.isoformat()}",
                    columns=columns,
                    rows=table,
                    sections=sections if fmt in ("pdf", "docx") else None,
                )
            return report

        return await c.execute("owner_distribution_report", _run)

    # -----------------------------------------------------------------------
    # 8. Delinquency Trend & Collection Scoring
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def delinquency_trend(
        property_id: int | None = None,
        as_of_date: str | None = None,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Score delinquent leases by severity and surface improving/worsening trends.

        For every lease with an outstanding balance, ages the open charges at three
        points in time (today, 30 days ago, 60 days ago) to detect whether the
        delinquency is growing or shrinking. A collection risk score
        (``days_delinquent × balance``) ranks the hardest-to-collect balances at
        the top. Leases with a balance in the 90+ bucket for multiple periods are
        flagged as chronic.

        Args:
            property_id: Optional property to scope to.
            as_of_date: Current reference date (YYYY-MM-DD); defaults to today.
            export_format: Optional ``csv``/``xlsx``/``pdf`` download.
        """
        try:
            fmt = _validate_export(export_format)
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        as_of = m.parse_date(as_of_date) or date.today()
        as_of_30 = as_of - timedelta(days=30)
        as_of_60 = as_of - timedelta(days=60)

        async def _run() -> dict[str, Any]:
            def _bal_page(limit: int, offset: int) -> Any:
                kwargs: dict[str, Any] = {"leasestatuses": ["Active"], "limit": limit, "offset": offset}
                return client.lease_transactions_api.external_api_lease_outstanding_balances_get_lease_outstanding_balances(
                    **kwargs
                )

            balances = await c.paginate_all(_bal_page)

            rows: list[dict[str, Any]] = []
            for balance in balances:
                lease_id = balance.get("LeaseId") or balance.get("Id")
                pid = balance.get("PropertyId")
                if property_id is not None and pid != property_id:
                    continue
                if m.money(balance.get("TotalBalance")) <= 0 or lease_id is None:
                    continue

                # Fetch charges and payments once.
                def _charges_page(limit: int, offset: int, _lid: Any = lease_id) -> Any:
                    return client.lease_transactions_api.external_api_lease_ledger_charges_read_get_all_charges(
                        lease_id=_lid, limit=limit, offset=offset
                    )

                charges = await c.paginate_all(_charges_page)

                def _ledger_page(limit: int, offset: int, _lid: Any = lease_id) -> Any:
                    return client.lease_transactions_api.external_api_lease_ledger_transactions_get_lease_ledgers(
                        lease_id=_lid, limit=limit, offset=offset
                    )

                ledger = await c.paginate_all(_ledger_page)
                payments_total = 0.0
                for txn in ledger:
                    ttype = str(txn.get("TransactionType") or txn.get("TransactionTypeEnum") or "").lower()
                    if any(w in ttype for w in ("payment", "credit", "refund applied", "electronic")):
                        payments_total += abs(m.to_float(txn.get("TotalAmount") or txn.get("Amount")))
                payments_total = round(payments_total, m.CENTS)

                # Age at three time points.
                aging_now = m.age_receivables(charges, payments_total, as_of)
                aging_30 = m.age_receivables(charges, payments_total, as_of_30)
                aging_60 = m.age_receivables(charges, payments_total, as_of_60)

                balance_now = aging_now.total
                balance_30 = aging_30.total
                balance_60 = aging_60.total

                # Days delinquent = age of the oldest open charge.
                oldest_age = 0
                for oc in aging_now.open_charges:
                    age = m.days_between(oc.charge_date, as_of) or 0
                    oldest_age = max(oldest_age, age)

                risk_score = round(oldest_age * balance_now, m.CENTS)

                # Trend: compare today's balance to 30 days ago.
                trend_delta = round(balance_now - balance_30, m.CENTS)
                if trend_delta > 0:
                    trend = "worsening"
                elif trend_delta < 0:
                    trend = "improving"
                else:
                    trend = "unchanged"

                # Chronic: balance in 90+ bucket at all three snapshots.
                chronic = (
                    aging_now.buckets.get("days_over_90", 0.0) > 0
                    and aging_30.buckets.get("days_over_90", 0.0) > 0
                    and aging_60.buckets.get("days_over_90", 0.0) > 0
                )

                rows.append(
                    {
                        "lease_id": lease_id,
                        "property_id": pid,
                        "unit_id": balance.get("UnitId"),
                        "balance_now": balance_now,
                        "balance_30_days_ago": balance_30,
                        "balance_60_days_ago": balance_60,
                        "trend": trend,
                        "trend_delta": trend_delta,
                        "oldest_charge_age_days": oldest_age,
                        "risk_score": risk_score,
                        "chronic_delinquent": chronic,
                        "aging_buckets": {
                            "current": aging_now.buckets.get("current", 0.0),
                            "days_31_60": aging_now.buckets.get("days_31_60", 0.0),
                            "days_61_90": aging_now.buckets.get("days_61_90", 0.0),
                            "days_over_90": aging_now.buckets.get("days_over_90", 0.0),
                        },
                    }
                )

            rows.sort(key=lambda r: r["risk_score"], reverse=True)
            chronic_count = sum(1 for r in rows if r["chronic_delinquent"])
            worsening_count = sum(1 for r in rows if r["trend"] == "worsening")

            report: dict[str, Any] = {
                "report": "delinquency_trend",
                "as_of": as_of.isoformat(),
                "property_id": property_id,
                "delinquent_lease_count": len(rows),
                "chronic_count": chronic_count,
                "worsening_count": worsening_count,
                "total_delinquent_balance": round(sum(r["balance_now"] for r in rows), m.CENTS),
                "rows": rows,
            }
            if fmt:
                columns = [
                    "Lease",
                    "Property",
                    "Balance Now",
                    "Balance 30d Ago",
                    "Balance 60d Ago",
                    "Trend",
                    "Risk Score",
                    "Chronic",
                ]
                table = [
                    [
                        r["lease_id"],
                        r["property_id"],
                        r["balance_now"],
                        r["balance_30_days_ago"],
                        r["balance_60_days_ago"],
                        r["trend"],
                        r["risk_score"],
                        r["chronic_delinquent"],
                    ]
                    for r in rows
                ]
                report["export"] = _make_artifact(
                    fmt,
                    filename=f"delinquency_trend_{as_of.isoformat()}",
                    title=f"Delinquency Trend — {as_of.isoformat()}",
                    columns=columns,
                    rows=table,
                )
            return report

        return await c.execute("delinquency_trend", _run)


# ---------------------------------------------------------------------------
# Private helpers shared by vacancy_analysis and rent_trend_report
# ---------------------------------------------------------------------------

def _lease_rent(lease: dict[str, Any]) -> float:
    details = lease.get("AccountDetails") or {}
    if isinstance(details, dict) and details.get("Rent") is not None:
        return m.money(details.get("Rent"))
    for key in ("Rent", "RentAmount", "CurrentRent"):
        if lease.get(key) is not None:
            return m.money(lease.get(key))
    return 0.0


def _lease_end_value(lease: dict[str, Any]) -> Any:
    for key in ("LeaseToDate", "ToDate", "CurrentTermEnd", "LeaseEndDate", "EndDate"):
        if lease.get(key):
            return lease[key]
    return None
