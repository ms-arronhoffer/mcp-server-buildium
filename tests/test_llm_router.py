"""Tests for the model router: classifier, ModelRouter, build_router, and /chat integration.

All tests are pure (no network). Provider adapters are replaced with simple
fakes. The /chat integration tests boot the FastMCP ASGI app in-process using
a router configuration with two mock providers.
"""

from __future__ import annotations

import json
import os

import pytest

from mcp_server_buildium.llm.base import Completion, LLMProvider
from mcp_server_buildium.llm.router import (
    _TASK_AGENTIC,
    _TASK_CREATIVE,
    _TASK_EXTRACTION,
    _TASK_REASONING,
    ModelRouter,
    RouterEntry,
    _sort_by_task,
    classify_task,
)

# -- Helpers -----------------------------------------------------------------


def _user(text: str, attachments=None) -> dict:
    m: dict = {"role": "user", "content": text}
    if attachments:
        m["attachments"] = attachments
    return m


def _fake_att():
    """Return a non-empty attachments list to trigger extraction."""
    return [{"name": "doc.pdf", "media_type": "application/pdf"}]


class FakeProvider(LLMProvider):
    """Replays scripted completions; tracks call count."""

    name = "fake"

    def __init__(self, completions: list[Completion], *, fail: bool = False) -> None:
        super().__init__(api_key="k", model="m", base_url="http://x")
        self._completions = list(completions)
        self.calls = 0
        self._fail = fail

    async def complete(self, messages, tools) -> Completion:
        self.calls += 1
        if self._fail:
            raise RuntimeError("provider down")
        return self._completions.pop(0)


# -- Classifier --------------------------------------------------------------


def test_classify_financial_is_reasoning():
    task, reason = classify_task([_user("Show me the general ledger for March")])
    assert task == _TASK_REASONING
    assert "financial" in reason.lower()


def test_classify_reconciliation_is_reasoning():
    task, _ = classify_task([_user("Reconcile the bank statement for Q2")])
    assert task == _TASK_REASONING


def test_classify_analysis_is_reasoning():
    task, _ = classify_task([_user("Analyze the portfolio performance and evaluate risks")])
    assert task == _TASK_REASONING


def test_classify_extraction_from_attachment():
    task, reason = classify_task([_user("read this", attachments=_fake_att())])
    assert task == _TASK_EXTRACTION
    assert "attachment" in reason.lower()


def test_classify_extraction_from_keywords():
    task, reason = classify_task([_user("Extract fields from this lease document")])
    assert task == _TASK_EXTRACTION
    assert "extraction" in reason.lower()


def test_classify_agentic_all_records():
    task, _ = classify_task([_user("List all active leases across the portfolio")])
    assert task == _TASK_AGENTIC


def test_classify_agentic_bulk():
    task, _ = classify_task([_user("Bulk update all unit rents for building A")])
    assert task == _TASK_AGENTIC


def test_classify_creative_short():
    task, reason = classify_task([_user("Hi")])
    assert task == _TASK_CREATIVE
    assert "conversational" in reason.lower()


def test_classify_creative_draft():
    task, _ = classify_task([_user("Draft a lease renewal email for tenant Smith")])
    assert task == _TASK_CREATIVE


def test_classify_creative_summarize():
    task, _ = classify_task([_user("Summarize this month's rent collection")])
    assert task == _TASK_CREATIVE


def test_classify_long_no_signal_defaults_to_agentic():
    # A long prompt with no strong keywords defaults to agentic.
    long_text = "please look into the unit and check everything carefully " * 5
    task, _ = classify_task([_user(long_text)])
    assert task == _TASK_AGENTIC


def test_classify_uses_last_user_message():
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
        {"role": "user", "content": "Reconcile the bank statement for Q1"},
    ]
    task, _ = classify_task(messages)
    assert task == _TASK_REASONING


def test_classify_extraction_beats_reasoning():
    # Attachments override keyword matching.
    task, _ = classify_task([_user("analyze this document", attachments=_fake_att())])
    assert task == _TASK_EXTRACTION


def test_classify_empty_messages():
    task, reason = classify_task([])
    # No user message → short/empty → creative
    assert task == _TASK_CREATIVE


# -- Sort by task ------------------------------------------------------------


class _StubProvider(LLMProvider):
    """Minimal no-op provider for sort tests (never called)."""

    def __init__(self) -> None:
        super().__init__(api_key="", model="stub", base_url="")

    async def complete(self, messages, tools):  # pragma: no cover
        raise NotImplementedError


def _entries(*names: str) -> list[RouterEntry]:
    return [RouterEntry(provider_name=n, model=f"{n}-model", provider=_StubProvider()) for n in names]


