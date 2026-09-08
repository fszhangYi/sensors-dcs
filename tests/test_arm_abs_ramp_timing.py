"""Unit tests for abs-send T(d) + cosine S-curve path helpers."""

from __future__ import annotations

from sensors_dcs.runtime import Orchestrator


def test_abs_move_duration_scales_with_d() -> None:
    t_near = Orchestrator._abs_move_duration_s(
        d_rad=0.01, v_norm_rad_s=0.02, t_min_s=0.5, t_max_s=30.0
    )
    t_far = Orchestrator._abs_move_duration_s(
        d_rad=0.4, v_norm_rad_s=0.02, t_min_s=0.5, t_max_s=30.0
    )
    assert abs(t_near - 0.5) < 1e-9  # clamped to t_min
    assert abs(t_far - 20.0) < 1e-9  # 0.4/0.02


def test_go_arm_home_is_abs_send_sugar() -> None:
    """Home must call arm_command with scale_by_d + timing knobs (not fixed duration)."""
    import threading

    from sensors_dcs.config import AgentConfig, ArmAbsRampConfig, DcsConfig

    cfg = DcsConfig(
        sensors_config="./sensors_default.yaml",
        home_joints_rad=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        home_duration_s=20.0,
        arm_abs_ramp=ArmAbsRampConfig(),
        agents=[AgentConfig(id="arm", type="arm", sensor_id="a", hz=5)],
    )
    orch = Orchestrator.__new__(Orchestrator)
    orch.cfg = cfg
    orch._home_lock = threading.Lock()
    orch._home_joints_rad = list(cfg.home_joints_rad)
    orch._home_duration_s = 20.0
    orch._home_source = "yaml"
    captured: dict = {}

    def fake_arm_command(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "duration_s": 1.5, "phase": "ramping"}

    orch.arm_command = fake_arm_command  # type: ignore[method-assign]
    out = orch.go_arm_home(
        duration_s=99.0,
        t_min_s=0.2,
        t_max_s=5.0,
        v_norm_rad_s=0.01,
    )
    assert out["ok"] is True
    assert captured["timing"] == "scale_by_d"
    assert captured["joints_rad"] == cfg.home_joints_rad
    assert captured["t_min_s"] == 0.2
    assert captured["t_max_s"] == 5.0
    assert captured["v_norm_rad_s"] == 0.01
    assert "duration_s" not in captured


def test_abs_move_duration_clamps_tmax() -> None:
    t = Orchestrator._abs_move_duration_s(
        d_rad=2.0, v_norm_rad_s=0.02, t_min_s=0.1, t_max_s=10.0
    )
    assert abs(t - 10.0) < 1e-9


def test_cosine_path_endpoints_and_slower_ends() -> None:
    qa = [0.0] * 6
    qg = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    n = 20
    path = Orchestrator._interp_path(qa, qg, n, profile="cosine")
    assert len(path) == n
    assert abs(path[-1][0] - 1.0) < 1e-12
    # First step smaller than mid step (ease-in/out)
    step0 = abs(path[0][0] - qa[0])
    mid_i = n // 2
    step_mid = abs(path[mid_i][0] - path[mid_i - 1][0])
    assert step0 < step_mid


def test_linear_path_unchanged_for_sync() -> None:
    qa = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
    qg = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5]
    path = Orchestrator._interp_path(qa, qg, 100, profile="linear")
    mid = path[49]
    for i in range(6):
        assert abs(mid[i] - (qa[i] + 0.5 * (qg[i] - qa[i]))) < 1e-9


def test_path_peak_step_cosine_gt_linear_avg() -> None:
    qa = [0.0] * 6
    qg = [0.8, 0.0, 0.0, 0.0, 0.0, 0.0]
    n = 100
    lin = Orchestrator._interp_path(qa, qg, n, profile="linear")
    cos = Orchestrator._interp_path(qa, qg, n, profile="cosine")
    peak_lin = Orchestrator._path_peak_step(qa, lin)
    peak_cos = Orchestrator._path_peak_step(qa, cos)
    assert peak_cos > peak_lin  # mid of S-curve faster than uniform
