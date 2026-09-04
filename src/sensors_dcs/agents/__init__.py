from __future__ import annotations

from typing import TYPE_CHECKING

from sensors_dcs.agents.arm_agent import ArmAgent
from sensors_dcs.agents.arm_write_agent import ArmWriteAgent
from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.agents.gello_agent import GelloAgent
from sensors_dcs.agents.gripper_read_agent import GripperReadAgent
from sensors_dcs.agents.gripper_write_agent import GripperWriteAgent
from sensors_dcs.agents.pi05_agent import Pi05ClientAgent
from sensors_dcs.agents.realsense_agent import RealSenseAgent
from sensors_dcs.config import AgentConfig, Pi05Config

if TYPE_CHECKING:
    from sensors.core.base import Sensor


def build_agent(
    cfg: AgentConfig,
    sensor: Sensor | None = None,
    *,
    pi05_defaults: Pi05Config | None = None,
) -> BaseAgent:
    if cfg.type == "pi05":
        d = pi05_defaults or Pi05Config()
        return Pi05ClientAgent(
            agent_id=cfg.id,
            hz=cfg.hz,
            buffer_frames=cfg.buffer_frames,
            host=d.host,
            port=d.port,
            prompt=d.prompt,
            camera_map=dict(d.camera_map),
            arm_agent_id=d.arm_agent_id,
            gripper_agent_id=d.gripper_agent_id,
        )
    if sensor is None:
        raise ValueError(f"agent {cfg.id!r} type={cfg.type!r} requires a sensor")
    if cfg.type == "gello":
        return GelloAgent(
            agent_id=cfg.id,
            sensor=sensor,
            hz=cfg.hz,
            buffer_frames=cfg.buffer_frames,
        )
    if cfg.type == "arm":
        return ArmAgent(
            agent_id=cfg.id,
            sensor=sensor,
            hz=cfg.hz,
            buffer_frames=cfg.buffer_frames,
        )
    if cfg.type == "arm_write":
        return ArmWriteAgent(
            agent_id=cfg.id,
            sensor=sensor,
            hz=cfg.hz,
            buffer_frames=cfg.buffer_frames,
        )
    if cfg.type == "gripper_read":
        return GripperReadAgent(
            agent_id=cfg.id,
            sensor=sensor,
            hz=cfg.hz,
            buffer_frames=cfg.buffer_frames,
        )
    if cfg.type == "gripper_write":
        return GripperWriteAgent(
            agent_id=cfg.id,
            sensor=sensor,
            hz=cfg.hz,
            buffer_frames=cfg.buffer_frames,
        )
    if cfg.type == "realsense":
        return RealSenseAgent(
            agent_id=cfg.id,
            sensor=sensor,
            hz=cfg.hz,
            buffer_frames=cfg.buffer_frames,
        )
    raise ValueError(f"unsupported agent type: {cfg.type}")
