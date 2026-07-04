"""Server-side LLM orchestration for the Buildium MCP ``/chat`` endpoint.

The assistant loop runs on the server so that provider API keys never reach the
browser. Provider-specific request/response shaping lives in
:mod:`mcp_server_buildium.llm.providers` (pure, unit-tested mappings), while the
tool-calling loop lives in :mod:`mcp_server_buildium.llm.agent`.
"""

from __future__ import annotations

from .agent import ChatEvent, flatten_tool_result, run_chat
from .artifacts import (
    ArtifactError,
    GeneratedFile,
    Section,
    Slide,
    add_current_artifact,
    build_generated_file,
    get_current_artifacts,
    set_current_artifacts,
)
from .attachments import (
    Attachment,
    AttachmentError,
    mb_to_bytes,
    normalize_attachments,
    set_current_attachments,
)
from .base import Completion, LLMProvider, ToolCall, build_llm, build_provider
from .router import ModelRouter, RouterEntry, build_router, classify_task

__all__ = [
    "ArtifactError",
    "Attachment",
    "AttachmentError",
    "ChatEvent",
    "Completion",
    "GeneratedFile",
    "LLMProvider",
    "ModelRouter",
    "RouterEntry",
    "Section",
    "Slide",
    "ToolCall",
    "add_current_artifact",
    "build_generated_file",
    "build_llm",
    "build_provider",
    "build_router",
    "classify_task",
    "flatten_tool_result",
    "get_current_artifacts",
    "mb_to_bytes",
    "normalize_attachments",
    "run_chat",
    "set_current_artifacts",
    "set_current_attachments",
]
