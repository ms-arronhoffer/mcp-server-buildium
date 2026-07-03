"""Proactive, scheduled portfolio intelligence (alerts & digest).

Everything else in this server is *pull* — you ask, it answers. This module adds
*push*: a rules layer that watches the portfolio and surfaces what needs
attention without being asked. Point a scheduler (cron, a task runner, or the
host app) at :func:`portfolio_alerts` on a daily cadence and it becomes a virtual
analyst that prevents lost revenue.

The rules evaluated (all thresholds configurable):

* **lease_expiration** — active leases ending within N days with no renewal
  (no Future lease on the same unit).
* **late_rent** — active leases carrying an outstanding balance.
* **low_bank_balance** — operating accounts below a reserve threshold.
* **aging_work_order** — open work orders older than N days.

The tool returns structured alerts grouped by rule, a human-readable digest, and
optional ``csv``/``xlsx``/``pdf`` export. It is server-local and read/non-sensitive.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastmcp import FastMCP

from ..buildium_client import BuildiumClient
from ..llm.artifacts import add_current_artifact, build_generated_file
from . import _common as c
from . import _money as m

_OPEN_WORK_ORDER_STATUSES = ("New", "InProgress", "Deferred")
_EXPORT_FORMATS = {"csv", "xlsx", "pdf"}

# Severity ranking used to order the digest (most urgent first).
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _alert(rule: str, severity: str, message: str, **details: Any) -> dict[str, Any]:
    return {"rule": rule, "severity": severity, "message": message, "details": details}


def register_alert_tools(mcp: FastMCP, client: BuildiumClient) -> None:
    """Register the proactive portfolio-alert tools with the MCP server."""

    c.register_local_tool("portfolio_alerts", op_type="read", sensitive=False)

    @mcp.tool()
    async def portfolio_alerts(
        property_id: int | None = None,
        lease_expiry_days: int = 60,
        include_late_rent: bool = True,
        min_bank_reserve: float | None = None,
        work_order_age_days: int = 14,
        as_of_date: str | None = None,
        export_format: str | None = None,
    ) -> dict[str, Any]:
        """Scan the portfolio and return prioritised alerts + a daily digest.

        Run this on a schedule (e.g. a daily cron hitting the assistant) to turn
        the server from a query tool into a proactive analyst. Each rule can be
        tuned or disabled via its threshold argument.

        Args:
            property_id: Optional property to scope the scan to.
            lease_expiry_days: Flag active leases ending within this many days
                that have no renewal. Set to 0 to disable.
            include_late_rent: Flag active leases with an outstanding balance.
            min_bank_reserve: Flag bank accounts below this balance. ``None`` disables.
            work_order_age_days: Flag open work orders older than this many days.
                Set to 0 to disable.
            as_of_date: Reference date (YYYY-MM-DD); defaults to today.
            export_format: Optional ``csv``/``xlsx``/``pdf`` download of the alerts.
        """
        fmt = (export_format or "").strip().lower() or None
        if fmt is not None and fmt not in _EXPORT_FORMATS:
            return c.failure(
                f"Unsupported export_format {export_format!r}. "
                f"Choose one of: {', '.join(sorted(_EXPORT_FORMATS))}.",
                code="validation_error",
            )
        as_of = m.parse_date(as_of_date) or date.today()

        async def _run() -> dict[str, Any]:
            alerts: list[dict[str, Any]] = []
            if lease_expiry_days and lease_expiry_days > 0:
                alerts.extend(
                    await _lease_expiration_alerts(client, property_id, lease_expiry_days, as_of)
                )
            if include_late_rent:
                alerts.extend(await _late_rent_alerts(client, property_id))
            if min_bank_reserve is not None:
                alerts.extend(await _low_bank_balance_alerts(client, min_bank_reserve))
            if work_order_age_days and work_order_age_days > 0:
                alerts.extend(await _aging_work_order_alerts(client, work_order_age_days, as_of))

            alerts.sort(key=lambda a: _SEVERITY_RANK.get(a["severity"], 9))
            by_rule: dict[str, int] = {}
            for alert in alerts:
                by_rule[alert["rule"]] = by_rule.get(alert["rule"], 0) + 1

            digest = _build_digest(as_of, alerts, by_rule)
            result: dict[str, Any] = {
                "as_of": as_of.isoformat(),
                "property_id": property_id,
                "alert_count": len(alerts),
                "counts_by_rule": by_rule,
                "digest": digest,
                "alerts": alerts,
            }
            if fmt:
                result["export"] = _alerts_export(fmt, as_of, alerts)
            return result

        return await c.execute("portfolio_alerts", _run)


async def _lease_expiration_alerts(
    client: BuildiumClient, property_id: int | None, within_days: int, as_of: date
) -> list[dict[str, Any]]:
    def _active_page(limit: int, offset: int) -> Any:
        kwargs: dict[str, Any] = {"leasestatuses": ["Active"], "limit": limit, "offset": offset}
        if property_id is not None:
            kwargs["propertyids"] = [property_id]
        return client.leases_api.external_api_leases_get_leases(**kwargs)

    def _future_page(limit: int, offset: int) -> Any:
        kwargs: dict[str, Any] = {"leasestatuses": ["Future"], "limit": limit, "offset": offset}
        if property_id is not None:
            kwargs["propertyids"] = [property_id]
        return client.leases_api.external_api_leases_get_leases(**kwargs)

    active = await c.paginate_all(_active_page)
    future = await c.paginate_all(_future_page)
    renewed_units = {
        lease.get("UnitId") or (lease.get("Unit") or {}).get("Id")
        for lease in future
        if (lease.get("UnitId") or (lease.get("Unit") or {}).get("Id")) is not None
    }

    alerts: list[dict[str, Any]] = []
    for lease in active:
        end = m.parse_date(_lease_end_value(lease))
        if end is None:
            continue
        days_left = m.days_between(as_of, end)
        if days_left is None or days_left < 0 or days_left > within_days:
            continue
        unit_id = lease.get("UnitId") or (lease.get("Unit") or {}).get("Id")
        if unit_id in renewed_units:
            continue
        severity = "high" if days_left <= 30 else "medium"
        alerts.append(
            _alert(
                "lease_expiration",
                severity,
                f"Lease {lease.get('Id')} expires in {days_left} days with no renewal.",
                lease_id=lease.get("Id"),
                property_id=lease.get("PropertyId"),
                unit_id=unit_id,
                lease_to=end.isoformat(),
                days_until_expiry=days_left,
            )
        )
    return alerts


def _lease_end_value(lease: dict[str, Any]) -> Any:
    for key in ("LeaseToDate", "ToDate", "CurrentTermEnd", "LeaseEndDate", "EndDate"):
        if lease.get(key):
            return lease[key]
    return None


async def _late_rent_alerts(
    client: BuildiumClient, property_id: int | None
) -> list[dict[str, Any]]:
    def _page(limit: int, offset: int) -> Any:
        return client.lease_transactions_api.external_api_lease_outstanding_balances_get_lease_outstanding_balances(
            leasestatuses=["Active"], limit=limit, offset=offset
        )

    balances = await c.paginate_all(_page)
    alerts: list[dict[str, Any]] = []
    for balance in balances:
        if property_id is not None and balance.get("PropertyId") not in (property_id, None):
            continue
        total = m.money(balance.get("TotalBalance"))
        if total <= 0:
            continue
        severity = "high" if total >= 1000 else "medium"
        alerts.append(
            _alert(
                "late_rent",
                severity,
                f"Lease {balance.get('LeaseId')} owes {total:.2f}.",
                lease_id=balance.get("LeaseId"),
                property_id=balance.get("PropertyId"),
                unit_id=balance.get("UnitId"),
                balance=total,
            )
        )
    return alerts


async def _low_bank_balance_alerts(
    client: BuildiumClient, min_reserve: float
) -> list[dict[str, Any]]:
    def _page(limit: int, offset: int) -> Any:
        return client.bank_accounts_api.external_api_bank_accounts_get_all_bank_accounts(
            limit=limit, offset=offset
        )

    accounts = await c.paginate_all(_page)
    threshold = m.money(min_reserve)
    alerts: list[dict[str, Any]] = []
    for account in accounts:
        if account.get("IsActive") is False:
            continue
        balance = m.money(_bank_balance(account))
        if balance >= threshold:
            continue
        alerts.append(
            _alert(
                "low_bank_balance",
                "high",
                f"Account '{account.get('Name')}' balance {balance:.2f} is below "
                f"reserve {threshold:.2f}.",
                bank_account_id=account.get("Id"),
                name=account.get("Name"),
                balance=balance,
                reserve=threshold,
            )
        )
    return alerts


def _bank_balance(account: dict[str, Any]) -> Any:
    balance = account.get("Balance")
    if isinstance(balance, dict):
        for key in ("Balance", "Available", "Current"):
            if balance.get(key) is not None:
                return balance[key]
    if balance is not None:
        return balance
    return account.get("CurrentBalance") or account.get("AvailableBalance")


async def _aging_work_order_alerts(
    client: BuildiumClient, age_days: int, as_of: date
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for status in _OPEN_WORK_ORDER_STATUSES:

        def _page(limit: int, offset: int, _status: str = status) -> Any:
            return client.work_orders_api.external_api_work_orders_get_all_work_orders(
                statuses=[_status], limit=limit, offset=offset
            )

        work_orders = await c.paginate_all(_page)
        for wo in work_orders:
            wo_id = wo.get("Id")
            if wo_id in seen:
                continue
            seen.add(wo_id)
            created = m.parse_date(_work_order_date(wo))
            age = m.days_between(created, as_of)
            if age is None or age <= age_days:
                continue
            severity = "high" if age > age_days * 2 else "medium"
            alerts.append(
                _alert(
                    "aging_work_order",
                    severity,
                    f"Work order {wo_id} has been open {age} days.",
                    work_order_id=wo_id,
                    status=wo.get("Status"),
                    age_days=age,
                    property_id=(wo.get("Property") or {}).get("Id"),
                )
            )
    return alerts


def _work_order_date(wo: dict[str, Any]) -> Any:
    for key in ("CreatedDateTime", "DateCreated", "CreatedDate", "WorkOrderDate", "EnteredDate"):
        if wo.get(key):
            return wo[key]
    return None


def _build_digest(as_of: date, alerts: list[dict[str, Any]], by_rule: dict[str, int]) -> str:
    """Compose a short, human-readable daily digest of the alerts."""
    if not alerts:
        return f"Portfolio digest for {as_of.isoformat()}: all clear — no alerts."
    lines = [f"Portfolio digest for {as_of.isoformat()}: {len(alerts)} alert(s)."]
    labels = {
        "lease_expiration": "leases expiring soon with no renewal",
        "late_rent": "leases with outstanding rent",
        "low_bank_balance": "bank accounts below reserve",
        "aging_work_order": "work orders open too long",
    }
    for rule, count in sorted(by_rule.items(), key=lambda kv: kv[0]):
        lines.append(f"- {count} {labels.get(rule, rule)}")
    return "\n".join(lines)


def _alerts_export(export_format: str, as_of: date, alerts: list[dict[str, Any]]) -> dict[str, Any]:
    columns = ["Severity", "Rule", "Message"]
    rows = [[a["severity"], a["rule"], a["message"]] for a in alerts]
    generated = build_generated_file(
        file_format=export_format,
        filename=f"portfolio_alerts_{as_of.isoformat()}",
        title=f"Portfolio Alerts - {as_of.isoformat()}",
        columns=columns,
        rows=rows,
    )
    add_current_artifact(generated)
    return {
        "file_name": generated.name,
        "format": export_format,
        "media_type": generated.media_type,
        "size_bytes": generated.size,
    }
