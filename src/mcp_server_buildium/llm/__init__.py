"""Server-side LLM orchestration for the Buildium MCP ``/chat`` endpoint.

The assistant loop runs on the server so that provider API keys never reach the
browser. Provider-specific request/response shaping lives in
:mod:`mcp_server_buildium.llm.providers` (pure, unit-tested mappings), while the
tool-calling loop lives in :mod:`mcp_server_buildium.llm.agent`.
"""

from __future__ import annotations

from .agent import ChatEvent, flatten_tool_result, run_chat
from .base import Completion, LLMProvider, ToolCall, build_provider

__all__ = [
    "ChatEvent",
    "Completion",
    "LLMProvider",
    "ToolCall",
    "build_provider",
    "flatten_tool_result",
    "run_chat",
]
