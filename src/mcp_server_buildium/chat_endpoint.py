"""HTTP routes for the server-side assistant: ``/chat`` (SSE) and ``/capabilities``.

These routes run the LLM loop on the server so provider API keys never reach the
browser. They are registered on the FastMCP HTTP app via ``custom_route`` and are
protected by the *same* authentication as the MCP endpoint (Entra JWT or static
bearer token), unless ``BUILDIUM_DEV_AUTH_BYPASS`` is enabled.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from .llm import (
    build_provider,
    flatten_tool_result,
    mb_to_bytes,
    normalize_attachments,
    run_chat,
    set_current_attachments,
)
from .llm.attachments import Attachment, AttachmentError, current_attachments
from .logging_config import get_logger
from .security.policy import effective_policy_for_claims

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastmcp.server.auth.auth import TokenVerifier

    from .config import BuildiumConfig
    from .security.policy import ToolPolicy

logger = get_logger("mcp_server_buildium.chat")

CHAT_PATH = "/chat"
CAPABILITIES_PATH = "/capabilities"


async def _authenticate(
    request: Request, config: BuildiumConfig, verifier: TokenVerifier | None
) -> tuple[bool, dict]:
    """Verify the request and return ``(authorized, claims)``.

    ``claims`` carries the verified JWT claims (including the Entra ``roles``
    App Role claim) when a token was validated, else an empty dict. Mirrors the
    MCP auth precedence: dev bypass → configured verifier → open when no auth is
    configured (e.g. stdio/dev).
    """
    if config.dev_auth_bypass:
        return True, {}
    if verifier is None:
        return True, {}
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        logger.info("Chat request rejected: missing or malformed Authorization header")
        return False, {}
    token = header[len("bearer ") :].strip()
    if not token:
        logger.info("Chat request rejected: empty credentials in Authorization header")
        return False, {}
    try:
        result = await verifier.verify_token(token)
    except Exception:  # pragma: no cover - defensive
        logger.warning("Chat request rejected: token verification raised an error", exc_info=True)
        return False, {}
    if result is None:
        logger.info(
            "Chat request rejected: token failed verification "
            "(issuer/audience/scope/expiry mismatch or bad signature)"
        )
        return False, {}
    claims = getattr(result, "claims", None) or {}
    return True, claims


async def _authorized(
    request: Request, config: BuildiumConfig, verifier: TokenVerifier | None
) -> bool:
    """Return True when the request is permitted (see :func:`_authenticate`)."""
    authorized, _ = await _authenticate(request, config, verifier)
    return authorized


def _sse(event: dict[str, Any]) -> str:
    """Serialize an event as a single Server-Sent Event frame."""
    return f"data: {json.dumps(event)}\n\n"


# Event types that are internal to the tool-calling loop. They drive the loop
# server-side but must never be forwarded to the chat UI, so users never see raw
# tool calls or their results in the conversation.
_INTERNAL_EVENT_TYPES = frozenset({"tool_call", "tool_result"})


def _current_datetime_note(now: datetime | None = None) -> str:
    """Return a system-prompt line stating the current UTC date and time.

    Injected on every request so the assistant always anchors relative or
    date-offset calculations (e.g. "in 30 days", "leases expiring next month")
    to the real current time instead of its training-time assumptions.
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)
    stamp = current.strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"The current date and time is {stamp} (UTC), {current.strftime('%A, %d %B %Y')}. "
        "Always use this as 'now' when computing any date-related offsets or "
        "relative dates (for example 'today', 'in 30 days', 'last month', "
        "'expiring soon'); never rely on your own assumption of the current date."
    )


