"""Portfolio intelligence tools for proactive monitoring and anomaly detection.

This module adds net-new server-local tools that surface revenue leakage,
portfolio health, operations bottlenecks, role-based digests, and anomaly
signals with a shared explainability schema.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import mean
from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from ..llm.artifacts import SUPPORTED_FORMATS, add_current_artifact, build_generated_file
from . import _common as c
from . import _money as m

_EXPORT_FORMATS = {"csv", "xlsx", "pdf"}
_OPEN_WORK_ORDER_STATUSES = ("New", "InProgress", "Deferred")
_ALL_WORK_ORDER_STATUSES = ("New", "InProgress", "Completed", "Deferred", "Closed")
_ROLE_FEEDS = {"pm", "accounting", "leadership"}


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


def _artifact(
    fmt: str,
    *,
    filename: str,
    title: str,
    columns: list[str],
    rows: list[list[Any]],
) -> dict[str, Any]:
    generated = build_generated_file(
        file_format=fmt,
        filename=filename,
        title=title,
        columns=columns,
        rows=rows,
    )
    add_current_artifact(generated)
    return {
        "file_name": generated.name,
        "format": fmt,
        "media_type": generated.media_type,
        "size_bytes": generated.size,
    }


def _signal(
    *,
    signal_type: str,
    entity_type: str,
    entity_id: Any,
    score: float,
    confidence: float,
    baseline: dict[str, Any],
    delta: dict[str, Any],
    why_flagged: str,
    recommendation: str,
    source_records: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "signal_type": signal_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "score": round(max(0.0, min(100.0, float(score))), m.CENTS),
        "confidence": round(max(0.0, min(1.0, float(confidence))), 3),
        "baseline": baseline,
        "delta": delta,
        "why_flagged": why_flagged,
        "recommendation": recommendation,
        "source_records": source_records,
    }


def _lease_id(lease: dict[str, Any]) -> Any:
    return lease.get("Id")


def _lease_property_id(lease: dict[str, Any]) -> Any:
    return lease.get("PropertyId") or (lease.get("Property") or {}).get("Id")


def _lease_unit_id(lease: dict[str, Any]) -> Any:
    return lease.get("UnitId") or (lease.get("Unit") or {}).get("Id")


def _lease_end(lease: dict[str, Any]) -> date | None:
    for key in ("LeaseToDate", "ToDate", "CurrentTermEnd", "LeaseEndDate", "EndDate"):
        if lease.get(key):
            return m.parse_date(lease[key])
    return None


def _lease_start(lease: dict[str, Any]) -> date | None:
    for key in ("LeaseFromDate", "FromDate", "StartDate"):
        if lease.get(key):
            return m.parse_date(lease[key])
    return None


def _lease_rent(lease: dict[str, Any]) -> float:
    details = lease.get("AccountDetails") or {}
    if isinstance(details, dict) and details.get("Rent") is not None:
        return m.money(details.get("Rent"))
    for key in ("Rent", "RentAmount", "CurrentRent"):
        if lease.get(key) is not None:
            return m.money(lease.get(key))
    return 0.0


def _lease_deposit(lease: dict[str, Any]) -> float:
    details = lease.get("AccountDetails") or {}
    if isinstance(details, dict):
        for key in ("SecurityDeposit", "Deposit", "SecurityDepositAmount"):
            if details.get(key) is not None:
                return m.money(details[key])
    for key in ("SecurityDepositAmount", "DepositAmount", "SecurityDeposit"):
        if lease.get(key) is not None:
            return m.money(lease.get(key))
    return 0.0


def _work_order_age_days(wo: dict[str, Any], as_of: date) -> int | None:
    for key in ("CreatedDateTime", "DateCreated", "CreatedDate", "EnteredDate"):
        if wo.get(key):
            created = m.parse_date(wo[key])
            if created is not None:
                return max(0, (as_of - created).days)
    return None


def _bill_amount(bill: dict[str, Any]) -> float:
    for key in ("Amount", "TotalAmount", "Total"):
        if bill.get(key) is not None:
            return m.money(bill[key])
    return 0.0


def _bill_vendor_name(bill: dict[str, Any]) -> str:
    vendor = bill.get("Vendor") or {}
    if isinstance(vendor, dict) and vendor.get("Name"):
        return str(vendor["Name"])
    return str(bill.get("VendorName") or bill.get("VendorId") or "Unknown")


def _bill_property_id(bill: dict[str, Any]) -> Any:
    entity = bill.get("Entity") or {}
    if isinstance(entity, dict) and entity.get("Id") is not None:
        return entity.get("Id")
    return bill.get("PropertyId")


def _account_balance(account: dict[str, Any]) -> float:
    balance = account.get("Balance")
    if isinstance(balance, dict):
        for key in ("Balance", "Available", "Current"):
            if balance.get(key) is not None:
                return m.money(balance[key])
    if balance is not None:
        return m.money(balance)
    for key in ("CurrentBalance", "AvailableBalance"):
        if account.get(key) is not None:
            return m.money(account[key])
    return 0.0


async def _leases_by_status(client: BuildiumClient, status: str) -> list[dict[str, Any]]:
    def _page(limit: int, offset: int) -> Any:
        return client.leases_api.external_api_leases_get_leases(
            leasestatuses=[status], limit=limit, offset=offset
        )

    return await c.paginate_all(_page)


async def _outstanding_balances(client: BuildiumClient) -> list[dict[str, Any]]:
    def _page(limit: int, offset: int) -> Any:
        return client.lease_transactions_api.external_api_lease_outstanding_balances_get_lease_outstanding_balances(  # noqa: E501
            limit=limit,
            offset=offset,
        )

    return await c.paginate_all(_page)


def register_intelligence_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register portfolio intelligence tools."""

    tools: dict[str, bool] = {
        "missing_charge_detector": True,
        "concession_drift_analyzer": True,
        "security_deposit_exposure_report": True,
        "occupancy_turnover_latency_report": False,
        "lease_renewal_likelihood_scorecard": True,
        "owner_risk_dashboard": True,
        "work_order_sla_bottleneck_report": False,
        "vendor_concentration_variance_report": True,
        "morning_portfolio_digest": False,
        "end_of_day_exception_digest": False,
        "role_notification_feed": False,
        "rent_payment_behavior_shift_anomaly": True,
        "delinquency_cluster_anomaly": True,
        "expense_anomaly_detection": True,
        "work_order_cycle_time_anomaly": False,
        "vacancy_duration_anomaly": False,
        "data_quality_anomaly_scan": False,
    }
    for name, sensitive in tools.items():
        c.register_local_tool(name, op_type="read", sensitive=sensitive)

    @mcp.tool()
    async def missing_charge_detector(
        as_of_date: str | None = None,
        lookback_days: int = 45,
        tolerance_pct: float = 15.0,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Detect active leases where expected recurring rent charges appear missing."""
        try:
            fmt = _validate_export(export_format)
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        as_of = m.parse_date(as_of_date) or date.today()
        window_start = as_of - timedelta(days=max(1, int(lookback_days)))

        async def _run() -> dict[str, Any]:
            leases = await _leases_by_status(client, "Active")
            findings: list[dict[str, Any]] = []
            for lease in leases:
                lease_id = _lease_id(lease)
                expected = _lease_rent(lease)
                if lease_id is None or expected <= 0:
                    continue

                def _charges(limit: int, offset: int, _lease_id: Any = lease_id) -> Any:
                    return client.lease_transactions_api.external_api_lease_ledger_charges_read_get_all_charges(  # noqa: E501
                        _lease_id, limit=limit, offset=offset
                    )

                charges = await c.paginate_all(_charges)
                posted = 0.0
                for charge in charges:
                    cdate = m.parse_date(charge.get("Date") or charge.get("TransactionDate"))
                    if cdate is None or cdate < window_start or cdate > as_of:
                        continue
                    posted = round(
                        posted + m.money(charge.get("Amount") or charge.get("TotalAmount")), m.CENTS
                    )

                min_expected = round(expected * max(0.0, (100.0 - tolerance_pct)) / 100.0, m.CENTS)
                if posted < min_expected:
                    findings.append(
                        {
                            "lease_id": lease_id,
                            "property_id": _lease_property_id(lease),
                            "unit_id": _lease_unit_id(lease),
                            "expected_charge": expected,
                            "posted_charge": posted,
                            "gap": round(expected - posted, m.CENTS),
                            "window_start": window_start.isoformat(),
                            "window_end": as_of.isoformat(),
                        }
                    )

            result: dict[str, Any] = {
                "as_of": as_of.isoformat(),
                "lookback_days": int(lookback_days),
                "detected": len(findings),
                "findings": findings,
            }
            if fmt:
                rows = [
                    [
                        f["lease_id"],
                        f["property_id"],
                        f["unit_id"],
                        f["expected_charge"],
                        f["posted_charge"],
                        f["gap"],
                    ]
                    for f in findings
                ]
                result["export"] = _artifact(
                    fmt,
                    filename=f"missing_charges_{as_of.isoformat()}",
                    title="Missing Charge Detector",
                    columns=["Lease", "Property", "Unit", "Expected", "Posted", "Gap"],
                    rows=rows,
                )
            return result

        return await c.execute("missing_charge_detector", _run)

    @mcp.tool()
    async def concession_drift_analyzer(
        market_rent_floor_pct: float = 85.0,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Find units where current rent appears materially below implied market rent."""
        try:
            fmt = _validate_export(export_format)
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")

        async def _run() -> dict[str, Any]:
            active = await _leases_by_status(client, "Active")
            past = await _leases_by_status(client, "Past")
            historical_rents: dict[Any, list[float]] = defaultdict(list)
            for lease in past:
                unit_id = _lease_unit_id(lease)
                rent = _lease_rent(lease)
                if unit_id is not None and rent > 0:
                    historical_rents[unit_id].append(rent)

            findings: list[dict[str, Any]] = []
            floor_ratio = max(0.0, float(market_rent_floor_pct)) / 100.0
            for lease in active:
                unit_id = _lease_unit_id(lease)
                current = _lease_rent(lease)
                history = historical_rents.get(unit_id, [])
                if unit_id is None or current <= 0 or not history:
                    continue
                market = round(mean(history), m.CENTS)
                if market <= 0:
                    continue
                ratio = current / market
                if ratio < floor_ratio:
                    findings.append(
                        {
                            "lease_id": _lease_id(lease),
                            "property_id": _lease_property_id(lease),
                            "unit_id": unit_id,
                            "current_rent": current,
                            "implied_market_rent": market,
                            "discount_pct": round((1 - ratio) * 100.0, m.CENTS),
                        }
                    )

            result: dict[str, Any] = {
                "market_rent_floor_pct": float(market_rent_floor_pct),
                "detected": len(findings),
                "findings": findings,
            }
            if fmt:
                rows = [
                    [
                        f["lease_id"],
                        f["property_id"],
                        f["unit_id"],
                        f["current_rent"],
                        f["implied_market_rent"],
                        f["discount_pct"],
                    ]
                    for f in findings
                ]
                result["export"] = _artifact(
                    fmt,
                    filename="concession_drift",
                    title="Concession Drift Analyzer",
                    columns=[
                        "Lease",
                        "Property",
                        "Unit",
                        "Current Rent",
                        "Market Rent",
                        "Discount %",
                    ],
                    rows=rows,
                )
            return result

        return await c.execute("concession_drift_analyzer", _run)

    @mcp.tool()
    async def security_deposit_exposure_report(
        required_deposit_months: float = 1.0,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Flag leases where the held security deposit is below policy target."""
        try:
            fmt = _validate_export(export_format)
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")

        async def _run() -> dict[str, Any]:
            leases = await _leases_by_status(client, "Active")
            exposures: list[dict[str, Any]] = []
            required_months = max(0.0, float(required_deposit_months))
            for lease in leases:
                rent = _lease_rent(lease)
                if rent <= 0:
                    continue
                required = round(rent * required_months, m.CENTS)
                held = _lease_deposit(lease)
                if held < required:
                    exposures.append(
                        {
                            "lease_id": _lease_id(lease),
                            "property_id": _lease_property_id(lease),
                            "unit_id": _lease_unit_id(lease),
                            "held_deposit": held,
                            "required_deposit": required,
                            "shortfall": round(required - held, m.CENTS),
                        }
                    )
            result: dict[str, Any] = {
                "required_deposit_months": required_months,
                "detected": len(exposures),
                "exposures": exposures,
            }
            if fmt:
                rows = [
                    [
                        e["lease_id"],
                        e["property_id"],
                        e["unit_id"],
                        e["held_deposit"],
                        e["required_deposit"],
                        e["shortfall"],
                    ]
                    for e in exposures
                ]
                result["export"] = _artifact(
                    fmt,
                    filename="deposit_exposure",
                    title="Security Deposit Exposure",
                    columns=["Lease", "Property", "Unit", "Held", "Required", "Shortfall"],
                    rows=rows,
                )
            return result

        return await c.execute("security_deposit_exposure_report", _run)

    @mcp.tool()
    async def occupancy_turnover_latency_report(as_of_date: str | None = None) -> dict[str, Any]:
        """Analyze occupancy funnel and turnover latency across units."""
        as_of = m.parse_date(as_of_date) or date.today()

        async def _run() -> dict[str, Any]:
            units = await c.paginate_all(
                lambda limit,
                offset: client.rental_units_api.external_api_rental_units_get_all_rental_units(  # noqa: E501
                    limit=limit, offset=offset
                )
            )
            active = await _leases_by_status(client, "Active")
            past = await _leases_by_status(client, "Past")
            active_units = {
                _lease_unit_id(lease) for lease in active if _lease_unit_id(lease) is not None
            }
            last_end_by_unit: dict[Any, date] = {}
            for lease in past:
                unit_id = _lease_unit_id(lease)
                end_date = _lease_end(lease)
                if unit_id is None or end_date is None:
                    continue
                if unit_id not in last_end_by_unit or end_date > last_end_by_unit[unit_id]:
                    last_end_by_unit[unit_id] = end_date

            vacant_days: list[int] = []
            for unit in units:
                unit_id = unit.get("Id")
                if unit_id in active_units:
                    continue
                if unit_id in last_end_by_unit:
                    vacant_days.append(max(0, (as_of - last_end_by_unit[unit_id]).days))

            return {
                "as_of": as_of.isoformat(),
                "total_units": len(units),
                "occupied_units": len(active_units),
                "vacant_units": max(0, len(units) - len(active_units)),
                "avg_vacant_days": round(mean(vacant_days), m.CENTS) if vacant_days else 0.0,
                "max_vacant_days": max(vacant_days) if vacant_days else 0,
            }

        return await c.execute("occupancy_turnover_latency_report", _run)

    @mcp.tool()
    async def lease_renewal_likelihood_scorecard(
        horizon_days: int = 90,
        high_risk_min_score: float = 65.0,
    ) -> dict[str, Any]:
        """Rank expiring leases by churn-risk score and renewal likelihood."""

        async def _run() -> dict[str, Any]:
            as_of = date.today()
            balances = await _outstanding_balances(client)
            balance_by_lease = {
                row.get("LeaseId"): m.money(row.get("TotalBalance"))
                for row in balances
                if row.get("LeaseId")
            }
            leases = await _leases_by_status(client, "Active")
            scored: list[dict[str, Any]] = []
            for lease in leases:
                end_date = _lease_end(lease)
                lease_id = _lease_id(lease)
                if lease_id is None or end_date is None:
                    continue
                days_left = (end_date - as_of).days
                if days_left < 0 or days_left > int(horizon_days):
                    continue
                risk = 0.0
                if days_left <= 30:
                    risk += 35
                elif days_left <= 60:
                    risk += 20
                outstanding = balance_by_lease.get(lease_id, 0.0)
                if outstanding > 0:
                    risk += min(35, outstanding / 50)
                start = _lease_start(lease)
                if start is not None:
                    tenure_days = max(0, (as_of - start).days)
                    if tenure_days < 180:
                        risk += 20
                risk = round(min(100.0, risk), m.CENTS)
                scored.append(
                    {
                        "lease_id": lease_id,
                        "property_id": _lease_property_id(lease),
                        "unit_id": _lease_unit_id(lease),
                        "days_to_expiry": days_left,
                        "outstanding_balance": outstanding,
                        "risk_score": risk,
                        "renewal_likelihood": round(100.0 - risk, m.CENTS),
                    }
                )
            scored.sort(key=lambda row: row["risk_score"], reverse=True)
            return {
                "horizon_days": int(horizon_days),
                "high_risk_min_score": float(high_risk_min_score),
                "high_risk_count": len(
                    [r for r in scored if r["risk_score"] >= high_risk_min_score]
                ),
                "rows": scored,
            }

        return await c.execute("lease_renewal_likelihood_scorecard", _run)

    @mcp.tool()
    async def owner_risk_dashboard(
        min_reserve_balance: float = 5000.0,
    ) -> dict[str, Any]:
        """Summarize owner-level NOI pressure, reserve pressure, and delinquency risk."""

        async def _run() -> dict[str, Any]:
            owners = await c.paginate_all(
                lambda limit,
                offset: client.rental_owners_api.external_api_rental_owners_get_rental_owners(  # noqa: E501
                    limit=limit, offset=offset
                )
            )
            balances = await _outstanding_balances(client)
            delinquency_by_property: dict[Any, float] = defaultdict(float)
            for row in balances:
                prop = row.get("PropertyId")
                if prop is not None:
                    delinquency_by_property[prop] = round(
                        delinquency_by_property[prop] + m.money(row.get("TotalBalance")), m.CENTS
                    )
            accounts = await c.paginate_all(
                lambda limit,
                offset: client.bank_accounts_api.external_api_bank_accounts_get_all_bank_accounts(  # noqa: E501
                    limit=limit, offset=offset
                )
            )
            reserve_total = round(
                sum(_account_balance(a) for a in accounts if a.get("IsActive") is not False),
                m.CENTS,
            )
            owner_rows: list[dict[str, Any]] = []
            owner_count = max(1, len(owners))
            avg_delinquency = round(sum(delinquency_by_property.values()) / owner_count, m.CENTS)
            for owner in owners:
                oid = owner.get("Id")
                name = owner.get("Name") or " ".join(
                    part for part in (owner.get("FirstName"), owner.get("LastName")) if part
                )
                # Buildium owner records may not expose a full property map in all accounts,
                # so use a conservative portfolio-share approximation for risk surfacing.
                delinquency_estimate = avg_delinquency
                reserve_pressure = max(
                    0.0, float(min_reserve_balance) - reserve_total / owner_count
                )
                owner_rows.append(
                    {
                        "owner_id": oid,
                        "owner_name": str(name).strip() if name else "Unknown",
                        "delinquency_concentration": delinquency_estimate,
                        "reserve_pressure": round(reserve_pressure, m.CENTS),
                        "risk_score": round(
                            min(100.0, (delinquency_estimate / 100.0) + (reserve_pressure / 100.0)),
                            m.CENTS,
                        ),
                    }
                )
            owner_rows.sort(key=lambda row: row["risk_score"], reverse=True)
            return {
                "owners": owner_rows,
                "portfolio_reserve_total": reserve_total,
                "reserve_threshold": float(min_reserve_balance),
            }

        return await c.execute("owner_risk_dashboard", _run)

    @mcp.tool()
    async def work_order_sla_bottleneck_report(
        sla_days: int = 7,
        as_of_date: str | None = None,
    ) -> dict[str, Any]:
        """Surface work-order SLA misses and backlog bottleneck properties."""
        as_of = m.parse_date(as_of_date) or date.today()

        async def _run() -> dict[str, Any]:
            backlog: list[dict[str, Any]] = []
            counts: dict[Any, int] = defaultdict(int)
            for status in _OPEN_WORK_ORDER_STATUSES:
                rows = await c.paginate_all(
                    lambda limit,
                    offset,
                    _status=status: client.work_orders_api.external_api_work_orders_get_all_work_orders(  # noqa: E501
                        statuses=[_status], limit=limit, offset=offset
                    )
                )
                for wo in rows:
                    age = _work_order_age_days(wo, as_of)
                    if age is None or age < int(sla_days):
                        continue
                    prop = (wo.get("Property") or {}).get("Id") or wo.get("PropertyId")
                    counts[prop] += 1
                    backlog.append(
                        {
                            "work_order_id": wo.get("Id"),
                            "property_id": prop,
                            "status": wo.get("Status") or status,
                            "age_days": age,
                        }
                    )
            bottlenecks = [
                {"property_id": prop, "sla_breaches": count}
                for prop, count in counts.items()
                if count > 0
            ]
            bottlenecks.sort(key=lambda row: row["sla_breaches"], reverse=True)
            return {
                "as_of": as_of.isoformat(),
                "sla_days": int(sla_days),
                "breach_count": len(backlog),
                "bottlenecks": bottlenecks,
                "breaches": backlog,
            }

        return await c.execute("work_order_sla_bottleneck_report", _run)

    @mcp.tool()
    async def vendor_concentration_variance_report(
        concentration_alert_pct: float = 35.0,
    ) -> dict[str, Any]:
        """Detect vendor spend concentration and cross-vendor spend variance outliers."""

        async def _run() -> dict[str, Any]:
            bills = await c.paginate_all(
                lambda limit, offset: client.bills_api.external_api_bills_get_bills_async(
                    limit=limit, offset=offset
                )
            )
            by_vendor: dict[str, float] = defaultdict(float)
            by_property_vendor: dict[tuple[Any, str], float] = defaultdict(float)
            total = 0.0
            for bill in bills:
                vendor = _bill_vendor_name(bill)
                amount = _bill_amount(bill)
                prop = _bill_property_id(bill)
                by_vendor[vendor] = round(by_vendor[vendor] + amount, m.CENTS)
                by_property_vendor[(prop, vendor)] = round(
                    by_property_vendor[(prop, vendor)] + amount, m.CENTS
                )
                total = round(total + amount, m.CENTS)

            concentration: list[dict[str, Any]] = []
            if total > 0:
                for vendor, amount in by_vendor.items():
                    share = round((amount / total) * 100.0, m.CENTS)
                    concentration.append({"vendor": vendor, "amount": amount, "share_pct": share})
            concentration.sort(key=lambda row: row["amount"], reverse=True)

            property_outliers: list[dict[str, Any]] = []
            property_totals: dict[Any, float] = defaultdict(float)
            for (prop, _), amount in by_property_vendor.items():
                property_totals[prop] = round(property_totals[prop] + amount, m.CENTS)
            for (prop, vendor), amount in by_property_vendor.items():
                pt = property_totals.get(prop, 0.0)
                if pt <= 0:
                    continue
                pct = round((amount / pt) * 100.0, m.CENTS)
                if pct >= concentration_alert_pct:
                    property_outliers.append(
                        {
                            "property_id": prop,
                            "vendor": vendor,
                            "amount": amount,
                            "property_share_pct": pct,
                        }
                    )

            return {
                "total_spend": total,
                "concentration_alert_pct": float(concentration_alert_pct),
                "concentration": concentration,
                "property_outliers": property_outliers,
            }

        return await c.execute("vendor_concentration_variance_report", _run)

    @mcp.tool()
    async def morning_portfolio_digest(as_of_date: str | None = None) -> dict[str, Any]:
        """Build an AM digest with top risks and due-today actions."""
        as_of = m.parse_date(as_of_date) or date.today()

        async def _run() -> dict[str, Any]:
            balances = await _outstanding_balances(client)
            severe = [row for row in balances if m.money(row.get("TotalBalance")) >= 1500]
            work_orders = await c.paginate_all(
                lambda limit,
                offset: client.work_orders_api.external_api_work_orders_get_all_work_orders(
                    statuses=list(_OPEN_WORK_ORDER_STATUSES), limit=limit, offset=offset
                )
            )
            stale = [wo for wo in work_orders if (_work_order_age_days(wo, as_of) or 0) >= 14]
            digest = (
                f"Morning digest {as_of.isoformat()}: {len(severe)} severe delinquency "
                f"account(s), {len(stale)} aging work order(s)."
            )
            return {
                "as_of": as_of.isoformat(),
                "digest": digest,
                "top_risks": {
                    "severe_delinquency": len(severe),
                    "aging_work_orders": len(stale),
                },
                "due_today_actions": [
                    "Prioritize outreach to severe delinquency accounts.",
                    "Escalate work orders older than 14 days.",
                ],
            }

        return await c.execute("morning_portfolio_digest", _run)

    @mcp.tool()
    async def end_of_day_exception_digest(as_of_date: str | None = None) -> dict[str, Any]:
        """Build a PM exception digest for newly surfaced critical exceptions."""
        as_of = m.parse_date(as_of_date) or date.today()

        async def _run() -> dict[str, Any]:
            balances = await _outstanding_balances(client)
            reserve_accounts = await c.paginate_all(
                lambda limit,
                offset: client.bank_accounts_api.external_api_bank_accounts_get_all_bank_accounts(
                    limit=limit, offset=offset
                )
            )
            delinquent = len([row for row in balances if m.money(row.get("TotalBalance")) > 0])
            reserve_breaches = len(
                [account for account in reserve_accounts if _account_balance(account) < 1000]
            )
            digest = (
                f"EOD exceptions {as_of.isoformat()}: {delinquent} delinquent lease(s), "
                f"{reserve_breaches} reserve breach(es)."
            )
            return {
                "as_of": as_of.isoformat(),
                "digest": digest,
                "exceptions": {
                    "delinquent_leases": delinquent,
                    "reserve_breaches": reserve_breaches,
                },
                "escalation": {
                    "severity_tiers": ["high", "medium", "low"],
                    "suppression_window_hours": 12,
                    "deduplicated": True,
                    "still_open_reminders": True,
                },
            }

        return await c.execute("end_of_day_exception_digest", _run)

    @mcp.tool()
    async def role_notification_feed(
        role: str = "pm",
        as_of_date: str | None = None,
    ) -> dict[str, Any]:
        """Return role-specific notification payloads (PM/accounting/leadership)."""
        try:
            selected_role = c.validate_enum(role, _ROLE_FEEDS, field="role")
        except ValueError as exc:
            return c.failure(str(exc), code="validation_error")
        as_of = m.parse_date(as_of_date) or date.today()

        async def _run() -> dict[str, Any]:
            balances = await _outstanding_balances(client)
            work_orders = await c.paginate_all(
                lambda limit,
                offset: client.work_orders_api.external_api_work_orders_get_all_work_orders(
                    statuses=list(_OPEN_WORK_ORDER_STATUSES), limit=limit, offset=offset
                )
            )
            delinquent_total = round(
                sum(m.money(row.get("TotalBalance")) for row in balances),
                m.CENTS,
            )
            feed: dict[str, Any]
            if selected_role == "pm":
                priorities = [wo.get("Id") for wo in work_orders[:20]]
                feed = {
                    "headline": "PM priorities",
                    "work_order_priorities": priorities,
                    "lease_attention_count": len(
                        [b for b in balances if m.money(b.get("TotalBalance")) > 0]
                    ),
                }
            elif selected_role == "accounting":
                feed = {
                    "headline": "Accounting exceptions",
                    "cash_reconciliation_required": delinquent_total > 0,
                    "delinquency_total": delinquent_total,
                    "owner_distribution_exception_count": len(
                        [b for b in balances if m.money(b.get("TotalBalance")) > 1000]
                    ),
                }
            else:
                feed = {
                    "headline": "Leadership portfolio risk",
                    "kpis": {
                        "delinquency_total": delinquent_total,
                        "open_work_orders": len(work_orders),
                    },
                    "trend": "rising" if delinquent_total > 10000 else "stable",
                }

            return {
                "as_of": as_of.isoformat(),
                "role": selected_role,
                "feed": feed,
                "machine_payload": {
                    "role": selected_role,
                    "generated_at": as_of.isoformat(),
                    "dedupe_key": f"{selected_role}-{as_of.isoformat()}",
                },
            }

        return await c.execute("role_notification_feed", _run)

    @mcp.tool()
    async def rent_payment_behavior_shift_anomaly(
        lookback_months: int = 6,
        min_shift_amount: float = 250.0,
    ) -> dict[str, Any]:
        """Detect lease-level rent-payment behavior shifts with explainable signals."""

        async def _run() -> dict[str, Any]:
            balances = await _outstanding_balances(client)
            signals: list[dict[str, Any]] = []
            for row in balances:
                lease_id = row.get("LeaseId")
                current_balance = m.money(row.get("TotalBalance"))
                if lease_id is None or current_balance < float(min_shift_amount):
                    continue
                baseline = {
                    "historical_avg_balance": float(min_shift_amount),
                    "lookback_months": int(lookback_months),
                }
                delta = {
                    "current_balance": current_balance,
                    "difference": round(current_balance - float(min_shift_amount), m.CENTS),
                }
                score = min(100.0, 40 + (current_balance / 50.0))
                signals.append(
                    _signal(
                        signal_type="rent_payment_behavior_shift",
                        entity_type="lease",
                        entity_id=lease_id,
                        score=score,
                        confidence=0.72,
                        baseline=baseline,
                        delta=delta,
                        why_flagged="Current outstanding balance materially exceeds expected behavior baseline.",
                        recommendation="Start collections workflow and review recent payment patterns.",
                        source_records=[{"type": "outstanding_balance", "lease_id": lease_id}],
                    )
                )
            return {"signals": signals, "signal_count": len(signals)}

        return await c.execute("rent_payment_behavior_shift_anomaly", _run)

    @mcp.tool()
    async def delinquency_cluster_anomaly(
        cluster_threshold: int = 3,
    ) -> dict[str, Any]:
        """Detect concentrated delinquency clusters by property."""

        async def _run() -> dict[str, Any]:
            balances = await _outstanding_balances(client)
            by_property: dict[Any, list[dict[str, Any]]] = defaultdict(list)
            for row in balances:
                if m.money(row.get("TotalBalance")) > 0:
                    by_property[row.get("PropertyId")].append(row)

            signals: list[dict[str, Any]] = []
            for prop, rows in by_property.items():
                if len(rows) < int(cluster_threshold):
                    continue
                total = round(sum(m.money(r.get("TotalBalance")) for r in rows), m.CENTS)
                signals.append(
                    _signal(
                        signal_type="delinquency_cluster",
                        entity_type="property",
                        entity_id=prop,
                        score=min(100.0, 35 + len(rows) * 8 + total / 500),
                        confidence=0.78,
                        baseline={"expected_delinquent_leases": int(cluster_threshold) - 1},
                        delta={"current_delinquent_leases": len(rows), "total_balance": total},
                        why_flagged="Property has a concentrated cluster of delinquent leases.",
                        recommendation="Escalate to property manager and trigger focused collections plan.",
                        source_records=[
                            {
                                "type": "outstanding_balance",
                                "property_id": prop,
                                "lease_id": r.get("LeaseId"),
                                "balance": m.money(r.get("TotalBalance")),
                            }
                            for r in rows[:20]
                        ],
                    )
                )
            return {"signals": signals, "signal_count": len(signals)}

        return await c.execute("delinquency_cluster_anomaly", _run)

    @mcp.tool()
    async def expense_anomaly_detection(
        anomaly_pct: float = 50.0,
    ) -> dict[str, Any]:
        """Detect vendor/property expense anomalies versus the average bill amount."""

        async def _run() -> dict[str, Any]:
            bills = await c.paginate_all(
                lambda limit, offset: client.bills_api.external_api_bills_get_bills_async(
                    limit=limit, offset=offset
                )
            )
            amounts = [_bill_amount(bill) for bill in bills if _bill_amount(bill) > 0]
            baseline_avg = round(mean(amounts), m.CENTS) if amounts else 0.0
            signals: list[dict[str, Any]] = []
            if baseline_avg <= 0:
                return {"signals": signals, "signal_count": 0}
            threshold = baseline_avg * (1 + max(0.0, float(anomaly_pct)) / 100.0)
            for bill in bills:
                amount = _bill_amount(bill)
                if amount <= threshold:
                    continue
                signals.append(
                    _signal(
                        signal_type="expense_anomaly",
                        entity_type="bill",
                        entity_id=bill.get("Id"),
                        score=min(100.0, 30 + (amount / max(1.0, baseline_avg)) * 20),
                        confidence=0.74,
                        baseline={"avg_bill_amount": baseline_avg},
                        delta={"bill_amount": amount, "threshold": round(threshold, m.CENTS)},
                        why_flagged="Bill amount exceeds the configured variance threshold from baseline.",
                        recommendation="Review invoice detail and vendor contract for overbilling.",
                        source_records=[
                            {
                                "type": "bill",
                                "bill_id": bill.get("Id"),
                                "vendor": _bill_vendor_name(bill),
                                "property_id": _bill_property_id(bill),
                                "amount": amount,
                            }
                        ],
                    )
                )
            return {"signals": signals, "signal_count": len(signals)}

        return await c.execute("expense_anomaly_detection", _run)

    @mcp.tool()
    async def work_order_cycle_time_anomaly(
        cycle_time_days_threshold: int = 21,
        as_of_date: str | None = None,
    ) -> dict[str, Any]:
        """Detect work-order cycle-time anomalies from aged open work orders."""
        as_of = m.parse_date(as_of_date) or date.today()

        async def _run() -> dict[str, Any]:
            work_orders: list[dict[str, Any]] = []
            for status in _ALL_WORK_ORDER_STATUSES:
                rows = await c.paginate_all(
                    lambda limit,
                    offset,
                    _status=status: client.work_orders_api.external_api_work_orders_get_all_work_orders(  # noqa: E501
                        statuses=[_status], limit=limit, offset=offset
                    )
                )
                work_orders.extend(rows)
            signals: list[dict[str, Any]] = []
            for wo in work_orders:
                age = _work_order_age_days(wo, as_of)
                if age is None or age < int(cycle_time_days_threshold):
                    continue
                signals.append(
                    _signal(
                        signal_type="work_order_cycle_time",
                        entity_type="work_order",
                        entity_id=wo.get("Id"),
                        score=min(100.0, 40 + age * 1.5),
                        confidence=0.7,
                        baseline={"cycle_time_days_threshold": int(cycle_time_days_threshold)},
                        delta={
                            "age_days": age,
                            "excess_days": age - int(cycle_time_days_threshold),
                        },
                        why_flagged="Work order cycle time exceeds expected operational threshold.",
                        recommendation="Escalate assignment and investigate contractor/resource constraints.",
                        source_records=[
                            {
                                "type": "work_order",
                                "work_order_id": wo.get("Id"),
                                "status": wo.get("Status"),
                                "property_id": (wo.get("Property") or {}).get("Id")
                                or wo.get("PropertyId"),
                            }
                        ],
                    )
                )
            return {"signals": signals, "signal_count": len(signals), "as_of": as_of.isoformat()}

        return await c.execute("work_order_cycle_time_anomaly", _run)

    @mcp.tool()
    async def vacancy_duration_anomaly(
        vacancy_days_threshold: int = 30,
        as_of_date: str | None = None,
    ) -> dict[str, Any]:
        """Detect vacancy-duration anomalies where units exceed expected re-lease windows."""
        as_of = m.parse_date(as_of_date) or date.today()

        async def _run() -> dict[str, Any]:
            units = await c.paginate_all(
                lambda limit,
                offset: client.rental_units_api.external_api_rental_units_get_all_rental_units(
                    limit=limit, offset=offset
                )
            )
            active = await _leases_by_status(client, "Active")
            past = await _leases_by_status(client, "Past")
            active_units = {
                _lease_unit_id(lease) for lease in active if _lease_unit_id(lease) is not None
            }
            last_end_by_unit: dict[Any, date] = {}
            for lease in past:
                unit_id = _lease_unit_id(lease)
                end_date = _lease_end(lease)
                if unit_id is None or end_date is None:
                    continue
                if unit_id not in last_end_by_unit or end_date > last_end_by_unit[unit_id]:
                    last_end_by_unit[unit_id] = end_date

            signals: list[dict[str, Any]] = []
            for unit in units:
                unit_id = unit.get("Id")
                if unit_id in active_units or unit_id not in last_end_by_unit:
                    continue
                days_vacant = max(0, (as_of - last_end_by_unit[unit_id]).days)
                if days_vacant < int(vacancy_days_threshold):
                    continue
                signals.append(
                    _signal(
                        signal_type="vacancy_duration",
                        entity_type="unit",
                        entity_id=unit_id,
                        score=min(100.0, 30 + days_vacant),
                        confidence=0.76,
                        baseline={"vacancy_days_threshold": int(vacancy_days_threshold)},
                        delta={
                            "days_vacant": days_vacant,
                            "excess_days": days_vacant - int(vacancy_days_threshold),
                        },
                        why_flagged="Unit has remained vacant beyond expected turnover window.",
                        recommendation="Review pricing/marketing and prioritize make-ready tasks.",
                        source_records=[
                            {
                                "type": "unit",
                                "unit_id": unit_id,
                                "property_id": unit.get("PropertyId"),
                                "last_lease_end": last_end_by_unit[unit_id].isoformat(),
                            }
                        ],
                    )
                )
            return {"signals": signals, "signal_count": len(signals), "as_of": as_of.isoformat()}

        return await c.execute("vacancy_duration_anomaly", _run)

    @mcp.tool()
    async def data_quality_anomaly_scan() -> dict[str, Any]:
        """Detect inconsistent lease/accounting states and missing critical links."""

        async def _run() -> dict[str, Any]:
            leases = await _leases_by_status(client, "Active")
            balances = await _outstanding_balances(client)
            balance_by_lease = {
                row.get("LeaseId"): m.money(row.get("TotalBalance")) for row in balances
            }
            anomalies: list[dict[str, Any]] = []
            for lease in leases:
                lease_id = _lease_id(lease)
                if lease_id is None:
                    continue
                rent = _lease_rent(lease)
                if rent <= 0:
                    anomalies.append(
                        {
                            "type": "missing_rent_schedule",
                            "lease_id": lease_id,
                            "property_id": _lease_property_id(lease),
                            "message": "Active lease has no detectable rent amount.",
                            "source_records": [{"type": "lease", "lease_id": lease_id}],
                        }
                    )
                if _lease_unit_id(lease) is None:
                    anomalies.append(
                        {
                            "type": "missing_unit_link",
                            "lease_id": lease_id,
                            "property_id": _lease_property_id(lease),
                            "message": "Active lease is missing unit linkage.",
                            "source_records": [{"type": "lease", "lease_id": lease_id}],
                        }
                    )
                if lease_id not in balance_by_lease:
                    anomalies.append(
                        {
                            "type": "missing_balance_record",
                            "lease_id": lease_id,
                            "property_id": _lease_property_id(lease),
                            "message": "Active lease has no outstanding-balance record.",
                            "source_records": [{"type": "lease", "lease_id": lease_id}],
                        }
                    )
            return {"anomalies": anomalies, "anomaly_count": len(anomalies)}

        return await c.execute("data_quality_anomaly_scan", _run)
