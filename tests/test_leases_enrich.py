"""Tests for lease read enrichment with a human-friendly property name.

A lease read model only carries ``PropertyId``/``UnitId``, so the assistant would
otherwise render a bare "property 4". The lease tools resolve the property name and
attach ``PropertyName`` so it can show e.g. "Riverside Commons, Unit 12".
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from mcp_server_buildium.tools._common import list_tools_map
from mcp_server_buildium.tools.leases import register_lease_tools


class _LeasesApi:
    async def external_api_leases_get_lease_by_id(self, lease_id: int) -> dict[str, Any]:
        return {"Id": lease_id, "PropertyId": 4, "UnitId": 12, "UnitNumber": "12"}

    async def external_api_leases_get_leases(self, **kwargs: Any) -> list[dict[str, Any]]:
        if kwargs.get("offset", 0):
            return []
        return [
            {"Id": 12, "PropertyId": 4, "UnitId": 12, "UnitNumber": "12"},
            {"Id": 13, "PropertyId": 4, "UnitId": 15, "UnitNumber": "15"},
            {"Id": 14, "PropertyId": 7, "UnitId": 1, "UnitNumber": "1"},
        ]


class _RentalsApi:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self._names = {4: "Riverside Commons", 7: "Maple Court"}

    async def external_api_rentals_get_rental_by_id(self, property_id: int) -> dict[str, Any]:
        self.calls.append(property_id)
        return {"Id": property_id, "Name": self._names.get(property_id)}


class _FailingRentalsApi:
    async def external_api_rentals_get_rental_by_id(self, property_id: int) -> dict[str, Any]:
        raise RuntimeError("boom")


class _Client:
    def __init__(self, rentals: Any) -> None:
        self.leases_api = _LeasesApi()
        self.rentals_api = rentals


async def _get_tool(client: Any, name: str) -> Any:
    mcp = FastMCP("test")
    register_lease_tools(mcp, client)
    tools = await list_tools_map(mcp)
    return tools[name]


@pytest.mark.asyncio
async def test_get_lease_attaches_property_name() -> None:
    client = _Client(_RentalsApi())
    tool = await _get_tool(client, "get_lease")
    result = await tool.fn(lease_id=12)
    assert result["error"] is None
    assert result["data"]["PropertyId"] == 4
    assert result["data"]["PropertyName"] == "Riverside Commons"
    assert result["data"]["UnitNumber"] == "12"


@pytest.mark.asyncio
async def test_list_leases_attaches_property_name_and_caches_lookups() -> None:
    rentals = _RentalsApi()
    client = _Client(rentals)
    tool = await _get_tool(client, "list_leases")
    result = await tool.fn(limit=100, offset=0)
    assert result["error"] is None
    names = [row.get("PropertyName") for row in result["data"]]
    assert names == ["Riverside Commons", "Riverside Commons", "Maple Court"]
    # Two distinct property ids -> resolved once each despite three leases.
    assert sorted(rentals.calls) == [4, 7]


@pytest.mark.asyncio
async def test_enrichment_failure_is_non_fatal() -> None:
    client = _Client(_FailingRentalsApi())
    tool = await _get_tool(client, "get_lease")
    result = await tool.fn(lease_id=12)
    assert result["error"] is None
    assert result["data"]["PropertyId"] == 4
    assert "PropertyName" not in result["data"]
