"""File-backed LLM configuration store with optional Fernet encryption.

The store persists per-tier model assignments and provider API keys to a JSON
file on disk. It is the **primary** LLM configuration source when the admin UI
is used; the legacy ``BUILDIUM_LLM_*`` environment variables are only consulted
as a one-time migration seed when the store file does not yet exist.

Encryption
----------
When ``BUILDIUM_LLM_STORE_KEY`` is set to a Fernet key (generate one with
``python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"``)
all API keys in the JSON file are stored encrypted. If the env var is absent,
keys are stored in plaintext and a startup warning is emitted.

Hot-reload
----------
:class:`LLMConfigStore` caches the parsed config together with the file's
modification time. A subsequent :meth:`~LLMConfigStore.load` call re-reads the
file only when the mtime has changed, making the per-request overhead negligible.

Model tiers
-----------
Four configurable tiers map to the router's internal task-type classifications:

+----------+-------------------------------+------------------------------+
| Tier     | Task type (router)            | Typical use                  |
+==========+===============================+==============================+
| simple   | creative / conversational     | Short queries, drafting      |
+----------+-------------------------------+------------------------------+
| thinking | reasoning / financial         | Analysis, compliance         |
+----------+-------------------------------+------------------------------+
| agentic  | agentic / multi-tool          | Portfolio-wide operations    |
+----------+-------------------------------+------------------------------+
| artifact | extraction / document         | PDF/DOCX extraction, export  |
+----------+-------------------------------+------------------------------+
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import warnings
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import BuildiumConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIERS: frozenset[str] = frozenset({"simple", "thinking", "agentic", "artifact"})
PROVIDERS: frozenset[str] = frozenset({"openai", "anthropic", "gemini"})

# Maps UI tier name → router task-type constant (matches router.py).
TIER_TO_TASK: dict[str, str] = {
    "simple": "creative",
    "thinking": "reasoning",
    "agentic": "agentic",
    "artifact": "extraction",
}

# Canonical default base URLs per provider.
DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}

_STORE_VERSION = 1

# Sentinel used in PUT/PATCH bodies to mean "keep existing key".
_MASKED_SENTINEL = "****"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ProviderEntry:
    """Configuration for a single LLM provider."""

    provider: str
    """Provider identifier: ``"openai"``, ``"anthropic"``, or ``"gemini"``."""
    api_key: str = ""
    """Plaintext API key (never written to disk in this form when encrypted)."""
    base_url: str = ""
    """Base URL override; empty string means use the canonical default."""
    enabled: bool = True
    """Whether this provider is available for selection."""

    def effective_base_url(self) -> str:
        return self.base_url.strip() or DEFAULT_BASE_URLS.get(self.provider, "")


@dataclass
class TierEntry:
    """Model assignment for a single routing tier."""

    tier: str
    """Tier name: ``"simple"``, ``"thinking"``, ``"agentic"``, or ``"artifact"``."""
    provider: str = ""
    """Which provider to use for this tier (must be in :data:`PROVIDERS`)."""
    model: str = ""
    """Model identifier (e.g. ``"gpt-4o-mini"``)."""

    def is_configured(self) -> bool:
        return bool(self.provider.strip() and self.model.strip())


@dataclass
class LLMConfig:
    """Full LLM configuration stored on disk."""

    version: int = _STORE_VERSION
    providers: dict[str, ProviderEntry] = field(default_factory=dict)
    tiers: dict[str, TierEntry] = field(default_factory=dict)

    def is_configured(self) -> bool:
        """Return True when at least one tier is fully configured."""
        return any(t.is_configured() for t in self.tiers.values())

    def get_configured_tiers(self) -> dict[str, TierEntry]:
        return {k: v for k, v in self.tiers.items() if v.is_configured()}


# ---------------------------------------------------------------------------
# Encryption helpers (Fernet)
# ---------------------------------------------------------------------------


def _make_fernet(key_b64: str):  # type: ignore[return]
    """Return a :class:`~cryptography.fernet.Fernet` instance or raise."""
    try:
        from cryptography.fernet import Fernet

        return Fernet(key_b64.strip().encode())
    except ImportError as exc:
        raise RuntimeError(
            "The 'cryptography' package is required for LLM config encryption "
            "(it should already be installed as a transitive dependency)."
        ) from exc
    except Exception as exc:
        raise ValueError(
            "BUILDIUM_LLM_STORE_KEY is not a valid Fernet key. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        ) from exc


def _encrypt(plaintext: str, fernet) -> str:
    return fernet.encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str, fernet) -> str:
    from cryptography.fernet import InvalidToken

    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "Failed to decrypt an LLM API key from the config store. "
            "The BUILDIUM_LLM_STORE_KEY may have changed since the file was written."
        ) from exc


def _mask_key(key: str) -> str:
    """Return a masked version of *key* safe to return in API responses."""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:2] + "…" + key[-4:]


def _is_masked(value: str) -> bool:
    """Return True when *value* looks like a masked key (should not overwrite stored key)."""
    return not value or value == _MASKED_SENTINEL or "…" in value or value.startswith("****")


# ---------------------------------------------------------------------------
# Serialisation / deserialisation
# ---------------------------------------------------------------------------


def _serialise(config: LLMConfig, fernet=None) -> dict:
    """Convert *config* to a JSON-serialisable dict, encrypting keys if *fernet* is set."""
    providers_out: dict = {}
    for pname, pentry in config.providers.items():
        key_raw = pentry.api_key or ""
        if fernet and key_raw:
            key_field = {"api_key_enc": _encrypt(key_raw, fernet)}
        else:
            key_field = {"api_key": key_raw}
        providers_out[pname] = {
            **key_field,
            "base_url": pentry.base_url,
            "enabled": pentry.enabled,
        }

    tiers_out: dict = {}
    for tname, tentry in config.tiers.items():
        tiers_out[tname] = {"provider": tentry.provider, "model": tentry.model}

    return {
        "version": config.version,
        "encrypted": bool(fernet),
        "providers": providers_out,
        "tiers": tiers_out,
    }


def _deserialise(raw: dict, fernet=None) -> LLMConfig:
    """Parse a raw dict (read from disk) into an :class:`LLMConfig`."""
    providers: dict[str, ProviderEntry] = {}
    for pname, pdata in (raw.get("providers") or {}).items():
        if pname not in PROVIDERS:
            continue
        if fernet and "api_key_enc" in pdata:
            try:
                key = _decrypt(pdata["api_key_enc"], fernet)
            except ValueError:
                key = ""
        else:
            key = pdata.get("api_key", "")
        providers[pname] = ProviderEntry(
            provider=pname,
            api_key=key,
            base_url=pdata.get("base_url", ""),
            enabled=bool(pdata.get("enabled", True)),
        )

    tiers: dict[str, TierEntry] = {}
    for tname, tdata in (raw.get("tiers") or {}).items():
        if tname not in TIERS:
            continue
        tiers[tname] = TierEntry(
            tier=tname,
            provider=tdata.get("provider", ""),
            model=tdata.get("model", ""),
        )

    return LLMConfig(
        version=raw.get("version", _STORE_VERSION),
        providers=providers,
        tiers=tiers,
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class LLMConfigStore:
    """File-backed LLM configuration store.

    Thread/async-safe: a :class:`asyncio.Lock` guards concurrent writes.
    Reads use a mtime-based cache and are lock-free (optimistic reads are
    acceptable since config changes are infrequent and a stale read is benign).
    """

    def __init__(self, path: str, *, store_key: str | None = None) -> None:
        self._path = path
        self._fernet = _make_fernet(store_key) if store_key else None
        self._lock = asyncio.Lock()

        # Mtime cache: (mtime_ns, LLMConfig)
        self._cache: tuple[int, LLMConfig] | None = None

        if not store_key:
            warnings.warn(
                "BUILDIUM_LLM_STORE_KEY is not set. LLM API keys in the config store "
                "will be stored in plaintext. Set this variable to a Fernet key for "
                "encryption at rest. Generate one with: python -c "
                '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"',
                stacklevel=2,
            )

    @property
    def path(self) -> str:
        return self._path

    # --- reads ----------------------------------------------------------

    def load(self) -> LLMConfig | None:
        """Load the config from disk (hot-reload: re-reads only when mtime changes).

        Returns ``None`` if the store file does not exist yet.
        """
        try:
            mtime = os.stat(self._path).st_mtime_ns
        except FileNotFoundError:
            return None

        if self._cache is not None and self._cache[0] == mtime:
            return self._cache[1]

        try:
            with open(self._path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to load LLM config store %s: %s", self._path, exc)
            return None

        config = _deserialise(raw, self._fernet)
        self._cache = (mtime, config)
        logger.debug("LLM config store loaded from %s", self._path)
        return config

    def to_display(self, config: LLMConfig) -> dict:
        """Return a dict safe for API responses — keys are masked."""
        providers_out: dict = {}
        for pname, pentry in config.providers.items():
            providers_out[pname] = {
                "api_key_masked": _mask_key(pentry.api_key),
                "base_url": pentry.base_url,
                "enabled": pentry.enabled,
            }
        tiers_out: dict = {}
        for tname, tentry in config.tiers.items():
            tiers_out[tname] = {"provider": tentry.provider, "model": tentry.model}
        return {
            "version": config.version,
            "encrypted": self._fernet is not None,
            "providers": providers_out,
            "tiers": tiers_out,
        }

    # --- writes ---------------------------------------------------------

    async def save(self, config: LLMConfig) -> None:
        """Atomically write *config* to disk under the write lock."""
        async with self._lock:
            await asyncio.to_thread(self._save_sync, config)

    def _save_sync(self, config: LLMConfig) -> None:
        raw = _serialise(config, self._fernet)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, self._path)
        # Bust the cache so the next load re-reads.
        self._cache = None
        logger.info("LLM config store saved to %s", self._path)

    async def update_tier(self, tier: str, provider: str, model: str) -> LLMConfig:
        """Update a single tier and return the updated config."""
        current = self.load() or LLMConfig()
        current.tiers[tier] = TierEntry(tier=tier, provider=provider, model=model)
        await self.save(current)
        return current

    async def update_from_body(self, body: dict, existing: LLMConfig | None) -> LLMConfig:
        """Merge a PUT body into the existing config and save.

        Masked API keys (contains ``"…"`` or ``"****"``) are preserved from
        the existing config rather than overwritten.
        """
        config = existing or LLMConfig()

        # Update providers.
        for pname, pdata in (body.get("providers") or {}).items():
            if pname not in PROVIDERS or not isinstance(pdata, dict):
                continue
            existing_entry = config.providers.get(pname, ProviderEntry(provider=pname))
            raw_key = str(pdata.get("api_key") or "").strip()
            # Preserve existing key when the caller sends a masked placeholder.
            new_key = existing_entry.api_key if _is_masked(raw_key) else raw_key
            config.providers[pname] = ProviderEntry(
                provider=pname,
                api_key=new_key,
                base_url=str(pdata.get("base_url") or "").strip(),
                enabled=bool(pdata.get("enabled", existing_entry.enabled)),
            )

        # Update tiers.
        for tname, tdata in (body.get("tiers") or {}).items():
            if tname not in TIERS or not isinstance(tdata, dict):
                continue
            config.tiers[tname] = TierEntry(
                tier=tname,
                provider=str(tdata.get("provider") or "").strip(),
                model=str(tdata.get("model") or "").strip(),
            )

        await self.save(config)
        return config

    # --- seeding from env config ----------------------------------------

    @classmethod
    def seed_from_buildium_config(cls, buildium_cfg: "BuildiumConfig") -> LLMConfig:
        """Build an :class:`LLMConfig` from legacy env-based ``BuildiumConfig``.

        Used to populate the store on first run when env vars are still set.
        """
        providers: dict[str, ProviderEntry] = {}

        def _add(pname: str, key_attr: str, url_attr: str) -> None:
            key = getattr(buildium_cfg, key_attr, None) or ""
            if key:
                providers[pname] = ProviderEntry(
                    provider=pname,
                    api_key=key,
                    base_url=getattr(buildium_cfg, url_attr, "") or DEFAULT_BASE_URLS[pname],
                )

        _add("openai", "llm_openai_api_key", "llm_openai_base_url")
        _add("anthropic", "llm_anthropic_api_key", "llm_anthropic_base_url")
        _add("gemini", "llm_gemini_api_key", "llm_gemini_base_url")

        tiers: dict[str, TierEntry] = {}

        if buildium_cfg.llm_router_enabled:
            entries = buildium_cfg.get_llm_router_providers() or []
            # Map the first entry to the most general tier, subsequent entries
            # to more specialised tiers in a best-effort ordering.
            ordered_tiers = ["agentic", "thinking", "artifact", "simple"]
            for i, entry in enumerate(entries):
                if i >= len(ordered_tiers):
                    break
                tname = ordered_tiers[i]
                tiers[tname] = TierEntry(
                    tier=tname,
                    provider=entry["provider"],
                    model=entry["model"],
                )
        elif buildium_cfg.llm_provider and buildium_cfg.llm_model:
            pname = buildium_cfg.llm_provider.strip().lower()
            model = buildium_cfg.llm_model.strip()
            for tname in TIERS:
                tiers[tname] = TierEntry(tier=tname, provider=pname, model=model)

        return LLMConfig(providers=providers, tiers=tiers)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_store: LLMConfigStore | None = None
_store_config_key: tuple[str, str | None] | None = None  # (path, store_key)


def get_store(config: "BuildiumConfig") -> LLMConfigStore | None:
    """Return (creating if necessary) the process-wide :class:`LLMConfigStore`.

    Returns ``None`` when ``BUILDIUM_LLM_CONFIG_PATH`` is explicitly set to an
    empty string (opt-out), or when the config object has no path attribute.
    """
    global _store, _store_config_key

    path = getattr(config, "llm_config_path", None) or ""
    path = path.strip()
    if not path:
        return None

    store_key = getattr(config, "llm_store_key", None) or None
    key_tuple = (path, store_key)

    if _store is None or _store_config_key != key_tuple:
        _store = LLMConfigStore(path, store_key=store_key)
        _store_config_key = key_tuple

    return _store


def reset_store() -> None:
    """Clear the global singleton (used in tests)."""
    global _store, _store_config_key
    _store = None
    _store_config_key = None