def register_chat_routes(
    mcp: Any,
    config: BuildiumConfig,
    verifier: TokenVerifier | None,
    base_policy: ToolPolicy | None = None,
) -> None:
    """Register the ``/chat`` and ``/capabilities`` routes on the FastMCP app."""

    # When an Entra App Role map is configured (with Entra auth), each chat turn
    # only advertises/executes tools the caller's role permits.
    role_map = config.get_entra_role_policy_map()
    scoping_active = bool(role_map) and config.entra_enabled() and base_policy is not None

    @mcp.custom_route(CAPABILITIES_PATH, methods=["GET"])
    async def capabilities(request: Request) -> JSONResponse:  # noqa: RUF029
        if not await _authorized(request, config, verifier):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not config.llm_enabled():
            return JSONResponse({"enabled": False, "models": []})
        # Never include API keys or other secrets in this response.
        return JSONResponse(
            {
                "enabled": True,
                "provider": config.get_llm_provider(),
                "default_model": config.llm_model,
                "models": config.get_llm_models(),
            }
        )

    @mcp.custom_route(CHAT_PATH, methods=["POST"])
    async def chat(request: Request) -> Any:
        authorized, claims = await _authenticate(request, config, verifier)
        if not authorized:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not config.llm_enabled():
            return JSONResponse(
                {"error": "The assistant is not configured on this server."},
                status_code=503,
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body."}, status_code=400)

        history = body.get("messages")
        if not isinstance(history, list):
            return JSONResponse(
                {"error": "'messages' must be a list of {role, content} objects."},
                status_code=400,
            )

        requested_model = (body.get("model") or config.llm_model or "").strip()
        if not config.is_llm_model_allowed(requested_model):
            return JSONResponse(
                {"error": f"Model {requested_model!r} is not permitted."},
                status_code=400,
            )

        # Build the conversation: server-controlled system prompt + client history.
        # A dynamic date/time note is appended so the assistant always anchors
        # relative date calculations to the real current time.
        system_content = (
            config.get_llm_system_prompt() + "\n\n" + _current_datetime_note()
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]
        # Attachments on the most recent user message are decoded/validated and
        # threaded to the model as multimodal content; they are also published to
        # the tool context so a tool can save the raw bytes to Buildium.
        latest_attachments: list[Attachment] = []
        max_bytes = mb_to_bytes(config.llm_max_attachment_mb)
        max_count = config.llm_max_attachments_per_request
        for m in history:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            if role not in ("user", "assistant"):
                continue
            message: dict[str, Any] = {"role": role, "content": m.get("content") or ""}
            if role == "user" and m.get("attachments"):
                try:
                    atts = normalize_attachments(
                        m["attachments"], max_bytes=max_bytes, max_count=max_count
                    )
                except AttachmentError as exc:
                    return JSONResponse({"error": str(exc)}, status_code=400)
                if atts:
                    message["attachments"] = atts
                    latest_attachments = atts
            messages.append(message)

        # Resolve the caller's effective policy (server ceiling ∩ Entra App Role).
        effective = (
            effective_policy_for_claims(base_policy, role_map, claims)
            if scoping_active
            else None
        )

        def _permitted(name: str) -> bool:
            return effective is None or effective.is_allowed(name)

        # Advertise the in-process, policy-guarded tools to the model, filtered to
        # the caller's permitted set.
        tool_map = await mcp.get_tools()
        tool_specs = [
            {
                "name": name,
                "description": getattr(tool, "description", "") or "",
                "inputSchema": getattr(tool, "parameters", None)
                or {"type": "object", "properties": {}},
            }
            for name, tool in tool_map.items()
            if _permitted(name)
        ]

        async def tool_runner(name: str, args: dict[str, Any]) -> str:
            if not _permitted(name):
                return f"Error: tool '{name}' is not permitted for your role."
            tool = tool_map.get(name)
            if tool is None:
                return f"Error: unknown tool '{name}'."
            result = await tool.run(args)
            return flatten_tool_result(result)

        provider = build_provider(config, model=requested_model)

        async def event_stream():
            # Publish this request's attachments so in-process tools (e.g.
            # save_uploaded_document) can access the raw bytes by file name.
            token = set_current_attachments(latest_attachments)
            try:
                async for event in run_chat(
                    provider,
                    tool_specs,
                    tool_runner,
                    messages,
                    max_rounds=config.llm_max_tool_rounds,
                ):
                    # Internal tool-call/result events drive the loop but are
                    # never surfaced to the user in the chat.
                    if event.get("type") in _INTERNAL_EVENT_TYPES:
                        continue
                    yield _sse(event)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("chat stream failed")
                yield _sse({"type": "error", "message": str(exc)})
            finally:
                current_attachments.reset(token)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
