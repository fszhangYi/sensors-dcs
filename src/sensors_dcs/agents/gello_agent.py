from __future__ import annotations

import math
import time
from typing import Any

from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()
from sensors.core.base import Sensor  # noqa: E402

from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.frame import Frame


class GelloAgent(BaseAgent):
    """Upper-layer agent for hik-sensors ``kind=gello`` (Dynamixel leader)."""

    kind = "gello"

    def __init__(
        self,
        *,
        agent_id: str,
        sensor: Sensor,
        hz: float = 50.0,
        buffer_frames: int = 64,
        dry_run_synth: bool = True,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            sensor=sensor,
            hz=hz,
            buffer_frames=buffer_frames,
        )
        self.dry_run_synth = dry_run_synth
        n = len(list(getattr(sensor, "joint_ids", None) or [1, 2, 3, 4, 5, 6, 7]))
        self._n_joints = n

    def read_frame(self) -> Frame:
        sample = dict(self.sensor.read())
        t_wall = float(sample.get("ts") or time.time())
        t_mono = time.perf_counter()
        self._seq += 1

        joints = sample.get("joints_rad")
        dry = bool(sample.get("dry_run")) or joints is None
        if dry and self.dry_run_synth:
            joints = self._synth_joints(t_wall)
            sample = {
                **sample,
                "joints_rad": joints,
                "joints_raw_ticks": None,
                "dry_run": True,
                "synth": True,
            }

        payload: dict[str, Any] = {
            "joints_rad": joints,
            "joints_raw_ticks": sample.get("joints_raw_ticks"),
            "joint_ids": sample.get("joint_ids") or list(getattr(self.sensor, "joint_ids", [])),
            "port": sample.get("port"),
            "dry_run": dry,
            "synth": bool(sample.get("synth")),
        }
        return Frame(
            sensor_id=self.sensor_id,
            agent_id=self.agent_id,
            kind=self.kind,
            t_wall=t_wall,
            t_mono=t_mono,
            payload=payload,
            seq=self._seq,
        )

    def _synth_joints(self, t_wall: float) -> list[float]:
        """Sine demo so dry-run viz looks alive without hardware."""
        out: list[float] = []
        for i in range(self._n_joints):
            out.append(0.35 * math.sin(t_wall * (0.7 + 0.11 * i) + i * 0.4))
        return out
