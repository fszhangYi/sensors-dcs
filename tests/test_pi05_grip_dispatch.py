"""pi05 next_state grip → gripper_write on step."""

from __future__ import annotations

from sensors_dcs.runtime import Orchestrator


def test_next_state_grip_extract() -> None:
    assert Orchestrator._next_state_grip([0, 0, 0, 0, 0, 0, 0.32]) == 0.32
    assert Orchestrator._next_state_grip([0, 0, 0, 0, 0, 0]) is None
    assert Orchestrator._next_state_grip(None) is None
    assert Orchestrator._next_state_grip([0, 0, 0, 0, 0, 0, float("nan")]) is None


def test_pi05_step_commands_gripper(monkeypatch) -> None:
    """Successful step with 7-d next_state must dispatch gripper_write."""
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

        def step(self) -> dict:
            return {
                "ok": True,
                "connected": True,
                "next_state": [0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 0.41],
                "latency_ms": 1.0,
                "term_flag": 0.0,
                "reject_flag": 0,
            }

        def status_payload(self) -> dict:
            return {"connected": True}

    fake = _FakePi05()
    monkeypatch.setattr(rt, "_pi05_agent", lambda agent_id=None: fake)
    monkeypatch.setattr(
        rt,
        "_next_state_to_joints",
        lambda ns: {"ok": True, "joints_rad": [0.0] * 6, "error": None},
    )

    def _grip_cmd(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(rt, "gripper_command", _grip_cmd)

    out = Orchestrator.pi05_step(rt)
    assert out["ok"] is True
    assert out["next_grip"] == 0.41
    assert out["grip_ok"] is True
    assert calls and calls[0]["position_norm"] == 0.41
    assert calls[0].get("allow_during_sync") is True
