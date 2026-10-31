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
            teleop_jump_abort_rad=0.35,
            pick_mode="latest",
            eat_max_rad_s=1.5,
            eat_catch_up_rad_s=3.0,
            eat_lag_path_max_rad=0.25,
            eat_lag_age_max_s=0.15,
            eat_drop_stale=False,
        ),
        agents=[
            AgentConfig(id="gello", type="gello", sensor_id="g", hz=50, buffer_frames=8),
        ],
    )
    orch._arm_sync_enabled = False
    orch.agents = {}
    orch._arm_teleop_lock = __import__("threading").Lock()
    orch._arm_teleop_setpoint = []
    orch._arm_teleop_last_sent = None
    orch._arm_teleop_pending = None
    orch._arm_teleop_pick_mode = "latest"
    orch._arm_teleop_lag_path_rad = None
    orch._arm_teleop_lag_age_s = None
    orch._arm_teleop_last_step_rad = None
    orch._arm_teleop_catch_up = False
    orch._arm_teleop_enabled = False
    orch._arm_teleop_phase = "idle"
    orch._arm_teleop_hz = 100.0
    orch._arm_teleop_last_message = None
    orch._arm_teleop_last_t_wall = None
    orch._arm_teleop_gello_id = None
    orch._arm_teleop_arm_id = None
    orch._arm_teleop_writer_id = None
    orch._arm_teleop_delta_max = None
    orch._arm_teleop_worst_joint = None
    orch._arm_teleop_last_ok = None
    orch._arm_teleop_last_error = None
    orch._arm_teleop_write_count = 0
    orch._arm_teleop_rate_limited = False
    orch._arm_teleop_write_path = None
    return orch


def _agent_with_joints(joints: list[float] | None, *, armed: bool = True) -> MagicMock:
    agent = MagicMock()
    agent.sensor = SimpleNamespace(armed=armed)
    fr = None
    if joints is not None:
        fr = SimpleNamespace(payload={"joints_rad": list(joints), "armed": armed})
    agent.ring.latest.get.return_value = fr
    return agent


def test_teleop_config_ignores_legacy_step_max() -> None:
    cfg = GelloArmTeleopConfig.model_validate(
        {"teleop_hz": 100, "teleop_step_max_rad": 0.05}
    )
    assert cfg.teleop_hz == 100.0
    assert not hasattr(cfg, "teleop_step_max_rad") or "teleop_step_max_rad" not in cfg.model_dump()


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


def test_delta_max_joint_reports_worst_axis() -> None:
    dmax, ji = Orchestrator._delta_max_joint(
        [0.0] * 6, [0.01, 0.0, 0.4, 0.0, 0.0, 0.0]
    )
    assert ji == 2
    assert abs(dmax - 0.4) < 1e-12


def test_pick_mode_config_aliases() -> None:
    assert GelloArmTeleopConfig.model_validate({"pick_mode": "tt"}).pick_mode == "eat_stream"
    assert GelloArmTeleopConfig.model_validate({"pick_mode": "LATEST"}).pick_mode == "latest"


def test_advance_path_respects_linf_budget() -> None:
    q0 = [0.0] * 6
    samples = [
        {"t": 1.0, "q": [0.10, 0.0, 0.0, 0.0, 0.0, 0.0]},
        {"t": 2.0, "q": [0.30, 0.0, 0.0, 0.0, 0.0, 0.0]},
    ]
    q_cmd, rem, step = Orchestrator._arm_teleop_advance_path(q0, samples, budget=0.15)
    assert abs(q_cmd[0] - 0.15) < 1e-9
    assert abs(step - 0.15) < 1e-9
    # First sample fully consumed; second partially replaced as new head.
    assert len(rem) == 1
    assert abs(rem[0]["q"][0] - 0.15) < 1e-9


def test_pick_latest_commit_trims_prefix() -> None:
    orch = _make_orch()
    orch._arm_teleop_pick_mode = "latest"
    orch._arm_teleop_last_sent = [0.0] * 6
    orch._arm_teleop_setpoint = [
        {"t": 1.0, "q": [0.01, 0.0, 0.0, 0.0, 0.0, 0.0]},
        {"t": 2.0, "q": [0.02, 0.0, 0.0, 0.0, 0.0, 0.0]},
    ]
    with orch._arm_teleop_lock:
        q = orch._arm_teleop_pick_setpoint()
        assert q is not None and abs(q[0] - 0.02) < 1e-12
        orch._arm_teleop_commit_setpoint(ok=False)
        assert orch._arm_teleop_pending is not None
        assert len(orch._arm_teleop_setpoint) == 2
        orch._arm_teleop_commit_setpoint(ok=True)
        assert orch._arm_teleop_pending is None
        assert orch._arm_teleop_setpoint == []
        assert orch._arm_teleop_last_sent is not None
        assert abs(orch._arm_teleop_last_sent[0] - 0.02) < 1e-12


def test_pick_eat_stream_partial_consume() -> None:
    orch = _make_orch()
    orch._arm_teleop_pick_mode = "eat_stream"
    orch._arm_teleop_hz = 100.0
    orch.cfg.gello_arm_teleop = GelloArmTeleopConfig(
        eat_max_rad_s=10.0,  # 0.1 rad/tick at 100 Hz
        eat_catch_up_rad_s=10.0,
        eat_lag_path_max_rad=10.0,
        eat_lag_age_max_s=10.0,
    )
    orch._arm_teleop_last_sent = [0.0] * 6
    orch._arm_teleop_setpoint = [
        {"t": 1.0, "q": [0.25, 0.0, 0.0, 0.0, 0.0, 0.0]},
    ]
    with orch._arm_teleop_lock:
        q = orch._arm_teleop_pick_setpoint()
        assert q is not None and abs(q[0] - 0.10) < 1e-9
        orch._arm_teleop_commit_setpoint(ok=True)
        assert abs(orch._arm_teleop_last_sent[0] - 0.10) < 1e-9
        assert len(orch._arm_teleop_setpoint) == 1
        assert abs(orch._arm_teleop_setpoint[0]["q"][0] - 0.10) < 1e-9


def test_set_pick_mode_blocked_when_teleop_enabled() -> None:
    orch = _make_orch()
    orch._arm_teleop_enabled = True
    orch._arm_teleop_phase = "teleop"
    out = orch.set_gello_arm_teleop_pick_mode("eat_stream")
    assert out["ok"] is False
    assert orch._arm_teleop_pick_mode == "latest"
    orch._arm_teleop_enabled = False
    orch._arm_teleop_phase = "idle"
    out2 = orch.set_gello_arm_teleop_pick_mode("eat_stream")
    assert out2["ok"] is True
    assert out2["pick_mode"] == "eat_stream"
