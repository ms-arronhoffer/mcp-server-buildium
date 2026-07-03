"""Tests for GuardedMCP registration-time and runtime enforcement + auditing."""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from mcp_server_buildium import audit
from mcp_server_buildium.security.policy import RateLimiter, ToolPolicy
from mcp_server_buildium.security.registration import GuardedMCP


def _build(policy: ToolPolicy, recorder=None, limiter=None):
    recorder = recorder or audit.AuditRecorder(audit.NullSink())
    mcp = GuardedMCP(FastMCP("test"), policy, recorder, limiter)

    @mcp.tool()
    async def list_things(limit: int = 10) -> dict:
        """A read tool."""
        return {"data": [1, 2], "count": 2, "error": None, "meta": None}

    @mcp.tool()
    async def create_thing(name: str) -> dict:
        """A write tool."""
        return {"data": {"name": name}, "count": None, "error": None, "meta": None}

    tools = asyncio.run(mcp.get_tools())
    return mcp, tools


def test_forbidden_tools_not_registered() -> None:
    mcp, tools = _build(ToolPolicy(role="readonly"))
    assert "list_things" in tools
    assert "create_thing" not in tools  # write skipped under readonly
    assert "create_thing" in mcp.skipped


def test_allowed_tool_preserves_schema_and_runs() -> None:
    _mcp, tools = _build(ToolPolicy(role="admin"))
    tool = tools["list_things"]
    # signature/schema preserved through the guard wrapper
    assert "limit" in tool.parameters["properties"]
    result = asyncio.run(tool.fn(limit=5))
    assert result["count"] == 2


def test_runtime_records_audit_events(tmp_path) -> None:
    path = str(tmp_path / "audit.log")
    recorder = audit.AuditRecorder(audit.FileSink(path), role="admin")
    _mcp, tools = _build(ToolPolicy(role="admin"), recorder=recorder)
    asyncio.run(tools["create_thing"].fn(name="x"))
    events = audit.read_events(path)
    assert any(e["tool"] == "create_thing" and e["outcome"] == "success" for e in events)


def test_rate_limit_returns_envelope_and_audits(tmp_path) -> None:
    path = str(tmp_path / "audit.log")
    recorder = audit.AuditRecorder(audit.FileSink(path), role="admin")
    limiter = RateLimiter(1)
    _mcp, tools = _build(ToolPolicy(role="admin"), recorder=recorder, limiter=limiter)
    first = asyncio.run(tools["list_things"].fn())
    assert first["error"] is None
    second = asyncio.run(tools["list_things"].fn())
    assert second["error"]["code"] == "rate_limited"
    events = audit.read_events(path)
    assert any(e["outcome"] == "rate_limited" for e in events)
