"""Tests for the admin gate, Microsoft Graph client, and /manage/* routes."""

from __future__ import annotations

import asyncio

import pytest

from mcp_server_buildium.config import BuildiumConfig
from mcp_server_buildium.entra_graph import GraphClient, GraphError
from mcp_server_buildium.security.policy import is_admin_claims

ROLE_MAP = (
    '{"Buildium.Admin":"admin","Buildium.Operator":"operator","Buildium.ReadOnly":"readonly"}'
)
ROLE_ID_MAP = (
    '{"admin":"11111111-1111-1111-1111-111111111111",'
    '"operator":"22222222-2222-2222-2222-222222222222",'
    '"readonly":"33333333-3333-3333-3333-333333333333"}'
)


def _cfg(**kwargs) -> BuildiumConfig:
    base = {
        "client_id": "id",
        "client_secret": "secret",
        "entra_tenant_id": "tenant",
        "entra_audience": "api://app",
    }
    base.update(kwargs)
    return BuildiumConfig(**base)


# --- admin gate ------------------------------------------------------------
def test_is_admin_true_for_admin_role_claim() -> None:
    cfg = _cfg(entra_role_policy_map=ROLE_MAP)
    assert is_admin_claims(cfg, {"roles": ["Buildium.Admin"]}) is True


def test_is_admin_false_for_operator_and_readonly() -> None:
    cfg = _cfg(entra_role_policy_map=ROLE_MAP)
    assert is_admin_claims(cfg, {"roles": ["Buildium.Operator"]}) is False
    assert is_admin_claims(cfg, {"roles": ["Buildium.ReadOnly"]}) is False


def test_is_admin_false_for_unmapped_claims() -> None:
    cfg = _cfg(entra_role_policy_map=ROLE_MAP)
    assert is_admin_claims(cfg, {"roles": ["Something.Else"]}) is False
    assert is_admin_claims(cfg, {}) is False


def test_is_admin_falls_back_to_server_role_without_map() -> None:
    # No role map: the server-wide role decides. Default role is admin.
    assert is_admin_claims(_cfg(), {}) is True
    assert is_admin_claims(_cfg(role="operator"), {}) is False


# --- config validation -----------------------------------------------------
def test_management_enabled_without_graph_warns_but_starts() -> None:
    # Enabling management without full Graph config must NOT crash startup:
    # the admin UI + LLM-config routes still work; only user-management is off.
    with pytest.warns(UserWarning, match="user-management"):
        cfg = _cfg(management_enabled=True, entra_app_role_id_map=ROLE_ID_MAP)
    assert cfg.management_active() is True
    assert cfg.graph_management_configured() is False


def test_management_valid_config() -> None:
    cfg = _cfg(
        management_enabled=True,
        graph_client_id="graph-app",
        graph_client_secret="graph-secret",
        entra_api_service_principal_id="sp-object-id",
        entra_app_role_id_map=ROLE_ID_MAP,
    )
    assert cfg.management_active() is True
    assert cfg.graph_management_configured() is True
    assert cfg.get_graph_tenant_id() == "tenant"
    assert cfg.get_entra_app_role_id_map()["admin"].startswith("1111")
    assert cfg.get_app_role_id_to_role()["11111111-1111-1111-1111-111111111111"] == "admin"


def test_app_role_id_map_rejects_unknown_role() -> None:
    with pytest.raises(ValueError, match="APP_ROLE_ID_MAP key"):
        _cfg(entra_app_role_id_map='{"superuser":"x"}')


# --- Graph client (mocked transport) --------------------------------------
class _FakeResponse:
    def __init__(self, status_code: int, payload, *, content: bool = True) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"fake-body" if content else b""

    def json(self):
        return self._payload


