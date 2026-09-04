"""Record mode collect|infer filters pi05 / gello streams."""

from __future__ import annotations

import json
import time
from pathlib import Path

from sensors_dcs.buffer import FrameRing
from sensors_dcs.config import RecordConfig
from sensors_dcs.frame import Frame
from sensors_dcs.record import RecordController


class _FakeAgent:
    hz = 10.0
    sensor_id = "fake"

    def __init__(self, agent_id: str, kind: str) -> None:
        self.agent_id = agent_id
        self.kind = kind
        self.ring = FrameRing(maxlen=8)
        self._seq = 0

    def push(self, payload: dict) -> None:
        self._seq += 1
        self.ring.push(
            Frame(
                sensor_id=self.sensor_id,
                agent_id=self.agent_id,
                kind=self.kind,
                t_wall=time.time(),
                t_mono=time.perf_counter(),
                seq=self._seq,
                payload=payload,
            )
        )


def test_infer_records_pi05_skips_gello(tmp_path: Path) -> None:
    gello = _FakeAgent("gello", "gello")
    pi05 = _FakeAgent("pi05", "pi05")
    arm = _FakeAgent("arm-read", "arm_read")
    rec = RecordController(
        RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=64),
        agents={"gello": gello, "pi05": pi05, "arm-read": arm},  # type: ignore[arg-type]
        site="lab",
    )
    out = rec.start(mode="infer")
    assert out["ok"] is True
    assert out["record_mode"] == "infer"

    ep = tmp_path / "episode_00000"
    deadline = time.time() + 5.0
    while time.time() < deadline:
        gello.push({"joints_rad": [0.1] * 7})
        arm.push({"joints_rad": [0.0] * 6, "cartesian_xyzrpy": [0.1] * 6})
        pi05.push(
            {
                "connected": True,
                "next_state": [0.2, 0.0, 0.3, 0.0, 0.0, 0.0, 0.5],
                "robot_state": [0.1] * 7,
                "latency_ms": 12.0,
                "step": pi05._seq,
                "ok": True,
            }
        )
        if (ep / "states" / "pi05.jsonl").is_file() and (ep / "states" / "arm-read.jsonl").is_file():
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"timed out; status={rec.status()} files={list((ep / 'states').glob('*')) if (ep / 'states').exists() else []}")

    stop = rec.stop(valid=True, timeout=5.0)
    assert stop["ok"] is True
    man = json.loads((ep / "manifest.json").read_text(encoding="utf-8"))
    assert man["record_mode"] == "infer"

    assert not (ep / "states" / "gello.jsonl").exists()
    pi_lines = (ep / "states" / "pi05.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(pi_lines) >= 1
    row = json.loads(pi_lines[0])
    assert row["kind"] == "pi05"
    assert row["payload"]["next_state"][0] == 0.2
    assert (ep / "states" / "arm-read.jsonl").is_file()


def test_collect_skips_pi05(tmp_path: Path) -> None:
    gello = _FakeAgent("gello", "gello")
    pi05 = _FakeAgent("pi05", "pi05")
    rec = RecordController(
        RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=64),
        agents={"gello": gello, "pi05": pi05},  # type: ignore[arg-type]
        site="lab",
    )
    assert rec.start(mode="collect")["ok"] is True
    ep = tmp_path / "episode_00000"
    deadline = time.time() + 5.0
    while time.time() < deadline:
        gello.push({"joints_rad": [0.0] * 7})
        pi05.push({"next_state": [1.0] * 7, "ok": True, "step": 1})
        if (ep / "states" / "gello.jsonl").is_file():
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"timed out; status={rec.status()}")

    assert rec.stop(valid=True, timeout=5.0)["ok"] is True
    man = json.loads((ep / "manifest.json").read_text(encoding="utf-8"))
    assert man["record_mode"] == "collect"
    assert (ep / "states" / "gello.jsonl").is_file()
    assert not (ep / "states" / "pi05.jsonl").exists()
