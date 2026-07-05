"""Tests for the admin gate, Microsoft Graph client, and /manage/* routes."""

from __future__ import annotations

import asyncio

import pytest

from mcp_server_buildium.config import BuildiumConfig
from mcp_server_buildium.entra_graph import GraphClient, GraphError
from mcp_server_buildium.security.policy import is_admin_claims

ROLE_MAP = (
    '{"Buildium.Admin":"admin","Buildium.Operator":"operator",'
    '"Buildium.ReadOnly":"readonly"}'
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
def test_management_requires_graph_config() -> None:
    with pytest.raises(ValueError, match="GRAPH_CLIENT_ID"):
        _cfg(management_enabled=True, entra_app_role_id_map=ROLE_ID_MAP)


def test_management_valid_config() -> None:
    cfg = _cfg(
        management_enabled=True,
        graph_client_id="graph-app",
        graph_client_secret="graph-secret",
        entra_api_service_principal_id="sp-object-id",
        entra_app_role_id_map=ROLE_ID_MAP,
    )
    assert cfg.management_active() is True
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
    assert admin == {
        "enabled": True,
        "isAdmin": True,
        "roles": ["admin", "operator", "readonly"],
        "extensionBrowsers": ["chrome"],
    }
    op = manage_client.get("/manage/capabilities", headers=_auth("operator-token")).json()
    assert op["isAdmin"] is False


def test_route_downloads_chrome_extension(manage_client) -> None:
    resp = manage_client.get(
        "/manage/extension?browser=chrome", headers=_auth("admin-token")
    )
    assert resp.status_code == 200
    assert resp.content.startswith(b"PK")
    assert "attachment" in resp.headers.get("content-disposition", "")


def test_route_download_missing_firefox_is_503(manage_client) -> None:
    resp = manage_client.get(
        "/manage/extension?browser=firefox", headers=_auth("admin-token")
    )
    assert resp.status_code == 503

