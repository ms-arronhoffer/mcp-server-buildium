"""Tests for the owner-distribution "wow" capability.

These exercise the deterministic owner-payout math and the end-to-end
``owner_distributions`` tool against fake Buildium API objects (no network):
collected cash per property, netting out approved unpaid bills, holding back a
reserve, and reconciling that the parts sum to the whole.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from mcp_server_buildium.llm import artifacts
from mcp_server_buildium.tools import _money as m
from mcp_server_buildium.tools.distributions import register_distribution_tools


async def _get_tool(register: Any, client: Any, name: str) -> Any:
    mcp = FastMCP("test")
    register(mcp, client)
    tools = await mcp.get_tools()
    return tools[name]


class _LeasesApi:
    def __init__(self, by_property: dict[int, list[dict[str, Any]]]) -> None:
        self._by_property = by_property

    async def external_api_leases_get_leases(self, **kwargs: Any) -> list[dict[str, Any]]:
        prop = (kwargs.get("propertyids") or [None])[0]
        rows = self._by_property.get(prop, [])
        return rows if kwargs.get("offset", 0) == 0 else []


class _LeaseTxnApi:
    def __init__(
        self,
        charges: dict[int, list[dict[str, Any]]] | None = None,
        ledgers: dict[int, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._charges = charges or {}
        self._ledgers = ledgers or {}

    async def external_api_lease_ledger_charges_read_get_all_charges(
        self, lease_id: int, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._charges.get(lease_id, []) if kwargs.get("offset", 0) == 0 else []

    async def external_api_lease_ledger_transactions_get_lease_ledgers(
        self, lease_id: int, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return self._ledgers.get(lease_id, []) if kwargs.get("offset", 0) == 0 else []


class _BillsApi:
    def __init__(self, bills: list[dict[str, Any]]) -> None:
        self._bills = bills
        self.calls: list[dict[str, Any]] = []

    async def external_api_bills_get_bills_async(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        return self._bills if kwargs.get("offset", 0) == 0 else []


class _DistributionClient:
    def __init__(self, leases_api=None, lease_txn_api=None, bills_api=None) -> None:
        self.leases_api = leases_api
        self.lease_transactions_api = lease_txn_api
        self.bills_api = bills_api


# ---------------------------------------------------------------------------
# Pure math helper
# ---------------------------------------------------------------------------
def test_distributable_amount_flat_reserve_and_bills() -> None:
    distributable, reserve = m.distributable_amount(
        1000.0, unpaid_bills=200.0, reserve_amount=100.0
    )
    assert reserve == 100.0
    assert distributable == 700.0


def test_distributable_amount_percent_reserve() -> None:
    distributable, reserve = m.distributable_amount(1000.0, reserve_percent=10.0)
    assert reserve == 100.0
    assert distributable == 900.0


def test_distributable_amount_never_negative() -> None:
    distributable, reserve = m.distributable_amount(500.0, unpaid_bills=900.0)
    assert reserve == 0.0
    assert distributable == 0.0


# ---------------------------------------------------------------------------
# owner_distributions tool
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_owner_distributions_reconciles_and_nets_bills() -> None:
    leases = {10: [{"Id": 1, "PropertyId": 10}], 20: [{"Id": 2, "PropertyId": 20}]}
    # Lease 1 collected 1000 (a payment against a 1000 charge); lease 2 collected 500.
    charges = {1: [{"Id": 5, "Date": "2026-06-01", "Amount": 1000.0}], 2: []}
    ledgers = {
        1: [{"TransactionType": "Payment", "TotalAmount": 1000.0}],
        2: [{"TransactionType": "Payment", "TotalAmount": 500.0}],
    }
    # Property 10 has a 300 unpaid bill line; property 20 has none.
    bills = [
        {
            "Id": 99,
            "Lines": [
                {"Amount": 300.0, "AccountingEntity": {"Id": 10, "AccountingEntityType": "Rental"}}
            ],
        }
    ]
    client = _DistributionClient(
        leases_api=_LeasesApi(leases),
        lease_txn_api=_LeaseTxnApi(charges=charges, ledgers=ledgers),
        bills_api=_BillsApi(bills),
    )
    tool = await _get_tool(register_distribution_tools, client, "owner_distributions")
    result = await tool.fn(property_ids=[10, 20], reserve_percent=10.0)
    data = result["data"]
    assert data["reconciled"] is True
    by_prop = {r["property_id"]: r for r in data["rows"]}
    # Property 10: collected 1000, bills 300, reserve 100 -> 600.
    assert by_prop[10]["collected"] == 1000.0
    assert by_prop[10]["unpaid_bills"] == 300.0
    assert by_prop[10]["reserve_withheld"] == 100.0
    assert by_prop[10]["distributable"] == 600.0
    assert by_prop[10]["unpaid_bill_ids"] == [99]
    # Property 20: collected 500, no bills, reserve 50 -> 450.
    assert by_prop[20]["distributable"] == 450.0
    assert data["totals"]["distributable"] == 1050.0


@pytest.mark.asyncio
async def test_owner_distributions_can_skip_bills() -> None:
    leases = {10: [{"Id": 1, "PropertyId": 10}]}
    ledgers = {1: [{"TransactionType": "Payment", "TotalAmount": 800.0}]}
    bills_api = _BillsApi([{"Id": 1, "Lines": [{"Amount": 500.0, "AccountingEntity": {"Id": 10}}]}])
    client = _DistributionClient(
        leases_api=_LeasesApi(leases),
        lease_txn_api=_LeaseTxnApi(charges={1: []}, ledgers=ledgers),
        bills_api=bills_api,
    )
    tool = await _get_tool(register_distribution_tools, client, "owner_distributions")
    result = await tool.fn(property_id=10, include_unpaid_bills=False)
    data = result["data"]
    # Bills endpoint is never consulted, and nothing is netted out.
    assert bills_api.calls == []
    assert data["rows"][0]["unpaid_bills"] == 0.0
    assert data["rows"][0]["distributable"] == 800.0


@pytest.mark.asyncio
async def test_owner_distributions_export_creates_artifact() -> None:
    leases = {10: [{"Id": 1, "PropertyId": 10}]}
    ledgers = {1: [{"TransactionType": "Payment", "TotalAmount": 800.0}]}
    client = _DistributionClient(
        leases_api=_LeasesApi(leases),
        lease_txn_api=_LeaseTxnApi(charges={1: []}, ledgers=ledgers),
        bills_api=_BillsApi([]),
    )
    tool = await _get_tool(register_distribution_tools, client, "owner_distributions")
    token = artifacts.set_current_artifacts()
    try:
        result = await tool.fn(property_id=10, export_format="pdf")
        assert result["data"]["export"]["format"] == "pdf"
        assert len(artifacts.get_current_artifacts()) == 1
    finally:
        artifacts.current_artifacts.reset(token)


@pytest.mark.asyncio
async def test_owner_distributions_requires_a_property() -> None:
    client = _DistributionClient(leases_api=_LeasesApi({}), lease_txn_api=_LeaseTxnApi())
    tool = await _get_tool(register_distribution_tools, client, "owner_distributions")
    result = await tool.fn()
    assert result["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_owner_distributions_rejects_both_reserve_inputs() -> None:
    client = _DistributionClient(leases_api=_LeasesApi({}), lease_txn_api=_LeaseTxnApi())
    tool = await _get_tool(register_distribution_tools, client, "owner_distributions")
    result = await tool.fn(property_id=10, reserve_amount=100.0, reserve_percent=10.0)
    assert result["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_owner_distributions_rejects_bad_export_format() -> None:
    client = _DistributionClient(leases_api=_LeasesApi({}), lease_txn_api=_LeaseTxnApi())
    tool = await _get_tool(register_distribution_tools, client, "owner_distributions")
    result = await tool.fn(property_id=10, export_format="pptx")
    assert result["error"]["code"] == "validation_error"