def test_sort_reasoning_prefers_anthropic_first():
    entries = _entries("openai", "anthropic", "gemini")
    sorted_e = _sort_by_task(entries, _TASK_REASONING)
    assert sorted_e[0].provider_name == "anthropic"
    assert sorted_e[1].provider_name == "openai"


def test_sort_extraction_prefers_openai_first():
    entries = _entries("anthropic", "openai")
    sorted_e = _sort_by_task(entries, _TASK_EXTRACTION)
    assert sorted_e[0].provider_name == "openai"


def test_sort_preserves_relative_order_same_provider():
    # Two OpenAI entries: config order (0 before 1) should be preserved.
    e1 = RouterEntry("openai", "gpt-4o", provider=None)  # type: ignore[arg-type]
    e2 = RouterEntry("openai", "gpt-4o-mini", provider=None)  # type: ignore[arg-type]
    sorted_e = _sort_by_task([e1, e2], _TASK_CREATIVE)
    assert sorted_e[0].model == "gpt-4o"


def test_sort_unknown_task_keeps_original_order():
    entries = _entries("openai", "anthropic")
    sorted_e = _sort_by_task(entries, "unknown_type")
    assert [e.provider_name for e in sorted_e] == ["openai", "anthropic"]


# -- ModelRouter.complete (unit) ---------------------------------------------


async def _collect_events(gen):
    return [e async for e in gen]


@pytest.mark.asyncio
async def test_router_selects_provider_and_annotates_routing_info():
    answer = Completion(content="done")
    entry = RouterEntry("anthropic", "claude-x", FakeProvider([answer]))
    router = ModelRouter([entry], strategy="fallback")
    completion = await router.complete([_user("hi")], [])
    assert completion.content == "done"
    assert completion.routing_info is not None
    assert completion.routing_info["provider"] == "anthropic"
    assert completion.routing_info["model"] == "claude-x"
    assert completion.routing_info["reason"]


@pytest.mark.asyncio
async def test_router_sticks_to_first_successful_provider():
    p1 = FakeProvider([Completion(content="r1"), Completion(content="r2")])
    p2 = FakeProvider([Completion(content="should-not-be-called")])
    e1 = RouterEntry("openai", "gpt-4o", p1)
    e2 = RouterEntry("anthropic", "claude", p2)
    router = ModelRouter([e1, e2], strategy="fallback")

    c1 = await router.complete([_user("round 1")], [])
    assert c1.content == "r1"
    assert c1.routing_info is not None  # first call → routing info present

    c2 = await router.complete([_user("round 2")], [])
    assert c2.content == "r2"
    assert c2.routing_info is None  # subsequent call → no routing info
    assert p2.calls == 0  # p2 never used


@pytest.mark.asyncio
async def test_router_falls_back_on_first_provider_failure():
    fail = FakeProvider([], fail=True)
    ok = FakeProvider([Completion(content="fallback answer")])
    e1 = RouterEntry("openai", "gpt-4o", fail)
    e2 = RouterEntry("anthropic", "claude", ok)
    router = ModelRouter([e1, e2], strategy="fallback")

    completion = await router.complete([_user("hi")], [])
    assert completion.content == "fallback answer"
    assert completion.routing_info["provider"] == "anthropic"
    assert fail.calls == 1
    assert ok.calls == 1


@pytest.mark.asyncio
async def test_router_all_fail_raises():
    e1 = RouterEntry("openai", "gpt-4o", FakeProvider([], fail=True))
    e2 = RouterEntry("anthropic", "claude", FakeProvider([], fail=True))
    router = ModelRouter([e1, e2], strategy="fallback")
    with pytest.raises(RuntimeError, match=r"All \d+ configured router providers failed"):
        await router.complete([_user("hi")], [])


@pytest.mark.asyncio
async def test_router_pinned_model_uses_exact_entry():
    p_skip = FakeProvider([Completion(content="wrong")])
    p_use = FakeProvider([Completion(content="pinned")])
    e1 = RouterEntry("openai", "gpt-4o", p_skip)
    e2 = RouterEntry("anthropic", "claude-sonnet", p_use)
    router = ModelRouter([e1, e2], strategy="classifier", pinned_model="claude-sonnet")

    completion = await router.complete([_user("hi")], [])
    assert completion.content == "pinned"
    assert completion.routing_info["model"] == "claude-sonnet"
    assert p_skip.calls == 0


