"""Unit tests for gello→arm one-shot alignment helpers."""

from __future__ import annotations

from sensors_dcs.runtime import Orchestrator


def test_interp_path_endpoints_and_count() -> None:
    qa = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
    qg = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5]
    path = Orchestrator._interp_path(qa, qg, 100)
    assert len(path) == 100
    assert path[-1] == qg
    # Midpoint ~ halfway
    mid = path[49]
    for i in range(6):
        assert abs(mid[i] - (qa[i] + 0.5 * (qg[i] - qa[i]))) < 1e-9


def test_delta_max_joint() -> None:
    qa = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    qg = [0.1, -0.5, 0.2, 0.0, 0.0, 0.0]
    dmax, ji = Orchestrator._delta_max_joint(qa, qg)
    assert ji == 1
    assert abs(dmax - 0.5) < 1e-12


def test_ramp_n_from_duration_hz() -> None:
    # 20s * 5Hz = 100
    n = int(round(20.0 * 5.0))
    assert n == 100
    qa = [0.0] * 6
    qg = [0.8, 0.0, 0.0, 0.0, 0.0, 0.0]
    path = Orchestrator._interp_path(qa, qg, n)
    step0 = abs(path[0][0] - qa[0])
    assert abs(step0 - 0.8 / 100) < 1e-12
