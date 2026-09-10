# Pure topology / pose / term_cond checks for flow orchestration.
from __future__ import annotations

from sensors_dcs.flow_graph import (
    chain_order,
    find_entry_ids,
    has_cycle,
    pose_near,
    term_cond_configured,
    term_cond_triggered,
    validate_chain,
)


def test_find_single_entry() -> None:
    modules = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
    assert find_entry_ids(modules, edges) == ["a"]
    ok, entry, err = validate_chain(modules, edges)
    assert ok and entry == "a" and err is None
    assert chain_order(modules, edges, "a") == ["a", "b", "c"]


def test_need_single_entry() -> None:
    modules = [{"id": "a"}, {"id": "b"}]
    edges: list[dict] = []
    ok, entry, err = validate_chain(modules, edges)
    assert not ok and entry is None and err == "need_single_entry"


def test_cycle_detected() -> None:
    modules = [{"id": "a"}, {"id": "b"}]
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]
    assert has_cycle(modules, edges)
    ok, _, err = validate_chain(modules, edges)
    assert not ok and err == "cycle"


def test_out_degree_gt1() -> None:
    modules = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    edges = [{"from": "a", "to": "b"}, {"from": "a", "to": "c"}]
    ok, mid, err = validate_chain(modules, edges)
    assert not ok and err == "out_degree_gt1" and mid == "a"


def test_pose_near() -> None:
    expect = {"xyzrpy": [0.1, 0.2, 0.3, 0.0, 0.0, 0.0], "gripper": 0.5}
    tol = {"pos_m": 0.01, "rot_rad": 0.05, "grip": 0.05}
    ok, why = pose_near([0.105, 0.2, 0.3, 0.0, 0.0, 0.0], 0.52, expect, tol)
    assert ok and why is None
    ok, why = pose_near([0.2, 0.2, 0.3, 0.0, 0.0, 0.0], 0.5, expect, tol)
    assert not ok and why == "pos"
    ok, why = pose_near([0.1, 0.2, 0.3, 0.0, 0.0, 0.0], 0.7, expect, tol)
    assert not ok and why == "grip"


def test_term_cond() -> None:
    assert not term_cond_configured(None)
    assert not term_cond_configured({})
    assert term_cond_configured({"direction": "z_rise", "threshold": 0.4})
    assert term_cond_configured({"direction": "z_fall", "threshold": 0.1})
    assert not term_cond_configured({"direction": "z_rise", "threshold": None})
    # legacy fields still accepted
    assert term_cond_configured({"z_rise_to": 0.4})
    assert term_cond_configured({"z_fall_to": 0.1})
    assert not term_cond_triggered(0.3, {"direction": "z_rise", "threshold": 0.4})
    assert term_cond_triggered(0.41, {"direction": "z_rise", "threshold": 0.4})
    assert term_cond_triggered(0.05, {"direction": "z_fall", "threshold": 0.1})
    assert not term_cond_triggered(0.2, {"direction": "z_fall", "threshold": 0.1})
    assert term_cond_triggered(0.41, {"z_rise_to": 0.4})
    assert term_cond_triggered(0.05, {"z_fall_to": 0.1})
