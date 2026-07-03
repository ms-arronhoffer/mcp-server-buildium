"""Tests for the newly added tool families and cross-cutting output helpers.

Covers the Part 1-5 expansion: the ``paginate_all`` auto-pagination helper, the
``get_reference_data`` reference-vocabulary tool, the ``lease_receivables_summary``
aggregation report, representative fetch-then-merge update tools for the new
ledger/communication/task-history modules, and the sensitivity classification of
financial write tools.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from mcp_server_buildium.tools import _common as c
from mcp_server_buildium.tools.budgets import register_budget_tools
from mcp_server_buildium.tools.communications import register_communication_tools
from mcp_server_buildium.tools.leases import register_lease_tools
from mcp_server_buildium.tools.ownership_accounts import register_ownership_account_tools
from mcp_server_buildium.tools.reference import REFERENCE_DATA, register_reference_tools
from mcp_server_buildium.tools.tasks import register_task_tools


class _FakeModel:
    """Minimal stand-in for an SDK model exposing ``to_dict``."""

    def __init__(self, doc: dict[str, Any]) -> None:
        self._doc = doc

    def to_dict(self) -> dict[str, Any]:
        return self._doc


async def _get_tool(register: Any, client: Any, name: str) -> Any:
    mcp = FastMCP("test")
    register(mcp, client)
    tools = await mcp.get_tools()
    return tools[name]


# ---------------------------------------------------------------------------
# paginate_all
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_paginate_all_walks_pages_until_short_page() -> None:
    pages = [[1, 2], [3, 4], [5]]  # last page short -> stop

    async def fetch(limit: int, offset: int) -> list[int]:
        index = offset // 2
        return pages[index] if index < len(pages) else []

    out = await c.paginate_all(fetch, page_size=2)
    assert out == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_paginate_all_stops_at_max_records() -> None:
    async def fetch(limit: int, offset: int) -> list[int]:
        # Always returns a full page -> would loop forever without the ceiling.
        return list(range(offset, offset + limit))

    out = await c.paginate_all(fetch, page_size=10, max_records=25)
    assert len(out) == 25
    assert out[0] == 0
    assert out[-1] == 24


@pytest.mark.asyncio
async def test_paginate_all_serializes_models() -> None:
    async def fetch(limit: int, offset: int) -> Any:
        if offset == 0:
            return [_FakeModel({"Id": 1}), _FakeModel({"Id": 2})]
        return []

    out = await c.paginate_all(fetch, page_size=2)
    assert out == [{"Id": 1}, {"Id": 2}]


# ---------------------------------------------------------------------------
# get_reference_data
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_reference_data_returns_all_topics() -> None:
    tool = await _get_tool(register_reference_tools, object(), "get_reference_data")
    result = await tool.fn()
    assert result["error"] is None
    assert set(result["data"]) == set(REFERENCE_DATA)


@pytest.mark.asyncio
async def test_get_reference_data_single_topic_case_insensitive() -> None:
    tool = await _get_tool(register_reference_tools, object(), "get_reference_data")
    result = await tool.fn(topic="LEASE_STATUSES")
    assert result["error"] is None
    assert result["data"]["values"] == ["Active", "Past", "Future"]


@pytest.mark.asyncio
async def test_get_reference_data_unknown_topic_is_validation_error() -> None:
    tool = await _get_tool(register_reference_tools, object(), "get_reference_data")
    result = await tool.fn(topic="nope")
    assert result["data"] is None
    assert result["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_reference_resources_registered() -> None:
    mcp = FastMCP("test")
    register_reference_tools(mcp, object())
    resources = await mcp.get_resources()
    assert any("reference/lease-statuses" in uri for uri in resources)


# ---------------------------------------------------------------------------
# lease_receivables_summary (aggregation over paginated outstanding balances)
# ---------------------------------------------------------------------------
class _OutstandingApi:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls: list[dict[str, Any]] = []

    async def external_api_lease_outstanding_balances_get_lease_outstanding_balances(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        return self._rows


class _LeaseClient:
    def __init__(self, api: _OutstandingApi) -> None:
        self.lease_transactions_api = api


@pytest.mark.asyncio
async def test_lease_receivables_summary_aggregates_and_ranks() -> None:
    rows = [
        {"LeaseId": 1, "PropertyId": 10, "UnitId": 100, "TotalBalance": 50.0},
        {"LeaseId": 2, "PropertyId": 11, "UnitId": 101, "TotalBalance": 200.0},
        {"LeaseId": 3, "PropertyId": 12, "UnitId": 102, "TotalBalance": 25.5},
    ]
    api = _OutstandingApi(rows)
    tool = await _get_tool(register_lease_tools, _LeaseClient(api), "lease_receivables_summary")

    result = await tool.fn(lease_status="Active", top_n=2)

    assert result["error"] is None
    data = result["data"]
    assert data["lease_status"] == "Active"
    assert data["lease_count"] == 3
    assert data["total_outstanding"] == 275.5
    # Ranked by descending balance, limited to top_n.
    assert [b["LeaseId"] for b in data["top_balances"]] == [2, 1]
    assert api.calls and api.calls[0]["leasestatuses"] == ["Active"]


@pytest.mark.asyncio
async def test_lease_receivables_summary_rejects_bad_status() -> None:
    api = _OutstandingApi([])
    tool = await _get_tool(register_lease_tools, _LeaseClient(api), "lease_receivables_summary")
    result = await tool.fn(lease_status="Bogus")
    assert result["data"] is None
    assert result["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# Representative fetch-then-merge update tools for the new modules
# ---------------------------------------------------------------------------
class _FakeApi:
    """Canned GET + capturing PUT keyed by SDK method name."""

    def __init__(self, current: dict[str, Any], get_method: str, update_method: str) -> None:
        self._current = current
        self._get_method = get_method
        self._update_method = update_method
        self.received: Any = None

    def __getattr__(self, name: str) -> Any:
        if name == self._get_method:

            async def _get(**_kwargs: Any) -> _FakeModel:
                return _FakeModel(self._current)

            return _get
        if name == self._update_method:

            async def _update(**kwargs: Any) -> Any:
                for key, value in kwargs.items():
                    if key.endswith("_message"):
                        self.received = value
                return kwargs

            return _update
        raise AttributeError(name)


class _FakeClient:
    def __init__(self, **apis: Any) -> None:
        for attr, api in apis.items():
            setattr(self, attr, api)


@pytest.mark.asyncio
async def test_update_task_history_partial_merges_current() -> None:
    api = _FakeApi(
        {"Id": 7, "Message": "old note"},
        "external_api_task_history_get_task_history_by_id",
        "external_api_task_history_update_task_history",
    )
    client = _FakeClient(tasks_api=api)
    tool = await _get_tool(register_task_tools, client, "update_task_history")

    result = await tool.fn(task_id=1, task_history_id=7, history_data={"message": "new note"})

    assert result["error"] is None
    sent = api.received.to_dict()
    assert sent["Message"] == "new note"


@pytest.mark.asyncio
async def test_update_phone_log_partial_preserves_required() -> None:
    api = _FakeApi(
        {
            "Id": 3,
            "Subject": "Call about lease",
            "Description": "Tenant called",
            "CallDateTime": "2026-01-01T10:00:00Z",
        },
        "external_api_phone_logs_get_phone_log_by_id",
        "external_api_phone_logs_update_phone_log",
    )
    client = _FakeClient(communications_api=api)
    tool = await _get_tool(register_communication_tools, client, "update_phone_log")

    result = await tool.fn(phone_log_id=3, phone_log_data={"subject": "Updated subject"})

    assert result["error"] is None
    sent = api.received.to_dict()
    assert sent["Subject"] == "Updated subject"
    assert sent["Description"] == "Tenant called"
    # The SDK parses CallDateTime into a datetime; the untouched required field
    # is still carried through the merge.
    assert sent["CallDateTime"] is not None


# ---------------------------------------------------------------------------
# Sensitivity classification of financial writes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool_name",
    [
        "create_lease_charge",
        "update_lease_charge",
        "create_lease_payment",
        "create_lease_credit",
        "create_lease_refund",
        "create_ownership_account_charge",
        "create_budget",
    ],
)
def test_financial_writes_classified_sensitive(tool_name: str) -> None:
    assert c.classify_sensitive(tool_name)


@pytest.mark.parametrize("tool_name", ["list_leases", "get_lease", "get_reference_data"])
def test_reads_not_classified_sensitive(tool_name: str) -> None:
    assert not c.classify_sensitive(tool_name)


# ---------------------------------------------------------------------------
# Registration wiring: new categories expose their tools
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_new_category_registrars_expose_expected_tools() -> None:
    checks = [
        (register_ownership_account_tools, "create_ownership_account_charge"),
        (register_communication_tools, "create_announcement"),
        (register_budget_tools, "update_budget"),
    ]
    for register, expected in checks:
        mcp = FastMCP("test")
        register(mcp, _FakeClient())
        tools = await mcp.get_tools()
        assert expected in tools
