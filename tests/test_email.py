"""Tests for the AWS SES email dispatch tools (tools/email.py)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import FastMCP

from mcp_server_buildium.config import BuildiumConfig
from mcp_server_buildium.tools.email import register_email_tools

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> BuildiumConfig:
    """Build a minimal BuildiumConfig, overriding AWS fields as needed."""
    defaults = {
        "BUILDIUM_CLIENT_ID": "test-id",
        "BUILDIUM_CLIENT_SECRET": "test-secret",
    }
    defaults.update({f"BUILDIUM_{k.upper()}": v for k, v in overrides.items()})
    return BuildiumConfig(**{k.lower().removeprefix("buildium_"): v for k, v in defaults.items()})


def _make_client(config: BuildiumConfig, tenant_record: dict | None = None, owner_record: dict | None = None) -> Any:
    client = MagicMock()
    client.config = config
    if tenant_record is not None:
        client.rental_tenants_api.external_api_rental_tenants_get_tenant_by_id = AsyncMock(
            return_value=tenant_record
        )
    if owner_record is not None:
        client.rental_owners_api.external_api_rental_owners_get_rental_owner_by_id = AsyncMock(
            return_value=owner_record
        )
    return client


async def _get_tool(mcp: FastMCP, name: str) -> Any:
    if hasattr(mcp, "get_tools"):
        tools = await mcp.get_tools()
        return tools[name]
    return await mcp.get_tool(name)


def _ses_config() -> dict[str, str]:
    return {
        "client_id": "test-id",
        "client_secret": "test-secret",
        "aws_ses_sender": "noreply@example.com",
        "aws_region": "us-east-1",
    }


# ---------------------------------------------------------------------------
# Fixture: registered MCP with SES configured
# ---------------------------------------------------------------------------


@pytest.fixture()
def ses_mcp_and_client():
    config = BuildiumConfig(**_ses_config())
    client = _make_client(
        config,
        tenant_record={"Id": 1, "Email": "tenant@example.com", "FirstName": "Alice"},
        owner_record={"Id": 2, "Email": "owner@example.com", "FirstName": "Bob"},
    )
    mcp = FastMCP("test")
    register_email_tools(mcp, client)
    return mcp, client, config


# ---------------------------------------------------------------------------
# send_email — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_happy_path(ses_mcp_and_client):
    mcp, client, config = ses_mcp_and_client

    fake_ses = MagicMock()
    fake_ses.send_email.return_value = {"MessageId": "msg-abc-123"}

    with patch("mcp_server_buildium.tools.email._build_ses_client", return_value=fake_ses):
        tool = await _get_tool(mcp, "send_email")
        result = await tool.fn(
            to=["recipient@example.com"],
            subject="Hello",
            body="Plain text body",
        )

    assert result["error"] is None
    assert result["data"]["message_id"] == "msg-abc-123"
    assert result["data"]["to"] == ["recipient@example.com"]

    fake_ses.send_email.assert_called_once()
    call_kwargs = fake_ses.send_email.call_args[1]
    assert call_kwargs["Source"] == "noreply@example.com"
    assert call_kwargs["Destination"]["ToAddresses"] == ["recipient@example.com"]
    assert call_kwargs["Message"]["Subject"]["Data"] == "Hello"
    assert call_kwargs["Message"]["Body"]["Text"]["Data"] == "Plain text body"


@pytest.mark.asyncio
async def test_send_email_with_html_cc_bcc(ses_mcp_and_client):
    mcp, client, config = ses_mcp_and_client

    fake_ses = MagicMock()
    fake_ses.send_email.return_value = {"MessageId": "msg-xyz"}

    with patch("mcp_server_buildium.tools.email._build_ses_client", return_value=fake_ses):
        tool = await _get_tool(mcp, "send_email")
        result = await tool.fn(
            to=["a@example.com"],
            subject="Hi",
            body="text",
            html_body="<p>HTML</p>",
            cc=["cc@example.com"],
            bcc=["bcc@example.com"],
        )

    assert result["error"] is None
    call_kwargs = fake_ses.send_email.call_args[1]
    assert "Html" in call_kwargs["Message"]["Body"]
    assert call_kwargs["Destination"]["CcAddresses"] == ["cc@example.com"]
    assert call_kwargs["Destination"]["BccAddresses"] == ["bcc@example.com"]


# ---------------------------------------------------------------------------
# send_email — SES not configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_ses_not_configured():
    config = BuildiumConfig(client_id="x", client_secret="y")  # no aws_ses_sender
    client = _make_client(config)
    mcp = FastMCP("test")
    register_email_tools(mcp, client)

    tool = await _get_tool(mcp, "send_email")
    result = await tool.fn(to=["x@example.com"], subject="s", body="b")

    assert result["error"] is not None
    assert result["data"] is None
    assert "not configured" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# send_email — validation errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_empty_to(ses_mcp_and_client):
    mcp, *_ = ses_mcp_and_client
    tool = await _get_tool(mcp, "send_email")
    result = await tool.fn(to=[], subject="s", body="b")
    assert result["error"] is not None
    assert "recipient" in result["error"]["message"].lower()


@pytest.mark.asyncio
async def test_send_email_empty_subject(ses_mcp_and_client):
    mcp, *_ = ses_mcp_and_client
    tool = await _get_tool(mcp, "send_email")
    result = await tool.fn(to=["a@example.com"], subject="", body="b")
    assert result["error"] is not None
    assert "subject" in result["error"]["message"].lower()


@pytest.mark.asyncio
async def test_send_email_empty_body(ses_mcp_and_client):
    mcp, *_ = ses_mcp_and_client
    tool = await _get_tool(mcp, "send_email")
    result = await tool.fn(to=["a@example.com"], subject="s", body="")
    assert result["error"] is not None
    assert "body" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# send_email — SES raises (invalid recipient etc.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_ses_exception(ses_mcp_and_client):
    mcp, *_ = ses_mcp_and_client

    fake_ses = MagicMock()
    fake_ses.send_email.side_effect = Exception("MessageRejected: Email address not verified")

    with patch("mcp_server_buildium.tools.email._build_ses_client", return_value=fake_ses):
        tool = await _get_tool(mcp, "send_email")
        result = await tool.fn(to=["bad@example.com"], subject="s", body="b")

    assert result["error"] is not None
    assert result["data"] is None
    assert result["error"]["code"] == "internal_error"


# ---------------------------------------------------------------------------
# send_email_to_tenant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_to_tenant_happy_path(ses_mcp_and_client):
    mcp, client, config = ses_mcp_and_client

    fake_ses = MagicMock()
    fake_ses.send_email.return_value = {"MessageId": "msg-tenant-1"}

    with patch("mcp_server_buildium.tools.email._build_ses_client", return_value=fake_ses):
        tool = await _get_tool(mcp, "send_email_to_tenant")
        result = await tool.fn(tenant_id=1, subject="Rent reminder", body="Your rent is due.")

    assert result["error"] is None
    assert result["data"]["to"] == ["tenant@example.com"]
    assert result["data"]["tenant_id"] == 1
    assert result["data"]["message_id"] == "msg-tenant-1"

    call_kwargs = fake_ses.send_email.call_args[1]
    assert call_kwargs["Destination"]["ToAddresses"] == ["tenant@example.com"]


@pytest.mark.asyncio
async def test_send_email_to_tenant_no_email(ses_mcp_and_client):
    mcp, client, config = ses_mcp_and_client
    # Override tenant record to have no email
    client.rental_tenants_api.external_api_rental_tenants_get_tenant_by_id = AsyncMock(
        return_value={"Id": 1, "FirstName": "Alice"}
    )

    tool = await _get_tool(mcp, "send_email_to_tenant")
    result = await tool.fn(tenant_id=1, subject="s", body="b")

    assert result["error"] is not None
    assert "no email" in result["error"]["message"].lower()


@pytest.mark.asyncio
async def test_send_email_to_tenant_ses_not_configured():
    config = BuildiumConfig(client_id="x", client_secret="y")
    client = _make_client(config)
    mcp = FastMCP("test")
    register_email_tools(mcp, client)

    tool = await _get_tool(mcp, "send_email_to_tenant")
    result = await tool.fn(tenant_id=1, subject="s", body="b")

    assert result["error"] is not None
    assert "not configured" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# send_email_to_owner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_to_owner_happy_path(ses_mcp_and_client):
    mcp, client, config = ses_mcp_and_client

    fake_ses = MagicMock()
    fake_ses.send_email.return_value = {"MessageId": "msg-owner-2"}

    with patch("mcp_server_buildium.tools.email._build_ses_client", return_value=fake_ses):
        tool = await _get_tool(mcp, "send_email_to_owner")
        result = await tool.fn(owner_id=2, subject="Distribution notice", body="Your distribution is ready.")

    assert result["error"] is None
    assert result["data"]["to"] == ["owner@example.com"]
    assert result["data"]["owner_id"] == 2
    assert result["data"]["message_id"] == "msg-owner-2"


@pytest.mark.asyncio
async def test_send_email_to_owner_no_email(ses_mcp_and_client):
    mcp, client, config = ses_mcp_and_client
    client.rental_owners_api.external_api_rental_owners_get_rental_owner_by_id = AsyncMock(
        return_value={"Id": 2, "FirstName": "Bob"}
    )

    tool = await _get_tool(mcp, "send_email_to_owner")
    result = await tool.fn(owner_id=2, subject="s", body="b")

    assert result["error"] is not None
    assert "no email" in result["error"]["message"].lower()


@pytest.mark.asyncio
async def test_send_email_to_owner_ses_not_configured():
    config = BuildiumConfig(client_id="x", client_secret="y")
    client = _make_client(config)
    mcp = FastMCP("test")
    register_email_tools(mcp, client)

    tool = await _get_tool(mcp, "send_email_to_owner")
    result = await tool.fn(owner_id=2, subject="s", body="b")

    assert result["error"] is not None
    assert "not configured" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# ses_enabled config helper
# ---------------------------------------------------------------------------


def test_ses_enabled_when_sender_set():
    config = BuildiumConfig(client_id="x", client_secret="y", aws_ses_sender="a@b.com")
    assert config.ses_enabled() is True


def test_ses_enabled_false_when_sender_empty():
    config = BuildiumConfig(client_id="x", client_secret="y")
    assert config.ses_enabled() is False


def test_ses_enabled_false_when_sender_whitespace():
    config = BuildiumConfig(client_id="x", client_secret="y", aws_ses_sender="   ")
    assert config.ses_enabled() is False
