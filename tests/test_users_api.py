"""Admin user CRUD API (cookie-session auth)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sensors_dcs.auth_session import COOKIE_NAME, init_auth
from sensors_dcs.viz import VizHub, create_viz_app


@pytest.fixture()
def auth_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    monkeypatch.delenv("SENSORS_DCS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("SENSORS_DCS_AUTH_USER", "sensors")
    monkeypatch.setenv("SENSORS_DCS_AUTH_PASSWORD", "test-pass-123")
    monkeypatch.setattr("sensors_dcs.users_store.user_data_dir", lambda: tmp_path)

    import sensors_dcs.auth_session as aus
    import sensors_dcs.users_store as us

    us._users = []
    us._users_path = None
    aus._sessions.clear()
    init_auth()

    hub = VizHub()
    app = create_viz_app(hub, lambda: {"ok": True, "agents": {}})
    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"username": "sensors", "password": "test-pass-123"},
    )
    assert login.status_code == 200
    client.cookies.set(COOKIE_NAME, login.cookies[COOKIE_NAME])
    return client, {"username": "sensors", "password": "test-pass-123"}


def test_users_crud_admin_only(auth_client: tuple[TestClient, dict[str, str]]) -> None:
    client, _ = auth_client

    listed = client.get("/api/users")
    assert listed.status_code == 200
    body = listed.json()
    assert body["ok"] is True
    assert any(u["username"] == "sensors" for u in body["users"])

    created = client.post(
        "/api/users",
        json={"username": "op1", "password": "op-pass-456", "role": "operator"},
    )
    assert created.status_code == 200
    assert created.json()["user"]["username"] == "op1"
    assert created.json()["user"]["role"] == "operator"

    patched = client.put("/api/users/op1", json={"role": "guest", "enabled": True})
    assert patched.status_code == 200
    assert patched.json()["user"]["role"] == "guest"

    reset = client.put("/api/users/op1", json={"password": "new-pass-789"})
    assert reset.status_code == 200

    # Non-admin cannot list
    op_login = client.post(
        "/api/auth/login",
        json={"username": "op1", "password": "new-pass-789"},
    )
    assert op_login.status_code == 200
    op_client = TestClient(client.app)
    op_client.cookies.set(COOKIE_NAME, op_login.cookies[COOKIE_NAME])
    denied = op_client.get("/api/users")
    assert denied.status_code == 403

    # Admin can delete
    deleted = client.delete("/api/users/op1")
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True


def test_cannot_delete_self(auth_client: tuple[TestClient, dict[str, str]]) -> None:
    client, creds = auth_client
    r = client.delete(f"/api/users/{creds['username']}")
    assert r.status_code == 400
    assert "yourself" in r.json()["error"]


def test_settings_modal_in_preview() -> None:
    from sensors_dcs.viz import PREVIEW_HTML

    assert 'id="btnSettings"' in PREVIEW_HTML
    assert 'id="settingsModal"' in PREVIEW_HTML
    assert 'data-i18n="settings.title"' in PREVIEW_HTML
    assert 'data-i18n="settings.lang_label"' in PREVIEW_HTML
    assert "/api/users" in PREVIEW_HTML
    assert 'id="btnLogout"' not in PREVIEW_HTML
