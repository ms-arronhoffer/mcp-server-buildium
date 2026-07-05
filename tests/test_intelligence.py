"""Tests for portfolio intelligence tools (intelligence.py)."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from mcp_server_buildium.tools.intelligence import register_intelligence_tools


async def _get_tool(client: Any, name: str) -> Any:
    mcp = FastMCP("test")
    register_intelligence_tools(mcp, client)
    if hasattr(mcp, "get_tools"):
        tools = await mcp.get_tools()
        return tools[name]
    return await mcp.get_tool(name)


class _LeasesApi:
    def __init__(self, by_status: dict[str, list[dict[str, Any]]]) -> None:
        self._by_status = by_status

    async def external_api_leases_get_leases(self, **kwargs: Any) -> list[dict[str, Any]]:
        statuses = kwargs.get("leasestatuses") or ["Active"]
        rows = self._by_status.get(statuses[0], [])
        return rows if kwargs.get("offset", 0) == 0 else []


class _LeaseTxnApi:
    def __init__(
        self,
        balances: list[dict[str, Any]] | None = None,
        charges: dict[int, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._balances = balances or []
        self._charges = charges or {}

    async def external_api_lease_outstanding_balances_get_lease_outstanding_balances(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._balances if kwargs.get("offset", 0) == 0 else []

    async def external_api_lease_ledger_charges_read_get_all_charges(
        self, lease_id: int, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._charges.get(lease_id, []) if kwargs.get("offset", 0) == 0 else []


class _UnitsApi:
    def __init__(self, units: list[dict[str, Any]]) -> None:
        self._units = units

    async def external_api_rental_units_get_all_rental_units(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._units if kwargs.get("offset", 0) == 0 else []


class _WorkOrdersApi:
    def __init__(self, work_orders: list[dict[str, Any]]) -> None:
        self._work_orders = work_orders

    async def external_api_work_orders_get_all_work_orders(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        statuses = kwargs.get("statuses")
        rows = self._work_orders
        if statuses:
            rows = [wo for wo in rows if (wo.get("Status") or "") in statuses]
        return rows if kwargs.get("offset", 0) == 0 else []


class _BillsApi:
    def __init__(self, bills: list[dict[str, Any]]) -> None:
        self._bills = bills

    async def external_api_bills_get_bills_async(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._bills if kwargs.get("offset", 0) == 0 else []


class _OwnersApi:
    def __init__(self, owners: list[dict[str, Any]]) -> None:
        self._owners = owners

    async def external_api_rental_owners_get_rental_owners(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._owners if kwargs.get("offset", 0) == 0 else []


class _BankAccountsApi:
    def __init__(self, accounts: list[dict[str, Any]]) -> None:
        self._accounts = accounts

    async def external_api_bank_accounts_get_all_bank_accounts(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._accounts if kwargs.get("offset", 0) == 0 else []


class _Client:
    def __init__(self, **apis: Any) -> None:
        self.leases_api = apis.get("leases_api")
        self.lease_transactions_api = apis.get("lease_transactions_api")
        self.rental_units_api = apis.get("rental_units_api")
        self.work_orders_api = apis.get("work_orders_api")
        self.bills_api = apis.get("bills_api")
        self.rental_owners_api = apis.get("rental_owners_api")
        self.bank_accounts_api = apis.get("bank_accounts_api")


@pytest.mark.asyncio
async def test_missing_charge_detector_finds_gap() -> None:
    leases = {"Active": [{"Id": 1, "PropertyId": 10, "UnitId": 100, "Rent": 1200.0}]}
    charges = {1: [{"Id": 99, "Date": "2026-07-01", "Amount": 200.0}]}
    client = _Client(
        leases_api=_LeasesApi(leases), lease_transactions_api=_LeaseTxnApi(charges=charges)
    )
    tool = await _get_tool(client, "missing_charge_detector")
    result = await tool.fn(as_of_date="2026-07-10", lookback_days=30)
    assert result["error"] is None
    assert result["data"]["detected"] == 1
    assert result["data"]["findings"][0]["gap"] == 1000.0


@pytest.mark.asyncio
async def test_concession_drift_analyzer_flags_under_market() -> None:
    leases = {
        "Active": [{"Id": 11, "PropertyId": 10, "UnitId": 9, "Rent": 1000.0}],
        "Past": [{"Id": 10, "PropertyId": 10, "UnitId": 9, "Rent": 1500.0}],
    }
    client = _Client(leases_api=_LeasesApi(leases), lease_transactions_api=_LeaseTxnApi())
    tool = await _get_tool(client, "concession_drift_analyzer")
    result = await tool.fn(market_rent_floor_pct=80)
    assert result["error"] is None
    assert result["data"]["detected"] == 1
    assert result["data"]["findings"][0]["discount_pct"] > 0


@pytest.mark.asyncio
async def test_security_deposit_exposure_report_flags_shortfall() -> None:
    leases = {
        "Active": [
            {"Id": 21, "PropertyId": 8, "UnitId": 3, "Rent": 1500.0, "SecurityDeposit": 500.0}
        ]
    }
    client = _Client(leases_api=_LeasesApi(leases), lease_transactions_api=_LeaseTxnApi())
    tool = await _get_tool(client, "security_deposit_exposure_report")
    result = await tool.fn(required_deposit_months=1.0)
    assert result["error"] is None
    assert result["data"]["detected"] == 1
    assert result["data"]["exposures"][0]["shortfall"] == 1000.0


@pytest.mark.asyncio
async def test_work_order_sla_bottleneck_report_counts_breaches() -> None:
    work_orders = [
        {"Id": 1, "Status": "New", "CreatedDate": "2026-06-01", "PropertyId": 10},
        {"Id": 2, "Status": "InProgress", "CreatedDate": "2026-07-09", "PropertyId": 10},
    ]
    client = _Client(
        work_orders_api=_WorkOrdersApi(work_orders), lease_transactions_api=_LeaseTxnApi()
    )
    tool = await _get_tool(client, "work_order_sla_bottleneck_report")
    result = await tool.fn(as_of_date="2026-07-10", sla_days=7)
    assert result["error"] is None
    assert result["data"]["breach_count"] == 1


@pytest.mark.asyncio
async def test_vendor_concentration_variance_report_surfaces_outlier() -> None:
    bills = [
        {"Id": 1, "Amount": 800.0, "Vendor": {"Name": "A"}, "PropertyId": 10},
        {"Id": 2, "Amount": 200.0, "Vendor": {"Name": "B"}, "PropertyId": 10},
    ]
    client = _Client(bills_api=_BillsApi(bills), lease_transactions_api=_LeaseTxnApi())
    tool = await _get_tool(client, "vendor_concentration_variance_report")
    result = await tool.fn(concentration_alert_pct=70)
    assert result["error"] is None
    assert len(result["data"]["property_outliers"]) == 1
    assert result["data"]["property_outliers"][0]["vendor"] == "A"


@pytest.mark.asyncio
async def test_role_notification_feed_validates_role() -> None:
    client = _Client(lease_transactions_api=_LeaseTxnApi(), work_orders_api=_WorkOrdersApi([]))
    tool = await _get_tool(client, "role_notification_feed")
    bad = await tool.fn(role="invalid")
    assert bad["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_rent_payment_behavior_shift_anomaly_has_explainability_fields() -> None:
    balances = [{"LeaseId": 77, "PropertyId": 10, "TotalBalance": 1200.0}]
    client = _Client(lease_transactions_api=_LeaseTxnApi(balances=balances))
    tool = await _get_tool(client, "rent_payment_behavior_shift_anomaly")
    result = await tool.fn(min_shift_amount=200)
    assert result["error"] is None
    signal = result["data"]["signals"][0]
    assert set(
        [
            "score",
            "confidence",
            "baseline",
            "delta",
            "why_flagged",
            "recommendation",
            "source_records",
        ]
    ).issubset(signal.keys())


@pytest.mark.asyncio
async def test_vacancy_duration_anomaly_flags_long_vacancy() -> None:
    leases = {
        "Active": [{"Id": 1, "UnitId": 200, "PropertyId": 10, "Rent": 1000}],
        "Past": [{"Id": 2, "UnitId": 201, "PropertyId": 10, "EndDate": "2026-05-01", "Rent": 1100}],
    }
    units = [{"Id": 200, "PropertyId": 10}, {"Id": 201, "PropertyId": 10}]
    client = _Client(
        leases_api=_LeasesApi(leases),
        rental_units_api=_UnitsApi(units),
        lease_transactions_api=_LeaseTxnApi(),
    )
    tool = await _get_tool(client, "vacancy_duration_anomaly")
    result = await tool.fn(as_of_date="2026-07-10", vacancy_days_threshold=30)
    assert result["error"] is None
    assert result["data"]["signal_count"] == 1


@pytest.mark.asyncio
async def test_data_quality_anomaly_scan_detects_missing_links() -> None:
    leases = {"Active": [{"Id": 99, "PropertyId": 10, "Rent": 0.0}]}
    balances = []
    client = _Client(
        leases_api=_LeasesApi(leases), lease_transactions_api=_LeaseTxnApi(balances=balances)
    )
    tool = await _get_tool(client, "data_quality_anomaly_scan")
    result = await tool.fn()
    assert result["error"] is None
    assert result["data"]["anomaly_count"] >= 2
