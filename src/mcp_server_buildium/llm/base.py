"""Core LLM types and the provider factory.

Types here are provider-neutral. A conversation is a list of ``Message`` dicts
with roles ``system`` / ``user`` / ``assistant`` / ``tool``; provider adapters
translate these to and from their wire formats in
:mod:`mcp_server_buildium.llm.providers`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx

    from ..config import BuildiumConfig

# A single chat message in the neutral internal format.
#   {"role": "system"|"user", "content": str}
#   {"role": "assistant", "content": str | None, "tool_calls": list[ToolCall]}
#   {"role": "tool", "tool_call_id": str, "name": str, "content": str}
Message = dict[str, Any]

# An MCP tool advertised to the model: {"name", "description", "inputSchema"}.
ToolSpec = dict[str, Any]


@dataclass
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Completion:
    """A single (non-streamed) model turn: free text plus any tool calls."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMProvider(ABC):
    """Abstract provider producing a :class:`Completion` from messages + tools."""

    #: Short provider identifier (e.g. ``"openai"``).
    name: str = "base"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = client

    async def _http(self) -> httpx.AsyncClient:
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    @abstractmethod
    async def complete(self, messages: list[Message], tools: list[ToolSpec]) -> Completion:
        """Perform one model turn and return the normalized result."""
        raise NotImplementedError


def build_provider(
    config: BuildiumConfig,
    *,
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> LLMProvider:
    """Construct the configured provider adapter.

    Args:
        config: Server configuration with LLM settings populated.
        model: Optional model override (must already be validated as allowed).
        client: Optional shared ``httpx.AsyncClient`` (mainly for tests).

    Raises:
        ValueError: when no provider is configured.
    """
    from .providers import AnthropicProvider, GeminiProvider, OpenAIProvider

    provider = config.get_llm_provider()
    if provider is None:
        raise ValueError("No LLM provider configured (set BUILDIUM_LLM_PROVIDER)")

    key = config.get_active_llm_key() or ""
    base_url = config.get_llm_base_url() or ""
    chosen_model = model or config.llm_model or ""

    classes: dict[str, type[LLMProvider]] = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "gemini": GeminiProvider,
    }
    return classes[provider](api_key=key, model=chosen_model, base_url=base_url, client=client)