@pytest.mark.asyncio
async def test_router_classifier_strategy_routes_reasoning_to_anthropic():
    p_anthropic = FakeProvider([Completion(content="anthropic-answer")])
    p_openai = FakeProvider([Completion(content="openai-answer")])
    e1 = RouterEntry("openai", "gpt-4o", p_openai)
    e2 = RouterEntry("anthropic", "claude", p_anthropic)
    router = ModelRouter([e1, e2], strategy="classifier")

    # Reasoning prompt → anthropic preferred
    completion = await router.complete(
        [_user("Reconcile the bank statements and analyze the general ledger discrepancy")], []
    )
    assert completion.content == "anthropic-answer"
    assert completion.routing_info["provider"] == "anthropic"
    assert p_openai.calls == 0


@pytest.mark.asyncio
async def test_router_classifier_strategy_routes_extraction_to_openai():
    p_openai = FakeProvider([Completion(content="openai-answer")])
    p_anthropic = FakeProvider([Completion(content="anthropic-answer")])
    e1 = RouterEntry("anthropic", "claude", p_anthropic)
    e2 = RouterEntry("openai", "gpt-4o", p_openai)
    router = ModelRouter([e1, e2], strategy="classifier")

    # Extraction prompt → openai preferred
    completion = await router.complete(
        [_user("extract fields", attachments=_fake_att())], []
    )
    assert completion.content == "openai-answer"
    assert completion.routing_info["provider"] == "openai"
    assert p_anthropic.calls == 0


@pytest.mark.asyncio
async def test_router_routing_event_emitted_in_run_chat():
    """run_chat emits a 'routing' event before the first 'token' event."""
    from mcp_server_buildium.llm.agent import run_chat

    p = FakeProvider([Completion(content="hello")])
    entry = RouterEntry("anthropic", "claude-x", p)
    router = ModelRouter([entry], strategy="fallback")

    events = []
    async for e in run_chat(router, [], lambda n, a: "result", [_user("hi")]):
        events.append(e)

    types = [e["type"] for e in events]
    # routing must come before token
    assert "routing" in types
    assert "token" in types
    assert types.index("routing") < types.index("token")
    routing_evt = next(e for e in events if e["type"] == "routing")
    assert routing_evt["provider"] == "anthropic"
    assert routing_evt["model"] == "claude-x"


@pytest.mark.asyncio
async def test_no_routing_event_for_plain_provider():
    """A non-router provider never emits a routing event."""
    from mcp_server_buildium.llm.agent import run_chat

    provider = FakeProvider([Completion(content="hello")])
    events = []
    async for e in run_chat(provider, [], lambda n, a: "result", [_user("hi")]):
        events.append(e)

    assert not any(e["type"] == "routing" for e in events)


# -- build_router (unit) -----------------------------------------------------


def test_build_router_constructs_entries():
    from mcp_server_buildium.config import BuildiumConfig
    from mcp_server_buildium.llm.router import build_router

    cfg = BuildiumConfig(
        client_id="cid",
        client_secret="secret",
        llm_router_enabled=True,
        llm_router_providers=json.dumps(
            [
                {"provider": "anthropic", "model": "claude-sonnet"},
                {"provider": "openai", "model": "gpt-4o"},
            ]
        ),
        llm_anthropic_api_key="anthr-key",
        llm_openai_api_key="sk-key",
    )
    router = build_router(cfg)
    assert isinstance(router, ModelRouter)
    assert len(router._entries) == 2
    assert router._entries[0].provider_name == "anthropic"
    assert router._entries[0].model == "claude-sonnet"
    assert router._entries[1].provider_name == "openai"
    assert router._entries[1].model == "gpt-4o"


def test_build_router_with_pinned_model():
    from mcp_server_buildium.config import BuildiumConfig
    from mcp_server_buildium.llm.router import build_router

    cfg = BuildiumConfig(
        client_id="cid",
        client_secret="secret",
        llm_router_enabled=True,
        llm_router_providers=json.dumps(
            [{"provider": "openai", "model": "gpt-4o"}]
        ),
        llm_openai_api_key="sk-key",
    )
    router = build_router(cfg, pinned_model="gpt-4o")
    assert router._pinned_model == "gpt-4o"


def test_build_router_raises_without_providers():
    from mcp_server_buildium.llm.router import build_router

    class FakeCfg:
        llm_router_enabled = True
        llm_router_providers = None
        llm_router_strategy = "classifier"
        llm_openai_api_key = ""
        llm_anthropic_api_key = ""
        llm_gemini_api_key = ""
        llm_openai_base_url = "http://x"
        llm_anthropic_base_url = "http://x"
        llm_gemini_base_url = "http://x"

        def get_llm_router_providers(self):
            return None

    with pytest.raises(ValueError, match="No router providers configured"):
        build_router(FakeCfg())


