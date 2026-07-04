"""Integration tests for the /chat and /capabilities HTTP routes.

Boots the FastMCP ASGI app in-process (Starlette ``TestClient``) with the
server-side assistant enabled, stubs the provider so no network is used, and
verifies streaming events, model allow-list enforcement, auth, and that no key
material is ever exposed.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("BUILDIUM_CLIENT_ID", "test-client-id")
os.environ.setdefault("BUILDIUM_CLIENT_SECRET", "test-client-secret")
# Keep the CORS origin consistent with test_http_transport: the server module
# builds its configuration once at import time, and whichever test module imports
# it first wins, so both must agree on the configured origin.
os.environ.setdefault("BUILDIUM_CORS_ALLOW_ORIGINS", "chrome-extension://testext")
# Enable the assistant with a two-model allow-list. The key must never leak.
os.environ["BUILDIUM_LLM_PROVIDER"] = "openai"
os.environ["BUILDIUM_LLM_MODEL"] = "gpt-4o-mini"
os.environ["BUILDIUM_LLM_ALLOWED_MODELS"] = "gpt-4o-mini,gpt-4o"
os.environ["BUILDIUM_LLM_OPENAI_API_KEY"] = "sk-super-secret-value"

from mcp_server_buildium import (
    chat_endpoint,  # noqa: E402
    server,  # noqa: E402
)
from mcp_server_buildium.llm.base import Completion, ToolCall  # noqa: E402

# The server module captured this configuration at import time, so the process
# environment no longer needs the LLM variables. Remove them now to avoid
# leaking assistant configuration into other test modules that build their own
# BuildiumConfig from the environment.
for _leaked in (
    "BUILDIUM_LLM_PROVIDER",
    "BUILDIUM_LLM_MODEL",
    "BUILDIUM_LLM_ALLOWED_MODELS",
    "BUILDIUM_LLM_OPENAI_API_KEY",
):
    os.environ.pop(_leaked, None)


@pytest.fixture()
def client():
    from starlette.testclient import TestClient

    app = server.mcp.http_app(path="/mcp", middleware=server._build_cors_middleware())
    with TestClient(app) as test_client:
        yield test_client


def _sse_events(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:") :].strip()))
    return events


def test_capabilities_lists_models_without_keys(client) -> None:
    resp = client.get("/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["provider"] == "openai"
    assert data["default_model"] == "gpt-4o-mini"
    assert data["models"] == ["gpt-4o-mini", "gpt-4o"]
    # The API key must never appear anywhere in the response body.
    assert "sk-super-secret-value" not in resp.text


def test_chat_rejects_disallowed_model(client) -> None:
    resp = client.post(
        "/chat", json={"messages": [{"role": "user", "content": "hi"}], "model": "gpt-5"}
    )
    assert resp.status_code == 400


def test_chat_streams_direct_answer(client, monkeypatch) -> None:
    class StubProvider:
        def __init__(self, *a, **k) -> None:
            pass

        async def complete(self, messages, tools):
            return Completion(content="Hello from the server.")

    monkeypatch.setattr(chat_endpoint, "build_provider", lambda *a, **k: StubProvider())

    resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    assert {"type": "token", "text": "Hello from the server."} in events
    assert events[-1]["type"] == "done"


def test_chat_executes_inprocess_tool(client, monkeypatch) -> None:
    # First turn asks to call the built-in health_check tool; second turn answers.
    completions = [
        Completion(content="", tool_calls=[ToolCall("c1", "health_check", {})]),
        Completion(content="All good."),
    ]

    class StubProvider:
        def __init__(self, *a, **k) -> None:
            pass

        async def complete(self, messages, tools):
            return completions.pop(0)

    monkeypatch.setattr(chat_endpoint, "build_provider", lambda *a, **k: StubProvider())

    resp = client.post("/chat", json={"messages": [{"role": "user", "content": "status?"}]})
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    # Internal tool-call/result events must never be surfaced to the chat UI.
    assert not [e for e in events if e["type"] in ("tool_call", "tool_result")]
    # The tool still executed in-process (the model answered on the second turn).
    assert events[-1] == {"type": "done", "content": "All good."}


def test_chat_auth_required_when_verifier_configured() -> None:
    """The auth helper enforces bearer tokens unless bypassed/unconfigured."""
    import asyncio

    from starlette.requests import Request

    from mcp_server_buildium.chat_endpoint import _authorized

    class Cfg:
        dev_auth_bypass = False

    class Verifier:
        async def verify_token(self, token):
            return {"sub": "u"} if token == "good" else None

    def make_request(headers: dict[str, str]) -> Request:
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        return Request({"type": "http", "headers": raw})

    async def run():
        cfg = Cfg()
        verifier = Verifier()
        scheme = "Bearer "
        # No Authorization header -> rejected.
        assert await _authorized(make_request({}), cfg, verifier) is False
        # Wrong token -> rejected.
        assert (
            await _authorized(make_request({"Authorization": scheme + "bad"}), cfg, verifier)
            is False
        )
        # Valid token -> allowed.
        assert (
            await _authorized(make_request({"Authorization": scheme + "good"}), cfg, verifier)
            is True
        )
        # No verifier configured -> open (e.g. stdio/dev).
        assert await _authorized(make_request({}), cfg, None) is True
        # Dev bypass overrides everything.
        cfg.dev_auth_bypass = True
        assert await _authorized(make_request({}), cfg, verifier) is True

    asyncio.run(run())


def test_authenticate_returns_verified_claims() -> None:
    """_authenticate surfaces JWT claims (e.g. Entra App Roles) when valid."""
    import asyncio

    from starlette.requests import Request

    from mcp_server_buildium.chat_endpoint import _authenticate

    class Cfg:
        dev_auth_bypass = False

    class TokenObj:
        claims = {"roles": ["Buildium.ReadOnly"], "sub": "u"}

    class Verifier:
        async def verify_token(self, token):
            return TokenObj() if token == "good" else None

    def make_request(headers: dict[str, str]) -> Request:
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        return Request({"type": "http", "headers": raw})

    async def run():
        cfg = Cfg()
        verifier = Verifier()
        scheme = "Bearer "
        ok, claims = await _authenticate(
            make_request({"Authorization": scheme + "good"}), cfg, verifier
        )
        assert ok is True
        assert claims == {"roles": ["Buildium.ReadOnly"], "sub": "u"}
        # Rejected token -> not authorized, empty claims.
        ok, claims = await _authenticate(
            make_request({"Authorization": scheme + "bad"}), cfg, verifier
        )
        assert ok is False
        assert claims == {}
        # Dev bypass / no verifier -> authorized with empty claims.
        cfg.dev_auth_bypass = True
        ok, claims = await _authenticate(make_request({}), cfg, verifier)
        assert ok is True and claims == {}

    asyncio.run(run())


def test_current_datetime_note_mentions_now_and_utc() -> None:
    from datetime import UTC, datetime

    note = chat_endpoint._current_datetime_note(datetime(2026, 7, 3, 19, 22, 29, tzinfo=UTC))
    assert "2026-07-03T19:22:29Z" in note
    assert "UTC" in note
    assert "now" in note.lower()


def test_chat_system_prompt_includes_current_datetime(client, monkeypatch) -> None:
    """The assistant's system prompt is augmented with the current date/time."""
    captured: dict = {}

    class StubProvider:
        def __init__(self, *a, **k) -> None:
            pass

        async def complete(self, messages, tools):
            captured["messages"] = messages
            return Completion(content="Done.")

    monkeypatch.setattr(chat_endpoint, "build_provider", lambda *a, **k: StubProvider())

    resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    system = captured["messages"][0]
    assert system["role"] == "system"
    assert "current date and time is" in system["content"].lower()


