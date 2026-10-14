"""Slave station HTTP control gate (P1)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sensors_dcs.viz import VizHub, create_viz_app


def _app(monkeypatch: pytest.MonkeyPatch, *, role: str = "master") -> TestClient:
    monkeypatch.setenv("SENSORS_DCS_AUTH_DISABLED", "1")
    from sensors_dcs.auth_session import init_auth

    init_auth()
    hub = VizHub()

    def status_fn() -> dict[str, Any]:
        return {
            "ok": True,
            "station": {
                "role": role,
                "id": f"{role}-x",
                "advertise_ip": "127.0.0.1",
                "peer_port": 7010,
                "is_master": role == "master",
                "is_slave": role == "slave",
            },
            "peer": {"link_state": "idle", "slaves": []},
        }

    class _Rec:
        def start(self, *, mode: str = "collect") -> dict[str, Any]:
            return {"ok": True, "state": "recording", "mode": mode}

        def stop(self, **_kw: Any) -> dict[str, Any]:
            return {"ok": True, "state": "idle"}

        def set_save_dir(self, path: str) -> dict[str, Any]:
            return {"ok": True, "save_dir": path}

        def status(self) -> dict[str, Any]:
            return {"state": "idle", "save_dir": "/tmp", "episode_index": 0}

    app = create_viz_app(
        hub,
        status_fn,
        recorder=_Rec(),
        arm_command=lambda **kw: {"ok": True},
        gripper_command=lambda **kw: {"ok": True},
        pi05_connect=lambda **kw: {"ok": True, "configured": True},
        pi05_step=lambda **kw: {"ok": True},
    )
    return TestClient(app)


def test_master_can_start_record(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _app(monkeypatch, role="master")
    r = c.post("/api/record/start", json={"mode": "collect"})
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_slave_denied_record_and_arm(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _app(monkeypatch, role="slave")
    r = c.post("/api/record/start", json={"mode": "collect"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False
    assert body.get("station_denied") is True

    r2 = c.post("/api/arm/command", json={"arm": True})
    assert r2.json().get("station_denied") is True

    r3 = c.post("/api/gripper/command", json={"position_norm": 0.5})
    assert r3.json().get("station_denied") is True

    r4 = c.post("/api/pi05/connect", json={"host": "127.0.0.1", "port": 5000})
    assert r4.json().get("station_denied") is True


def test_status_exposes_station_control_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _app(monkeypatch, role="slave")
    j = c.get("/api/status").json()
    assert j.get("station_control_locked") is True
    assert j["station"]["is_slave"] is True

    c2 = _app(monkeypatch, role="master")
    j2 = c2.get("/api/status").json()
    assert j2.get("station_control_locked") is False