# ---------------------------------------------------------------------------
# /chat + /capabilities HTTP integration
# ---------------------------------------------------------------------------
# Each integration test creates a fresh FastMCP app scoped to a router config
# so tests are fully isolated from the shared server module imported by other
# test files (which may use a single-provider OpenAI config).

os.environ.setdefault("BUILDIUM_CLIENT_ID", "test-client-id")
os.environ.setdefault("BUILDIUM_CLIENT_SECRET", "test-client-secret")

# Import chat_endpoint for monkeypatching build_llm in tests below.
from mcp_server_buildium import chat_endpoint  # noqa: E402

_ROUTER_CFG = dict(
    client_id="test-client-id",
    client_secret="test-client-secret",
    llm_router_enabled=True,
    llm_router_providers=json.dumps(
        [
            {"provider": "anthropic", "model": "claude-sonnet-test"},
            {"provider": "openai", "model": "gpt-4o-test"},
        ]
    ),
    llm_anthropic_api_key="anthr-secret",
    llm_openai_api_key="sk-secret",
    llm_router_strategy="classifier",
)


@pytest.fixture()
def router_client():
    """A fresh ASGI test client backed by a router-configured FastMCP app."""
    from fastmcp import FastMCP
    from starlette.testclient import TestClient

    from mcp_server_buildium.auth import build_auth
    from mcp_server_buildium.chat_endpoint import register_chat_routes
    from mcp_server_buildium.config import BuildiumConfig

    cfg = BuildiumConfig(**_ROUTER_CFG)
    _mcp = FastMCP("buildium-test-router")
    verifier = build_auth(cfg)
    register_chat_routes(_mcp, cfg, verifier)
    app = _mcp.http_app(path="/mcp")
    with TestClient(app) as c:
        yield c


def _sse_events(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    return events


def test_capabilities_router_enabled(router_client) -> None:
    resp = router_client.get("/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["provider"] == "router"
    assert data["routing"] is True
    assert data["models"] == ["claude-sonnet-test", "gpt-4o-test"]
    assert len(data["router_providers"]) == 2
    assert data["router_providers"][0] == {
        "provider": "anthropic",
        "model": "claude-sonnet-test",
    }
    assert data["strategy"] == "classifier"
    # API keys must never appear in the response.
    assert "anthr-secret" not in resp.text
    assert "sk-secret" not in resp.text


def test_capabilities_router_no_model_field(router_client) -> None:
    """default_model should be None (router doesn't have a single default)."""
    resp = router_client.get("/capabilities")
    data = resp.json()
    assert data["default_model"] is None


def test_chat_router_auto_route(router_client, monkeypatch) -> None:
    """Without a model in the request body, the router auto-selects and emits routing."""

    class StubRouter:
        def __init__(self, *a, **k) -> None:
            pass

        async def complete(self, messages, tools):
            c = Completion(content="Routed answer.")
            c.routing_info = {
                "provider": "anthropic",
                "model": "claude-sonnet-test",
                "reason": "financial analysis",
            }
            return c

    monkeypatch.setattr(chat_endpoint, "build_llm", lambda *a, **k: StubRouter())

    resp = router_client.post(
        "/chat", json={"messages": [{"role": "user", "content": "reconcile the ledger"}]}
    )
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    routing_events = [e for e in events if e["type"] == "routing"]
    assert len(routing_events) == 1
    assert routing_events[0]["provider"] == "anthropic"
    assert routing_events[0]["model"] == "claude-sonnet-test"
    assert routing_events[0]["reason"] == "financial analysis"


def test_chat_router_model_pinning(router_client, monkeypatch) -> None:
    """Requesting a specific model is allowed when it's in the router's pool."""

    class StubRouter:
        def __init__(self, *a, **k) -> None:
            pass

        async def complete(self, messages, tools):
            return Completion(content="Pinned.")

    monkeypatch.setattr(chat_endpoint, "build_llm", lambda *a, **k: StubRouter())

    resp = router_client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "model": "gpt-4o-test",
        },
    )
    assert resp.status_code == 200


def test_chat_router_rejects_unknown_model(router_client) -> None:
    resp = router_client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "model": "not-a-real-model",
        },
    )
    assert resp.status_code == 400
    assert "not permitted" in resp.json()["error"]


def test_chat_router_empty_model_allowed(router_client, monkeypatch) -> None:
    """Omitting 'model' from the request body is valid in router mode (auto-route)."""

    class StubRouter:
        def __init__(self, *a, **k) -> None:
            pass

        async def complete(self, messages, tools):
            return Completion(content="OK")

    monkeypatch.setattr(chat_endpoint, "build_llm", lambda *a, **k: StubRouter())

    resp = router_client.post(
        "/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    )
    assert resp.status_code == 200
