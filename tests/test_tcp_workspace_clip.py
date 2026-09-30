"""Unit tests for TCP workspace box clip (arm_pose)."""

from __future__ import annotations

import pytest

from sensors_dcs.arm_pose import (
    TCP_CLIP_FREE,
    clip_tcp_xyzrpy,
    default_tcp_clip,
    normalize_tcp_clip,
    tcp_clip_is_active,
)


def test_default_tcp_clip_all_free() -> None:
    b = default_tcp_clip()
    assert all(v == TCP_CLIP_FREE for v in b.values())
    assert not tcp_clip_is_active(b)
    assert not tcp_clip_is_active(None)


def test_normalize_none_and_partial() -> None:
    assert normalize_tcp_clip(None) == default_tcp_clip()
    b = normalize_tcp_clip({"x_min": 0.1, "z_max": 0.5})
    assert b["x_min"] == 0.1
    assert b["x_max"] == TCP_CLIP_FREE
    assert b["z_max"] == 0.5
    assert tcp_clip_is_active(b)


def test_normalize_lo_gt_hi() -> None:
    with pytest.raises(ValueError, match="x_min"):
        normalize_tcp_clip({"x_min": 0.5, "x_max": 0.1})


def test_normalize_rejects_non_finite() -> None:
    with pytest.raises(ValueError, match="y_min"):
        normalize_tcp_clip({"y_min": float("nan")})


def test_clip_noop_when_all_free() -> None:
    pose = [0.4, -0.1, 0.3, 0.01, -0.02, 0.03]
    r = clip_tcp_xyzrpy(pose, default_tcp_clip())
    assert r["ok"]
    assert r["applied"] is False
    assert r["goal_xyzrpy"] == pose
    assert r["goal_xyzrpy_raw"] == pose


def test_clip_xyz_only_leaves_rpy() -> None:
    pose = [1.0, -1.0, 2.0, 0.5, -0.5, 0.25]
    b = normalize_tcp_clip(
        {
            "x_min": 0.0,
            "x_max": 0.5,
            "y_min": -0.2,
            "y_max": 0.2,
            "z_min": 0.1,
            "z_max": 0.8,
        }
    )
    r = clip_tcp_xyzrpy(pose, b)
    assert r["ok"]
    assert r["applied"] is True
    assert r["goal_xyzrpy_raw"] == pose
    assert r["goal_xyzrpy"][:3] == pytest.approx([0.5, -0.2, 0.8])
    assert r["goal_xyzrpy"][3:] == pose[3:]


def test_clip_mixed_minus_one() -> None:
    pose = [0.0, 0.5, -0.5, 0.0, 0.0, 0.0]
    b = normalize_tcp_clip({"y_max": 0.2, "z_min": 0.0})  # x free both sides
    r = clip_tcp_xyzrpy(pose, b)
    assert r["ok"]
    assert r["applied"] is True
    assert r["goal_xyzrpy"][:3] == pytest.approx([0.0, 0.2, 0.0])


def test_clip_inside_box_not_applied() -> None:
    pose = [0.2, 0.0, 0.4, 0.0, 0.0, 0.0]
    b = normalize_tcp_clip(
        {"x_min": 0.0, "x_max": 0.5, "y_min": -0.1, "y_max": 0.1, "z_min": 0.2, "z_max": 0.6}
    )
    r = clip_tcp_xyzrpy(pose, b)
    assert r["ok"]
    assert r["applied"] is False
    assert r["goal_xyzrpy"] == pose
