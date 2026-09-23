"""Discard with keep_files=False deletes the episode directory."""

from __future__ import annotations

from pathlib import Path

from sensors_dcs.config import RecordConfig
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


def test_discard_keep_files_false_deletes_episode(tmp_path: Path) -> None:
    cfg = RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=32)
    rec = RecordController(cfg, agents={"gello": _DummyAgent()}, site="test")  # type: ignore[arg-type]

    assert rec.start(mode="infer")["ok"] is True
    ep0 = tmp_path / "episode_00000"
    assert ep0.is_dir()
    assert (ep0 / "manifest.json").is_file()

    stop = rec.stop(valid=False, keep_files=False)
    assert stop["ok"] is True
    assert stop.get("kept_files") is False
    assert stop.get("finished_episode_path") is None
    assert stop.get("valid") is False
    assert not ep0.exists()
    assert rec.status()["state"] == "idle"
    assert rec.status()["episode_index"] == 1


def test_discard_keep_files_true_still_archives(tmp_path: Path) -> None:
    cfg = RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=32)
    rec = RecordController(cfg, agents={"gello": _DummyAgent()}, site="test")  # type: ignore[arg-type]

    assert rec.start(mode="collect")["ok"] is True
    ep0 = tmp_path / "episode_00000"
    stop = rec.stop(valid=False, keep_files=True)
    assert stop["ok"] is True
    assert stop.get("kept_files") is True
    assert ep0.is_dir()
    man = (ep0 / "manifest.json").read_text(encoding="utf-8")
    assert '"valid": false' in man
