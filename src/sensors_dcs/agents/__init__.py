from __future__ import annotations

from typing import TYPE_CHECKING

from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.agents.gello_agent import GelloAgent
from sensors_dcs.agents.gripper_read_agent import GripperReadAgent
from sensors_dcs.config import AgentConfig

if TYPE_CHECKING:
    from sensors.core.base import Sensor


def build_agent(cfg: AgentConfig, sensor: Sensor) -> BaseAgent:
    if cfg.type == "gello":
        return GelloAgent(
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
    raise ValueError(f"unsupported agent type: {cfg.type}")
