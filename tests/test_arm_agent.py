from __future__ import annotations

import math

import pytest

from sensors_dcs.agents.arm_agent import ArmAgent
from sensors_dcs.config import load_dcs_config
from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()
from sensors import SensorManager  # noqa: E402
from sensors.core.base import SensorCapability  # noqa: E402
from sensors.core.kinds import SensorKind  # noqa: E402
from sensors.core.registry import get_sensor_class  # noqa: E402
from sensors.drivers import load_all_drivers  # noqa: E402


def test_arm_read_sensor_registered() -> None:
    load_all_drivers()
    cls = get_sensor_class("arm_read")
    assert cls.kind == SensorKind.ARM_READ
    assert not (cls.capabilities & SensorCapability.CONTROL)


def test_robot_only_config_loads() -> None:
    cfg = load_dcs_config("configs/robot_only.yaml")
    assert cfg.agents[0].type == "arm"
    assert cfg.agents[0].sensor_id == "arm-elite"
    mgr = SensorManager.from_yaml(cfg.sensors_config, dry_run=True)
    sensor = mgr.get("arm-elite")
    assert sensor.kind.value == "arm_read"


def test_arm_agent_dry_run_synth() -> None:
    cfg = load_dcs_config("configs/robot_only.yaml")
    mgr = SensorManager.from_yaml(cfg.sensors_config, dry_run=True)
    sensor = mgr.get("arm-elite")
    sensor.open()
    try:
        agent = ArmAgent(agent_id="arm", sensor=sensor, hz=50.0)
        fr = agent.read_frame()
        assert fr.kind == "arm_read"
        assert fr.payload.get("mode") == "read_only"
        assert fr.payload.get("synth") is True
        joints = fr.payload["joints_rad"]
        assert len(joints) == 6
        assert all(isinstance(x, float) for x in joints)
    finally:
        sensor.close()


def test_arm_read_write_forbidden() -> None:
    cfg = load_dcs_config("configs/robot_only.yaml")
    mgr = SensorManager.from_yaml(cfg.sensors_config, dry_run=True)
    sensor = mgr.get("arm-elite")
    sensor.open()
    try:
        with pytest.raises(RuntimeError, match="read-only"):
            sensor.write({"joints": [0.0] * 6})
    finally:
        sensor.close()


def test_arm_read_radians_from_degrees() -> None:
    """machinePos is degrees in Elite monitor; agent/sensor expose radians."""
    deg = [10.0, -20.0, 30.0, 0.0, 45.0, -90.0]
    rad = [math.radians(x) for x in deg]
    assert rad[0] == pytest.approx(math.radians(10.0))
    assert rad[-1] == pytest.approx(-math.pi / 2)
