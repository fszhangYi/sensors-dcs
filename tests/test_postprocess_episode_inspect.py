"""Episode manifest inspect for postprocess master list."""

from __future__ import annotations

import json
from pathlib import Path


def test_inspect_episode_ok_and_master_list(tmp_path: Path) -> None:
    from sensors_dcs.postprocess_service import inspect_episode

    ep = tmp_path / "episode_00001"
    ep.mkdir()
    (ep / "manifest.json").write_text(
        json.dumps(
            {
                "valid": True,
                "agents": [
                    {"agent_id": "gello", "kind": "gello", "hz_target": 50},
                    {"agent_id": "cam-left", "kind": "realsense", "hz_target": 30},
                    {"agent_id": "arm", "kind": "arm", "hz_target": 50},
                ],
            }
        ),
        encoding="utf-8",
    )
    j = inspect_episode(ep)
    assert j["ok"] is True
    assert j["master_candidates"] == ["gello", "cam-left", "arm"]
    assert j["suggested_master"] == "gello"


def test_inspect_episode_invalid_shows_error(tmp_path: Path) -> None:
    from sensors_dcs.postprocess_service import inspect_episode

    ep = tmp_path / "episode_00002"
    ep.mkdir()
    (ep / "manifest.json").write_text(
        json.dumps(
            {
                "valid": False,
                "note": "discarded",
                "agents": [{"agent_id": "cam-left", "kind": "realsense"}],
            }
        ),
        encoding="utf-8",
    )
    j = inspect_episode(ep)
    assert j["ok"] is False
    assert j["valid"] is False
    assert "invalid" in (j["error"] or "").lower() or "valid" in (j["error"] or "")
    assert j["master_candidates"] == ["cam-left"]


def test_inspect_episode_missing_manifest(tmp_path: Path) -> None:
    from sensors_dcs.postprocess_service import inspect_episode

    ep = tmp_path / "episode_00003"
    ep.mkdir()
    j = inspect_episode(ep)
    assert j["ok"] is False
    assert "manifest.json" in (j["error"] or "")


def test_postprocess_episode_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SENSORS_DCS_AUTH_DISABLED", "1")
    from fastapi.testclient import TestClient

    from sensors_dcs.auth_session import init_auth
    from sensors_dcs.viz import VizHub, create_viz_app

    ep = tmp_path / "episode_00004"
    ep.mkdir()
    (ep / "manifest.json").write_text(
        json.dumps(
            {
                "valid": True,
                "agents": [{"agent_id": "cam-middle", "kind": "realsense"}],
            }
        ),
        encoding="utf-8",
    )
    init_auth()
    app = create_viz_app(VizHub(), lambda: {"ok": True}, boot_error=None)
    client = TestClient(app)
    r = client.get("/api/postprocess/episode", params={"path": str(ep)})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["master_candidates"] == ["cam-middle"]
