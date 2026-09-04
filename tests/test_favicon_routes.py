"""Favicon assets must be public and served for web + packaged desktop."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_favicon_assets_exist() -> None:
    from sensors_dcs.static_assets import favicon_path, static_root

    root = static_root()
    assert root.is_dir()
    for name in ("favicon.svg", "favicon.png", "favicon.ico"):
        p = favicon_path(name)
        assert p.is_file(), name
        assert p.stat().st_size > 0


def test_favicon_routes_public(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SENSORS_DCS_AUTH_DISABLED", "1")
    from sensors_dcs.auth_session import init_auth
    from sensors_dcs.viz import VizHub, create_viz_app

    init_auth()
    hub = VizHub()
    app = create_viz_app(hub, lambda: {"ok": True}, boot_error=None)
    client = TestClient(app)

    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert "image" in (r.headers.get("content-type") or "")

    for path in ("/assets/favicon.svg", "/assets/favicon.png", "/assets/favicon.ico"):
        rr = client.get(path)
        assert rr.status_code == 200, path

    html = client.get("/login").text
    assert 'rel="icon"' in html
    assert "/assets/favicon.svg" in html
