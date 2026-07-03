"""The server-side assistant loop: drive the model and execute MCP tools.

``run_chat`` is an async generator yielding :class:`ChatEvent` dicts describing
tokens, tool calls, tool results, completion, and errors. The transport layer
(the ``/chat`` route) serializes these as Server-Sent Events. Ported from the
former browser-side ``extension/src/llm.js`` ``Agent`` loop.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from .base import Completion, LLMProvider, Message, ToolSpec

# An event emitted to the client. ``type`` is one of:
#   "token"        -> {"text": str}
#   "tool_call"    -> {"name": str, "arguments": dict}
#   "tool_result"  -> {"name": str, "text": str}
#   "done"         -> {"content": str}
#   "error"        -> {"message": str}
ChatEvent = dict[str, Any]

# Executes a tool by name and returns its flattened textual result.
ToolRunner = Callable[[str, dict[str, Any]], Awaitable[str]]


def flatten_tool_result(result: Any) -> str:
    """Flatten a FastMCP ``ToolResult`` (or plain value) into a string for the model.

    Prefers ``structured_content``; otherwise concatenates text content blocks.
    """
    if result is None:
        return ""
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return json.dumps(structured)
    content = getattr(result, "content", None)
    if isinstance(content, list):
        parts = []
        for block in content:
            text = getattr(block, "text", None)
            parts.append(text if text is not None else json.dumps(_jsonable(block)))
        return "\n".join(parts)
    if isinstance(result, (dict, list)):
        return json.dumps(result)
    return str(result)


def _jsonable(obj: Any) -> Any:
    try:
        return obj if isinstance(obj, (dict, list, str, int, float, bool, type(None))) else str(obj)
    except Exception:  # pragma: no cover - defensive
        return str(obj)


async def run_chat(
    provider: LLMProvider,
    tools: list[ToolSpec],
    tool_runner: ToolRunner,
    messages: list[Message],
    *,
    max_rounds: int = 8,
):
    """Run one user turn to completion, executing any tool calls the model requests.

    Args:
        provider: The LLM provider adapter.
        tools: MCP tool specs to advertise to the model.
        tool_runner: Coroutine executing a tool and returning flattened text.
        messages: The conversation so far (system + history). Mutated in place.
        max_rounds: Safety cap on tool-call rounds.

    Yields:
        :class:`ChatEvent` dicts.
    """
    for _round in range(max_rounds):
        try:
            completion: Completion = await provider.complete(messages, tools)
        except Exception as exc:  # provider/network failure
            yield {"type": "error", "message": str(exc)}
            return

        if completion.content:
            yield {"type": "token", "text": completion.content}

        assistant_msg: Message = {"role": "assistant", "content": completion.content or None}
        if completion.tool_calls:
            assistant_msg["tool_calls"] = completion.tool_calls
        messages.append(assistant_msg)

        if not completion.tool_calls:
            yield {"type": "done", "content": completion.content}
            return

        for call in completion.tool_calls:
            yield {"type": "tool_call", "name": call.name, "arguments": call.arguments}
            try:
                text = await tool_runner(call.name, call.arguments)
            except Exception as exc:
                text = f"Error calling tool {call.name}: {exc}"
            yield {"type": "tool_result", "name": call.name, "text": text}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": text,
                }
            )

    yield {"type": "done", "content": "Stopped after the maximum number of tool-call rounds."}
