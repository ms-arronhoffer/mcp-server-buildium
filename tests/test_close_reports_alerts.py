"""Tests for the financial reporting, close automation, and alert tools.

These exercise the three "wow" capabilities end-to-end against fake Buildium API
objects (no network): deterministic, reconciled reports; the month-end close
orchestration (dry-run and execute); and the proactive portfolio-alert rules.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from mcp_server_buildium.llm import artifacts
from mcp_server_buildium.tools._common import list_tools_map
from mcp_server_buildium.tools.alerts import register_alert_tools
from mcp_server_buildium.tools.close import register_close_tools
from mcp_server_buildium.tools.reports import register_report_tools


async def _get_tool(register: Any, client: Any, name: str) -> Any:
    mcp = FastMCP("test")
    register(mcp, client)
    tools = await list_tools_map(mcp)
    return tools[name]


class _LeasesApi:
    def __init__(self, by_status: dict[str, list[dict[str, Any]]]) -> None:
        self._by_status = by_status
        self.calls: list[dict[str, Any]] = []

    async def external_api_leases_get_leases(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        statuses = kwargs.get("leasestatuses") or ["Active"]
        rows = self._by_status.get(statuses[0], [])
        # Honor pagination so paginate_all terminates.
        return rows if kwargs.get("offset", 0) == 0 else []


class _LeaseTxnApi:
    def __init__(
        self,
        balances: list[dict[str, Any]] | None = None,
        charges: dict[int, list[dict[str, Any]]] | None = None,
        ledgers: dict[int, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._balances = balances or []
        self._charges = charges or {}
        self._ledgers = ledgers or {}
        self.created_charges: list[dict[str, Any]] = []

    async def external_api_lease_outstanding_balances_get_lease_outstanding_balances(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._balances if kwargs.get("offset", 0) == 0 else []

    async def external_api_lease_ledger_charges_read_get_all_charges(
        self, lease_id: int, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._charges.get(lease_id, []) if kwargs.get("offset", 0) == 0 else []

    async def external_api_lease_ledger_transactions_get_lease_ledgers(
        self, lease_id: int, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._ledgers.get(lease_id, []) if kwargs.get("offset", 0) == 0 else []

    async def external_api_lease_ledger_charges_write_create_charge(
        self, lease_id: int, lease_charge_post_message: Any
    ) -> dict[str, Any]:
        self.created_charges.append({"lease_id": lease_id, "message": lease_charge_post_message})
        return {"Id": 999, "LeaseId": lease_id}


class _GLApi:
    def __init__(self, transactions: list[dict[str, Any]]) -> None:
        self._transactions = transactions

    async def external_api_general_ledger_transactions_get_all_transactions(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._transactions if kwargs.get("offset", 0) == 0 else []


class _ReportClient:
    def __init__(self, leases_api=None, lease_txn_api=None, gl_api=None) -> None:
        self.leases_api = leases_api
        self.lease_transactions_api = lease_txn_api
        self.general_ledger_api = gl_api


# ---------------------------------------------------------------------------
# Rent roll
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rent_roll_report_totals_and_reconciles() -> None:
    leases = {
        "Active": [
            {
                "Id": 1,
                "PropertyId": 10,
                "UnitId": 100,
                "AccountDetails": {"Rent": 1500.0},
                "Tenants": [{"FirstName": "Ann", "LastName": "Lee"}],
            },
            {"Id": 2, "PropertyId": 10, "UnitId": 101, "Rent": 2000.0, "Tenants": []},
        ]
    }
    client = _ReportClient(leases_api=_LeasesApi(leases))
    tool = await _get_tool(register_report_tools, client, "rent_roll_report")
    result = await tool.fn(property_id=10)
    data = result["data"]
    assert data["unit_count"] == 2
    assert data["total_monthly_rent"] == 3500.0
    assert data["reconciled"] is True
    assert data["rows"][0]["Tenants"] == "Ann Lee"


@pytest.mark.asyncio
async def test_rent_roll_report_export_creates_artifact() -> None:
    leases = {
        "Active": [{"Id": 1, "PropertyId": 10, "UnitId": 100, "AccountDetails": {"Rent": 1000.0}}]
    }
    client = _ReportClient(leases_api=_LeasesApi(leases))
    tool = await _get_tool(register_report_tools, client, "rent_roll_report")
    token = artifacts.set_current_artifacts()
    try:
        result = await tool.fn(export_format="xlsx")
        assert result["data"]["export"]["format"] == "xlsx"
        assert len(artifacts.get_current_artifacts()) == 1
    finally:
        artifacts.current_artifacts.reset(token)


@pytest.mark.asyncio
async def test_rent_roll_rejects_bad_export_format() -> None:
    client = _ReportClient(leases_api=_LeasesApi({"Active": []}))
    tool = await _get_tool(register_report_tools, client, "rent_roll_report")
    result = await tool.fn(export_format="pptx")
    assert result["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# Aged receivables
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_aged_receivables_report_buckets_and_reconciles() -> None:
    balances = [
        {"LeaseId": 1, "PropertyId": 10, "UnitId": 100, "TotalBalance": 300.0},
        {"LeaseId": 2, "PropertyId": 10, "UnitId": 101, "TotalBalance": 0.0},  # skipped
    ]
    charges = {
        1: [
            {"Id": 11, "Date": "2026-03-20", "Amount": 100.0},
            {"Id": 12, "Date": "2026-01-01", "Amount": 200.0},
        ]
    }
    ledgers = {1: []}  # no payments
    client = _ReportClient(
        lease_txn_api=_LeaseTxnApi(balances=balances, charges=charges, ledgers=ledgers)
    )
    tool = await _get_tool(register_report_tools, client, "aged_receivables_report")
    result = await tool.fn(as_of_date="2026-04-01")
    data = result["data"]
    assert data["lease_count"] == 1
    assert data["totals"]["total"] == 300.0
    assert data["reconciled"] is True
    # The old charge (Jan 1 -> ~90 days) lands in 61-90, the recent in current.
    assert data["rows"][0]["current"] == 100.0
    assert data["rows"][0]["days_61_90"] == 200.0
    assert data["rows"][0]["OpenChargeIds"] == [12, 11]


# ---------------------------------------------------------------------------
# Income statement
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_income_statement_report_reconciles() -> None:
    txns = [
        {
            "Id": 1,
            "Journal": {
                "Lines": [
                    {
                        "GLAccount": {"Id": 1, "Name": "Rent", "Type": "Income"},
                        "Amount": 1000.0,
                        "PostingType": "Credit",
                    },
                ]
            },
        },
        {
            "Id": 2,
            "Journal": {
                "Lines": [
                    {
                        "GLAccount": {"Id": 2, "Name": "Repairs", "Type": "Expense"},
                        "Amount": 300.0,
                        "PostingType": "Debit",
                    },
                ]
            },
        },
    ]
    client = _ReportClient(gl_api=_GLApi(txns))
    tool = await _get_tool(register_report_tools, client, "income_statement_report")
    result = await tool.fn(start_date="2026-07-01", end_date="2026-07-31")
    data = result["data"]
    assert data["total_income"] == 1000.0
    assert data["total_expense"] == 300.0
    assert data["net_income"] == 700.0
    assert data["reconciled"] is True
    assert set(data["source_transaction_ids"]) == {1, 2}


@pytest.mark.asyncio
async def test_income_statement_requires_dates() -> None:
    client = _ReportClient(gl_api=_GLApi([]))
    tool = await _get_tool(register_report_tools, client, "income_statement_report")
    result = await tool.fn(start_date="", end_date="2026-07-31")
    assert result["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# Month-end close
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_month_end_close_dry_run_plans_without_writing() -> None:
    leases = {
        "Active": [
            {"Id": 1, "PropertyId": 10, "UnitId": 100, "AccountDetails": {"Rent": 1200.0}},
        ]
    }
    # One old unpaid charge (overdue) so a late fee is planned.
    charges = {1: [{"Id": 5, "Date": "2026-01-01", "Amount": 1200.0}]}
    ledgers = {1: []}
    txn_api = _LeaseTxnApi(charges=charges, ledgers=ledgers)
    client = _ReportClient(leases_api=_LeasesApi(leases), lease_txn_api=txn_api)
    tool = await _get_tool(register_close_tools, client, "run_month_end_close")
    result = await tool.fn(
        property_id=10, as_of_date="2026-07-01", late_fee_amount=50.0, dry_run=True
    )
    data = result["data"]
    assert data["dry_run"] is True
    assert data["lease_count"] == 1
    action = data["lease_actions"][0]
    assert action["rent_charge"] == 1200.0
    assert action["overdue_balance"] == 1200.0
    assert action["late_fee"] == 50.0
    assert action["posted"] is None
    # No writes happened in a dry run.
    assert txn_api.created_charges == []
    assert data["totals"]["late_fees_assessed"] == 50.0


@pytest.mark.asyncio
async def test_run_month_end_close_execute_posts_charges() -> None:
    leases = {
        "Active": [
            {"Id": 1, "PropertyId": 10, "UnitId": 100, "AccountDetails": {"Rent": 1000.0}},
        ]
    }
    txn_api = _LeaseTxnApi(charges={1: []}, ledgers={1: []})
    client = _ReportClient(leases_api=_LeasesApi(leases), lease_txn_api=txn_api)
    tool = await _get_tool(register_close_tools, client, "run_month_end_close")
    result = await tool.fn(
        property_id=10,
        as_of_date="2026-07-01",
        rent_gl_account_id=42,
        dry_run=False,
    )
    assert result["error"] is None
    # Rent was posted for the single active lease.
    assert len(txn_api.created_charges) == 1
    assert txn_api.created_charges[0]["lease_id"] == 1


@pytest.mark.asyncio
async def test_run_month_end_close_execute_requires_rent_gl_account() -> None:
    leases = {"Active": []}
    client = _ReportClient(leases_api=_LeasesApi(leases), lease_txn_api=_LeaseTxnApi())
    tool = await _get_tool(register_close_tools, client, "run_month_end_close")
    result = await tool.fn(property_id=10, dry_run=False)
    assert result["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_run_month_end_close_requires_a_property() -> None:
    client = _ReportClient(leases_api=_LeasesApi({"Active": []}), lease_txn_api=_LeaseTxnApi())
    tool = await _get_tool(register_close_tools, client, "run_month_end_close")
    result = await tool.fn(dry_run=True)
    assert result["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# Portfolio alerts
# ---------------------------------------------------------------------------
class _WorkOrdersApi:
    def __init__(self, by_status: dict[str, list[dict[str, Any]]]) -> None:
        self._by_status = by_status

    async def external_api_work_orders_get_all_work_orders(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        status = (kwargs.get("statuses") or ["New"])[0]
        rows = self._by_status.get(status, [])
        return rows if kwargs.get("offset", 0) == 0 else []


class _BankAccountsApi:
    def __init__(self, accounts: list[dict[str, Any]]) -> None:
        self._accounts = accounts

    async def external_api_bank_accounts_get_all_bank_accounts(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._accounts if kwargs.get("offset", 0) == 0 else []


class _AlertClient:
    def __init__(
        self, leases_api=None, lease_txn_api=None, work_orders_api=None, bank_accounts_api=None
    ) -> None:
        self.leases_api = leases_api
        self.lease_transactions_api = lease_txn_api
        self.work_orders_api = work_orders_api
        self.bank_accounts_api = bank_accounts_api


@pytest.mark.asyncio
async def test_portfolio_alerts_lease_expiration_excludes_renewed_units() -> None:
    leases = {
        "Active": [
            {"Id": 1, "PropertyId": 10, "UnitId": 100, "LeaseToDate": "2026-07-20"},
            {"Id": 2, "PropertyId": 10, "UnitId": 101, "LeaseToDate": "2026-07-20"},
        ],
        # Unit 101 already has a future (renewal) lease -> excluded.
        "Future": [{"Id": 3, "PropertyId": 10, "UnitId": 101, "LeaseFromDate": "2026-07-21"}],
    }
    client = _AlertClient(
        leases_api=_LeasesApi(leases),
        lease_txn_api=_LeaseTxnApi(balances=[]),
        work_orders_api=_WorkOrdersApi({}),
    )
    tool = await _get_tool(register_alert_tools, client, "portfolio_alerts")
    result = await tool.fn(
        property_id=10,
        as_of_date="2026-07-01",
        lease_expiry_days=60,
        include_late_rent=True,
        work_order_age_days=0,
    )
    data = result["data"]
    expiries = [a for a in data["alerts"] if a["rule"] == "lease_expiration"]
    assert len(expiries) == 1
    assert expiries[0]["details"]["lease_id"] == 1


@pytest.mark.asyncio
async def test_portfolio_alerts_late_rent_and_digest() -> None:
    balances = [{"LeaseId": 1, "PropertyId": 10, "UnitId": 100, "TotalBalance": 1500.0}]
    client = _AlertClient(
        leases_api=_LeasesApi({"Active": [], "Future": []}),
        lease_txn_api=_LeaseTxnApi(balances=balances),
        work_orders_api=_WorkOrdersApi({}),
    )
    tool = await _get_tool(register_alert_tools, client, "portfolio_alerts")
    result = await tool.fn(as_of_date="2026-07-01", lease_expiry_days=0, work_order_age_days=0)
    data = result["data"]
    late = [a for a in data["alerts"] if a["rule"] == "late_rent"]
    assert len(late) == 1
    assert late[0]["severity"] == "high"
    assert "1 alert" in data["digest"]


@pytest.mark.asyncio
async def test_portfolio_alerts_low_bank_balance_and_aging_work_orders() -> None:
    accounts = [
        {"Id": 1, "Name": "Operating", "Balance": 500.0, "IsActive": True},
        {"Id": 2, "Name": "Reserve", "Balance": 20000.0, "IsActive": True},
    ]
    work_orders = {"New": [{"Id": 7, "Status": "New", "CreatedDateTime": "2026-06-01"}]}
    client = _AlertClient(
        leases_api=_LeasesApi({"Active": [], "Future": []}),
        lease_txn_api=_LeaseTxnApi(balances=[]),
        work_orders_api=_WorkOrdersApi(work_orders),
        bank_accounts_api=_BankAccountsApi(accounts),
    )
    tool = await _get_tool(register_alert_tools, client, "portfolio_alerts")
    result = await tool.fn(
        as_of_date="2026-07-01",
        lease_expiry_days=0,
        include_late_rent=False,
        min_bank_reserve=1000.0,
        work_order_age_days=14,
    )
    data = result["data"]
    bank = [a for a in data["alerts"] if a["rule"] == "low_bank_balance"]
    wos = [a for a in data["alerts"] if a["rule"] == "aging_work_order"]
    assert len(bank) == 1 and bank[0]["details"]["bank_account_id"] == 1
    assert len(wos) == 1 and wos[0]["details"]["work_order_id"] == 7


@pytest.mark.asyncio
async def test_portfolio_alerts_all_clear_digest() -> None:
    client = _AlertClient(
        leases_api=_LeasesApi({"Active": [], "Future": []}),
        lease_txn_api=_LeaseTxnApi(balances=[]),
        work_orders_api=_WorkOrdersApi({}),
    )
    tool = await _get_tool(register_alert_tools, client, "portfolio_alerts")
    result = await tool.fn(as_of_date="2026-07-01")
    assert result["data"]["alert_count"] == 0
    assert "all clear" in result["data"]["digest"]
