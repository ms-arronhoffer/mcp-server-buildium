"""Tests for the server-side chat agent loop and result flattening."""

from __future__ import annotations

import pytest

from mcp_server_buildium.llm.agent import flatten_tool_result, run_chat
from mcp_server_buildium.llm.base import Completion, LLMProvider, ToolCall


class FakeProvider(LLMProvider):
    """A provider that replays a scripted list of completions."""

    name = "fake"

    def __init__(self, completions: list[Completion]) -> None:  # noqa: D107
        super().__init__(api_key="k", model="m", base_url="http://x")
        self._completions = list(completions)
        self.calls: list[list[dict]] = []

    async def complete(self, messages, tools) -> Completion:  # type: ignore[override]
        # Record a shallow copy of the conversation seen on each round.
        self.calls.append([dict(m) for m in messages])
        return self._completions.pop(0)


async def _collect(gen):
    return [event async for event in gen]


class _ToolResult:
    def __init__(self, structured=None, content=None) -> None:
        self.structured_content = structured
        self.content = content


class _TextBlock:
    def __init__(self, text) -> None:
        self.text = text


def test_flatten_prefers_structured_content() -> None:
    assert flatten_tool_result(_ToolResult(structured={"a": 1})) == '{"a": 1}'


def test_flatten_concatenates_text_blocks() -> None:
    result = _ToolResult(content=[_TextBlock("l1"), _TextBlock("l2")])
    assert flatten_tool_result(result) == "l1\nl2"


def test_flatten_handles_plain_values() -> None:
    assert flatten_tool_result(None) == ""
    assert flatten_tool_result({"x": 1}) == '{"x": 1}'


@pytest.mark.asyncio
async def test_direct_answer_without_tools() -> None:
    provider = FakeProvider([Completion(content="Hello world")])

    async def runner(name, args):  # pragma: no cover - not called
        raise AssertionError("tool_runner should not be called")

    events = await _collect(run_chat(provider, [], runner, [{"role": "user", "content": "hi"}]))
    assert {"type": "token", "text": "Hello world"} in events
    assert events[-1] == {"type": "done", "content": "Hello world"}


@pytest.mark.asyncio
async def test_executes_tool_then_answers() -> None:
    provider = FakeProvider(
        [
            Completion(content="", tool_calls=[ToolCall("c1", "list_leases", {"limit": 5})]),
            Completion(content="You have 2 leases."),
        ]
    )
    seen = {}

    async def runner(name, args):
        seen["call"] = (name, args)
        return '{"count":2}'

    events = await _collect(
        run_chat(provider, [], runner, [{"role": "user", "content": "how many?"}])
    )
    assert seen["call"] == ("list_leases", {"limit": 5})
    assert {"type": "tool_call", "name": "list_leases", "arguments": {"limit": 5}} in events
    assert {"type": "tool_result", "name": "list_leases", "text": '{"count":2}'} in events
    assert events[-1] == {"type": "done", "content": "You have 2 leases."}


@pytest.mark.asyncio
async def test_tool_error_reported_not_raised() -> None:
    provider = FakeProvider(
        [
            Completion(content="", tool_calls=[ToolCall("c1", "boom", {})]),
            Completion(content="handled"),
        ]
    )

    async def runner(name, args):
        raise RuntimeError("kaboom")

    events = await _collect(run_chat(provider, [], runner, [{"role": "user", "content": "go"}]))
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert "kaboom" in tool_results[0]["text"]
    assert events[-1] == {"type": "done", "content": "handled"}


@pytest.mark.asyncio
async def test_provider_error_emits_error_event() -> None:
    class Boom(LLMProvider):
        name = "boom"

        async def complete(self, messages, tools):  # type: ignore[override]
            raise RuntimeError("provider down")

    provider = Boom(api_key="k", model="m", base_url="http://x")

    async def runner(name, args):  # pragma: no cover
        return ""

    events = await _collect(run_chat(provider, [], runner, [{"role": "user", "content": "hi"}]))
    assert events == [{"type": "error", "message": "provider down"}]


@pytest.mark.asyncio
async def test_stops_after_max_rounds() -> None:
    # Always requests a tool -> loop should terminate at max_rounds.
    provider = FakeProvider(
        [Completion(content="", tool_calls=[ToolCall(f"c{i}", "loop", {})]) for i in range(5)]
    )

    async def runner(name, args):
        return "ok"

    events = await _collect(
        run_chat(provider, [], runner, [{"role": "user", "content": "go"}], max_rounds=2)
    )
    assert events[-1]["type"] == "done"
    assert "maximum number of tool-call rounds" in events[-1]["content"]
