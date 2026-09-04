from __future__ import annotations

import math
import time
from typing import Any

from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()
from sensors.core.base import Sensor  # noqa: E402

from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.frame import Frame


class ArmAgent(BaseAgent):
    """Read-only Elite arm agent (``kind=arm_read``) — never commands the robot.

    Joints come from Elite EC ``monitor_info.machinePos`` (degrees → radians),
    matching ``elite_robot.get_joint_state`` / demo_test kinematics units.
    """

    kind = "arm_read"

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
        self._n_joints = int(getattr(sensor, "num_joints", None) or 6)

    def read_frame(self) -> Frame:
        sample = dict(self.sensor.read())
        t_wall = float(sample.get("ts") or time.time())
        t_mono = time.perf_counter()
        self._seq += 1

        joints = sample.get("joints_rad")
        dry = bool(sample.get("dry_run")) or joints is None
        # Only synth when the sensor has no joints yet — if sharing arm_write in
        # dry_run, keep the driver's reported pose so jog stays relative to it.
        if dry and self.dry_run_synth and joints is None:
            joints = self._synth_joints(t_wall)
            sample = {
                **sample,
                "joints_rad": joints,
                "joints_deg": [math.degrees(x) for x in joints],
                "dry_run": True,
                "synth": True,
            }

        payload: dict[str, Any] = {
            "joints_rad": joints,
            "joints_deg": sample.get("joints_deg"),
            "num_joints": sample.get("num_joints") or self._n_joints,
            "robot_ip": sample.get("robot_ip") or getattr(self.sensor, "robot_ip", None),
            "endpoint": sample.get("endpoint"),
            "backend": sample.get("backend") or "elite_ec",
            "mode": sample.get("mode") or "read_only",
            "dry_run": dry,
            "synth": bool(sample.get("synth")),
        }
        try:
            from sensors_dcs.arm_pose import cartesian_payload

            payload.update(cartesian_payload(joints if isinstance(joints, list) else None))
        except Exception:  # noqa: BLE001
            payload["cartesian_xyzrpy"] = None
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
        """Sine demo so dry-run viz looks alive without the controller."""
        out: list[float] = []
        for i in range(self._n_joints):
            out.append(0.25 * math.sin(t_wall * (0.55 + 0.09 * i) + i * 0.35))
        return out
