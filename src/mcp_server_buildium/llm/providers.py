"""Provider adapters for OpenAI, Anthropic, and Google Gemini.

Each provider exposes a ``complete`` coroutine, but the request/response shaping
is factored into *pure* module-level functions (``*_tools``, ``*_messages``,
``parse_*_response``) so the translation logic can be unit-tested without any
network access.
"""

from __future__ import annotations

import json
from typing import Any

from .base import Completion, LLMProvider, Message, ToolCall, ToolSpec

# ---------------------------------------------------------------------------
# OpenAI (Chat Completions)
# ---------------------------------------------------------------------------


def openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    """Map MCP tools to OpenAI ``function`` tool specs. Pure."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description") or "",
                "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


def openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Map neutral messages to the OpenAI Chat Completions format. Pure."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m["role"]
        if role == "assistant" and m.get("tool_calls"):
            out.append(
                {
                    "role": "assistant",
                    "content": m.get("content") or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in m["tool_calls"]
                    ],
                }
            )
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "content": m.get("content", ""),
                }
            )
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


def parse_openai_response(data: dict[str, Any]) -> Completion:
    """Parse an OpenAI Chat Completions response into a :class:`Completion`. Pure."""
    choices = data.get("choices") or []
    if not choices:
        return Completion()
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    tool_calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        tool_calls.append(
            ToolCall(
                id=tc.get("id") or f"call_{len(tool_calls)}",
                name=fn.get("name") or "",
                arguments=_loads(fn.get("arguments")),
            )
        )
    return Completion(content=content, tool_calls=tool_calls)


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible Chat Completions provider."""

    name = "openai"

    async def complete(self, messages: list[Message], tools: list[ToolSpec]) -> Completion:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages(messages),
        }
        if tools:
            body["tools"] = openai_tools(tools)
            body["tool_choice"] = "auto"
        client = await self._http()
        resp = await client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": "Bearer " + self.api_key},
            json=body,
        )
        _raise_for_status(resp, self.name)
        return parse_openai_response(resp.json())


# ---------------------------------------------------------------------------
# Anthropic (Messages API)
# ---------------------------------------------------------------------------

ANTHROPIC_VERSION = "2023-06-01"


def anthropic_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    """Map MCP tools to Anthropic tool specs. Pure."""
    return [
        {
            "name": t["name"],
            "description": t.get("description") or "",
            "input_schema": t.get("inputSchema") or {"type": "object", "properties": {}},
        }
        for t in tools
    ]


def anthropic_messages(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Split out the system prompt and map the rest to Anthropic messages. Pure.

    Consecutive ``tool`` results are merged into a single ``user`` message, as
    the Messages API expects ``tool_result`` blocks grouped in one user turn.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            if m.get("content"):
                system_parts.append(m["content"])
        elif role == "user":
            out.append(
                {"role": "user", "content": [{"type": "text", "text": m.get("content") or ""}]}
            )
        elif role == "assistant":
            content: list[dict[str, Any]] = []
            if m.get("content"):
                content.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                content.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                )
            out.append({"role": "assistant", "content": content})
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id", ""),
                "content": m.get("content", ""),
            }
            if (
                out
                and out[-1]["role"] == "user"
                and isinstance(out[-1]["content"], list)
                and _all_tool_results(out[-1]["content"])
            ):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return "\n".join(system_parts), out


def _all_tool_results(blocks: list[dict[str, Any]]) -> bool:
    return bool(blocks) and all(b.get("type") == "tool_result" for b in blocks)


def parse_anthropic_response(data: dict[str, Any]) -> Completion:
    """Parse an Anthropic Messages response into a :class:`Completion`. Pure."""
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in data.get("content") or []:
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text") or "")
        elif btype == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=block.get("id") or f"call_{len(tool_calls)}",
                    name=block.get("name") or "",
                    arguments=block.get("input") or {},
                )
            )
    return Completion(content="".join(text_parts), tool_calls=tool_calls)


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API provider."""

    name = "anthropic"
    max_tokens = 4096

    async def complete(self, messages: list[Message], tools: list[ToolSpec]) -> Completion:
        system, mapped = anthropic_messages(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": mapped,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = anthropic_tools(tools)
        client = await self._http()
        resp = await client.post(
            f"{self.base_url}/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            json=body,
        )
        _raise_for_status(resp, self.name)
        return parse_anthropic_response(resp.json())


# ---------------------------------------------------------------------------
# Google Gemini (generateContent)
# ---------------------------------------------------------------------------


def gemini_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    """Map MCP tools to a single Gemini ``function_declarations`` tool. Pure."""
    if not tools:
        return []
    return [
        {
            "function_declarations": [
                {
                    "name": t["name"],
                    "description": t.get("description") or "",
                    "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                }
                for t in tools
            ]
        }
    ]


def gemini_contents(messages: list[Message]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Split out the system instruction and map the rest to Gemini contents. Pure."""
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            if m.get("content"):
                system_parts.append(m["content"])
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": m.get("content") or ""}]})
        elif role == "assistant":
            parts: list[dict[str, Any]] = []
            if m.get("content"):
                parts.append({"text": m["content"]})
            for tc in m.get("tool_calls") or []:
                parts.append({"functionCall": {"name": tc.name, "args": tc.arguments}})
            contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": m.get("name", ""),
                                "response": {"content": m.get("content", "")},
                            }
                        }
                    ],
                }
            )
    system = {"parts": [{"text": "\n".join(system_parts)}]} if system_parts else None
    return system, contents


def parse_gemini_response(data: dict[str, Any]) -> Completion:
    """Parse a Gemini generateContent response into a :class:`Completion`. Pure."""
    candidates = data.get("candidates") or []
    if not candidates:
        return Completion()
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for part in parts:
        if "text" in part:
            text_parts.append(part.get("text") or "")
        elif "functionCall" in part:
            fc = part["functionCall"]
            tool_calls.append(
                ToolCall(
                    id=f"call_{len(tool_calls)}",
                    name=fc.get("name") or "",
                    arguments=fc.get("args") or {},
                )
            )
    return Completion(content="".join(text_parts), tool_calls=tool_calls)


class GeminiProvider(LLMProvider):
    """Google Gemini generateContent provider."""

    name = "gemini"

    async def complete(self, messages: list[Message], tools: list[ToolSpec]) -> Completion:
        system, contents = gemini_contents(messages)
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["system_instruction"] = system
        gtools = gemini_tools(tools)
        if gtools:
            body["tools"] = gtools
        client = await self._http()
        # The API key is passed as a query parameter, not a header.
        resp = await client.post(
            f"{self.base_url}/models/{self.model}:generateContent",
            params={"key": self.api_key},
            json=body,
        )
        _raise_for_status(resp, self.name)
        return parse_gemini_response(resp.json())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _loads(raw: Any) -> dict[str, Any]:
    """Best-effort JSON decode of tool-call arguments (models may emit '')."""
    if isinstance(raw, dict):
        return raw
    if not raw or not str(raw).strip():
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _raise_for_status(resp: Any, provider: str) -> None:
    """Raise a concise error (without secrets) on a non-2xx provider response."""
    if resp.status_code >= 400:
        body = resp.text[:300]
        raise RuntimeError(f"{provider} request failed ({resp.status_code}): {body}")
