"""Gello reinforce: frame-delta offset + decode compose."""

from __future__ import annotations

import math
import threading

import pytest

from sensors_dcs.arm_pose import compose_delta_xyzrpy, relative_xyzrpy
from sensors_dcs.config import AgentConfig
from sensors_dcs.runtime import Orchestrator


def test_relative_xyzrpy_roundtrip() -> None:
    cur = [0.4, 0.0, 0.3, 0.0, 0.0, 0.0]
    delta = [0.01, 0.02, -0.03, 0.0, 0.0, 0.0]
    nxt = compose_delta_xyzrpy(cur, delta)
    back = relative_xyzrpy(cur, nxt)
    for a, b in zip(delta, back):
        assert abs(a - b) < 1e-9


def test_agent_config_gello_reinforce_no_sensor() -> None:
    cfg = AgentConfig(id="gr", type="gello_reinforce", hz=20.0, gello_agent_id="gello")
    assert cfg.sensor_id is None
    assert cfg.gello_agent_id == "gello"


def _rt_offset(*, configured: bool = True, enabled: bool = False, offset=None):
    rt = Orchestrator.__new__(Orchestrator)
    rt.agents = {}
    rt._delta_pose_offset_lock = threading.Lock()
    rt._delta_pose_offset_configured = configured
    rt._delta_pose_offset_enabled = enabled
    rt._delta_pose_offset = list(offset or [0.0] * 7)
    rt._delta_pose_offset_prev_gello = None
    return rt


def test_effective_offset_zero_when_disabled() -> None:
    rt = _rt_offset(configured=True, enabled=False, offset=[0.1, 0, 0, 0, 0, 0, 0.2])
    assert rt.effective_delta_pose_offset() == [0.0] * 7


def test_set_offset_rejects_unconfigured() -> None:
    rt = _rt_offset(configured=False)
    out = rt.set_delta_pose_offset([0.1] * 7)
    assert out["ok"] is False
    assert "not configured" in str(out.get("error"))


def test_note_sample_frame_delta_set_not_accumulate(monkeypatch) -> None:
    rt = _rt_offset(configured=True, enabled=True)
    poses = {
        "a": [0.40, 0.0, 0.30, 0.0, 0.0, 0.0],
        "b": [0.41, 0.0, 0.30, 0.0, 0.0, 0.0],
        "c": [0.42, 0.0, 0.30, 0.0, 0.0, 0.0],
    }

    def _fk(j):
        key = "a" if abs(j[0] - 0.1) < 1e-9 else ("b" if abs(j[0] - 0.2) < 1e-9 else "c")
        return list(poses[key])

    monkeypatch.setattr(
        "sensors_dcs.arm_pose.joints_rad_to_xyzrpy",
        lambda j, **kw: _fk(j),
    )
    j_a = [0.1, 0, 0, 0, 0, 0, 0.3]
    j_b = [0.2, 0, 0, 0, 0, 0, 0.35]
    j_c = [0.3, 0, 0, 0, 0, 0, 0.35]
    r0 = rt.note_gello_reinforce_sample(j_a)
    assert r0["ok"] and r0.get("armed_prev")
    assert r0["offset"] == [0.0] * 7
    r1 = rt.note_gello_reinforce_sample(j_b)
    assert r1["ok"] and not r1.get("skipped")
    # Frame a→b: +0.01 x, +0.05 grip
    assert abs(r1["offset"][0] - 0.01) < 1e-9
    assert abs(r1["offset"][6] - 0.05) < 1e-9
    r2 = rt.note_gello_reinforce_sample(j_c)
    assert r2["ok"]
    # SET (not accumulate): b→c is also +0.01 x, grip 0 — not +0.02
    assert abs(r2["offset"][0] - 0.01) < 1e-9
    assert abs(r2["offset"][6]) < 1e-9
    # Still: same pose → near-zero offset
    r3 = rt.note_gello_reinforce_sample(j_c)
    assert all(abs(x) < 1e-9 for x in r3["offset"])


def test_decode_joints_passthrough_with_zero_offset(monkeypatch) -> None:
    rt = _rt_offset(configured=True, enabled=True, offset=[0.0] * 7)
    out = Orchestrator._decode_next_state(
        rt, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7], "joints"
    )
    assert out["ok"] is True
    assert out["joints_rad"] == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]


def test_decode_pose_applies_offset(monkeypatch) -> None:
    rt = _rt_offset(configured=True, enabled=True, offset=[0.01, 0, 0, 0, 0, 0, 0])
    monkeypatch.setattr(
        rt,
        "_xyzrpy_to_joints",
        lambda xyz: {"ok": True, "joints_rad": [9.0] * 6, "error": None},
    )
    # First call (no offset path inside pose decode before apply): return ok with goal
    # _decode calls _xyzrpy_to_joints twice when offset active — both fine.
    out = Orchestrator._decode_next_state(
        rt, [0.40, 0.0, 0.30, 0.0, 0.0, 0.0, 0.5], "pose"
    )
    assert out["ok"] is True
    assert out["joints_rad"] == [9.0] * 6
    assert abs(out["goal_xyzrpy"][0] - 0.41) < 1e-9
    assert out.get("delta_pose_offset_applied")[0] == pytest.approx(0.01)


def test_enable_resets_prev_and_offset(monkeypatch) -> None:
    rt = _rt_offset(configured=True, enabled=False, offset=[1.0] * 7)
    monkeypatch.setattr(rt, "_live_gello_joints7", lambda: [0.1] * 7)
    out = rt.set_delta_pose_offset_enabled(True)
    assert out["ok"] is True
    assert out["enabled"] is True
    assert out["offset"] == [0.0] * 7
    assert out["prev_set"] is True