def test_chat_rejects_oversize_attachment(client) -> None:
    """An attachment exceeding the size cap is rejected with a 400."""
    import base64

    big = base64.b64encode(b"x" * (11 * 1024 * 1024)).decode("ascii")
    resp = client.post(
        "/chat",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "save this",
                    "attachments": [{"name": "big.txt", "media_type": "text/plain", "data": big}],
                }
            ]
        },
    )
    assert resp.status_code == 400
    assert "too large" in resp.json()["error"].lower()


def test_chat_rejects_unsupported_attachment_type(client) -> None:
    import base64

    data = base64.b64encode(b"nope").decode("ascii")
    resp = client.post(
        "/chat",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "hi",
                    "attachments": [
                        {"name": "a.exe", "media_type": "application/octet-stream", "data": data}
                    ],
                }
            ]
        },
    )
    assert resp.status_code == 400


def test_chat_threads_attachments_to_provider(client, monkeypatch) -> None:
    """A valid attachment is decoded and passed to the provider on the user turn."""
    import base64

    captured: dict = {}

    class StubProvider:
        def __init__(self, *a, **k) -> None:
            pass

        async def complete(self, messages, tools):
            captured["messages"] = messages
            return Completion(content="Read it.")

    monkeypatch.setattr(chat_endpoint, "build_provider", lambda *a, **k: StubProvider())

    data = base64.b64encode(b"lease terms").decode("ascii")
    resp = client.post(
        "/chat",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "extract",
                    "attachments": [
                        {"name": "lease.txt", "media_type": "text/plain", "data": data}
                    ],
                }
            ]
        },
    )
    assert resp.status_code == 200
    user_msg = next(m for m in captured["messages"] if m["role"] == "user")
    assert user_msg["attachments"][0].name == "lease.txt"
    assert user_msg["attachments"][0].data == b"lease terms"


def test_chat_streams_artifact_for_generated_file(client, monkeypatch) -> None:
    """A create_download_file tool call surfaces an `artifact` SSE event."""
    completions = [
        Completion(
            content="",
            tool_calls=[
                ToolCall(
                    "c1",
                    "create_download_file",
                    {
                        "file_format": "csv",
                        "filename": "active-leases",
                        "columns": ["Lease", "Rent"],
                        "rows": [[1, 1225]],
                    },
                )
            ],
        ),
        Completion(content="Your spreadsheet is ready to download."),
    ]

    class StubProvider:
        def __init__(self, *a, **k) -> None:
            pass

        async def complete(self, messages, tools):
            return completions.pop(0)

    monkeypatch.setattr(chat_endpoint, "build_provider", lambda *a, **k: StubProvider())

    resp = client.post(
        "/chat", json={"messages": [{"role": "user", "content": "export my leases"}]}
    )
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    artifacts = [e for e in events if e["type"] == "artifact"]
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art["name"] == "active-leases.csv"
    assert art["media_type"] == "text/csv"
    assert art["size"] > 0
    assert art["data"]  # base64 payload present
    # Internal tool events are still hidden from the UI.
    assert not [e for e in events if e["type"] in ("tool_call", "tool_result")]


def test_chat_artifacts_do_not_leak_between_requests(client, monkeypatch) -> None:
    """A turn that generates no file must not emit stale artifact events."""

    class StubProvider:
        def __init__(self, *a, **k) -> None:
            pass

        async def complete(self, messages, tools):
            return Completion(content="No file this time.")

    monkeypatch.setattr(chat_endpoint, "build_provider", lambda *a, **k: StubProvider())

    resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    assert not [e for e in events if e["type"] == "artifact"]
