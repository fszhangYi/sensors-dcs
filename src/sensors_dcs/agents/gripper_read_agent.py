from __future__ import annotations

import math
import time
from typing import Any

from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()
from sensors.core.base import Sensor  # noqa: E402

from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.frame import Frame


class GripperReadAgent(BaseAgent):
    """Read-only agent for hik-sensors ``kind=gripper`` (DH AG95 position feedback).

    Control-layer separation: this agent only calls ``sensor.read()`` and never
    ``write()``. A future write agent can share the same underlying sensor
    instance under a lock — do not open a second Serial on the same port.
    """

    kind = "gripper_read"

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

    def read_frame(self) -> Frame:
        sample = dict(self.sensor.read())
        t_wall = float(sample.get("ts") or time.time())
        t_mono = time.perf_counter()
        self._seq += 1

        pos = sample.get("position_norm")
        dry = bool(sample.get("dry_run")) or pos is None
        if dry and self.dry_run_synth:
            pos = self._synth_position(t_wall)
            sample = {
                **sample,
                "position_norm": pos,
                "position_raw": None,
                "dry_run": True,
                "synth": True,
            }

        payload: dict[str, Any] = {
            "position_norm": pos,
            "position_raw": sample.get("position_raw"),
            "init_state": sample.get("init_state"),
            "fault": sample.get("fault"),
            "read_ms": sample.get("read_ms"),
            "port": getattr(self.sensor, "port", None) or sample.get("port"),
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

    def _synth_position(self, t_wall: float) -> float:
        """Oscillate in DH AG95 norm range (~0 .. 0.637) for dry-run viz."""
        return 0.3185 * (1.0 + math.sin(t_wall * 0.9))
