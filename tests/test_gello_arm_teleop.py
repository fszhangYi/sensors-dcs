"""Unit tests for gello→arm teleop helpers and gate logic."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from sensors_dcs.config import (
    AgentConfig,
    DcsConfig,
    GelloArmTeleopConfig,
    RecordConfig,
    RuntimeConfig,
)
from sensors_dcs.runtime import Orchestrator


def test_rate_limit_step_caps_delta() -> None:
    prev = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    target = [0.2, 0.0, 0.0, 0.0, 0.0, 0.0]
    out = Orchestrator._rate_limit_step(prev, target, 0.05)
    dmax, ji = Orchestrator._delta_max_joint(prev, out)
    assert ji == 0
    assert abs(dmax - 0.05) < 1e-12
    assert abs(out[0] - 0.05) < 1e-12


def test_rate_limit_step_passthrough_when_small() -> None:
    prev = [0.0] * 6
    target = [0.01, 0.02, 0.0, 0.0, 0.0, 0.0]
    out = Orchestrator._rate_limit_step(prev, target, 0.05)
    assert out == [0.01, 0.02, 0.0, 0.0, 0.0, 0.0]


def _make_orch() -> Orchestrator:
    """Minimal Orchestrator without SensorManager (bypass __init__)."""
    orch = object.__new__(Orchestrator)
    orch.cfg = DcsConfig(
        sensors_config="/tmp/unused.yaml",
        dry_run=True,
        runtime=RuntimeConfig(),
        record=RecordConfig(save_dir="/tmp", episode_index=0),
        gello_arm_teleop=GelloArmTeleopConfig(
            teleop_enter_max_rad=0.05,
            teleop_step_max_rad=0.05,
            teleop_jump_abort_rad=0.35,
        ),
        agents=[
            AgentConfig(id="gello", type="gello", sensor_id="g", hz=50, buffer_frames=8),
        ],
    )
    orch._arm_sync_enabled = False
    orch.agents = {}
    return orch


def _agent_with_joints(joints: list[float] | None, *, armed: bool = True) -> MagicMock:
    agent = MagicMock()
    agent.sensor = SimpleNamespace(armed=armed)
    fr = None
    if joints is not None:
        fr = SimpleNamespace(payload={"joints_rad": list(joints), "armed": armed})
    agent.ring.latest.get.return_value = fr
    return agent


def test_teleop_gate_requires_armed() -> None:
    orch = _make_orch()
    gello = _agent_with_joints([0.0] * 6)
    reader = _agent_with_joints([0.0] * 6)
    writer = _agent_with_joints([0.0] * 6, armed=False)
    gate = orch._gello_arm_teleop_gate(gello, reader, writer)
    assert gate["ok"] is False
    assert gate["gate_failed"] is True
    assert "Arm" in str(gate["error"])


def test_teleop_gate_requires_close_alignment() -> None:
    orch = _make_orch()
    gello = _agent_with_joints([0.2, 0.0, 0.0, 0.0, 0.0, 0.0])
    reader = _agent_with_joints([0.0] * 6)
    writer = _agent_with_joints([0.0] * 6, armed=True)
    gate = orch._gello_arm_teleop_gate(gello, reader, writer)
    assert gate["ok"] is False
    assert gate["gate_failed"] is True
    assert "同步" in str(gate["error"])
    assert abs(float(gate["delta_max"]) - 0.2) < 1e-12


def test_teleop_gate_ok_when_aligned_and_armed() -> None:
    orch = _make_orch()
    q = [0.01, -0.02, 0.0, 0.0, 0.0, 0.0]
    gello = _agent_with_joints(q)
    reader = _agent_with_joints([0.0] * 6)
    writer = _agent_with_joints([0.0] * 6, armed=True)
    gate = orch._gello_arm_teleop_gate(gello, reader, writer)
    assert gate["ok"] is True
    assert float(gate["delta_max"]) <= 0.05 + 1e-12


def test_teleop_gate_blocks_when_sync_active() -> None:
    orch = _make_orch()
    orch._arm_sync_enabled = True
    gello = _agent_with_joints([0.0] * 6)
    reader = _agent_with_joints([0.0] * 6)
    writer = _agent_with_joints([0.0] * 6, armed=True)
    gate = orch._gello_arm_teleop_gate(gello, reader, writer)
    assert gate["ok"] is False
    assert "sync" in str(gate["error"]).lower()
