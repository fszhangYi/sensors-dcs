"""Wire-format helpers for serve robot_state / next_state (option A)."""

from __future__ import annotations

import math

import pytest

from sensors_dcs.arm_pose import (
    compose_delta_xyzrpy,
    normalize_recv_state_format,
    normalize_send_state_format,
)
from sensors_dcs.runtime import Orchestrator


def test_normalize_send_rejects_delta() -> None:
    assert normalize_send_state_format("pose") == "pose"
    assert normalize_send_state_format("joints") == "joints"
    with pytest.raises(ValueError, match="delta_pose"):
        normalize_send_state_format("delta_pose")


def test_normalize_recv_allows_delta() -> None:
    assert normalize_recv_state_format("delta_pose") == "delta_pose"
    assert normalize_recv_state_format(None) == "pose"


def test_compose_delta_identity() -> None:
    cur = [0.4, 0.0, 0.3, 0.0, 0.0, 0.0]
    delta = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    out = compose_delta_xyzrpy(cur, delta)
    for a, b in zip(cur, out):
        assert abs(a - b) < 1e-9


def test_compose_delta_translation() -> None:
    cur = [0.4, 0.0, 0.3, 0.0, 0.0, 0.0]
    delta = [0.01, 0.02, -0.03, 0.0, 0.0, 0.0]
    out = compose_delta_xyzrpy(cur, delta)
    assert abs(out[0] - 0.41) < 1e-9
    assert abs(out[1] - 0.02) < 1e-9
    assert abs(out[2] - 0.27) < 1e-9


def test_decode_joints_passthrough(monkeypatch) -> None:
    rt = Orchestrator.__new__(Orchestrator)
    rt.agents = {}
    out = Orchestrator._decode_next_state(
        rt, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7], "joints"
    )
    assert out["ok"] is True
    assert out["joints_rad"] == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]


def test_decode_delta_composes(monkeypatch) -> None:
    rt = Orchestrator.__new__(Orchestrator)
    rt.agents = {}
    monkeypatch.setattr(
        rt, "_current_tcp_xyzrpy", lambda: [0.4, 0.0, 0.3, 0.0, 0.0, 0.0]
    )
    monkeypatch.setattr(
        rt,
        "_xyzrpy_to_joints",
        lambda xyz: {"ok": True, "joints_rad": [1.0] * 6, "error": None},
    )
    out = Orchestrator._decode_next_state(
        rt, [0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5], "delta_pose"
    )
    assert out["ok"] is True
    assert out["joints_rad"] == [1.0] * 6
    assert abs(out["goal_xyzrpy"][0] - 0.41) < 1e-9
    assert math.isfinite(out["goal_xyzrpy"][2])
