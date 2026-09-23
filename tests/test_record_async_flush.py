"""Async flush: next episode can start while previous still writes."""

from __future__ import annotations

import time
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


def test_async_flush_allows_next_start(tmp_path: Path) -> None:
    cfg = RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=32)
    rec = RecordController(cfg, agents={"gello": _DummyAgent()}, site="test")  # type: ignore[arg-type]

    assert rec.start()["ok"] is True
    ep0 = tmp_path / "episode_00000"
    assert ep0.is_dir()

    stop = rec.stop(valid=True, async_flush=True)
    assert stop["ok"] is True
    assert stop["async_flush"] is True
    assert stop["state"] == "idle"
    assert stop["finished_episode_index"] == 0
    assert stop["episode_index"] == 1
    assert stop["flushing_count"] >= 1

    # Next episode can start immediately (even if flush still running).
    start2 = rec.start()
    assert start2["ok"] is True
    assert start2["state"] == "recording"
    assert (tmp_path / "episode_00001").is_dir()

    # Wait for background flush of ep0
    deadline = time.time() + 5.0
    while time.time() < deadline:
        st = rec.status()
        jobs = {j["episode_index"]: j for j in st.get("flushing_jobs") or []}
        if 0 in jobs and jobs[0].get("state") in {"done", "error"}:
            break
        # also ok if pruned after done
        if 0 not in jobs and (ep0 / "manifest.json").is_file():
            man = (ep0 / "manifest.json").read_text(encoding="utf-8")
            if '"provisional"' not in man or '"provisional": true' not in man:
                break
        time.sleep(0.05)

    man = (ep0 / "manifest.json").read_text(encoding="utf-8")
    assert '"provisional": true' not in man
    assert '"valid": true' in man

    assert rec.stop(valid=True, async_flush=False)["ok"] is True


def test_wait_manifest_ready_resolves_async_provisional(tmp_path: Path) -> None:
    """Quick-collect previously raced the provisional start-time manifest."""
    import json

    from sensors_dcs.export.timeline import (
        ensure_episode_exportable,
        wait_episode_manifest_ready,
    )

    cfg = RecordConfig(save_dir=str(tmp_path), episode_index=0, queue_maxsize=32)
    rec = RecordController(cfg, agents={"gello": _DummyAgent()}, site="test")  # type: ignore[arg-type]
    assert rec.start()["ok"] is True
    ep0 = tmp_path / "episode_00000"
    provisional = json.loads((ep0 / "manifest.json").read_text(encoding="utf-8"))
    assert provisional.get("provisional") is True
    assert provisional.get("valid") is False

    # Without waiting, export must refuse the provisional file.
    try:
        ensure_episode_exportable(provisional, episode_label=ep0.name)
        raise AssertionError("expected provisional refuse")
    except ValueError as e:
        assert "provisional" in str(e).lower()

    stop = rec.stop(valid=True, async_flush=True)
    assert stop["ok"] is True

    # Simulate quick-collect: wait then exportable.
    ready = wait_episode_manifest_ready(ep0, timeout_s=5.0, poll_s=0.05)
    assert ready is not None
    assert ready.get("provisional") is not True
    assert ready.get("valid") is True
    ensure_episode_exportable(ready, episode_label=ep0.name)

    # Drain second episode if started elsewhere — stop cleanly if still recording.
    st = rec.status()
    if st.get("state") == "recording":
        assert rec.stop(valid=True, async_flush=False)["ok"] is True


def test_wait_manifest_ready_noop_when_already_final(tmp_path: Path) -> None:
    from sensors_dcs.export.timeline import wait_episode_manifest_ready

    ep = tmp_path / "episode_00000"
    ep.mkdir()
    (ep / "manifest.json").write_text(
        '{"episode_index": 0, "valid": true, "agents": []}\n',
        encoding="utf-8",
    )
    t0 = time.time()
    man = wait_episode_manifest_ready(ep, timeout_s=2.0, poll_s=0.05)
    assert time.time() - t0 < 1.0
    assert man is not None
    assert man.get("valid") is True
