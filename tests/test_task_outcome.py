"""E1: task_outcome on stop + outcomes summary aggregation."""

from __future__ import annotations

import json
from pathlib import Path

from sensors_dcs.config import RecordConfig
from sensors_dcs.record import RecordController
from sensors_dcs.task_outcome import normalize_outcome, summarize_outcomes


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


def test_normalize_outcome_aliases() -> None:
    assert normalize_outcome("ok") == "success"
    assert normalize_outcome("failed") == "fail"
    assert normalize_outcome("void") == "void"
    assert normalize_outcome("nope") == "unknown"


def test_stop_writes_task_outcome_and_tags(tmp_path: Path) -> None:
    cfg = RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=32)
    rec = RecordController(cfg, agents={"gello": _DummyAgent()}, site="test")  # type: ignore[arg-type]
    assert rec.start(mode="collect")["ok"] is True
    stop = rec.stop(
        valid=True,
        task_outcome={"outcome": "success", "labels": ["grasp", "place"], "note": "ok"},
        tags={"task_id": "pick_place_v1", "env_id": "lab", "object_ids": ["cup"]},
    )
    assert stop["ok"] is True
    man = json.loads((tmp_path / "episode_00000" / "manifest.json").read_text(encoding="utf-8"))
    assert man["valid"] is True
    assert man["task_outcome"]["outcome"] == "success"
    assert man["task_outcome"]["labels"] == ["grasp", "place"]
    assert man["tags"]["task_id"] == "pick_place_v1"
    assert man["tags"]["object_ids"] == ["cup"]


def test_discard_defaults_void(tmp_path: Path) -> None:
    cfg = RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=32)
    rec = RecordController(cfg, agents={"gello": _DummyAgent()}, site="test")  # type: ignore[arg-type]
    assert rec.start(mode="collect")["ok"] is True
    stop = rec.stop(valid=False, keep_files=True)
    assert stop["ok"] is True
    man = json.loads((tmp_path / "episode_00000" / "manifest.json").read_text(encoding="utf-8"))
    assert man["valid"] is False
    assert man["task_outcome"]["outcome"] == "void"


def test_apply_episode_outcome_after_stop(tmp_path: Path) -> None:
    cfg = RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=32)
    rec = RecordController(cfg, agents={"gello": _DummyAgent()}, site="test")  # type: ignore[arg-type]
    assert rec.start(mode="collect")["ok"] is True
    stop = rec.stop(valid=True)
    assert stop["ok"] is True
    man0 = json.loads((tmp_path / "episode_00000" / "manifest.json").read_text(encoding="utf-8"))
    assert "task_outcome" not in man0
    patched = rec.apply_episode_outcome(
        episode_path=stop["finished_episode_path"],
        task_outcome={"outcome": "fail", "labels": ["grasp"]},
        tags={"task_id": "t1"},
    )
    assert patched["ok"] is True
    man1 = json.loads((tmp_path / "episode_00000" / "manifest.json").read_text(encoding="utf-8"))
    assert man1["task_outcome"]["outcome"] == "fail"
    assert man1["tags"]["task_id"] == "t1"


def test_summarize_outcomes_completion_rate(tmp_path: Path) -> None:
    def _write(ep: int, outcome: str | None, *, valid: bool = True) -> None:
        d = tmp_path / f"episode_{ep:05d}"
        d.mkdir()
        man: dict = {
            "format": "dcs_episode_v1",
            "valid": valid,
            "duration_s": 10.0 + ep,
            "episode_index": ep,
        }
        if outcome is not None:
            man["task_outcome"] = {"outcome": outcome, "labels": []}
        (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")

    _write(0, "success")
    _write(1, "fail")
    _write(2, "void")
    _write(3, None)  # unlabeled
    _write(4, "success")

    s = summarize_outcomes(tmp_path)
    assert s["ok"] is True
    assert s["counts"]["episodes"] == 5
    assert s["counts"]["success"] == 2
    assert s["counts"]["fail"] == 1
    assert s["counts"]["void"] == 1
    assert s["counts"]["unlabeled"] == 1
    assert s["completion_denom"] == 3  # success+fail (+partial)
    assert abs(float(s["completion_rate"]) - (2 / 3)) < 1e-9
