"""E2: intervention jsonl + on/off duration pairing + stop summary."""

from __future__ import annotations

import json
import time
from pathlib import Path

from sensors_dcs.config import RecordConfig
from sensors_dcs.intervention_log import (
    InterventionLogger,
    append_intervention,
    summarize_episode_interventions,
    summarize_intervention_events,
    summarize_interventions,
)
from sensors_dcs.record import RecordController


class _DummyAgent:
    kind = "gello"
    agent_id = "gello"
    sensor_id = "gello-leader"
    hz = 50.0

    class _Ring:
        class _Latest:
            def get(self):
                return None

        latest = _Latest()

    ring = _Ring()

    def calib_dict(self):
        return {}


def test_on_off_duration_pairing() -> None:
    t0 = 1000.0
    events = [
        {"type": "teleop_on", "t_wall": t0},
        {"type": "teleop_off", "t_wall": t0 + 2.5},
        {"type": "delta_offset_on", "t_wall": t0 + 3.0},
        {"type": "delta_offset_off", "t_wall": t0 + 4.0},
        {"type": "estop", "t_wall": t0 + 5.0},
    ]
    s = summarize_intervention_events(events, t_end=t0 + 10.0)
    assert s["any"] is True
    assert s["counts"]["teleop_on"] == 1
    assert s["counts"]["estop"] == 1
    assert abs(s["duration_s"]["teleop"] - 2.5) < 1e-6
    assert abs(s["duration_s"]["delta_offset"] - 1.0) < 1e-6


def test_open_interval_closed_at_t_end() -> None:
    t0 = 50.0
    events = [{"type": "teleop_on", "t_wall": t0}]
    s = summarize_intervention_events(events, t_end=t0 + 8.0)
    assert abs(s["duration_s"]["teleop"] - 8.0) < 1e-6


def test_logger_append_and_summarize(tmp_path: Path) -> None:
    ep = tmp_path / "episode_00000"
    ep.mkdir()
    log = InterventionLogger()
    log.bind(ep, t_start=time.time() - 1.0)
    assert log.emit("teleop_on", source="gello")["ok"] is True
    time.sleep(0.05)
    assert log.emit("teleop_off", source="gello")["ok"] is True
    assert log.emit("estop", source="arm")["ok"] is True
    path = ep / "events" / "interventions.jsonl"
    assert path.is_file()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 3
    summary = summarize_episode_interventions(ep, t_end=time.time())
    assert summary["any"] is True
    assert summary["counts"].get("estop") == 1
    assert summary["duration_s"]["teleop"] >= 0.0


def test_append_never_raises_on_missing_dir(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    out = append_intervention(missing, type="estop", source="arm")
    assert out["ok"] is False


def test_stop_writes_intervention_summary(tmp_path: Path) -> None:
    cfg = RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=32)
    rec = RecordController(cfg, agents={"gello": _DummyAgent()}, site="test")  # type: ignore[arg-type]
    assert rec.start(mode="collect")["ok"] is True
    rec.note_intervention("teleop_on", source="gello")
    time.sleep(0.05)
    rec.note_intervention("teleop_off", source="gello")
    stop = rec.stop(valid=True, task_outcome={"outcome": "success"})
    assert stop["ok"] is True
    man = json.loads((tmp_path / "episode_00000" / "manifest.json").read_text(encoding="utf-8"))
    iv = man.get("intervention_summary")
    assert isinstance(iv, dict)
    assert iv.get("any") is True
    assert iv.get("counts", {}).get("teleop_on") == 1
    assert (tmp_path / "episode_00000" / "events" / "interventions.jsonl").is_file()


def test_summarize_interventions_root(tmp_path: Path) -> None:
    ep = tmp_path / "episode_00001"
    ep.mkdir()
    (ep / "events").mkdir()
    t0 = 10.0
    rows = [
        {"schema": "dcs_intervention_v1", "t_wall": t0, "type": "teleop_on", "source": "gello"},
        {"schema": "dcs_intervention_v1", "t_wall": t0 + 1.0, "type": "teleop_off", "source": "gello"},
    ]
    (ep / "events" / "interventions.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    (ep / "manifest.json").write_text(
        json.dumps(
            {
                "format": "dcs_episode_v1",
                "valid": True,
                "duration_s": 10.0,
                "intervention_summary": {
                    "any": True,
                    "counts": {"teleop_on": 1, "teleop_off": 1},
                    "duration_s": {"teleop": 1.0, "delta_offset": 0.0},
                    "event_count": 2,
                },
            }
        ),
        encoding="utf-8",
    )
    # empty episode without interventions
    ep2 = tmp_path / "episode_00002"
    ep2.mkdir()
    (ep2 / "manifest.json").write_text(
        json.dumps({"format": "dcs_episode_v1", "valid": True, "duration_s": 5.0}),
        encoding="utf-8",
    )
    out = summarize_interventions(tmp_path)
    assert out["ok"] is True
    assert out["episodes"] == 2
    assert out["with_any_intervention"] == 1
    assert out["intervention_rate"] == 0.5
    assert out["duration_s"]["teleop"] == 1.0
