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
    prompt = cfg.get_llm_system_prompt()
    assert "property-management assistant" in prompt
    # Steers friendly formatting and clickable, drill-down list rows.
    assert "action:" in prompt
    custom = _cfg(llm_system_prompt="Be terse.")
    assert custom.get_llm_system_prompt() == "Be terse."


# ---------------------------------------------------------------------------
# Router config tests
# ---------------------------------------------------------------------------

import json  # noqa: E402


def _router_cfg(**overrides) -> BuildiumConfig:
    """Build a minimal valid router config."""
    defaults = {
        "llm_router_enabled": True,
        "llm_router_providers": json.dumps(
            [
                {"provider": "anthropic", "model": "claude-sonnet"},
                {"provider": "openai", "model": "gpt-4o"},
            ]
        ),
        "llm_anthropic_api_key": "anthr-k",
        "llm_openai_api_key": "sk-k",
    }
    return _cfg(**{**defaults, **overrides})


def test_router_enabled_llm_enabled():
    cfg = _router_cfg()
    assert cfg.llm_enabled() is True
    assert cfg.get_llm_provider() == "router"


def test_router_get_llm_models_returns_all_configured():
    cfg = _router_cfg()
    assert cfg.get_llm_models() == ["claude-sonnet", "gpt-4o"]


def test_router_is_llm_model_allowed_empty_is_ok():
    cfg = _router_cfg()
    # Empty model = auto-route; always allowed.
    assert cfg.is_llm_model_allowed("") is True


def test_router_is_llm_model_allowed_valid_model():
    cfg = _router_cfg()
    assert cfg.is_llm_model_allowed("claude-sonnet") is True
    assert cfg.is_llm_model_allowed("gpt-4o") is True


def test_router_is_llm_model_allowed_unknown_model():
    cfg = _router_cfg()
    assert cfg.is_llm_model_allowed("unknown-model") is False


def test_router_get_llm_router_providers():
    cfg = _router_cfg()
    providers = cfg.get_llm_router_providers()
    assert providers is not None
    assert len(providers) == 2
    assert providers[0]["provider"] == "anthropic"
    assert providers[0]["model"] == "claude-sonnet"


def test_router_get_llm_router_providers_off():
    cfg = _cfg()  # router disabled
    assert cfg.get_llm_router_providers() is None


def test_router_requires_providers_field():
    with pytest.raises(ValueError, match="BUILDIUM_LLM_ROUTER_PROVIDERS is required"):
        _cfg(llm_router_enabled=True, llm_router_providers=None)


def test_router_providers_must_be_valid_json():
    with pytest.raises(ValueError, match="valid JSON array"):
        _cfg(llm_router_enabled=True, llm_router_providers="not-json")


def test_router_providers_must_be_array():
    with pytest.raises(ValueError, match="non-empty JSON array"):
        _cfg(llm_router_enabled=True, llm_router_providers=json.dumps({}))


def test_router_providers_array_must_be_nonempty():
    with pytest.raises(ValueError, match="non-empty JSON array"):
        _cfg(llm_router_enabled=True, llm_router_providers=json.dumps([]))


def test_router_provider_name_must_be_valid():
    with pytest.raises(ValueError, match="provider.*must be one of"):
        _cfg(
            llm_router_enabled=True,
            llm_router_providers=json.dumps([{"provider": "mistral", "model": "m"}]),
        )


def test_router_model_must_be_nonempty():
    with pytest.raises(ValueError, match="model must be a non-empty string"):
        _cfg(
            llm_router_enabled=True,
            llm_router_providers=json.dumps([{"provider": "openai", "model": ""}]),
            llm_openai_api_key="sk-k",
        )


def test_router_api_key_required_for_each_provider():
    with pytest.raises(ValueError, match="requires.*API_KEY"):
        _cfg(
            llm_router_enabled=True,
            llm_router_providers=json.dumps([{"provider": "anthropic", "model": "claude"}]),
            # No llm_anthropic_api_key supplied.
        )


def test_router_invalid_strategy_rejected():
    with pytest.raises(ValueError, match="BUILDIUM_LLM_ROUTER_STRATEGY must be one of"):
        _router_cfg(llm_router_strategy="round-robin")


def test_router_strategy_fallback_accepted():
    cfg = _router_cfg(llm_router_strategy="fallback")
    assert cfg.llm_router_strategy == "fallback"


def test_router_max_tool_rounds_validated():
    with pytest.raises(ValueError, match="BUILDIUM_LLM_MAX_TOOL_ROUNDS"):
        _router_cfg(llm_max_tool_rounds=0)


def test_router_single_provider_config_not_required():
    """Single-provider BUILDIUM_LLM_PROVIDER/MODEL are irrelevant when router is on."""
    cfg = _router_cfg()
    # No llm_provider or llm_model set — must not raise.
    assert cfg.llm_provider is None
    assert cfg.llm_model is None


def test_router_get_active_llm_key_returns_none():
    """In router mode get_active_llm_key() returns None (keys are per-entry)."""
    cfg = _router_cfg()
    assert cfg.get_active_llm_key() is None


def test_router_get_llm_base_url_returns_none():
    """In router mode get_llm_base_url() returns None."""
    cfg = _router_cfg()
    assert cfg.get_llm_base_url() is None

