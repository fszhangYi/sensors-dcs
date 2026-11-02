"""Federated multi-station episode → same shape as single-station dcs_episode_v1."""

from __future__ import annotations

import json
from pathlib import Path

from sensors_dcs.export.session_federate import build_federated_episode, federated_agent_id
from sensors_dcs.session_registry import (
    build_participant,
    build_session_doc,
    write_session_registry,
)


def _write_mini_episode(
    root: Path,
    *,
    station_id: str,
    t0: float,
    agent_id: str,
    kind: str = "arm_read",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "states").mkdir(exist_ok=True)
    (root / "cameras").mkdir(exist_ok=True)
    row = {
        "agent_id": agent_id,
        "sensor_id": agent_id,
        "kind": kind,
        "seq": 1,
        "t_wall": t0 + 0.10,
        "t_mono": 100.10,
        "payload": {"joints_rad": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]},
    }
    (root / "states" / f"{agent_id}.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )
    cam_id = f"cam-{station_id[-4:]}"
    cam_dir = root / "cameras" / cam_id
    cam_dir.mkdir(parents=True, exist_ok=True)
    (cam_dir / "00000001.jpg").write_bytes(b"fake-jpeg")
    cam_row = {
        "agent_id": cam_id,
        "sensor_id": cam_id,
        "kind": "realsense",
        "seq": 1,
        "t_wall": t0 + 0.11,
        "t_mono": 100.11,
        "file": "00000001.jpg",
        "serial": f"S-{station_id}",
        "role": "test",
    }
    (cam_dir / "index.jsonl").write_text(json.dumps(cam_row) + "\n", encoding="utf-8")
    man = {
        "format": "dcs_episode_v1",
        "episode_index": 1,
        "t_start": t0,
        "t_end": t0 + 1.0,
        "valid": True,
        "station_id": station_id,
        "agents": [
            {"agent_id": agent_id, "kind": kind, "sensor_id": agent_id},
            {"agent_id": cam_id, "kind": "realsense", "sensor_id": cam_id},
        ],
        "cameras": {cam_id: {"serial": f"S-{station_id}"}},
    }
    (root / "manifest.json").write_text(
        json.dumps(man, indent=2) + "\n", encoding="utf-8"
    )
    return root


def test_federated_agent_id() -> None:
    assert federated_agent_id("m", "arm", is_master=True) == "arm"
    assert federated_agent_id("slave-left", "cam-a", is_master=False) == "slave-left__cam-a"


def test_build_federated_episode_convention_layout(tmp_path: Path) -> None:
    master_save = tmp_path / "master_data"
    master_ep = _write_mini_episode(
        master_save / "episode_00001",
        station_id="master-a",
        t0=1000.0,
        agent_id="arm",
    )
    # Slave copied into convention path under master's sessions/
    slave_ep = _write_mini_episode(
        master_save / "sessions" / "sess-1" / "slave-b" / "episode_00001",
        station_id="slave-b",
        t0=1000.5,
        agent_id="arm",  # same local name → must be prefixed
    )

    parts = [
        build_participant(
            station_id="master-a",
            role="master",
            save_dir=str(master_save),
            episode_index=1,
            episode_path=str(master_ep),
            t_start=1000.0,
        ),
        build_participant(
            station_id="slave-b",
            role="slave",
            save_dir=str(tmp_path / "remote_slave"),  # original remote path (absent)
            episode_index=1,
            episode_path=str(tmp_path / "remote_slave" / "episode_00001"),
            t_start=1000.5,
        ),
    ]
    doc = build_session_doc(
        session_id="sess-1",
        episode_index=1,
        mode="collect",
        master_station_id="master-a",
        participants=parts,
    )
    write_session_registry(master_save, doc)

    # Convention discovery finds slave without remap.
    r = build_federated_episode(save_dir=master_save, session_id="sess-1")
    assert r.get("ok") is True, r
    fed = Path(r["episode_path"])
    assert fed.name.startswith("episode_"), fed
    assert (fed / "manifest.json").is_file()
    man = json.loads((fed / "manifest.json").read_text(encoding="utf-8"))
    assert man.get("federated") is True
    assert man["t_start"] == 1000.0

    # Master agent unprefixed; slave prefixed.
    assert (fed / "states" / "arm.jsonl").is_file()
    assert (fed / "states" / "slave-b__arm.jsonl").is_file()
    master_row = json.loads((fed / "states" / "arm.jsonl").read_text(encoding="utf-8").splitlines()[0])
    slave_row = json.loads(
        (fed / "states" / "slave-b__arm.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    # slave local t=1000.5+0.10 → τ=0.10 → t_wall'=1000.0+0.10
    assert abs(slave_row["t_wall"] - 1000.10) < 1e-6
    assert abs(master_row["t_wall"] - 1000.10) < 1e-6
    assert slave_row["agent_id"] == "slave-b__arm"

    # Camera media linked/copied; slave cams are prefixed.
    cam_dirs = [p for p in (fed / "cameras").iterdir() if p.is_dir()]
    assert cam_dirs
    slave_cams = [p for p in cam_dirs if p.name.startswith("slave-b__")]
    assert slave_cams
    assert (slave_cams[0] / "00000001.jpg").is_file()
    assert slave_ep.is_dir()  # source still intact
