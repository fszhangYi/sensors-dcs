"""Gello reinforce: cumulative offset + decode compose."""

from __future__ import annotations

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
    rt._delta_pose_offset_pending = [0.0] * 7
    rt._delta_pose_offset_prev_gello = None
    return rt


def test_effective_offset_zero_when_disabled() -> None:
    rt = _rt_offset(configured=True, enabled=False, offset=[0.1, 0, 0, 0, 0, 0, 0.2])
    assert rt.effective_delta_pose_offset() == [0.0] * 7


def test_effective_offset_follows_cumulative_when_enabled() -> None:
    rt = _rt_offset(configured=True, enabled=True, offset=[0.1, 0, 0, 0, 0, 0, 0.2])
    assert rt.effective_delta_pose_offset()[0] == pytest.approx(0.1)
    assert rt.effective_delta_pose_offset()[6] == pytest.approx(0.2)


def test_set_offset_rejects_unconfigured() -> None:
    rt = _rt_offset(configured=False)
    out = rt.set_delta_pose_offset([0.1] * 7)
    assert out["ok"] is False
    assert "not configured" in str(out.get("error"))


def test_note_sample_accumulates_and_holds(monkeypatch) -> None:
    del monkeypatch
    rt = _rt_offset(configured=True, enabled=True)
    j_a = [0.0] * 7
    j_b = [0.01, 0, 0, 0, 0, 0, 0.05]
    j_c = [0.03, 0, 0, 0, 0, 0, 0.05]
    r0 = rt.note_gello_reinforce_sample(j_a)
    assert r0["ok"] and r0.get("armed_prev")
    assert r0["offset"] == [0.0] * 7
    r1 = rt.note_gello_reinforce_sample(j_b)
    assert r1["ok"] and r1.get("folded")
    assert abs(r1["offset"][0] - 0.01) < 1e-9
    assert abs(r1["offset"][6] - 0.05) < 1e-9
    r2 = rt.note_gello_reinforce_sample(j_c)
    assert r2["ok"] and r2.get("folded")
    # Sum, not replace: 0.01 then another 0.02. Grip stopped changing.
    assert abs(r2["offset"][0] - 0.03) < 1e-9
    assert abs(r2["offset"][6] - 0.05) < 1e-9
    r3 = rt.note_gello_reinforce_sample(j_c)
    assert r3["ok"] and not r3.get("folded")
    assert abs(r3["offset"][0] - 0.03) < 1e-9
    assert all(abs(x) < 1e-9 for x in r3["pending"])


def test_note_sample_keeps_noise_in_pending(monkeypatch) -> None:
    del monkeypatch
    rt = _rt_offset(configured=True, enabled=True)
    rt.note_gello_reinforce_sample([0.0] * 7)
    nxt = rt.note_gello_reinforce_sample([1e-5, 0, 0, 0, 0, 0, 0])
    assert nxt["ok"] and not nxt.get("folded")
    assert nxt["offset"] == [0.0] * 7
    assert abs(nxt["pending"][0] - 1e-5) < 1e-12


def test_decode_joints_passthrough_with_zero_offset(monkeypatch) -> None:
    del monkeypatch
    rt = _rt_offset(configured=True, enabled=True, offset=[0.0] * 7)
    out = Orchestrator._decode_next_state(
        rt, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7], "joints"
    )
    assert out["ok"] is True
    assert out["joints_rad"] == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]


def test_decode_pose_ignores_joint_bias(monkeypatch) -> None:
    rt = _rt_offset(configured=True, enabled=True, offset=[0.01, 0, 0, 0, 0, 0, 0])
    calls = {"n": 0}

    def _ik(xyz):
        calls["n"] += 1
        return {"ok": True, "joints_rad": [9.0] * 6, "error": None}

    monkeypatch.setattr(rt, "_xyzrpy_to_joints", _ik)
    out = Orchestrator._decode_next_state(
        rt, [0.40, 0.0, 0.30, 0.0, 0.0, 0.0, 0.5], "pose"
    )
    assert out["ok"] is True
    assert abs(out["goal_xyzrpy"][0] - 0.40) < 1e-9
    assert calls["n"] == 1
    biased = rt.joints_plus_gello_bias([1.0, 0, 0, 0, 0, 0])
    assert biased[0] == pytest.approx(1.01)


def test_decode_delta_pose_ignores_joint_bias(monkeypatch) -> None:
    rt = _rt_offset(configured=True, enabled=True, offset=[0.05, 0, 0, 0, 0, 0, 0])
    monkeypatch.setattr(
        rt, "_current_tcp_xyzrpy", lambda: [0.40, 0.0, 0.30, 0.0, 0.0, 0.0]
    )

    def _ik(xyz):
        return {"ok": True, "joints_rad": [1.0] * 6, "error": None}

    monkeypatch.setattr(rt, "_xyzrpy_to_joints", _ik)
    out = Orchestrator._decode_next_state(
        rt, [0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5], "delta_pose"
    )
    assert out["ok"] is True
    assert abs(out["goal_xyzrpy"][0] - 0.41) < 1e-9


def test_enable_and_disable_reset_offset(monkeypatch) -> None:
    rt = _rt_offset(configured=True, enabled=False, offset=[1.0] * 7)
    rt._delta_pose_offset_pending = [0.1] * 7
    monkeypatch.setattr(rt, "_live_gello_joints7", lambda: [0.1] * 7)
    out = rt.set_delta_pose_offset_enabled(True)
    assert out["ok"] is True
    assert out["enabled"] is True
    assert out["offset"] == [0.0] * 7
    assert out["pending"] == [0.0] * 7
    assert out["prev_set"] is True
    rt._delta_pose_offset = [0.2] * 7
    off = rt.set_delta_pose_offset_enabled(False)
    assert off["enabled"] is False
    assert off["offset"] == [0.0] * 7
    assert rt.effective_delta_pose_offset() == [0.0] * 7


def test_clear_drops_offset_without_disabling(monkeypatch) -> None:
    rt = _rt_offset(configured=True, enabled=True, offset=[0.02, 0, 0, 0, 0, 0, 0])
    rt._delta_pose_offset_pending = [1e-5, 0, 0, 0, 0, 0, 0]
    monkeypatch.setattr(rt, "_live_gello_joints7", lambda: [0.3] * 7)
    cleared = rt.clear_delta_pose_offset()
    assert cleared["ok"] is True
    assert cleared["enabled"] is True
    assert cleared["offset"] == [0.0] * 7
    assert cleared["pending"] == [0.0] * 7
    assert rt._delta_pose_offset_prev_gello == [0.3] * 7


def test_go_arm_home_clears_offset(monkeypatch) -> None:
    rt = _rt_offset(configured=True, enabled=True, offset=[0.2, 0, 0, 0, 0, 0, 0])
    rt._home_lock = threading.Lock()
    rt._home_joints_rad = [0.1] * 6
    rt._home_source = "test"
    rt._home_duration_s = 1.0
    monkeypatch.setattr(rt, "_live_gello_joints7", lambda: [0.0] * 7)
    calls = {}

    def _cmd(**kw):
        calls.update(kw)
        return {"ok": True, "joints_rad": kw.get("joints_rad")}

    monkeypatch.setattr(rt, "arm_command", _cmd)
    out = rt.go_arm_home()
    assert out["ok"] is True
    assert calls.get("apply_gello_bias") is False
    assert rt._delta_pose_offset == [0.0] * 7
    assert rt._delta_pose_offset_pending == [0.0] * 7
