"""Tests for the file-backed LLM config store (llm/config_store.py)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import warnings

import pytest

from mcp_server_buildium.llm.config_store import (
    DEFAULT_BASE_URLS,
    PROVIDERS,
    TIERS,
    LLMConfig,
    LLMConfigStore,
    ProviderEntry,
    TierEntry,
    _decrypt,
    _encrypt,
    _is_masked,
    _make_fernet,
    _mask_key,
    _serialise,
    _deserialise,
    get_store,
    reset_store,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_path() -> str:
    f = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    f.close()
    os.unlink(f.name)  # store doesn't need the file to pre-exist
    return f.name


def _make_store(path: str | None = None, key: str | None = None) -> LLMConfigStore:
    p = path or _tmp_path()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return LLMConfigStore(p, store_key=key)


def _fernet_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def _simple_config() -> LLMConfig:
    return LLMConfig(
        providers={
            "openai": ProviderEntry(provider="openai", api_key="sk-test", base_url=""),
            "anthropic": ProviderEntry(provider="anthropic", api_key="sk-ant-test"),
        },
        tiers={
            "simple": TierEntry(tier="simple", provider="openai", model="gpt-4o-mini"),
            "thinking": TierEntry(tier="thinking", provider="anthropic", model="claude-opus-4-5"),
        },
    )


# ---------------------------------------------------------------------------
# Mask / is_masked helpers
# ---------------------------------------------------------------------------

def test_mask_key_short():
    assert _mask_key("abc") == "***"


def test_mask_key_normal():
    masked = _mask_key("sk-longapikey1234")
    assert masked.startswith("sk")
    assert masked.endswith("1234")
    assert "…" in masked


def test_mask_key_empty():
    assert _mask_key("") == ""


def test_is_masked_sentinel():
    assert _is_masked("****") is True


def test_is_masked_ellipsis():
    assert _is_masked("sk…1234") is True


def test_is_masked_empty():
    assert _is_masked("") is True


def test_is_masked_real_key():
    assert _is_masked("sk-realapikey") is False


# ---------------------------------------------------------------------------
# Fernet encrypt / decrypt
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_roundtrip():
    key = _fernet_key()
    f = _make_fernet(key)
    plaintext = "sk-supersecret"
    ciphertext = _encrypt(plaintext, f)
    assert ciphertext != plaintext
    assert _decrypt(ciphertext, f) == plaintext


def test_decrypt_invalid_token_raises():
    from cryptography.fernet import Fernet
    f1 = _make_fernet(Fernet.generate_key().decode())
    f2 = _make_fernet(Fernet.generate_key().decode())
    ct = _encrypt("secret", f1)
    with pytest.raises(ValueError, match="decrypt"):
        _decrypt(ct, f2)


# ---------------------------------------------------------------------------
# Serialise / deserialise (round-trip, no encryption)
# ---------------------------------------------------------------------------

def test_serialise_deserialise_roundtrip():
    cfg = _simple_config()
    raw = _serialise(cfg)
    assert raw["encrypted"] is False
    assert "api_key" in raw["providers"]["openai"]

    restored = _deserialise(raw)
    assert restored.providers["openai"].api_key == "sk-test"
    assert restored.tiers["simple"].model == "gpt-4o-mini"


def test_serialise_deserialise_roundtrip_encrypted():
    key = _fernet_key()
    f = _make_fernet(key)
    cfg = _simple_config()
    raw = _serialise(cfg, fernet=f)
    assert raw["encrypted"] is True
    assert "api_key_enc" in raw["providers"]["openai"]
    assert "api_key" not in raw["providers"]["openai"]

    restored = _deserialise(raw, fernet=f)
    assert restored.providers["openai"].api_key == "sk-test"


def test_deserialise_ignores_unknown_providers():
    raw = {
        "version": 1,
        "providers": {"unknown": {"api_key": "x"}},
        "tiers": {},
    }
    cfg = _deserialise(raw)
    assert "unknown" not in cfg.providers


def test_deserialise_ignores_unknown_tiers():
    raw = {
        "version": 1,
        "providers": {},
        "tiers": {"bogus": {"provider": "openai", "model": "x"}},
    }
    cfg = _deserialise(raw)
    assert "bogus" not in cfg.tiers


# ---------------------------------------------------------------------------
# Store: load / save round-trip (sync)
# ---------------------------------------------------------------------------

def test_store_load_returns_none_when_no_file():
    path = _tmp_path()
    store = _make_store(path)
    assert store.load() is None


def test_store_save_and_load():
    path = _tmp_path()
    store = _make_store(path)
    cfg = _simple_config()
    asyncio.run(store.save(cfg))
    restored = store.load()
    assert restored is not None
    assert restored.providers["openai"].api_key == "sk-test"
    assert restored.tiers["simple"].provider == "openai"


def test_store_save_and_load_encrypted():
    key = _fernet_key()
    path = _tmp_path()
    store = _make_store(path, key=key)
    cfg = _simple_config()
    asyncio.run(store.save(cfg))

    # Raw file should not contain plaintext key.
    with open(path) as fh:
        raw_text = fh.read()
    assert "sk-test" not in raw_text

    restored = store.load()
    assert restored is not None
    assert restored.providers["openai"].api_key == "sk-test"


def test_store_hot_reload():
    path = _tmp_path()
    store = _make_store(path)
    cfg = _simple_config()
    asyncio.run(store.save(cfg))

    first = store.load()
    # Load again — should return the cached object (same identity).
    second = store.load()
    assert first is second

    # Modify the file directly to simulate an external update.
    with open(path) as fh:
        raw = json.load(fh)
    raw["tiers"]["simple"]["model"] = "gpt-4o"
    with open(path, "w") as fh:
        json.dump(raw, fh)

    third = store.load()
    assert third is not None
    assert third.tiers["simple"].model == "gpt-4o"


def test_store_atomic_save_no_partial_reads(tmp_path):
    """The tmp file must not outlast a successful save."""
    path = str(tmp_path / "llm_config.json")
    store = _make_store(path)
    asyncio.run(store.save(_simple_config()))
    tmp = path + ".tmp"
    assert not os.path.exists(tmp), "tmp file left after save"


# ---------------------------------------------------------------------------
# update_tier
# ---------------------------------------------------------------------------

def test_update_tier_creates_entry():
    path = _tmp_path()
    store = _make_store(path)
    # Start with an empty store.
    result = asyncio.run(store.update_tier("simple", "openai", "gpt-4o-mini"))
    assert result.tiers["simple"].provider == "openai"
    assert result.tiers["simple"].model == "gpt-4o-mini"


def test_update_tier_updates_existing():
    path = _tmp_path()
    store = _make_store(path)
    asyncio.run(store.save(_simple_config()))
    result = asyncio.run(store.update_tier("simple", "anthropic", "claude-3-5-haiku"))
    assert result.tiers["simple"].provider == "anthropic"
    assert result.tiers["simple"].model == "claude-3-5-haiku"
    # Other tiers should be unchanged.
    assert result.tiers["thinking"].model == "claude-opus-4-5"


# ---------------------------------------------------------------------------
# update_from_body: masked key preservation
# ---------------------------------------------------------------------------

def test_update_from_body_preserves_masked_key():
    path = _tmp_path()
    store = _make_store(path)
    existing = _simple_config()  # openai key = "sk-test"
    asyncio.run(store.save(existing))

    # Simulate a PUT body with a masked key.
    display = store.to_display(existing)
    masked_key = display["providers"]["openai"]["api_key_masked"]
    body = {
        "providers": {
            "openai": {"api_key": masked_key, "base_url": "", "enabled": True},
        },
        "tiers": {"simple": {"provider": "openai", "model": "gpt-4o"}},
    }
    updated = asyncio.run(store.update_from_body(body, existing))
    # The real key must be preserved.
    assert updated.providers["openai"].api_key == "sk-test"
    # The model should be updated.
    assert updated.tiers["simple"].model == "gpt-4o"


def test_update_from_body_updates_real_key():
    path = _tmp_path()
    store = _make_store(path)
    existing = _simple_config()
    asyncio.run(store.save(existing))
    body = {
        "providers": {
            "openai": {"api_key": "sk-new-key", "base_url": "", "enabled": True},
        },
        "tiers": {},
    }
    updated = asyncio.run(store.update_from_body(body, existing))
    assert updated.providers["openai"].api_key == "sk-new-key"


def test_update_from_body_ignores_unknown_providers():
    path = _tmp_path()
    store = _make_store(path)
    body = {
        "providers": {"notaprovider": {"api_key": "x"}},
        "tiers": {},
    }
    result = asyncio.run(store.update_from_body(body, None))
    assert "notaprovider" not in result.providers


# ---------------------------------------------------------------------------
# to_display: keys are masked
# ---------------------------------------------------------------------------

def test_to_display_masks_keys():
    path = _tmp_path()
    store = _make_store(path)
    cfg = _simple_config()
    display = store.to_display(cfg)
    masked = display["providers"]["openai"]["api_key_masked"]
    assert "sk-test" not in masked
    assert _is_masked(masked)


def test_to_display_includes_tiers():
    path = _tmp_path()
    store = _make_store(path)
    cfg = _simple_config()
    display = store.to_display(cfg)
    assert display["tiers"]["simple"]["model"] == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# LLMConfig helper methods
# ---------------------------------------------------------------------------

def test_llm_config_is_configured_false_when_empty():
    cfg = LLMConfig()
    assert cfg.is_configured() is False


def test_llm_config_is_configured_true_when_tier_set():
    cfg = _simple_config()
    assert cfg.is_configured() is True


def test_get_configured_tiers_filters_incomplete():
    cfg = LLMConfig(
        tiers={
            "simple": TierEntry(tier="simple", provider="openai", model="gpt-4o"),
            "thinking": TierEntry(tier="thinking", provider="", model=""),
        }
    )
    configured = cfg.get_configured_tiers()
    assert "simple" in configured
    assert "thinking" not in configured


# ---------------------------------------------------------------------------
# Singleton get_store / reset_store
# ---------------------------------------------------------------------------

def test_get_store_returns_none_when_path_empty():
    from unittest.mock import MagicMock
    reset_store()
    cfg = MagicMock()
    cfg.llm_config_path = ""
    cfg.llm_store_key = None
    assert get_store(cfg) is None


def test_get_store_returns_store_when_path_set():
    from unittest.mock import MagicMock
    reset_store()
    path = _tmp_path()
    cfg = MagicMock()
    cfg.llm_config_path = path
    cfg.llm_store_key = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        store = get_store(cfg)
    assert store is not None
    assert store.path == path
    reset_store()


def test_get_store_singleton():
    from unittest.mock import MagicMock
    reset_store()
    path = _tmp_path()
    cfg = MagicMock()
    cfg.llm_config_path = path
    cfg.llm_store_key = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        s1 = get_store(cfg)
        s2 = get_store(cfg)
    assert s1 is s2
    reset_store()


# ---------------------------------------------------------------------------
# seed_from_buildium_config
# ---------------------------------------------------------------------------

def test_seed_from_buildium_config_single_provider():
    from mcp_server_buildium.config import BuildiumConfig
    cfg = BuildiumConfig(
        client_id="cid",
        client_secret="secret",
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        llm_openai_api_key="sk-env",
    )
    llm_cfg = LLMConfigStore.seed_from_buildium_config(cfg)
    assert llm_cfg.providers["openai"].api_key == "sk-env"
    for tier in TIERS:
        assert llm_cfg.tiers[tier].model == "gpt-4o-mini"


def test_seed_from_buildium_config_no_llm():
    from mcp_server_buildium.config import BuildiumConfig
    cfg = BuildiumConfig(client_id="cid", client_secret="secret")
    llm_cfg = LLMConfigStore.seed_from_buildium_config(cfg)
    assert llm_cfg.tiers == {}


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

def test_store_warns_when_no_encryption_key():
    path = _tmp_path()
    with pytest.warns(UserWarning, match="BUILDIUM_LLM_STORE_KEY"):
        LLMConfigStore(path, store_key=None)


def test_store_no_warning_when_key_set():
    key = _fernet_key()
    path = _tmp_path()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        LLMConfigStore(path, store_key=key)  # should not warn
