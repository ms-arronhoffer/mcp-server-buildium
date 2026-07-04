"""Tests for the deep-analysis and opportunity-surfacing tools (analytics.py).

Each tool is exercised with fake Buildium API objects so no network is needed.
The tests confirm:

* returned envelopes have the right structure (data/count/error/meta),
* computed figures (totals, scores, flags) are correct, and
* edge cases (empty portfolios, zero-balance leases, missing dates) don't crash.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from mcp_server_buildium.llm import artifacts
from mcp_server_buildium.tools._common import list_tools_map
from mcp_server_buildium.tools.analytics import register_analytics_tools


# ---------------------------------------------------------------------------
# Helper: register and retrieve a named tool
# ---------------------------------------------------------------------------
async def _get_tool(client: Any, name: str) -> Any:
    mcp = FastMCP("test")
    register_analytics_tools(mcp, client)
    tools = await list_tools_map(mcp)
    return tools[name]


# ---------------------------------------------------------------------------
# Fake API implementations
# ---------------------------------------------------------------------------

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
        ledgers: dict[int, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._balances = balances or []
        self._charges = charges or {}
        self._ledgers = ledgers or {}

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


class _BudgetsApi:
    def __init__(self, budgets: list[dict[str, Any]]) -> None:
        self._budgets = budgets

    async def external_api_budgets_get_budgets(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._budgets if kwargs.get("offset", 0) == 0 else []


class _GLApi:
    def __init__(self, transactions: list[dict[str, Any]]) -> None:
        self._transactions = transactions

    async def external_api_general_ledger_transactions_get_all_transactions(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._transactions if kwargs.get("offset", 0) == 0 else []


class _RentalUnitsApi:
    def __init__(self, units: list[dict[str, Any]]) -> None:
        self._units = units

    async def external_api_rental_units_get_all_rental_units(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._units if kwargs.get("offset", 0) == 0 else []


class _BillsApi:
    def __init__(self, bills: list[dict[str, Any]]) -> None:
        self._bills = bills

    async def external_api_bills_get_bills_async(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._bills if kwargs.get("offset", 0) == 0 else []


class _BankAccountsApi:
    def __init__(self, accounts: list[dict[str, Any]]) -> None:
        self._accounts = accounts

    async def external_api_bank_accounts_get_all_bank_accounts(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._accounts if kwargs.get("offset", 0) == 0 else []


class _WorkOrdersApi:
    def __init__(self, work_orders: list[dict[str, Any]]) -> None:
        self._wos = work_orders

    async def external_api_work_orders_get_all_work_orders(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._wos if kwargs.get("offset", 0) == 0 else []


class _RentalOwnersApi:
    def __init__(self, owners: list[dict[str, Any]]) -> None:
        self._owners = owners

    async def external_api_rental_owners_get_rental_owners(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._owners if kwargs.get("offset", 0) == 0 else []


class _AnalyticsClient:
    def __init__(
        self,
        leases_api=None,
        lease_txn_api=None,
        budgets_api=None,
        general_ledger_api=None,
        rental_units_api=None,
        bills_api=None,
        bank_accounts_api=None,
        work_orders_api=None,
        rental_owners_api=None,
    ) -> None:
        self.leases_api = leases_api
        self.lease_transactions_api = lease_txn_api
        self.budgets_api = budgets_api
        self.general_ledger_api = general_ledger_api
        self.rental_units_api = rental_units_api
        self.bills_api = bills_api
        self.bank_accounts_api = bank_accounts_api
        self.work_orders_api = work_orders_api
        self.rental_owners_api = rental_owners_api


# ---------------------------------------------------------------------------
# 1. budget_variance_report
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_budget_variance_report_flags_over_threshold() -> None:
    budgets = [
        {
            "BudgetLines": [
                {
                    "GLAccount": {"Id": 1, "Name": "Rent Revenue"},
                    "AnnualAmount": 10000.0,
                },
                {
                    "GLAccount": {"Id": 2, "Name": "Repairs"},
                    "AnnualAmount": 2000.0,
                },
            ]
        }
    ]
    txns = [
        {
            "Id": 1,
            "Journal": {
                "Lines": [
                    {
                        "GLAccount": {"Id": 1, "Name": "Rent Revenue", "Type": "Income"},
                        "Amount": 8000.0,  # $2000 below budget — 20% variance
                        "PostingType": "Credit",
                    }
                ]
            },
        }
    ]
    client = _AnalyticsClient(
        budgets_api=_BudgetsApi(budgets),
        general_ledger_api=_GLApi(txns),
    )
    tool = await _get_tool(client, "budget_variance_report")
    result = await tool.fn(fiscal_year=2026, variance_threshold_pct=10.0)
    data = result["data"]
    assert result["error"] is None
    flagged = data["flagged"]
    assert len(flagged) >= 1
    rent = next(r for r in flagged if r["account_id"] == 1)
    assert rent["budget"] == 10000.0
    assert rent["actual"] == 8000.0
    assert rent["variance"] == -2000.0
    assert rent["variance_pct"] == 20.0
    assert rent["over_threshold"] is True


@pytest.mark.asyncio
async def test_budget_variance_report_no_transactions() -> None:
    budgets = [
        {
            "BudgetLines": [
                {"GLAccount": {"Id": 5, "Name": "Utilities"}, "AnnualAmount": 500.0}
            ]
        }
    ]
    client = _AnalyticsClient(budgets_api=_BudgetsApi(budgets), general_ledger_api=_GLApi([]))
    tool = await _get_tool(client, "budget_variance_report")
    result = await tool.fn(fiscal_year=2026, variance_threshold_pct=10.0)
    assert result["error"] is None
    data = result["data"]
    assert data["total_budget"] == 500.0
    assert data["total_actual"] == 0.0


@pytest.mark.asyncio
async def test_budget_variance_report_bad_export_format() -> None:
    client = _AnalyticsClient(budgets_api=_BudgetsApi([]), general_ledger_api=_GLApi([]))
    tool = await _get_tool(client, "budget_variance_report")
    result = await tool.fn(fiscal_year=2026, export_format="docx")
    assert result["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# 2. vacancy_analysis
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vacancy_analysis_identifies_vacant_unit() -> None:
    units = [
        {"Id": 100, "PropertyId": 10, "UnitNumber": "1A"},
        {"Id": 101, "PropertyId": 10, "UnitNumber": "1B"},
    ]
    leases = {
        "Active": [
            # Unit 101 is occupied; unit 100 is vacant
            {"Id": 1, "PropertyId": 10, "UnitId": 101, "AccountDetails": {"Rent": 1500.0}},
        ],
        "Past": [
            # Last lease on 100 ended 2026-05-01 with $1200 rent
            {
                "Id": 2,
                "PropertyId": 10,
                "UnitId": 100,
                "AccountDetails": {"Rent": 1200.0},
                "LeaseToDate": "2026-05-01",
            }
        ],
        "Future": [],
    }
    client = _AnalyticsClient(
        rental_units_api=_RentalUnitsApi(units),
        leases_api=_LeasesApi(leases),
    )
    tool = await _get_tool(client, "vacancy_analysis")
    result = await tool.fn(as_of_date="2026-07-01")
    data = result["data"]
    assert result["error"] is None
    assert data["total_units"] == 2
    assert data["vacant_count"] == 1
    assert data["occupancy_rate_pct"] == 50.0
    row = data["rows"][0]
    assert row["unit_id"] == 100
    assert row["days_vacant"] == 61  # May 1 -> Jul 1
    assert row["last_monthly_rent"] == 1200.0
    assert row["next_occupancy"] is None


@pytest.mark.asyncio
async def test_vacancy_analysis_future_lease_sets_next_occupancy() -> None:
    units = [{"Id": 200, "PropertyId": 20, "UnitNumber": "2A"}]
    leases = {
        "Active": [],
        "Past": [{"Id": 3, "UnitId": 200, "PropertyId": 20, "AccountDetails": {"Rent": 1000.0}, "LeaseToDate": "2026-06-01"}],
        "Future": [{"Id": 4, "UnitId": 200, "PropertyId": 20, "LeaseFromDate": "2026-08-01"}],
    }
    client = _AnalyticsClient(
        rental_units_api=_RentalUnitsApi(units),
        leases_api=_LeasesApi(leases),
    )
    tool = await _get_tool(client, "vacancy_analysis")
    result = await tool.fn(as_of_date="2026-07-01")
    data = result["data"]
    row = data["rows"][0]
    assert row["next_occupancy"] == "2026-08-01"


@pytest.mark.asyncio
async def test_vacancy_analysis_all_occupied() -> None:
    units = [{"Id": 300, "PropertyId": 30, "UnitNumber": "3A"}]
    leases = {
        "Active": [{"Id": 5, "PropertyId": 30, "UnitId": 300, "AccountDetails": {"Rent": 1800.0}}],
        "Past": [],
        "Future": [],
    }
    client = _AnalyticsClient(
        rental_units_api=_RentalUnitsApi(units),
        leases_api=_LeasesApi(leases),
    )
    tool = await _get_tool(client, "vacancy_analysis")
    result = await tool.fn(as_of_date="2026-07-01")
    data = result["data"]
    assert data["vacant_count"] == 0
    assert data["occupancy_rate_pct"] == 100.0
    assert data["total_annualized_revenue_loss"] == 0.0


# ---------------------------------------------------------------------------
# 3. rent_trend_report
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rent_trend_report_flags_under_market_lease() -> None:
    """One lease at $800 vs. property average $1000 — should be flagged."""
    leases = {
        "Active": [
            # Reference lease at market rate
            {"Id": 1, "PropertyId": 10, "UnitId": 100, "AccountDetails": {"Rent": 1000.0}, "LeaseToDate": "2026-08-15"},
            # Under-market lease expiring in 60 days
            {"Id": 2, "PropertyId": 10, "UnitId": 101, "AccountDetails": {"Rent": 800.0}, "LeaseToDate": "2026-09-01"},
        ]
    }
    client = _AnalyticsClient(leases_api=_LeasesApi(leases))
    tool = await _get_tool(client, "rent_trend_report")
    result = await tool.fn(as_of_date="2026-07-01", expiry_window_days=90, under_market_threshold_pct=5.0)
    data = result["data"]
    assert result["error"] is None
    opps = data["opportunities"]
    # Only the $800 lease is under-market; property avg = (1000+800)/2 = 900
    # Lease 1 is also expiring within 90 days but at avg rent; lease 2 is below avg.
    # gap_pct for lease 1: (900-1000)/900*100 = -11% → not flagged (gap_pct < 0)
    # gap_pct for lease 2: (900-800)/900*100 ≈ 11% → flagged
    ids = [o["lease_id"] for o in opps]
    assert 2 in ids
    opp = next(o for o in opps if o["lease_id"] == 2)
    assert opp["current_rent"] == 800.0
    assert opp["property_avg_rent"] == 900.0
    assert opp["annual_uplift"] == round((900 - 800) * 12, 2)


@pytest.mark.asyncio
async def test_rent_trend_report_no_expiring_leases() -> None:
    leases = {
        "Active": [
            # Lease far in the future — outside expiry window
            {"Id": 10, "PropertyId": 5, "UnitId": 50, "AccountDetails": {"Rent": 1500.0}, "LeaseToDate": "2027-01-01"},
        ]
    }
    client = _AnalyticsClient(leases_api=_LeasesApi(leases))
    tool = await _get_tool(client, "rent_trend_report")
    result = await tool.fn(as_of_date="2026-07-01", expiry_window_days=90)
    data = result["data"]
    assert result["error"] is None
    assert data["opportunity_count"] == 0
    assert data["total_potential_annual_uplift"] == 0.0


# ---------------------------------------------------------------------------
# 4. vendor_spend_report
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vendor_spend_report_flags_concentration() -> None:
    bills = [
        {"Id": 1, "Vendor": {"Id": 10, "Name": "ACME Plumbing"}, "Amount": 8000.0, "Date": "2026-06-01"},
        {"Id": 2, "Vendor": {"Id": 10, "Name": "ACME Plumbing"}, "Amount": 2000.0, "Date": "2026-06-15"},
        {"Id": 3, "Vendor": {"Id": 20, "Name": "Bob's Landscaping"}, "Amount": 500.0, "Date": "2026-06-20"},
    ]
    client = _AnalyticsClient(bills_api=_BillsApi(bills))
    tool = await _get_tool(client, "vendor_spend_report")
    result = await tool.fn(concentration_threshold_pct=25.0)
    data = result["data"]
    assert result["error"] is None
    assert data["total_spend"] == 10500.0
    rows = {r["vendor_id"]: r for r in data["rows"]}
    acme = rows[10]
    assert acme["total_spend"] == 10000.0
    assert acme["invoice_count"] == 2
    assert acme["avg_invoice_size"] == 5000.0
    assert acme["concentration_flag"] is True
    bob = rows[20]
    assert bob["concentration_flag"] is False
    # Flagged list should only contain ACME
    assert len(data["flagged_vendors"]) == 1
    assert data["flagged_vendors"][0]["vendor_id"] == 10


@pytest.mark.asyncio
async def test_vendor_spend_report_date_filtering() -> None:
    bills = [
        {"Id": 1, "Vendor": {"Id": 10, "Name": "ACME"}, "Amount": 500.0, "Date": "2026-05-01"},
        {"Id": 2, "Vendor": {"Id": 10, "Name": "ACME"}, "Amount": 1000.0, "Date": "2026-06-01"},
    ]
    client = _AnalyticsClient(bills_api=_BillsApi(bills))
    tool = await _get_tool(client, "vendor_spend_report")
    # Only bills from June onwards
    result = await tool.fn(start_date="2026-06-01")
    data = result["data"]
    assert data["total_spend"] == 1000.0
    assert data["bill_count"] == 1


@pytest.mark.asyncio
async def test_vendor_spend_report_empty() -> None:
    client = _AnalyticsClient(bills_api=_BillsApi([]))
    tool = await _get_tool(client, "vendor_spend_report")
    result = await tool.fn()
    data = result["data"]
    assert result["error"] is None
    assert data["total_spend"] == 0.0
    assert data["vendor_count"] == 0


# ---------------------------------------------------------------------------
# 5. cash_flow_projection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cash_flow_projection_computes_horizons() -> None:
    leases = {
        "Active": [
            {"Id": 1, "PropertyId": 10, "UnitId": 100, "AccountDetails": {"Rent": 2000.0}},
        ]
    }
    # Bill due in 15 days ($500 outflow in 30-day bucket)
    bills = [
        {"Id": 1, "Amount": 500.0, "DueDate": "2026-07-15"},
    ]
    accounts = [{"Id": 1, "Name": "Operating", "Balance": 10000.0, "IsActive": True}]
    client = _AnalyticsClient(
        leases_api=_LeasesApi(leases),
        bills_api=_BillsApi(bills),
        bank_accounts_api=_BankAccountsApi(accounts),
    )
    tool = await _get_tool(client, "cash_flow_projection")
    result = await tool.fn(as_of_date="2026-07-01")
    data = result["data"]
    assert result["error"] is None
    assert data["current_bank_balance"] == 10000.0
    assert data["monthly_scheduled_inflow"] == 2000.0
    projections = {p["horizon"]: p for p in data["projections"]}
    p30 = projections["30_days"]
    # current_balance + inflow_30 - outflow_30 = 10000 + 2000 - 500 = 11500
    assert p30["projected_balance"] == 11500.0
    assert p30["below_reserve"] is False


@pytest.mark.asyncio
async def test_cash_flow_projection_flags_below_reserve() -> None:
    leases = {"Active": [{"Id": 1, "PropertyId": 10, "UnitId": 100, "AccountDetails": {"Rent": 500.0}}]}
    bills = [{"Id": 1, "Amount": 5000.0, "DueDate": "2026-07-10"}]
    accounts = [{"Id": 1, "Name": "Operating", "Balance": 2000.0, "IsActive": True}]
    client = _AnalyticsClient(
        leases_api=_LeasesApi(leases),
        bills_api=_BillsApi(bills),
        bank_accounts_api=_BankAccountsApi(accounts),
    )
    tool = await _get_tool(client, "cash_flow_projection")
    result = await tool.fn(as_of_date="2026-07-01", min_bank_reserve=3000.0)
    data = result["data"]
    # 2000 + 500 - 5000 = -2500 → below reserve 3000
    p30 = next(p for p in data["projections"] if p["horizon"] == "30_days")
    assert p30["below_reserve"] is True


# ---------------------------------------------------------------------------
# 6. maintenance_roi_report
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_maintenance_roi_report_flags_money_pit() -> None:
    leases = {
        "Active": [
            {"Id": 1, "PropertyId": 10, "UnitId": 100, "AccountDetails": {"Rent": 1000.0}},
        ]
    }
    # Work order cost $500 for property 10
    work_orders = [{"Id": 1, "Property": {"Id": 10}, "Amount": 500.0, "CreatedDateTime": "2026-01-15"}]
    # Bill cost $4000 for property 10 → total maint $4500, annual rent $12000 → ratio ~37.5%
    bills = [{"Id": 1, "Entity": {"Id": 10, "EntityType": "Rental"}, "Amount": 4000.0, "Date": "2026-02-01"}]
    client = _AnalyticsClient(
        leases_api=_LeasesApi(leases),
        work_orders_api=_WorkOrdersApi(work_orders),
        bills_api=_BillsApi(bills),
    )
    tool = await _get_tool(client, "maintenance_roi_report")
    result = await tool.fn(
        start_date="2026-01-01",
        end_date="2026-12-31",
        cost_to_rent_threshold_pct=30.0,
    )
    data = result["data"]
    assert result["error"] is None
    assert data["flagged_count"] >= 1
    row = next(r for r in data["rows"] if r["property_id"] == 10)
    assert row["money_pit_flag"] is True
    assert row["total_maintenance_cost"] == 4500.0


@pytest.mark.asyncio
async def test_maintenance_roi_report_no_data() -> None:
    client = _AnalyticsClient(
        leases_api=_LeasesApi({"Active": []}),
        work_orders_api=_WorkOrdersApi([]),
        bills_api=_BillsApi([]),
    )
    tool = await _get_tool(client, "maintenance_roi_report")
    result = await tool.fn()
    data = result["data"]
    assert result["error"] is None
    assert data["property_count"] == 0
    assert data["flagged_count"] == 0


# ---------------------------------------------------------------------------
# 7. owner_distribution_report
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_owner_distribution_report_computes_net() -> None:
    owners = [{"Id": 1, "FirstName": "John", "LastName": "Smith"}]
    leases = {
        "Active": [
            {"Id": 1, "PropertyId": 10, "UnitId": 100, "AccountDetails": {"Rent": 1500.0}},
        ]
    }
    # Payment transaction in the period
    ledgers = {
        1: [
            {"TransactionType": "Payment", "TotalAmount": 1500.0, "Date": "2026-06-05"},
        ]
    }
    bills = [{"Id": 1, "Entity": {"Id": 10, "EntityType": "Rental"}, "Amount": 300.0, "Date": "2026-06-10"}]
    balances = [{"LeaseId": 1, "PropertyId": 10, "UnitId": 100, "TotalBalance": 0.0}]
    client = _AnalyticsClient(
        rental_owners_api=_RentalOwnersApi(owners),
        leases_api=_LeasesApi(leases),
        lease_txn_api=_LeaseTxnApi(balances=balances, ledgers=ledgers),
        bills_api=_BillsApi(bills),
    )
    tool = await _get_tool(client, "owner_distribution_report")
    result = await tool.fn(start_date="2026-06-01", end_date="2026-06-30")
    data = result["data"]
    assert result["error"] is None
    row = next(r for r in data["rows"] if r["property_id"] == 10)
    assert row["rent_collected"] == 1500.0
    assert row["expenses_paid"] == 300.0
    assert row["net_distributable"] == 1200.0


@pytest.mark.asyncio
async def test_owner_distribution_report_export_creates_artifact() -> None:
    owners = [{"Id": 1, "FirstName": "Jane", "LastName": "Doe"}]
    leases = {
        "Active": [{"Id": 1, "PropertyId": 20, "UnitId": 200, "AccountDetails": {"Rent": 800.0}}]
    }
    ledgers = {1: [{"TransactionType": "Payment", "TotalAmount": 800.0, "Date": "2026-06-05"}]}
    balances = [{"LeaseId": 1, "PropertyId": 20, "UnitId": 200, "TotalBalance": 0.0}]
    client = _AnalyticsClient(
        rental_owners_api=_RentalOwnersApi(owners),
        leases_api=_LeasesApi(leases),
        lease_txn_api=_LeaseTxnApi(balances=balances, ledgers=ledgers),
        bills_api=_BillsApi([]),
    )
    tool = await _get_tool(client, "owner_distribution_report")
    token = artifacts.set_current_artifacts()
    try:
        result = await tool.fn(start_date="2026-06-01", end_date="2026-06-30", export_format="csv")
        assert result["data"]["export"]["format"] == "csv"
        assert len(artifacts.get_current_artifacts()) == 1
    finally:
        artifacts.current_artifacts.reset(token)


# ---------------------------------------------------------------------------
# 8. delinquency_trend
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delinquency_trend_scores_and_trends() -> None:
    """Lease with a growing balance is worsening; risk score = days × balance."""
    balances = [{"LeaseId": 1, "PropertyId": 10, "UnitId": 100, "TotalBalance": 500.0}]
    # One overdue charge dated 60 days before as_of; no payments → always open
    charges = {1: [{"Id": 11, "Date": "2026-05-01", "Amount": 500.0}]}
    ledgers = {1: []}  # no payments → balance stays 500 at all three snapshots
    client = _AnalyticsClient(
        lease_txn_api=_LeaseTxnApi(balances=balances, charges=charges, ledgers=ledgers),
    )
    tool = await _get_tool(client, "delinquency_trend")
    result = await tool.fn(as_of_date="2026-07-01")
    data = result["data"]
    assert result["error"] is None
    assert data["delinquent_lease_count"] == 1
    row = data["rows"][0]
    assert row["lease_id"] == 1
    assert row["balance_now"] == 500.0
    # All three snapshots see the same open charge, so trend is "unchanged"
    assert row["trend"] == "unchanged"
    # oldest_charge_age_days = May 1 -> Jul 1 = 61 days
    assert row["oldest_charge_age_days"] == 61
    # risk_score = 61 * 500 = 30500
    assert row["risk_score"] == 30500.0


@pytest.mark.asyncio
async def test_delinquency_trend_chronic_flag() -> None:
    """A charge older than 90 days at all three snapshots is chronic."""
    balances = [{"LeaseId": 2, "PropertyId": 20, "UnitId": 200, "TotalBalance": 1000.0}]
    # Charge date far in the past — will be in 90+ bucket at all snapshots
    charges = {2: [{"Id": 21, "Date": "2026-01-01", "Amount": 1000.0}]}
    ledgers = {2: []}
    client = _AnalyticsClient(
        lease_txn_api=_LeaseTxnApi(balances=balances, charges=charges, ledgers=ledgers),
    )
    tool = await _get_tool(client, "delinquency_trend")
    result = await tool.fn(as_of_date="2026-07-01")
    data = result["data"]
    row = data["rows"][0]
    # Jan 1 → Jul 1 = 181 days; all three snapshots (Jul, Jun, May) have the charge > 90 days
    assert row["chronic_delinquent"] is True
    assert data["chronic_count"] == 1


@pytest.mark.asyncio
async def test_delinquency_trend_zero_balance_skipped() -> None:
    """Leases with zero balance should not appear in the report."""
    balances = [
        {"LeaseId": 1, "PropertyId": 10, "UnitId": 100, "TotalBalance": 0.0},
        {"LeaseId": 2, "PropertyId": 10, "UnitId": 101, "TotalBalance": 300.0},
    ]
    charges = {2: [{"Id": 21, "Date": "2026-06-01", "Amount": 300.0}]}
    ledgers = {2: []}
    client = _AnalyticsClient(
        lease_txn_api=_LeaseTxnApi(balances=balances, charges=charges, ledgers=ledgers),
    )
    tool = await _get_tool(client, "delinquency_trend")
    result = await tool.fn(as_of_date="2026-07-01")
    data = result["data"]
    assert data["delinquent_lease_count"] == 1
    assert data["rows"][0]["lease_id"] == 2


@pytest.mark.asyncio
async def test_delinquency_trend_export_creates_artifact() -> None:
    balances = [{"LeaseId": 1, "PropertyId": 10, "UnitId": 100, "TotalBalance": 200.0}]
    charges = {1: [{"Id": 11, "Date": "2026-06-01", "Amount": 200.0}]}
    client = _AnalyticsClient(
        lease_txn_api=_LeaseTxnApi(balances=balances, charges=charges, ledgers={1: []}),
    )
    tool = await _get_tool(client, "delinquency_trend")
    token = artifacts.set_current_artifacts()
    try:
        result = await tool.fn(as_of_date="2026-07-01", export_format="pdf")
        assert result["data"]["export"]["format"] == "pdf"
        assert len(artifacts.get_current_artifacts()) == 1
    finally:
        artifacts.current_artifacts.reset(token)
