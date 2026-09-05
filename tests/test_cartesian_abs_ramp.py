"""Cartesian-linear abs ramp path (FK → lerp → IK)."""

from __future__ import annotations

import math

import pytest

from sensors_dcs.arm_pose import (
    _lerp_xyzrpy,
    cartesian_linear_joint_path,
    joints_rad_to_xyzrpy,
)


def test_lerp_xyz_is_linear() -> None:
    a = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    b = [0.2, -0.1, 0.05, 0.0, 0.0, 0.3]
    mid = _lerp_xyzrpy(a, b, 0.5)
    assert abs(mid[0] - 0.1) < 1e-9
    assert abs(mid[1] - (-0.05)) < 1e-9
    assert abs(mid[2] - 0.025) < 1e-9


def test_cartesian_linear_joint_path_tcp_straight() -> None:
    qa = [0.0, -0.4, 0.6, 0.0, 0.5, 0.0]
    pose_a = joints_rad_to_xyzrpy(qa)
    if pose_a is None:
        pytest.skip("FK unavailable in this environment")

    # Small TCP translate in +x via a nearby joint nudge then path between them.
    # Use IK goal from a Cartesian offset when possible.
    from sensors_dcs.arm_pose import xyzrpy_to_joints_rad

    pose_g = list(pose_a)
    pose_g[0] = float(pose_a[0]) + 0.03
    pose_g[1] = float(pose_a[1]) + 0.01
    ik = xyzrpy_to_joints_rad(pose_g, q_seed_rad=qa)
    if not ik.get("ok") or not ik.get("joints_rad"):
        pytest.skip(f"IK unavailable/failed: {ik.get('error')}")
    qg = list(ik["joints_rad"])

    n = 10
    built = cartesian_linear_joint_path(qa, qg, n)
    assert built["ok"], built.get("error")
    path = built["joints_path"]
    assert len(path) == n
    assert path[-1] == qg

    # Mid TCP should sit near the geometric midpoint (straight-line xyz).
    mid_q = path[n // 2 - 1]  # k=n/2 → alpha=0.5 for n even? k=5 → 5/10=0.5 for n=10, index 4
    # path[k-1] corresponds to alpha=k/n for k<n; for k=5 → path[4]
    mid_pose = joints_rad_to_xyzrpy(path[4])
    assert mid_pose is not None
    expect = [
        pose_a[0] + 0.5 * (pose_g[0] - pose_a[0]),
        pose_a[1] + 0.5 * (pose_g[1] - pose_a[1]),
        pose_a[2] + 0.5 * (pose_g[2] - pose_a[2]),
    ]
    err = math.sqrt(sum((mid_pose[i] - expect[i]) ** 2 for i in range(3)))
    assert err < 5e-3, f"mid TCP not on chord: err={err:.4e}m mid={mid_pose[:3]} expect={expect}"
