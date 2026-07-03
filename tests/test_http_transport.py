"""Smoke test for the HTTP (Streamable HTTP) transport and CORS wiring.

Boots the FastMCP ASGI app in-process with a Starlette ``TestClient``, performs
the MCP initialize handshake, and calls the built-in ``health_check`` tool. Also
verifies that the configured CORS origin is echoed on a preflight request.
"""

from __future__ import annotations

import json
import os

import pytest

# The server module reads configuration at import time, so credentials and CORS
# must be present in the environment before it is imported.
os.environ.setdefault("BUILDIUM_CLIENT_ID", "test-client-id")
os.environ.setdefault("BUILDIUM_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("BUILDIUM_CORS_ALLOW_ORIGINS", "chrome-extension://testext")

from mcp_server_buildium import server  # noqa: E402

_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    },
}
_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _parse_sse(text: str) -> dict:
    """Extract the JSON payload from an SSE ``data:`` line."""
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no SSE data line in response: {text!r}")


@pytest.fixture()
def client():
    from starlette.testclient import TestClient

    app = server.mcp.http_app(path="/mcp", middleware=server._build_cors_middleware())
    with TestClient(app) as test_client:
        yield test_client


def test_cors_preflight_allows_configured_origin(client) -> None:
    resp = client.options(
        "/mcp",
        headers={
            "Origin": "chrome-extension://testext",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "chrome-extension://testext"


def test_initialize_and_health_check_over_http(client) -> None:
    init = client.post("/mcp", json=_INIT, headers=_MCP_HEADERS)
    assert init.status_code == 200
    session_id = init.headers.get("mcp-session-id")
    assert session_id

    session_headers = {**_MCP_HEADERS, "Mcp-Session-Id": session_id}

    # Complete the handshake with the required 'notifications/initialized'.
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=session_headers,
    )

    call = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "health_check", "arguments": {}},
        },
        headers=session_headers,
    )
    assert call.status_code == 200
    payload = _parse_sse(call.text)
    structured = payload["result"]["structuredContent"]
    assert structured["data"]["status"] == "ok"
    assert structured["data"]["transport"] == "stdio"  # default in test env
    assert structured["data"]["auth_mode"] == "none"
