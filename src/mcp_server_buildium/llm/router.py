"""Model router: selects the best LLM provider for each /chat request.

The :class:`ModelRouter` implements the same :class:`~.base.LLMProvider`
interface as the concrete provider adapters (OpenAI, Anthropic, Gemini), so the
:func:`~.agent.run_chat` loop needs no changes. The router:

1. **Classifies** the prompt using lightweight keyword/pattern heuristics (zero
   external calls, zero added latency).
2. **Selects** the best configured provider for that task type.
3. **Falls back** to the next provider if the selected one fails (network error,
   rate-limit, 5xx), transparent to the caller.
4. **Sticks** to the chosen provider for the rest of the conversation turn so
   tool-call rounds stay on the same model.
5. **Annotates** the first :class:`~.base.Completion` with ``routing_info``
   so :func:`~.agent.run_chat` can emit a ``"routing"`` SSE event before the
   first token, letting the browser extension show which model answered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import Completion, LLMProvider, Message, ToolSpec

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx

    from ..config import BuildiumConfig
    from .config_store import LLMConfig

# ---------------------------------------------------------------------------
# Task types
# ---------------------------------------------------------------------------

_TASK_REASONING = "reasoning"
_TASK_CREATIVE = "creative"
_TASK_AGENTIC = "agentic"
_TASK_EXTRACTION = "extraction"

# Provider preference per task type (ordered best → least-preferred for the task).
# When multiple configured providers share the same type ranking, the original
# config order (index in BUILDIUM_LLM_ROUTER_PROVIDERS) is preserved as a
# tiebreaker via Python's stable sort.
_TASK_PREFERENCES: dict[str, list[str]] = {
    # Reasoning/compliance: Anthropic Claude is strong at multi-step analysis
    # and financial/legal reasoning; GPT-4o is close second.
    _TASK_REASONING: ["anthropic", "openai", "gemini"],
    # Tool-heavy agentic tasks: Anthropic has the best tool-call fidelity
    # in chained workflows; GPT-4o follows.
    _TASK_AGENTIC: ["anthropic", "openai", "gemini"],
    # Extraction / multimodal: GPT-4o handles images + PDFs natively;
    # Anthropic handles PDFs + text well too.
    _TASK_EXTRACTION: ["openai", "anthropic", "gemini"],
    # Conversational / drafting: GPT-4o / GPT-4o-mini are fast and fluent;
    # Anthropic Claude is close second.
    _TASK_CREATIVE: ["openai", "anthropic", "gemini"],
}

# ---------------------------------------------------------------------------
# Classifier patterns (all anchored to word boundaries, case-insensitive)
# ---------------------------------------------------------------------------

_PAT_FINANCIAL = re.compile(
    r"\b(?:"
    r"gl|general ledger|reconcil|bank|payment|invoice|bill|budget|"
    r"income statement|accounts? (?:payable|receivable)|journal entry|ledger|"
    r"fiscal|finance|financial|debit|credit|cash flow|rent roll|aged|"
    r"delinquency|delinquent|arrear|overdue|late fee|charge|late charge|"
    r"owner draw|owner distribution"
    r")",
    re.I,
)

_PAT_REASONING = re.compile(
    r"\b(?:"
    r"analyz|calculat|compar|audit|compliance|diagnos|review|why|explain why|"
    r"lease compliance|assess|evaluat|forecast|predict|risk|impact|root cause|"
    r"discrepancy|discrepanc"
    r")",
    re.I,
)

_PAT_AGENTIC = re.compile(
    r"\b(?:"
    r"all |every |bulk |for each |each of|list all|find all|update all|"
    r"run |process |check all|generate (?:report|summary)|across all|iterate|"
    r"batch|portfolio"
    r")",
    re.I,
)

_PAT_EXTRACTION = re.compile(
    r"\b(?:"
    r"extract|parse|pull (?:fields?|data)|read (?:this|the) (?:document|file|pdf|lease)|"
    r"from (?:this|the) (?:document|file|attachment)|"
    r"what (?:does|is) (?:this|the) (?:document|file)"
    r")",
    re.I,
)

_PAT_CREATIVE = re.compile(
    r"\b(?:"
    r"draft|write (?:an?|the)|email|summariz|describ|hello|hi|"
    r"help me write|compose|format|reword|rephrase|translate|greet"
    r")",
    re.I,
)

# Short conversational messages (word count below this) default to creative.
_SHORT_THRESHOLD = 40


# ---------------------------------------------------------------------------
# Public classifier
# ---------------------------------------------------------------------------


def classify_task(messages: list[Message]) -> tuple[str, str]:
    """Classify the dominant task type from the conversation messages.

    Inspects the last user message (content + presence of attachments) using
    keyword/pattern matching and length heuristics. No external calls are made.

    Args:
        messages: Neutral conversation message list (system + history).

    Returns:
        ``(task_type, reason)`` where *task_type* is one of ``"reasoning"``,
        ``"creative"``, ``"agentic"``, or ``"extraction"``, and *reason* is a
        short human-readable string suitable for the ``"routing"`` SSE event.
    """
    last_content = ""
    has_attachments = False
    for m in reversed(messages):
        if m.get("role") == "user":
            last_content = m.get("content") or ""
            has_attachments = bool(m.get("attachments"))
            break

    # Attachments always signal extraction regardless of text.
    if has_attachments:
        return _TASK_EXTRACTION, "prompt includes document attachments"

    # Explicit extraction language.
    if _PAT_EXTRACTION.search(last_content):
        return _TASK_EXTRACTION, "prompt requests data extraction from documents"

    # Financial/accounting → reasoning (Anthropic preferred for compliance).
    if _PAT_FINANCIAL.search(last_content):
        return _TASK_REASONING, "prompt involves financial or accounting analysis"

    # General multi-step reasoning/analysis.
    if _PAT_REASONING.search(last_content):
        return _TASK_REASONING, "prompt requires multi-step reasoning or analysis"

    # Portfolio-wide or agentic multi-tool prompts.
    if _PAT_AGENTIC.search(last_content):
        return _TASK_AGENTIC, "prompt requires querying or acting on many records"

    # Short or explicitly creative/conversational prompts.
    word_count = len(last_content.split())
    if word_count < _SHORT_THRESHOLD or _PAT_CREATIVE.search(last_content):
        return _TASK_CREATIVE, "prompt is conversational or a short/drafting request"

    # Default for longer prompts with no strong signal: agentic (likely tool-heavy).
    return _TASK_AGENTIC, "prompt likely requires multiple tool interactions"


# ---------------------------------------------------------------------------
# Router entry
# ---------------------------------------------------------------------------


@dataclass
class RouterEntry:
    """A single configured provider+model in the router pool."""

    provider_name: str
    """Normalized lower-case provider identifier (``"openai"``, etc.)."""
    model: str
    """Model name passed to the provider on every request."""
    provider: LLMProvider
    """Concrete provider adapter instance."""


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------


class ModelRouter(LLMProvider):
    """Routes each /chat request to the best available LLM provider.

    **First call (turn start):** classifies the task, sorts configured providers
    by preference, tries them in order (with silent fallback on failure), and
    annotates the returned :class:`~.base.Completion` with ``routing_info`` so
    :func:`~.agent.run_chat` can emit a ``"routing"`` SSE event.

    **Subsequent calls (tool-call rounds):** sticks to the provider that
    succeeded on the first call — switching mid-conversation would corrupt the
    tool-call context.
    """

    name = "router"

    def __init__(
        self,
        entries: list[RouterEntry],
        strategy: str = "classifier",
        *,
        pinned_model: str | None = None,
        task_map: dict[str, RouterEntry] | None = None,
    ) -> None:
        """
        Args:
            entries: Ordered list of provider+model pairs in the router pool.
            strategy: ``"classifier"`` (heuristic task routing) or
                ``"fallback"`` (try entries in config order).
            pinned_model: When set, only the entry whose model matches this
                string is used (no classification, no fallback to other models).
            task_map: Optional direct mapping of task-type string →
                :class:`RouterEntry`. When provided and the router is in
                classifier mode, the entry is looked up directly instead of
                sorting by ``_TASK_PREFERENCES``. Used by the store-based
                router so each tier's exact configured model is used.
        """
        # Pass dummy credentials — the router delegates to concrete providers.
        super().__init__(api_key="", model="router", base_url="")
        self._entries = entries
        self._strategy = strategy.strip().lower()
        self._pinned_model = pinned_model
        self._task_map = task_map or {}
        # Sticky state — set on the first complete() call.
        self._active_entry: RouterEntry | None = None

    async def _http(self):  # type: ignore[override]  # pragma: no cover
        raise NotImplementedError("ModelRouter does not make direct HTTP calls")

    async def complete(self, messages: list[Message], tools: list[ToolSpec]) -> Completion:
        """Delegate to the chosen provider, emitting routing info on the first call."""
        if self._active_entry is not None:
            # Subsequent rounds: stay on the same provider.
            return await self._active_entry.provider.complete(messages, tools)

        # First call: pick an ordered list and try each until one succeeds.
        ordered, reason = self._pick_ordered(messages)
        last_exc: Exception = RuntimeError("No providers available to try")
        for entry in ordered:
            try:
                completion = await entry.provider.complete(messages, tools)
            except Exception as exc:
                last_exc = exc
                continue
            # Success — make this entry sticky and annotate the completion.
            self._active_entry = entry
            completion.routing_info = {
                "provider": entry.provider_name,
                "model": entry.model,
                "reason": reason,
            }
            return completion

        raise RuntimeError(
            f"All {len(ordered)} configured router providers failed. Last error: {last_exc}"
        )

    def _pick_ordered(self, messages: list[Message]) -> tuple[list[RouterEntry], str]:
        """Return ``(ordered_entries, reason)`` for the current request.

        *ordered_entries* is the list of :class:`RouterEntry` objects to try in
        descending preference order. *reason* is a short human-readable string
        that will be included in the ``"routing"`` SSE event.
        """
        if self._pinned_model:
            for entry in self._entries:
                if entry.model == self._pinned_model:
                    return [entry], f"client pinned model to {self._pinned_model!r}"
            # Pinned model not found — fall back to full list (should not happen
            # if endpoint validation is correct, but degrade gracefully).
            return list(self._entries), f"pinned model {self._pinned_model!r} not found in pool"

        if self._strategy == "fallback":
            return list(self._entries), "fallback strategy — using config order"

        # classifier strategy
        task_type, reason = classify_task(messages)

        # Store-based direct task map: return the single configured entry for
        # this task type (no preference sorting needed).
        if self._task_map:
            entry = self._task_map.get(task_type)
            if entry is not None:
                return [entry], reason
            # Task type not in map — fall back to full entries list.
            return list(self._entries), reason

        return _sort_by_task(self._entries, task_type), reason


# ---------------------------------------------------------------------------
# Sorting helper
# ---------------------------------------------------------------------------


def _sort_by_task(entries: list[RouterEntry], task_type: str) -> list[RouterEntry]:
    """Return *entries* sorted by descending preference for *task_type*.

    Uses ``_TASK_PREFERENCES[task_type]`` as the preference ranking. Entries
    whose provider name appears earlier in the preference list sort first.
    Python's stable sort preserves the relative order of entries with the same
    provider name (i.e. config index order is the tiebreaker).
    """
    preference = _TASK_PREFERENCES.get(task_type, [])

    def _rank(entry: RouterEntry) -> int:
        try:
            return preference.index(entry.provider_name)
        except ValueError:
            return len(preference)

    return sorted(entries, key=_rank)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_router(
    config: BuildiumConfig,
    *,
    pinned_model: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> ModelRouter:
    """Construct a :class:`ModelRouter` from *config*.

    API keys and base URLs for each provider are taken from the corresponding
    ``BUILDIUM_LLM_<PROVIDER>_API_KEY`` / ``BUILDIUM_LLM_<PROVIDER>_BASE_URL``
    config fields (the same fields used in single-provider mode).

    Args:
        config: Server configuration with router settings populated.
        pinned_model: Optional model to pin for the returned router (used when
            a ``/chat`` client explicitly requests a specific model).
        client: Optional shared ``httpx.AsyncClient`` (mainly for tests).

    Raises:
        ValueError: when no router providers are configured.
    """
    from .providers import AnthropicProvider, GeminiProvider, OpenAIProvider

    _provider_classes = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "gemini": GeminiProvider,
    }
    _key_map = {
        "openai": config.llm_openai_api_key or "",
        "anthropic": config.llm_anthropic_api_key or "",
        "gemini": config.llm_gemini_api_key or "",
    }
    _url_map = {
        "openai": config.llm_openai_base_url,
        "anthropic": config.llm_anthropic_base_url,
        "gemini": config.llm_gemini_base_url,
    }

    entries_cfg = config.get_llm_router_providers()
    if not entries_cfg:
        raise ValueError(
            "No router providers configured "
            "(set BUILDIUM_LLM_ROUTER_PROVIDERS when BUILDIUM_LLM_ROUTER_ENABLED=true)"
        )

    entries: list[RouterEntry] = []
    for entry_cfg in entries_cfg:
        pname = entry_cfg["provider"].strip().lower()
        model = entry_cfg["model"].strip()
        provider_instance = _provider_classes[pname](
            api_key=_key_map[pname],
            model=model,
            base_url=_url_map[pname],
            client=client,
        )
        entries.append(RouterEntry(provider_name=pname, model=model, provider=provider_instance))

    strategy = (config.llm_router_strategy or "classifier").strip().lower()
    return ModelRouter(entries=entries, strategy=strategy, pinned_model=pinned_model)


def build_router_from_store(
    llm_cfg: "LLMConfig",
    *,
    pinned_model: str | None = None,
    client: "httpx.AsyncClient | None" = None,
) -> ModelRouter:
    """Construct a :class:`ModelRouter` from an :class:`~.config_store.LLMConfig`.

    This is the preferred factory when the admin-UI config store is active. It
    reads provider keys and per-tier model assignments directly from *llm_cfg*
    (already loaded from disk) rather than from ``BuildiumConfig`` env fields.

    Each configured tier becomes a :class:`RouterEntry`. A ``task_map`` is
    built so the classifier's output maps directly to the tier's entry without
    going through preference-sorting.

    Args:
        llm_cfg: Loaded and decrypted LLM configuration from the store.
        pinned_model: Optional model to pin for the returned router.
        client: Optional shared ``httpx.AsyncClient`` (mainly for tests).

    Raises:
        ValueError: when no tiers are configured in the store.
    """
    from .config_store import TIER_TO_TASK
    from .providers import AnthropicProvider, GeminiProvider, OpenAIProvider

    _provider_classes: dict[str, type[LLMProvider]] = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "gemini": GeminiProvider,
    }

    configured = llm_cfg.get_configured_tiers()
    if not configured:
        raise ValueError(
            "No tiers are configured in the LLM config store. "
            "Open the admin UI at /manage/ to assign a provider and model to each tier."
        )

    entries: list[RouterEntry] = []
    task_map: dict[str, RouterEntry] = {}

    # Deduplicate: multiple tiers may share the same provider+model. Build one
    # provider instance per (provider_name, model, api_key) triple.
    _instance_cache: dict[tuple[str, str, str], LLMProvider] = {}

    for tier_name, tier in configured.items():
        pname = tier.provider.strip().lower()
        model = tier.model.strip()
        provider_entry = llm_cfg.providers.get(pname)
        if provider_entry is None:
            continue
        api_key = provider_entry.api_key or ""
        base_url = provider_entry.effective_base_url()
        cls = _provider_classes.get(pname)
        if cls is None:
            continue

        cache_key = (pname, model, api_key)
        if cache_key not in _instance_cache:
            _instance_cache[cache_key] = cls(
                api_key=api_key, model=model, base_url=base_url, client=client
            )
        provider_instance = _instance_cache[cache_key]

        entry = RouterEntry(provider_name=pname, model=model, provider=provider_instance)
        entries.append(entry)

        task_type = TIER_TO_TASK.get(tier_name)
        if task_type:
            task_map[task_type] = entry

    if not entries:
        raise ValueError(
            "No valid provider entries could be built from the LLM config store. "
            "Check that each configured tier has a valid provider name and API key."
        )

    return ModelRouter(
        entries=entries,
        strategy="classifier",
        pinned_model=pinned_model,
        task_map=task_map,
    )
