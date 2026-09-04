"""Filesystem browse + apply-config (path picker / restart)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sensors_dcs.auth_session import init_auth
from sensors_dcs.viz import PREVIEW_HTML, VizHub, create_viz_app


@pytest.fixture()
def auth_off_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SENSORS_DCS_AUTH_DISABLED", "1")
    init_auth()
    app = create_viz_app(
        VizHub(),
        lambda: {"ok": True},
        config_path=str(Path("/root/autodl-tmp/sensors-dcs/configs/gello_only.yaml").resolve()),
    )
    return TestClient(app)


def test_fs_roots_and_children(auth_off_client: TestClient) -> None:
    from sensors_dcs.paths import project_root

    roots = auth_off_client.get("/api/fs/roots").json()
    assert roots["ok"] is True
    assert "workspace" in roots["roots"]
    assert Path(roots["roots"]["workspace"]).resolve() == project_root().resolve().parent

    kids = auth_off_client.get(
        "/api/fs/children",
        params={"root": "workspace", "path": roots["roots"]["workspace"]},
    ).json()
    assert kids["ok"] is True
    names = {e["name"] for e in kids["entries"]}
    assert project_root().name in names


def test_runtime_config_get(auth_off_client: TestClient) -> None:
    j = auth_off_client.get("/api/runtime/config").json()
    assert j["ok"] is True
    assert j["path"].endswith("gello_only.yaml")
    assert "workspace" in j["roots"]


def test_apply_config_validates(auth_off_client: TestClient, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sensors_dcs.reexec.user_data_dir", lambda: tmp_path)
    monkeypatch.setattr("sensors_dcs.reexec.schedule_reexec", lambda *a, **k: None)

    bad = auth_off_client.post("/api/runtime/apply-config", json={"path": "/no/such.yaml"})
    assert bad.status_code == 400

    good_path = Path("/root/autodl-tmp/sensors-dcs/configs/gello_only.yaml").resolve()
    ok = auth_off_client.post("/api/runtime/apply-config", json={"path": str(good_path)})
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["restarting"] is True
    ptr = tmp_path / "configs" / "active_config.path"
    assert ptr.is_file()
    assert str(good_path) in ptr.read_text(encoding="utf-8")


def test_settings_config_ui_present() -> None:
    assert 'id="settingsNavConfig"' in PREVIEW_HTML
    assert 'id="pathPickerOverlay"' in PREVIEW_HTML
    assert 'data-i18n="settings.config_apply"' in PREVIEW_HTML
    assert "/api/fs/children" in PREVIEW_HTML
    assert "/api/runtime/apply-config" in PREVIEW_HTML
