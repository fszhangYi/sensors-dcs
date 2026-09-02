from __future__ import annotations

import math
import time
from typing import Any

from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()
from sensors.core.base import Sensor  # noqa: E402

from sensors_dcs.agents.base import BaseAgent
from sensors_dcs.frame import Frame


def _as_float_list(values: Any, *, n: int, fill: float) -> list[float]:
    seq = list(values or [])
    out = [float(x) for x in seq[:n]]
    if len(out) < n:
        out.extend([fill] * (n - len(out)))
    return out


def apply_gello_affine(
    joints_raw: list[float],
    *,
    offsets: list[float],
    signs: list[float],
) -> list[float]:
    """q = (q_raw - offsets) * signs (same as hik-sensors GelloLeaderSensor / gello.py)."""
    n = len(joints_raw)
    off = _as_float_list(offsets, n=n, fill=0.0)
    sgn = _as_float_list(signs, n=n, fill=1.0)
    return [(float(joints_raw[i]) - off[i]) * sgn[i] for i in range(n)]


def invert_gello_affine(
    joints: list[float],
    *,
    offsets: list[float],
    signs: list[float],
) -> list[float]:
    """Inverse of apply_gello_affine (sign must be ±1)."""
    n = len(joints)
    off = _as_float_list(offsets, n=n, fill=0.0)
    sgn = _as_float_list(signs, n=n, fill=1.0)
    out: list[float] = []
    for i in range(n):
        s = sgn[i] if sgn[i] != 0.0 else 1.0
        out.append(float(joints[i]) / s + off[i])
    return out


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

    def calib_dict(self) -> dict[str, Any]:
        """Affine parameters loaded from sensors YAML (next to port)."""
        sensor = self.sensor
        ids = list(getattr(sensor, "joint_ids", None) or list(range(1, self._n_joints + 1)))
        n = len(ids)
        offsets = _as_float_list(getattr(sensor, "joint_offsets", None), n=n, fill=0.0)
        signs = _as_float_list(getattr(sensor, "joint_signs", None), n=n, fill=1.0)
        return {
            "joint_ids": ids,
            "joint_offsets": offsets,
            "joint_signs": [
                int(round(s)) if abs(s - round(s)) < 1e-12 else float(s) for s in signs
            ],
            "affine": "q=(q_raw-offsets)*signs",
        }

    def read_frame(self) -> Frame:
        sample = dict(self.sensor.read())
        t_wall = float(sample.get("ts") or time.time())
        t_mono = time.perf_counter()
        self._seq += 1

        calib = self.calib_dict()
        offsets = list(calib["joint_offsets"])
        signs = [float(s) for s in calib["joint_signs"]]

        joints = sample.get("joints_rad")
        joints_raw = sample.get("joints_rad_raw")
        dry = bool(sample.get("dry_run")) or joints is None
        if dry and self.dry_run_synth:
            joints = self._synth_joints(t_wall)
            joints_raw = invert_gello_affine(joints, offsets=offsets, signs=signs)
            sample = {
                **sample,
                "joints_rad": joints,
                "joints_rad_raw": joints_raw,
                "joints_raw_ticks": None,
                "dry_run": True,
                "synth": True,
            }
        elif joints is not None and joints_raw is None:
            # Older driver / missing raw: reconstruct from inverse affine
            joints_raw = invert_gello_affine(
                [float(x) for x in joints],
                offsets=offsets,
                signs=signs,
            )
        elif joints is None and joints_raw is not None:
            joints = apply_gello_affine(
                [float(x) for x in joints_raw],
                offsets=offsets,
                signs=signs,
            )

        payload: dict[str, Any] = {
            # Primary: affine-calibrated joints (use for follow / dataset / FK)
            "joints_rad": joints,
            # Raw Dynamixel radians before affine
            "joints_rad_raw": joints_raw,
            "joints_raw_ticks": sample.get("joints_raw_ticks"),
            "joint_ids": sample.get("joint_ids") or calib["joint_ids"],
            "joint_offsets": offsets,
            "joint_signs": calib["joint_signs"],
            "port": sample.get("port") or getattr(self.sensor, "port", None),
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
