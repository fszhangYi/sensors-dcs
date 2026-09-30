"""TCP flange offset: YAML config → arm_pose process-wide active offset."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from sensors_dcs.arm_pose import (
    DEFAULT_TCP_XYZ,
    cartesian_payload,
    get_tcp_xyz,
    normalize_tcp_xyz,
    set_tcp_xyz,
)
from sensors_dcs.config import DcsConfig


@pytest.fixture(autouse=True)
def _reset_tcp_xyz():
    set_tcp_xyz(DEFAULT_TCP_XYZ)
    yield
    set_tcp_xyz(DEFAULT_TCP_XYZ)


def test_normalize_default_and_custom() -> None:
    assert normalize_tcp_xyz(None) == DEFAULT_TCP_XYZ
    assert normalize_tcp_xyz([0.01, -0.02, 0.2]) == (0.01, -0.02, 0.2)


def test_normalize_rejects_bad() -> None:
    with pytest.raises(ValueError, match="exactly 3"):
        normalize_tcp_xyz([0.0, 0.18])
    with pytest.raises(ValueError, match="finite"):
        normalize_tcp_xyz([0.0, 0.0, float("nan")])


def test_set_get_tcp_xyz() -> None:
    assert get_tcp_xyz() == DEFAULT_TCP_XYZ
    set_tcp_xyz([0.0, 0.0, 0.22])
    assert get_tcp_xyz() == (0.0, 0.0, 0.22)
    payload = cartesian_payload(None)
    assert payload["cartesian_tcp_xyz"] == [0.0, 0.0, 0.22]


def test_postprocess_defaults_and_resolve_export_tcp() -> None:
    from sensors_dcs.postprocess_service import postprocess_defaults, resolve_export_tcp_xyz

    set_tcp_xyz([0.02, -0.01, 0.19])
    d = postprocess_defaults()
    assert d["tcp_xyz"] == [0.02, -0.01, 0.19]
    assert resolve_export_tcp_xyz(None) == (0.02, -0.01, 0.19)
    assert resolve_export_tcp_xyz([0.0, 0.0, 0.3]) == (0.0, 0.0, 0.3)


def test_dcs_config_tcp_xyz_optional() -> None:
    cfg = DcsConfig(sensors_config="/tmp/x.yaml")
    assert cfg.tcp_xyz is None
    cfg2 = DcsConfig(sensors_config="/tmp/x.yaml", tcp_xyz=[0.0, 0.0, 0.25])
    assert cfg2.tcp_xyz == [0.0, 0.0, 0.25]
    with pytest.raises(ValidationError):
        DcsConfig(sensors_config="/tmp/x.yaml", tcp_xyz=[0.0, 0.18])


def test_orchestrator_applies_tcp_xyz(monkeypatch) -> None:
    from sensors_dcs.runtime import Orchestrator

    applied: list[Any] = []

    def _capture_set(raw):
        from sensors_dcs.arm_pose import normalize_tcp_xyz

        t = normalize_tcp_xyz(raw)
        applied.append(t)
        # Still install for get_tcp_xyz assertions.
        from sensors_dcs import arm_pose as ap

        ap._active_tcp_xyz = t
        return t

    monkeypatch.setattr("sensors_dcs.arm_pose.set_tcp_xyz", _capture_set)
    monkeypatch.setattr(
        "sensors_dcs.runtime.SensorManager.from_yaml",
        lambda *a, **k: type(
            "M",
            (),
            {"ids": lambda self: [], "ctx": type("C", (), {"dry_run": True})()},
        )(),
    )
    monkeypatch.setattr("sensors_dcs.runtime.VizHub", lambda: object())
    monkeypatch.setattr(
        "sensors_dcs.runtime.RecordController",
        lambda *a, **k: object(),
    )
    monkeypatch.setattr("sensors_dcs.runtime.build_agent", lambda *a, **k: object())
    monkeypatch.setattr(
        Orchestrator,
        "_sync_home_to_arm_agents",
        lambda self: None,
    )

    cfg = DcsConfig(
        sensors_config="/tmp/x.yaml",
        tcp_xyz=[0.05, 0.0, 0.20],
        agents=[
            {
                "id": "pi05",
                "type": "pi05",
                "host": "127.0.0.1",
                "port": 5000,
            }
        ],
    )
    orch = Orchestrator(cfg)
    assert orch._tcp_xyz == [0.05, 0.0, 0.20]
    assert applied == [(0.05, 0.0, 0.20)]
    assert get_tcp_xyz() == (0.05, 0.0, 0.20)
