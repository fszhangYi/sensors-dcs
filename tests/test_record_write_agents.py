"""Tests for recording write agents and exporting their states."""

from __future__ import annotations

import json
from pathlib import Path

from sensors_dcs.export.filter import _agent_kind
from sensors_dcs.export.timeline import _flatten_state_row, build_events_frame
from sensors_dcs.frame import Frame
from sensors_dcs.record import RecordController
from sensors_dcs.config import RecordConfig


def test_record_writes_arm_write_state(tmp_path: Path) -> None:
    cfg = RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=8)
    rc = RecordController(cfg, agents={}, site="lab")
    ep = tmp_path / "episode_00000"
    ep.mkdir()
    (ep / "states").mkdir()

    def state_fp(aid: str):
        p = ep / "states" / f"{aid}.jsonl"
        return p.open("a", encoding="utf-8")

    def cam_paths(aid: str):
        raise AssertionError("camera path should not be used")

    fr = Frame(
        sensor_id="arm-elite",
        agent_id="arm-write",
        kind="arm_write",
        t_wall=1.0,
        t_mono=1.0,
        seq=3,
        payload={
            "command_joints_rad": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
            "armed": True,
            "last_ok": True,
            "last_result": {"ok": True, "armed": True, "extra": "drop-me"},
        },
    )
    fps = {}

    def state_fp_cached(aid: str):
        if aid not in fps:
            fps[aid] = state_fp(aid)
        return fps[aid]

    try:
        rc._write_frame(fr, state_fp_cached, cam_paths)
    finally:
        for f in fps.values():
            f.close()

    lines = (ep / "states" / "arm-write.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["kind"] == "arm_write"
    assert row["payload"]["command_joints_rad"][0] == 0.1
    assert row["payload"]["last_result"] == {"ok": True, "armed": True}


def test_flatten_arm_write_and_events() -> None:
    row = {
        "agent_id": "arm-write",
        "sensor_id": "arm-elite",
        "kind": "arm_write",
        "seq": 1,
        "t_wall": 10.0,
        "t_mono": 10.0,
        "payload": {
            "command_joints_rad": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "feedback_joints_rad": [0.9, 0.0, 0.0, 0.0, 0.0, 0.0],
            "armed": True,
        },
    }
    sample = _flatten_state_row(row)
    assert sample.kind == "arm_write"
    assert sample.fields["joints_rad"][0] == 1.0
    events = build_events_frame({"t_start": 10.0}, [sample])
    assert float(events.iloc[0]["j0"]) == 1.0
    assert float(events.iloc[0]["feedback_j0"]) == 0.9


def test_agent_kind_detects_writers() -> None:
    cols = ["arm-write.j0", "arm-write.armed", "grip-w.command_position_norm"]
    assert _agent_kind(cols, "arm-write") == "arm_write"
    assert _agent_kind(cols, "grip-w") == "gripper_write"
    assert (
        _agent_kind(cols, "arm-write", kind_hint={"arm-write": "arm_write"}) == "arm_write"
    )