class _FakeClient:
    """Records requests and returns queued responses by (method, url-substring)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.token_response = _FakeResponse(200, {"access_token": "t", "expires_in": 3600})
        self.handlers: list = []

    async def post(self, url, data=None, headers=None):  # token endpoint
        self.calls.append(("POST-token", url, data))
        return self.token_response

    async def request(self, method, url, json=None, headers=None):
        self.calls.append((method, url, json))
        handler = self.handlers.pop(0)
        return handler(method, url, json)


def _graph_cfg() -> BuildiumConfig:
    return _cfg(
        management_enabled=True,
        graph_client_id="graph-app",
        graph_client_secret="graph-secret",
        entra_api_service_principal_id="sp-1",
        entra_app_role_id_map=ROLE_ID_MAP,
    )


def test_graph_invite_and_assign_role() -> None:
    fake = _FakeClient()
    fake.handlers = [
        lambda m, u, j: _FakeResponse(
            201, {"status": "PendingAcceptance", "invitedUser": {"id": "user-9"}}
        ),
        lambda m, u, j: _FakeResponse(201, {"id": "assignment-1"}),
    ]
    client = GraphClient(_graph_cfg(), fake)

    async def run():
        invited = await client.invite_guest("guest@example.com")
        assert invited["id"] == "user-9"
        assigned = await client.assign_app_role(invited["id"], "operator")
        return assigned

    assigned = asyncio.run(run())
    assert assigned["id"] == "assignment-1"
    # Invitation payload carried the email and the assignment used the operator id.
    invite_call = next(c for c in fake.calls if c[0] == "POST" and "invitations" in c[1])
    assert invite_call[2]["invitedUserEmailAddress"] == "guest@example.com"
    assign_call = next(c for c in fake.calls if "appRoleAssignedTo" in c[1])
    assert assign_call[2]["appRoleId"] == "22222222-2222-2222-2222-222222222222"


def test_graph_unknown_role_raises() -> None:
    fake = _FakeClient()
    client = GraphClient(_graph_cfg(), fake)

    async def run():
        await client.assign_app_role("user-9", "wizard")

    with pytest.raises(GraphError) as exc:
        asyncio.run(run())
    assert exc.value.code == "unknown_role"


def test_graph_list_users_maps_roles() -> None:
    fake = _FakeClient()
    fake.handlers = [
        lambda m, u, j: _FakeResponse(
            200,
            {
                "value": [
                    {
                        "id": "a1",
                        "principalId": "user-1",
                        "principalType": "User",
                        "principalDisplayName": "Ada",
                        "appRoleId": "11111111-1111-1111-1111-111111111111",
                    },
                    {
                        "id": "g1",
                        "principalId": "grp-1",
                        "principalType": "Group",
                        "appRoleId": "22222222-2222-2222-2222-222222222222",
                    },
                ]
            },
        ),
    ]
    client = GraphClient(_graph_cfg(), fake)
    users = asyncio.run(client.list_users_with_roles())
    assert len(users) == 1  # group assignment filtered out
    assert users[0]["role"] == "admin"
    assert users[0]["display_name"] == "Ada"


def test_graph_set_user_role_removes_old_assignment() -> None:
    fake = _FakeClient()
    fake.handlers = [
        # list existing
        lambda m, u, j: _FakeResponse(
            200,
            {
                "value": [
                    {
                        "id": "old-1",
                        "principalId": "user-1",
                        "appRoleId": "33333333-3333-3333-3333-333333333333",
                    }
                ]
            },
        ),
        # create new
        lambda m, u, j: _FakeResponse(201, {"id": "new-1"}),
        # delete old
        lambda m, u, j: _FakeResponse(204, None, content=False),
    ]
    client = GraphClient(_graph_cfg(), fake)
    created = asyncio.run(client.set_user_role("user-1", "admin"))
    assert created["id"] == "new-1"
    delete_calls = [c for c in fake.calls if c[0] == "DELETE"]
    assert delete_calls and "old-1" in delete_calls[0][1]


def test_graph_http_error_becomes_graph_error() -> None:
    fake = _FakeClient()
    fake.handlers = [
        lambda m, u, j: _FakeResponse(
            403, {"error": {"code": "Authorization_RequestDenied", "message": "denied"}}
        ),
    ]
    client = GraphClient(_graph_cfg(), fake)

    async def run():
        await client.list_app_role_assignments()

    with pytest.raises(GraphError) as exc:
        asyncio.run(run())
    assert exc.value.status == 403
    assert exc.value.code == "Authorization_RequestDenied"


# --- HTTP routes (fresh app, stub verifier + mocked Graph) -----------------
class _StubVerifier:
    """Maps opaque bearer tokens to fixed claims for testing."""

    def __init__(self, tokens: dict[str, dict]) -> None:
        self._tokens = tokens

    async def verify_token(self, token):
        claims = self._tokens.get(token)
        if claims is None:
            return None

        class _Result:
            pass

        r = _Result()
        r.claims = claims
        return r


class _StubGraph:
    def __init__(self, *a, **k) -> None:
        pass

    async def list_users_with_roles(self):
        return [{"user_id": "u1", "display_name": "Ada", "role": "operator"}]

    async def invite_guest(self, email):
        return {"id": "user-new", "email": email, "status": "PendingAcceptance"}

    async def assign_app_role(self, user_id, role):
        return {"id": "assign-1"}

    async def set_user_role(self, user_id, role):
        return {"id": "assign-2"}


@pytest.fixture()
def manage_client(monkeypatch, tmp_path):
    from fastmcp import FastMCP
    from starlette.testclient import TestClient

    from mcp_server_buildium import management_endpoint

    # A prebuilt Chrome archive on disk so the download route can serve it.
    archive = tmp_path / "chrome.zip"
    archive.write_bytes(b"PK\x03\x04 fake-zip")

    cfg = _cfg(
        entra_role_policy_map=ROLE_MAP,
        management_enabled=True,
        graph_client_id="graph-app",
        graph_client_secret="graph-secret",
        entra_api_service_principal_id="sp-1",
        entra_app_role_id_map=ROLE_ID_MAP,
        management_extension_chrome_path=str(archive),
    )
    verifier = _StubVerifier(
        {
            "admin-token": {"roles": ["Buildium.Admin"]},
            "operator-token": {"roles": ["Buildium.Operator"]},
        }
    )
    monkeypatch.setattr(management_endpoint, "GraphClient", _StubGraph)

    mcp = FastMCP("test")
    management_endpoint.register_management_routes(mcp, cfg, verifier, None)
    app = mcp.http_app(path="/mcp")
    with TestClient(app) as tc:
        yield tc


@pytest.fixture()
def manage_client_with_llm(monkeypatch, tmp_path):
    """Like manage_client but with an LLM config store path set."""
    import warnings

    from fastmcp import FastMCP
    from starlette.testclient import TestClient

    from mcp_server_buildium import management_endpoint
    from mcp_server_buildium.llm.config_store import reset_store

    archive = tmp_path / "chrome.zip"
    archive.write_bytes(b"PK\x03\x04 fake-zip")
    llm_store_path = str(tmp_path / "llm_config.json")

    cfg = _cfg(
        entra_role_policy_map=ROLE_MAP,
        management_enabled=True,
        graph_client_id="graph-app",
        graph_client_secret="graph-secret",
        entra_api_service_principal_id="sp-1",
        entra_app_role_id_map=ROLE_ID_MAP,
        management_extension_chrome_path=str(archive),
        llm_config_path=llm_store_path,
    )
    verifier = _StubVerifier(
        {
            "admin-token": {"roles": ["Buildium.Admin"]},
            "operator-token": {"roles": ["Buildium.Operator"]},
        }
    )
    monkeypatch.setattr(management_endpoint, "GraphClient", _StubGraph)
    reset_store()

    mcp = FastMCP("test")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        management_endpoint.register_management_routes(mcp, cfg, verifier, None)
    app = mcp.http_app(path="/mcp")
    with TestClient(app) as tc:
        yield tc

    reset_store()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


def test_route_requires_auth(manage_client) -> None:
    assert manage_client.get("/manage/users").status_code == 401


def test_route_forbids_non_admin(manage_client) -> None:
    resp = manage_client.get("/manage/users", headers=_auth("operator-token"))
    assert resp.status_code == 403


def test_route_lists_users_for_admin(manage_client) -> None:
    resp = manage_client.get("/manage/users", headers=_auth("admin-token"))
    assert resp.status_code == 200
    assert resp.json()["users"][0]["display_name"] == "Ada"


def test_route_invites_user(manage_client) -> None:
    resp = manage_client.post(
        "/manage/users",
        headers=_auth("admin-token"),
        json={"email": "guest@example.com", "role": "operator"},
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["id"] == "user-new"


def test_route_invite_rejects_bad_role(manage_client) -> None:
    resp = manage_client.post(
        "/manage/users",
        headers=_auth("admin-token"),
        json={"email": "guest@example.com", "role": "wizard"},
    )
    assert resp.status_code == 400


def test_route_invite_rejects_bad_email(manage_client) -> None:
    resp = manage_client.post(
        "/manage/users",
        headers=_auth("admin-token"),
        json={"email": "not-an-email", "role": "operator"},
    )
    assert resp.status_code == 400


def test_route_edits_role(manage_client) -> None:
    resp = manage_client.patch(
        "/manage/users/user-1/role",
        headers=_auth("admin-token"),
        json={"role": "admin"},
    )
    assert resp.status_code == 200
    assert resp.json()["user"] == {"id": "user-1", "role": "admin"}


def test_route_capabilities_reports_admin(manage_client) -> None:
    admin = manage_client.get("/manage/capabilities", headers=_auth("admin-token")).json()
    assert admin["enabled"] is True
    assert admin["isAdmin"] is True
    assert admin["roles"] == ["admin", "operator", "readonly"]
    assert admin["extensionBrowsers"] == ["chrome"]
    assert "llmConfigured" in admin  # present regardless of store config
    op = manage_client.get("/manage/capabilities", headers=_auth("operator-token")).json()
    assert op["isAdmin"] is False


def test_route_downloads_chrome_extension(manage_client) -> None:
    resp = manage_client.get("/manage/extension?browser=chrome", headers=_auth("admin-token"))
    assert resp.status_code == 200
    assert resp.content.startswith(b"PK")
    assert "attachment" in resp.headers.get("content-disposition", "")


def test_route_download_missing_firefox_is_503(manage_client) -> None:
    resp = manage_client.get("/manage/extension?browser=firefox", headers=_auth("admin-token"))
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Admin UI (GET /manage/)
# ---------------------------------------------------------------------------

def test_admin_ui_returns_html(manage_client) -> None:
    resp = manage_client.get("/manage/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "<html" in resp.text.lower()


def test_admin_ui_disabled_when_management_off(monkeypatch, tmp_path) -> None:
    import warnings
    from fastmcp import FastMCP
    from starlette.testclient import TestClient
    from mcp_server_buildium import management_endpoint

    cfg = _cfg(management_enabled=False)
    verifier = _StubVerifier({})
    monkeypatch.setattr(management_endpoint, "GraphClient", _StubGraph)
    mcp = FastMCP("test")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        management_endpoint.register_management_routes(mcp, cfg, verifier, None)
    app = mcp.http_app(path="/mcp")
    with TestClient(app) as tc:
        resp = tc.get("/manage/")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Capabilities: llmConfigured field
# ---------------------------------------------------------------------------

def test_capabilities_reports_llm_configured_false_without_store(manage_client) -> None:
    resp = manage_client.get("/manage/capabilities", headers=_auth("admin-token"))
    assert resp.status_code == 200
    data = resp.json()
    assert "llmConfigured" in data
    assert data["llmConfigured"] is False


def test_capabilities_reports_llm_configured_true_when_store_has_tiers(
    manage_client_with_llm,
) -> None:
    # Seed the store via PUT first.
    manage_client_with_llm.put(
        "/manage/llm",
        headers=_auth("admin-token"),
        json={
            "providers": {"openai": {"api_key": "sk-test", "base_url": "", "enabled": True}},
            "tiers": {"simple": {"provider": "openai", "model": "gpt-4o-mini"}},
        },
    )
    resp = manage_client_with_llm.get("/manage/capabilities", headers=_auth("admin-token"))
    assert resp.json()["llmConfigured"] is True


# ---------------------------------------------------------------------------
# GET /manage/llm
# ---------------------------------------------------------------------------

def test_llm_get_requires_admin(manage_client_with_llm) -> None:
    assert manage_client_with_llm.get("/manage/llm").status_code == 401
    assert manage_client_with_llm.get(
        "/manage/llm", headers=_auth("operator-token")
    ).status_code == 403


def test_llm_get_returns_empty_config_initially(manage_client_with_llm) -> None:
    resp = manage_client_with_llm.get("/manage/llm", headers=_auth("admin-token"))
    assert resp.status_code == 200
    data = resp.json()
    assert "tiers" in data
    assert "providers" in data


def test_llm_get_503_when_no_store(monkeypatch, tmp_path) -> None:
    """GET /manage/llm returns 503 when the LLM store path is explicitly empty."""
    import warnings
    from fastmcp import FastMCP
    from starlette.testclient import TestClient
    from mcp_server_buildium import management_endpoint
    from mcp_server_buildium.llm.config_store import reset_store

    archive = tmp_path / "chrome.zip"
    archive.write_bytes(b"PK\x03\x04 fake-zip")
    cfg = _cfg(
        entra_role_policy_map=ROLE_MAP,
        management_enabled=True,
        graph_client_id="graph-app",
        graph_client_secret="graph-secret",
        entra_api_service_principal_id="sp-1",
        entra_app_role_id_map=ROLE_ID_MAP,
        management_extension_chrome_path=str(archive),
        llm_config_path="",  # explicitly disabled
    )
    verifier = _StubVerifier({"admin-token": {"roles": ["Buildium.Admin"]}})
    monkeypatch.setattr(management_endpoint, "GraphClient", _StubGraph)
    reset_store()

    mcp = FastMCP("test")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        management_endpoint.register_management_routes(mcp, cfg, verifier, None)
    app = mcp.http_app(path="/mcp")
    with TestClient(app) as tc:
        resp = tc.get("/manage/llm", headers=_auth("admin-token"))
    assert resp.status_code == 503
    reset_store()


# ---------------------------------------------------------------------------
# PUT /manage/llm
# ---------------------------------------------------------------------------

def test_llm_put_saves_config(manage_client_with_llm) -> None:
    body = {
        "providers": {"openai": {"api_key": "sk-test", "base_url": "", "enabled": True}},
        "tiers": {"simple": {"provider": "openai", "model": "gpt-4o-mini"}},
    }
    resp = manage_client_with_llm.put(
        "/manage/llm", headers=_auth("admin-token"), json=body
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tiers"]["simple"]["model"] == "gpt-4o-mini"
    # Key must be masked in response.
    assert "sk-test" not in str(data)


def test_llm_put_requires_admin(manage_client_with_llm) -> None:
    assert manage_client_with_llm.put(
        "/manage/llm", headers=_auth("operator-token"), json={}
    ).status_code == 403


def test_llm_put_preserves_masked_key(manage_client_with_llm) -> None:
    # First, save a real key.
    manage_client_with_llm.put(
        "/manage/llm",
        headers=_auth("admin-token"),
        json={
            "providers": {"openai": {"api_key": "sk-secret", "base_url": "", "enabled": True}},
            "tiers": {},
        },
    )
    # Get the masked display.
    get_resp = manage_client_with_llm.get("/manage/llm", headers=_auth("admin-token"))
    masked = get_resp.json()["providers"]["openai"]["api_key_masked"]

    # PUT back with the masked value — key must not be cleared.
    put_resp = manage_client_with_llm.put(
        "/manage/llm",
        headers=_auth("admin-token"),
        json={
            "providers": {"openai": {"api_key": masked, "base_url": "", "enabled": True}},
            "tiers": {"thinking": {"provider": "openai", "model": "gpt-4o"}},
        },
    )
    assert put_resp.status_code == 200
    # If the key was accidentally cleared, GET would show an empty masked string.
    second_get = manage_client_with_llm.get("/manage/llm", headers=_auth("admin-token"))
    new_masked = second_get.json()["providers"]["openai"]["api_key_masked"]
    assert new_masked  # not empty — key was preserved


# ---------------------------------------------------------------------------
# PATCH /manage/llm/tier/{tier}
# ---------------------------------------------------------------------------

def test_llm_tier_patch_updates_tier(manage_client_with_llm) -> None:
    resp = manage_client_with_llm.patch(
        "/manage/llm/tier/simple",
        headers=_auth("admin-token"),
        json={"provider": "openai", "model": "gpt-4o"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tiers"]["simple"]["model"] == "gpt-4o"


def test_llm_tier_patch_rejects_invalid_tier(manage_client_with_llm) -> None:
    resp = manage_client_with_llm.patch(
        "/manage/llm/tier/bogus",
        headers=_auth("admin-token"),
        json={"provider": "openai", "model": "gpt-4o"},
    )
    assert resp.status_code == 400


def test_llm_tier_patch_rejects_invalid_provider(manage_client_with_llm) -> None:
    resp = manage_client_with_llm.patch(
        "/manage/llm/tier/simple",
        headers=_auth("admin-token"),
        json={"provider": "unknown", "model": "some-model"},
    )
    assert resp.status_code == 400


def test_llm_tier_patch_requires_admin(manage_client_with_llm) -> None:
    assert manage_client_with_llm.patch(
        "/manage/llm/tier/simple",
        headers=_auth("operator-token"),
        json={"provider": "openai", "model": "gpt-4o"},
    ).status_code == 403


# ---------------------------------------------------------------------------
# POST /manage/llm/test
# ---------------------------------------------------------------------------

def test_llm_test_rejects_missing_provider(manage_client_with_llm) -> None:
    resp = manage_client_with_llm.post(
        "/manage/llm/test",
        headers=_auth("admin-token"),
        json={"provider": "unknown", "api_key": "x"},
    )
    assert resp.status_code == 400


def test_llm_test_rejects_missing_key(manage_client_with_llm) -> None:
    resp = manage_client_with_llm.post(
        "/manage/llm/test",
        headers=_auth("admin-token"),
        json={"provider": "openai", "api_key": ""},
    )
    assert resp.status_code == 400


def test_llm_test_requires_admin(manage_client_with_llm) -> None:
    assert manage_client_with_llm.post(
        "/manage/llm/test",
        headers=_auth("operator-token"),
        json={"provider": "openai", "api_key": "sk-x"},
    ).status_code == 403


def test_llm_test_uses_stored_key_when_no_key_provided(
    manage_client_with_llm, monkeypatch
) -> None:
    import asyncio
    from mcp_server_buildium import management_endpoint

    # Pre-store a key.
    manage_client_with_llm.put(
        "/manage/llm",
        headers=_auth("admin-token"),
        json={
            "providers": {"openai": {"api_key": "sk-stored", "base_url": "", "enabled": True}},
            "tiers": {},
        },
    )

    called_with = {}

    async def _mock_test(provider, api_key, base_url):
        called_with["provider"] = provider
        called_with["api_key"] = api_key
        return True, "ok"

    monkeypatch.setattr(management_endpoint, "_test_provider", _mock_test)

    resp = manage_client_with_llm.post(
        "/manage/llm/test",
        headers=_auth("admin-token"),
        json={"provider": "openai", "api_key": ""},
    )
    # Should succeed using the stored key, not fail with "no key provided".
    assert resp.status_code == 200
    assert called_with.get("api_key") == "sk-stored"


# --- admin UI page (Content-Security-Policy) -------------------------------
def test_admin_ui_served_with_nonce_csp(manage_client) -> None:
    """The /manage page must ship a nonce-based CSP that allows its own inline
    <style>/<script> so the LLM configuration UI actually runs in the browser."""
    resp = manage_client.get("/manage")
    assert resp.status_code == 200

    csp = resp.headers.get("content-security-policy")
    assert csp is not None
    # The global "default-src 'none'" (set by the security middleware) alone
    # would block the page; this route must supply script/style nonces.
    assert "script-src 'nonce-" in csp
    assert "style-src 'nonce-" in csp
    assert "connect-src 'self'" in csp

    # The nonce in the header must match the one on the inline tags.
    import re as _re

    match = _re.search(r"script-src 'nonce-([^']+)'", csp)
    assert match is not None, csp
    nonce = match.group(1)
    body = resp.text
    assert f'<style nonce="{nonce}">' in body
    assert f'<script nonce="{nonce}">' in body


def test_admin_ui_has_no_inline_handlers_or_styles(manage_client) -> None:
    """CSP with nonces (no 'unsafe-inline') blocks inline event handlers and
    inline style attributes, so the markup must not contain any."""
    body = manage_client.get("/manage").text
    assert "onclick=" not in body
    assert "style=" not in body


def test_admin_ui_nonce_is_per_request(manage_client) -> None:
    first = manage_client.get("/manage").headers["content-security-policy"]
    second = manage_client.get("/manage").headers["content-security-policy"]
    assert first != second
