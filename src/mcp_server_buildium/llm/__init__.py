"""Server-side LLM orchestration for the Buildium MCP ``/chat`` endpoint.

The assistant loop runs on the server so that provider API keys never reach the
browser. Provider-specific request/response shaping lives in
:mod:`mcp_server_buildium.llm.providers` (pure, unit-tested mappings), while the
tool-calling loop lives in :mod:`mcp_server_buildium.llm.agent`.
"""

from __future__ import annotations

from .agent import ChatEvent, flatten_tool_result, run_chat
from .attachments import (
    Attachment,
    AttachmentError,
    mb_to_bytes,
    normalize_attachments,
    set_current_attachments,
)
from .base import Completion, LLMProvider, ToolCall, build_provider

__all__ = [
    "Attachment",
    "AttachmentError",
    "ChatEvent",
    "Completion",
    "LLMProvider",
    "ToolCall",
    "build_provider",
    "flatten_tool_result",
    "mb_to_bytes",
    "normalize_attachments",
    "run_chat",
    "set_current_attachments",
]
