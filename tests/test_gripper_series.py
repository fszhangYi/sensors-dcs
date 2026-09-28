"""Gripper progress series for rate-policy chart overlay."""

from __future__ import annotations

import json
from pathlib import Path

from sensors_dcs.postprocess_service import (
    GRIPPER_SERIES_MAX_POINTS,
    load_episode_gripper_series,
    load_gripper_series_batch,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _make_episode(tmp: Path, name: str, *, n_grip: int = 20, n_arm: int = 25) -> Path:
    ep = tmp / name
    (ep / "states").mkdir(parents=True)
    (ep / "manifest.json").write_text(
        json.dumps({"valid": True, "agents": [{"agent_id": "gripper-read", "kind": "gripper_read"}]}),
        encoding="utf-8",
    )
    # Arm spans a wider wall range so gripper is not stretched to 0–100 alone.
    arm_rows = [
        {
            "agent_id": "arm",
            "kind": "arm_read",
            "seq": i,
            "t_wall": 1000.0 + i * 0.4,
            "payload": {"joints_rad": [0.0] * 6},
        }
        for i in range(n_arm)
    ]
    grip_rows = [
        {
            "agent_id": "gripper-read",
            "kind": "gripper_read",
            "seq": i,
            "t_wall": 1000.0 + i * 0.2,
            "payload": {"position_norm": 0.05 * i},
        }
        for i in range(n_grip)
    ]
    write_rows = [
        {
            "agent_id": "gripper-write",
            "kind": "gripper_write",
            "seq": i,
            "t_wall": 1000.0 + i * 0.2,
            "payload": {"command_position_norm": 0.02 * i},
        }
        for i in range(n_grip)
    ]
    _write_jsonl(ep / "states" / "arm.jsonl", arm_rows)
    _write_jsonl(ep / "states" / "gripper-read.jsonl", grip_rows)
    _write_jsonl(ep / "states" / "gripper-write.jsonl", write_rows)
    return ep


def test_load_episode_gripper_series_read_and_write(tmp_path: Path) -> None:
    ep = _make_episode(tmp_path, "episode_00001")
    j = load_episode_gripper_series(ep)
    assert j["ok"] is True
    assert j["episode_label"] == "episode_00001"
    kinds = {s["kind"] for s in j["series"]}
    assert kinds == {"gripper_read", "gripper_write"}
    read = next(s for s in j["series"] if s["kind"] == "gripper_read")
    assert read["field"] == "position_norm"
    assert read["points"]
    assert read["points"][0]["pct"] == 0.0
    # Gripper ends at t=1000+19*0.2=1003.8; arm ends at 1000+24*0.4=1009.6
    # so last grip pct < 100.
    assert read["points"][-1]["pct"] < 100.0
    assert abs(read["points"][-1]["v"] - 0.05 * 19) < 1e-9
    write = next(s for s in j["series"] if s["kind"] == "gripper_write")
    assert write["field"] == "command_position_norm"


def test_load_episode_kinds_filter(tmp_path: Path) -> None:
    ep = _make_episode(tmp_path, "episode_00002")
    j = load_episode_gripper_series(ep, kinds=["gripper_read"])
    assert j["ok"] is True
    assert len(j["series"]) == 1
    assert j["series"][0]["kind"] == "gripper_read"


def test_load_missing_episode() -> None:
    j = load_episode_gripper_series("/nonexistent/episode_xyz")
    assert j["ok"] is False
    assert j["error"]


def test_batch_partial_errors(tmp_path: Path) -> None:
    ep = _make_episode(tmp_path, "episode_00003")
    j = load_gripper_series_batch([str(ep), "/no/such/ep"])
    assert j["ok"] is True
    assert len(j["series"]) >= 1
    assert any(e["path"] == "/no/such/ep" for e in j["errors"])


def test_downsample_caps_points(tmp_path: Path) -> None:
    ep = _make_episode(tmp_path, "episode_00004", n_grip=2000, n_arm=2000)
    j = load_episode_gripper_series(ep, kinds=["gripper_read"], max_points=100)
    assert j["ok"] is True
    assert len(j["series"][0]["points"]) <= 100
    # Default cap still applies when using default max_points
    j2 = load_episode_gripper_series(ep, kinds=["gripper_read"])
    assert len(j2["series"][0]["points"]) <= GRIPPER_SERIES_MAX_POINTS


def test_gripper_series_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SENSORS_DCS_AUTH_DISABLED", "1")
    from fastapi.testclient import TestClient

    from sensors_dcs.auth_session import init_auth
    from sensors_dcs.viz import VizHub, create_viz_app

    ep = _make_episode(tmp_path, "episode_00005")
    init_auth()
    app = create_viz_app(VizHub(), lambda: {"ok": True}, boot_error=None)
    client = TestClient(app)
    r = client.post(
        "/api/postprocess/gripper-series",
        json={"paths": [str(ep)], "kinds": ["gripper_read", "gripper_write"]},
    )
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert len(j["series"]) == 2
