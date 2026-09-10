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
    """Dry-run Read uses configured home (static), not a sine demo."""
    home = [3.1754, -1.9317, 1.8884, -1.2678, 1.6674, -0.0654]
    cfg = load_dcs_config("configs/robot_only.yaml")
    mgr = SensorManager.from_yaml(cfg.sensors_config, dry_run=True)
    sensor = mgr.get("arm-elite")
    sensor.open()
    try:
        agent = ArmAgent(agent_id="arm", sensor=sensor, hz=50.0, home_joints_rad=home)
        fr = agent.read_frame()
        assert fr.kind == "arm_read"
        assert fr.payload.get("mode") == "read_only"
        assert fr.payload.get("synth") is True
        joints = fr.payload["joints_rad"]
        assert joints == pytest.approx(home)
        # Unset home → zeros
        agent.set_home_joints_rad(None)
        fr2 = agent.read_frame()
        assert fr2.payload["joints_rad"] == pytest.approx([0.0] * 6)
    finally:
        sensor.close()


def test_orchestrator_wires_home_into_arm_agent() -> None:
    from sensors_dcs.agents.arm_agent import ArmAgent
    from sensors_dcs.runtime import Orchestrator

    cfg = load_dcs_config("configs/default.yaml")
    assert cfg.dry_run is True
    assert cfg.home_joints_rad is not None
    orch = Orchestrator(cfg)
    arm = orch.agents.get("arm")
    assert isinstance(arm, ArmAgent)
    assert arm._home_joints_rad == pytest.approx(list(cfg.home_joints_rad)[:6])
    # Shared arm_write device should be seeded so Read returns home without sine.
    sens = arm.sensor
    last = getattr(sens, "_last_cmd_rad", None)
    if last is not None:
        assert list(last)[:6] == pytest.approx(list(cfg.home_joints_rad)[:6])


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
