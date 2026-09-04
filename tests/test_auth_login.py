"""Auth session + login gate smoke tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sensors_dcs.auth_session import (
    COOKIE_NAME,
    init_auth,
    sanitize_from,
    try_login,
)
from sensors_dcs.viz import VizHub, create_viz_app


@pytest.fixture()
def auth_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENSORS_DCS_AUTH_DISABLED", "1")
    init_auth()


@pytest.fixture()
def auth_enabled_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    monkeypatch.delenv("SENSORS_DCS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("SENSORS_DCS_AUTH_USER", "sensors")
    monkeypatch.setenv("SENSORS_DCS_AUTH_PASSWORD", "test-pass-123")
    monkeypatch.setattr(
        "sensors_dcs.users_store.user_data_dir",
        lambda: tmp_path,
    )
    # Clear users module state by re-init
    import sensors_dcs.users_store as us
    import sensors_dcs.auth_session as aus

    us._users = []
    us._users_path = None
    aus._sessions.clear()
    info = init_auth()
    assert info["enabled"] is True
    return {"username": "sensors", "password": "test-pass-123"}


def test_sanitize_from() -> None:
    assert sanitize_from("/app") == "/app"
    assert sanitize_from("//evil") == "/"
    assert sanitize_from("/login") == "/"
    assert sanitize_from("/login/x") == "/"
    assert sanitize_from("https://x") == "/"


def test_auth_disabled_allows_root(auth_disabled: None) -> None:
    app = create_viz_app(VizHub(), lambda: {"ok": True})
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "数据采集" in r.text or "Collect" in r.text or "sensors-dcs" in r.text


def test_login_page_and_gate(auth_enabled_tmp: dict[str, str]) -> None:
    app = create_viz_app(VizHub(), lambda: {"ok": True})
    client = TestClient(app, follow_redirects=False)
    r = client.get("/")
    assert r.status_code in (302, 307)
    assert "/login" in (r.headers.get("location") or "")

    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert "login-page" in login_page.text
    assert "login-card" in login_page.text

    bad = client.post("/api/auth/login", json={"username": "sensors", "password": "wrong"})
    assert bad.status_code == 401

    ok = client.post(
        "/api/auth/login",
        json={
            "username": auth_enabled_tmp["username"],
            "password": auth_enabled_tmp["password"],
        },
    )
    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert COOKIE_NAME in ok.cookies

    # session unlocks /
    client.cookies.set(COOKIE_NAME, ok.cookies[COOKIE_NAME])
    home = client.get("/")
    assert home.status_code == 200


def test_try_login_empty(auth_enabled_tmp: dict[str, str]) -> None:
    out = try_login("", "")
    assert out["ok"] is False


def test_boot_error_app_login_and_collect_locked(auth_enabled_tmp: dict[str, str]) -> None:
    from sensors_dcs.viz import create_error_app

    app = create_error_app(error="agents must not be empty", config_path="/tmp/bad.yaml")
    client = TestClient(app, follow_redirects=False)

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["boot_error"] is True
    assert health.json()["collect_ok"] is False

    root = client.get("/")
    assert root.status_code in (302, 307)
    assert "/login" in (root.headers.get("location") or "")

    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert "login-page" in login_page.text

    ok = client.post(
        "/api/auth/login",
        json={
            "username": auth_enabled_tmp["username"],
            "password": auth_enabled_tmp["password"],
        },
    )
    assert ok.status_code == 200
    client.cookies.set(COOKIE_NAME, ok.cookies[COOKIE_NAME])

    home = client.get("/")
    assert home.status_code == 200
    assert 'id="tabBtnHome"' in home.text
    assert 'id="tabBtnPost"' in home.text
    assert "bootBanner" in home.text
    assert "switchTab('home')" in home.text or "switchTab(\"home\")" in home.text
    assert 'data-i18n="home.title"' in home.text

    st = client.get("/api/status")
    assert st.status_code == 200
    body = st.json()
    assert body["boot_error"] is True
    assert body["collect_ok"] is False
    assert "agents must not be empty" in (body.get("error") or "")

    pp = client.get("/api/postprocess/defaults")
    assert pp.status_code == 200
    assert pp.json()["ok"] is True

    rec = client.post("/api/record/start")
    assert rec.status_code == 200
    assert rec.json()["ok"] is False
