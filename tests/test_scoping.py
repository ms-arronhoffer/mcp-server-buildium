"""Tests for per-identity Entra App Role tool scoping middleware."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from mcp_server_buildium.config import BuildiumConfig
from mcp_server_buildium.security import scoping
from mcp_server_buildium.security.policy import ToolPolicy
from mcp_server_buildium.security.scoping import EntraScopingMiddleware


def _cfg(**kwargs):
    base = {
        "client_id": "id",
        "client_secret": "secret",
        "entra_tenant_id": "tenant",
        "entra_audience": "api://app",
    }
    base.update(kwargs)
    return BuildiumConfig(**base)


ROLE_MAP = '{"Buildium.ReadOnly":"readonly","Buildium.Admin":"admin"}'


@dataclass
class _Tool:
    name: str


@dataclass
class _Token:
    claims: dict


class _ListCtx:
    """Minimal MiddlewareContext stand-in for list_tools."""


@dataclass
class _CallMsg:
    name: str


@dataclass
class _CallCtx:
    message: _CallMsg


def _set_token(monkeypatch, token):
    monkeypatch.setattr(scoping, "get_access_token", lambda: token)


def test_inactive_without_role_map() -> None:
    mw = EntraScopingMiddleware(_cfg(), ToolPolicy(role="admin"))
    assert mw.active is False


def test_inactive_without_entra() -> None:
    cfg = BuildiumConfig(
        client_id="id", client_secret="secret", entra_role_policy_map=ROLE_MAP
    )
    mw = EntraScopingMiddleware(cfg, ToolPolicy(role="admin"))
    assert mw.active is False


def test_active_with_map_and_entra() -> None:
    mw = EntraScopingMiddleware(_cfg(entra_role_policy_map=ROLE_MAP), ToolPolicy(role="admin"))
    assert mw.active is True


def test_list_tools_filtered_to_role(monkeypatch) -> None:
    mw = EntraScopingMiddleware(_cfg(entra_role_policy_map=ROLE_MAP), ToolPolicy(role="admin"))
    _set_token(monkeypatch, _Token(claims={"roles": ["Buildium.ReadOnly"]}))

    tools = [_Tool("list_leases"), _Tool("create_lease"), _Tool("health_check")]

    async def call_next(_ctx):
        return tools

    result = asyncio.run(mw.on_list_tools(_ListCtx(), call_next))
    names = {t.name for t in result}
    assert names == {"list_leases", "health_check"}  # write tool filtered out


def test_list_tools_denies_all_unmapped(monkeypatch) -> None:
    mw = EntraScopingMiddleware(_cfg(entra_role_policy_map=ROLE_MAP), ToolPolicy(role="admin"))
    _set_token(monkeypatch, _Token(claims={"roles": ["Some.Other.Role"]}))

    async def call_next(_ctx):
        return [_Tool("list_leases"), _Tool("health_check")]

    result = asyncio.run(mw.on_list_tools(_ListCtx(), call_next))
    assert result == []


def test_list_tools_denies_all_without_token(monkeypatch) -> None:
    mw = EntraScopingMiddleware(_cfg(entra_role_policy_map=ROLE_MAP), ToolPolicy(role="admin"))
    _set_token(monkeypatch, None)

    async def call_next(_ctx):
        return [_Tool("list_leases")]

    assert asyncio.run(mw.on_list_tools(_ListCtx(), call_next)) == []


def test_call_tool_allows_permitted(monkeypatch) -> None:
    mw = EntraScopingMiddleware(_cfg(entra_role_policy_map=ROLE_MAP), ToolPolicy(role="admin"))
    _set_token(monkeypatch, _Token(claims={"roles": ["Buildium.ReadOnly"]}))

    async def call_next(_ctx):
        return "ok"

    assert asyncio.run(mw.on_call_tool(_CallCtx(_CallMsg("list_leases")), call_next)) == "ok"


def test_call_tool_denies_forbidden(monkeypatch) -> None:
    from fastmcp.exceptions import ToolError

    mw = EntraScopingMiddleware(_cfg(entra_role_policy_map=ROLE_MAP), ToolPolicy(role="admin"))
    _set_token(monkeypatch, _Token(claims={"roles": ["Buildium.ReadOnly"]}))

    async def call_next(_ctx):  # pragma: no cover - must not be reached
        return "ok"

    with pytest.raises(ToolError):
        asyncio.run(mw.on_call_tool(_CallCtx(_CallMsg("create_lease")), call_next))
