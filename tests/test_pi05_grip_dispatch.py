"""pi05 next_state grip is deferred to a one-shot write at abs-ramp start."""

from __future__ import annotations

from sensors_dcs.runtime import Orchestrator


def test_next_state_grip_extract() -> None:
    assert Orchestrator._next_state_grip([0, 0, 0, 0, 0, 0, 0.32]) == 0.32
    assert Orchestrator._next_state_grip([0, 0, 0, 0, 0, 0]) is None
    assert Orchestrator._next_state_grip(None) is None
    assert Orchestrator._next_state_grip([0, 0, 0, 0, 0, 0, float("nan")]) is None


def test_path_alpha_s_curve_small_ends() -> None:
    """Seven-segment / cosine: Δα smaller near ends than mid-path."""
    n = 20
    alphas = [
        Orchestrator._path_alpha(k / n, profile="seven_segment")
        for k in range(1, n + 1)
    ]
    d0 = alphas[0] - 0.0
    d_mid = alphas[n // 2] - alphas[n // 2 - 1]
    d_end = 1.0 - alphas[-2]
    assert d0 < d_mid
    assert d_end < d_mid


def test_pi05_step_defers_gripper(monkeypatch) -> None:
    """Successful step with 7-d next_state must NOT dispatch gripper_write yet."""
    from sensors_dcs.agents.pi05_agent import Pi05ClientAgent

    rt = Orchestrator.__new__(Orchestrator)
    rt.agents = {}
    rt._sync_enabled = False
    calls: list[dict] = []

    class _FakePi05(Pi05ClientAgent):
        def __init__(self) -> None:
            self.agent_id = "pi05"
            self.kind = "pi05"

        def set_prompt(self, prompt: str) -> dict:
            return {"ok": True}

        def step(self, **kwargs) -> dict:
            return {
                "ok": True,
                "connected": True,
                "next_state": [0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 0.41],
                "latency_ms": 1.0,
                "term_flag": 0.0,
                "reject_flag": 0,
            }

        def note_wire_meta(self, **kwargs) -> None:
            return None

        def _push_status_frame(self):
            return None

        def status_payload(self) -> dict:
            return {"connected": True}

    fake = _FakePi05()
    monkeypatch.setattr(rt, "_pi05_agent", lambda agent_id=None: fake)
    monkeypatch.setattr(
        rt,
        "_decode_next_state",
        lambda ns, fmt: {
            "ok": True,
            "joints_rad": [0.0] * 6,
            "error": None,
            "goal_xyzrpy": list(ns)[:6],
        },
    )

    def _grip_cmd(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(rt, "gripper_command", _grip_cmd)

    out = Orchestrator.pi05_step(rt)
    assert out["ok"] is True
    assert out["next_grip"] == 0.41
    assert out["grip_ok"] is True
    assert out["grip_deferred"] is True
    assert calls == []


def _make_ramp_rt(monkeypatch, *, grip_fn):
    import threading

    from sensors_dcs.agents.arm_agent import ArmAgent
    from sensors_dcs.agents.arm_write_agent import ArmWriteAgent

    rt = Orchestrator.__new__(Orchestrator)
    rt.agents = {}
    rt._stop = threading.Event()
    rt._abs_ramp_lock = threading.Lock()
    rt._abs_ramp_stop = threading.Event()
    rt._abs_ramp_thread = None
    rt._abs_ramp_enabled = False
    rt._abs_ramp_phase = "idle"
    rt._abs_ramp_index = 0
    rt._abs_ramp_n = 0
    rt._abs_ramp_duration_s = 0.0
    rt._abs_ramp_hz = 5.0
    rt._abs_ramp_arm_id = None
    rt._abs_ramp_writer_id = None
    rt._abs_ramp_last_ok = None
    rt._abs_ramp_last_error = None
    rt._abs_ramp_last_message = None
    rt._abs_ramp_write_count = 0
    rt._abs_ramp_last_t_wall = None
    rt._abs_ramp_target_joints = None
    rt._abs_ramp_timing = None
    rt._abs_ramp_delta_max = None
    rt._abs_ramp_gripper = None

    joint_writes: list[list[float]] = []

    class _Latest:
        def get(self):
            return type("F", (), {"payload": {"joints_rad": [0.0] * 6}})()

    sensor = type("S", (), {"id": "s1", "armed": True, "max_delta_rad": 0.0})()
    reader = ArmAgent.__new__(ArmAgent)
    reader.agent_id = "arm-r"
    reader.sensor = sensor
    reader.ring = type("Ring", (), {"latest": _Latest()})()

    writer = ArmWriteAgent.__new__(ArmWriteAgent)
    writer.agent_id = "arm-w"
    writer.sensor = sensor
    writer.ring = type("Ring", (), {"latest": _Latest()})()

    def _cmd(**kwargs):
        if "joints_rad" in kwargs:
            joint_writes.append(list(kwargs["joints_rad"]))
        return {"ok": True}

    writer.command = _cmd  # type: ignore[method-assign]
    rt.agents = {"arm-r": reader, "arm-w": writer}
    monkeypatch.setattr(rt, "_joints6_from_ring", lambda ag: [0.0] * 6)
    monkeypatch.setattr(rt, "gripper_command", grip_fn)
    return rt, joint_writes


def test_abs_ramp_writes_gripper_once_at_start(monkeypatch) -> None:
    """Grip is one Modbus write before the joint loop — not per waypoint."""
    grip_calls: list[dict] = []
    rt, joint_writes = _make_ramp_rt(
        monkeypatch,
        grip_fn=lambda **kw: (grip_calls.append(kw) or {"ok": True}),
    )

    n = 10
    g1 = 0.50
    Orchestrator._arm_abs_ramp_loop(
        rt,
        "arm-r",
        "arm-w",
        [0.0] * 6,
        [0.2, 0, 0, 0, 0, 0],
        n,
        100.0,
        "seven_segment",
        0.10,
        0.15,
        g1,
    )
    assert len(joint_writes) == n
    assert len(grip_calls) == 1
    assert abs(grip_calls[0]["position_norm"] - g1) < 1e-9
    assert grip_calls[0].get("allow_during_sync") is True
    assert rt._abs_ramp_phase == "completed"
    assert rt._abs_ramp_last_ok is True
    assert "夹爪一次下发" in str(rt._abs_ramp_last_message)


def test_abs_ramp_gripper_soft_fail_does_not_abort_joints(monkeypatch) -> None:
    """Gripper Modbus failure must not stop joint waypoints."""
    n = 8

    def _grip(**kw):
        return {"ok": False, "error": "modbus write REG_POSITION failed"}

    rt, joint_writes = _make_ramp_rt(monkeypatch, grip_fn=_grip)
    Orchestrator._arm_abs_ramp_loop(
        rt,
        "arm-r",
        "arm-w",
        [0.0] * 6,
        [0.2, 0, 0, 0, 0, 0],
        n,
        100.0,
        "seven_segment",
        0.10,
        0.15,
        0.50,
    )
    assert len(joint_writes) == n
    assert rt._abs_ramp_phase == "completed"
    assert rt._abs_ramp_last_ok is True
    assert rt._abs_ramp_last_error is None
    assert "夹爪软失败" in str(rt._abs_ramp_last_message)


def test_abs_ramp_without_gripper_skips_grip(monkeypatch) -> None:
    grip_calls: list[dict] = []
    rt, joint_writes = _make_ramp_rt(
        monkeypatch,
        grip_fn=lambda **kw: (grip_calls.append(kw) or {"ok": True}),
    )
    Orchestrator._arm_abs_ramp_loop(
        rt,
        "arm-r",
        "arm-w",
        [0.0] * 6,
        [0.1, 0, 0, 0, 0, 0],
        5,
        100.0,
        "linear",
        0.10,
        0.15,
        None,
    )
    assert len(joint_writes) == 5
    assert grip_calls == []
    assert rt._abs_ramp_phase == "completed"
