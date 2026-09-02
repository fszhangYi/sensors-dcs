from __future__ import annotations

import math

from sensors_dcs.agents.gello_agent import apply_gello_affine, invert_gello_affine


def test_gello_affine_roundtrip() -> None:
    offsets = [math.pi / 2, 2 * math.pi, 0.0]
    signs = [1.0, -1.0, 1.0]
    raw = [1.0, 0.5, -0.25]
    cal = apply_gello_affine(raw, offsets=offsets, signs=signs)
    assert cal[0] == raw[0] - offsets[0]
    assert cal[1] == (raw[1] - offsets[1]) * -1
    back = invert_gello_affine(cal, offsets=offsets, signs=signs)
    for a, b in zip(back, raw):
        assert abs(a - b) < 1e-12


def test_gello_affine_matches_gello_py_formula() -> None:
    # Same formula as DynamixelRobot.get_joint_state
    offsets = [1 * math.pi / 2, 4 * math.pi / 2]
    signs = [1, -1]
    raw = [2.0, 3.0]
    got = apply_gello_affine(raw, offsets=offsets, signs=signs)
    expect = [(raw[i] - offsets[i]) * signs[i] for i in range(2)]
    assert got == expect
