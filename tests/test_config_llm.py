"""Tests for the server-side LLM configuration and dev auth bypass."""

from __future__ import annotations

import pytest

from mcp_server_buildium.config import BuildiumConfig

BASE = {"client_id": "cid", "client_secret": "secret"}


def _cfg(**overrides) -> BuildiumConfig:
    return BuildiumConfig(**{**BASE, **overrides})


def test_llm_disabled_by_default() -> None:
    cfg = _cfg()
    assert cfg.llm_enabled() is False
    assert cfg.get_llm_provider() is None
    assert cfg.get_llm_models() == []


def test_llm_openai_enabled() -> None:
    cfg = _cfg(llm_provider="openai", llm_model="gpt-4o-mini", llm_openai_api_key="sk-x")
    assert cfg.llm_enabled() is True
    assert cfg.get_llm_provider() == "openai"
    assert cfg.get_active_llm_key() == "sk-x"
    assert cfg.get_llm_base_url() == "https://api.openai.com/v1"
    assert cfg.get_llm_models() == ["gpt-4o-mini"]
    assert cfg.is_llm_model_allowed("gpt-4o-mini") is True
    assert cfg.is_llm_model_allowed("other") is False


def test_llm_allow_list() -> None:
    cfg = _cfg(
        llm_provider="anthropic",
        llm_model="claude-3-5-sonnet",
        llm_anthropic_api_key="k",
        llm_allowed_models="claude-3-5-sonnet, claude-3-haiku",
    )
    assert cfg.get_llm_models() == ["claude-3-5-sonnet", "claude-3-haiku"]
    assert cfg.is_llm_model_allowed("claude-3-haiku") is True


def test_llm_unknown_provider_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown BUILDIUM_LLM_PROVIDER"):
        _cfg(llm_provider="mistral", llm_model="m")


def test_llm_missing_model_rejected() -> None:
    with pytest.raises(ValueError, match="BUILDIUM_LLM_MODEL is required"):
        _cfg(llm_provider="openai", llm_openai_api_key="sk-x")


def test_llm_missing_key_rejected() -> None:
    with pytest.raises(ValueError, match="API key is required"):
        _cfg(llm_provider="gemini", llm_model="gemini-1.5-flash")


def test_llm_model_must_be_in_allow_list() -> None:
    with pytest.raises(ValueError, match="member of BUILDIUM_LLM_ALLOWED_MODELS"):
        _cfg(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            llm_openai_api_key="sk-x",
            llm_allowed_models="gpt-4o",
        )


def test_dev_auth_bypass_disables_auth() -> None:
    from mcp_server_buildium.auth import build_auth

    cfg = _cfg(entra_tenant_id="tid", entra_audience="api://x", dev_auth_bypass=True)
    # Even with Entra configured, the bypass returns no verifier.
    assert build_auth(cfg) is None


def test_default_system_prompt_present() -> None:
    cfg = _cfg()
    assert "property-management assistant" in cfg.get_llm_system_prompt()
    custom = _cfg(llm_system_prompt="Be terse.")
    assert custom.get_llm_system_prompt() == "Be terse."
